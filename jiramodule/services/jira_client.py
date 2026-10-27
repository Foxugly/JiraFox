import hashlib
import time
import urllib.parse
from datetime import date
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from jira import JIRA

from ..exceptions import is_transient_jira_error
from ..models import JiraConfiguration
from ..utils.datetime_utils import _to_hours
from .jql import JqlBuilder, STANDARD_ISSUE_TYPES, quote_value
from .kanban_metrics import KanbanMetricsService


def _status_category_key(fields: Any) -> Optional[str]:
    """'new', 'indeterminate' or 'done': unlike status and category names, the key is never renamed or translated."""
    category = getattr(getattr(fields, "status", None), "statusCategory", None)
    return getattr(category, "key", None)


class JiraManager:
    JIRA_ONLY_STANDARD_TYPES = "issuetype in standardIssueTypes()"
    # (connect, read) seconds, passed to requests by the jira client.
    JIRA_REQUEST_TIMEOUT_SECONDS = (5, 30)
    SPRINT_ISSUES_CACHE_TTL_SECONDS = 300
    CONNECTION_CACHE_TTL_SECONDS = 3600
    THROUGHPUT_DEFAULT_DAYS = 7
    WORKLOG_SEARCH_PAGE_SIZE = 50
    RETRY_ATTEMPTS = 2
    RETRY_DELAY_SECONDS = 1

    def __init__(self, jc: JiraConfiguration) -> None:
        self.metrics = KanbanMetricsService()
        self.cfg = jc
        self._jira: Optional[JIRA] = None
        self._story_points_field_id: Optional[str] = None

    @property
    def jira(self) -> JIRA:
        if not self._jira:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._jira

    def _with_retries(self, func, *args, **kwargs):
        # 4xx (bad token, missing permission, unknown issue) are never retried: retrying a 401
        # only slows the page down and can trigger Jira's CAPTCHA / account lockout.
        for attempt in range(1, self.RETRY_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if attempt == self.RETRY_ATTEMPTS or not is_transient_jira_error(exc):
                    raise
                time.sleep(self.RETRY_DELAY_SECONDS)

    def _issue_fields(self, detailed: bool) -> str:
        fields = [
            "summary",
            "status",
            "assignee",
            "issuetype",
            "project",
            "labels",
        ]
        if detailed:
            fields.extend(["timetracking", "created", "updated", "resolutiondate"])
            if self._story_points_field_id:
                fields.append(self._story_points_field_id)
        return ",".join(fields)

    def _serialize_assignee(self, assignee_obj: Any) -> Optional[dict[str, Any]]:
        if not assignee_obj:
            return None
        return {
            "displayName": getattr(assignee_obj, "displayName", None),
            "emailAddress": getattr(assignee_obj, "emailAddress", None),
            "accountId": getattr(assignee_obj, "accountId", None),
            "name": getattr(assignee_obj, "name", None),
        }

    def _connection_cache_key(self) -> str:
        fingerprint = hashlib.sha256(f"{self.cfg.jira_url}|{self.cfg.jira_token}".encode()).hexdigest()[:24]
        return f"jira_connection:{self.cfg.pk}:{fingerprint}"

    def _build_client(self, *, get_server_info: bool) -> JIRA:
        return JIRA(
            options={"server": self.cfg.jira_url, "verify": True},
            token_auth=self.cfg.jira_token,
            timeout=self.JIRA_REQUEST_TIMEOUT_SECONDS,
            get_server_info=get_server_info,
            # The library's own retry waits 20-60 s per attempt on network errors; _with_retries is enough.
            max_retries=0,
        )

    def connect(self, *, force: bool = False) -> None:
        """
        Open the Jira client. The first connection validates the credentials and detects the
        server version and story points field; that metadata is cached for an hour (keyed on the
        URL + token) so later requests skip those three round-trips.
        """
        if not self.cfg.jira_url:
            raise RuntimeError("Missing jira_url configuration.")
        if not self.cfg.jira_token:
            raise RuntimeError("Missing jira_token configuration.")

        key = self._connection_cache_key()
        cached = None if force else cache.get(key)
        if cached:
            self._jira = self._build_client(get_server_info=False)
            # Restore what get_server_info=True would have fetched (drives Cloud vs Server API choices).
            self._jira._version = tuple(cached["version"])
            self._jira.deploymentType = cached["deployment_type"]
            self._story_points_field_id = cached["story_points_field_id"]
            return

        self._jira = self._with_retries(self._build_client, get_server_info=True)
        self._with_retries(self.jira.myself)
        self._story_points_field_id = self._detect_story_points_field_id()
        cache.set(
            key,
            {
                "version": list(self._jira._version),
                "deployment_type": self._jira.deploymentType,
                "story_points_field_id": self._story_points_field_id,
            },
            self.CONNECTION_CACHE_TTL_SECONDS,
        )

    def forget_connection(self) -> None:
        cache.delete(self._connection_cache_key())

    def server_info(self) -> dict[str, Any]:
        return self._with_retries(self.jira.server_info)

    def get_dict_statuses(self) -> dict:
        dict_statuses = {
            "Done": {"order": 2, "statuses": []},
            "To Do": {"order": 0, "statuses": []},
            "In Progress": {"order": 1, "statuses": []},
        }

        for status in self.list_statuses():
            category = status.get("statusCategory")
            if category in dict_statuses:
                dict_statuses[category]["statuses"].append(status)

        return dict_statuses

    def list_sprints_for_current_board(self, states):
        if not self.cfg.jira_board_id:
            raise RuntimeError("Missing jira_board_id configuration.")

        return self.list_sprints(
            board_id=self.cfg.jira_board_id,
            state=states,
        )

    def list_statuses(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for status in self._with_retries(self.jira.statuses):
            out.append(
                {
                    "id": getattr(status, "id", None),
                    "name": getattr(status, "name", None),
                    "statusCategory": getattr(getattr(status, "statusCategory", None), "name", None),
                }
            )
        return out

    def list_projects(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for project in self._with_retries(self.jira.projects):
            out.append(
                {
                    "id": getattr(project, "id", None),
                    "key": getattr(project, "key", None),
                    "name": getattr(project, "name", None),
                }
            )
        return out

    def list_views(self) -> List[Dict[str, Any]]:
        try:
            boards = self._with_retries(self.jira.boards)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Unable to list Jira boards. Check Jira Software availability and account permissions."
            ) from exc

        out: List[Dict[str, Any]] = []
        for board in boards:
            out.append(
                {
                    "id": getattr(board, "id", None),
                    "name": getattr(board, "name", None),
                    "type": getattr(board, "type", None),
                }
            )
        return out

    def list_sprints(
        self,
        board_id: int,
        *,
        state: str = "active,future,closed",
    ) -> List[Dict[str, Any]]:
        # maxResults=0 makes python-jira follow every page. With a number it reads a single page,
        # and Jira caps that page at 50 sprints, oldest first: the latest closed sprints were lost.
        sprints = self._with_retries(self.jira.sprints, board_id, state=state, maxResults=0)
        out: List[Dict[str, Any]] = []
        for sprint in sprints:
            out.append(
                {
                    "id": getattr(sprint, "id", None),
                    "name": getattr(sprint, "name", None),
                    "state": getattr(sprint, "state", None),
                    "startDate": getattr(sprint, "startDate", None),
                    "endDate": getattr(sprint, "endDate", None),
                    "completeDate": getattr(sprint, "completeDate", None),
                    "originBoardId": getattr(sprint, "originBoardId", None),
                    "url": self.get_url_jql(f"sprint={sprint.id}"),
                }
            )
        return out

    def get_current_sprint(self, board_id: int = 0) -> Optional[Dict[str, Any]]:
        """
        Find the active sprint for the given board.
        Returns None if not found.
        """
        if board_id == 0:
            board_id = self.cfg.jira_board_id
        sprints = self.list_sprints(board_id, state="active")
        candidates = [sprint for sprint in sprints if isinstance(sprint.get("id"), int)]
        if not candidates:
            return None
        return min(candidates, key=lambda sprint: sprint["id"])

    def get_next_sprint(self, board_id: int) -> Optional[Dict[str, Any]]:
        current_sprint = self.get_current_sprint(board_id)
        if not current_sprint:
            return None
        current_sprint_id = int(current_sprint.get("id"))
        sprints = self.list_sprints(board_id, state="future")
        candidates = [
            sprint
            for sprint in sprints
            if isinstance(sprint.get("id"), int) and sprint["id"] > current_sprint_id
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda sprint: sprint["id"])

    def get_sprint(self, sprint_id: int) -> Dict[str, Any]:
        sprint = self._with_retries(self.jira.sprint, sprint_id)
        return {
            "id": getattr(sprint, "id", None),
            "name": getattr(sprint, "name", None),
            "state": getattr(sprint, "state", None),
            "startDate": getattr(sprint, "startDate", None),
            "endDate": getattr(sprint, "endDate", None),
            "completeDate": getattr(sprint, "completeDate", None),
            "originBoardId": getattr(sprint, "originBoardId", None),
            "goal": getattr(sprint, "goal", None),
            "url": self.get_url_jql(f"sprint={sprint_id}"),
        }

    def _search_issues(self, jql: str, *, max_results: int = 1000, detailed: bool = False):
        expand = "changelog" if detailed else None
        return self._with_retries(
            self.jira.search_issues,
            jql,
            maxResults=max_results,
            fields=self._issue_fields(detailed),
            expand=expand,
        )

    def get_cached_sprint_issues(self, sprint_id, jql_extra=JIRA_ONLY_STANDARD_TYPES, full=True, max_results: int = 1000):
        extra = (jql_extra or "").strip()
        extra_hash = hashlib.md5(extra.encode()).hexdigest()
        key = f"sprint_issues:{self.cfg.user_id}:{self.cfg.pk}:{sprint_id}:{int(full)}:{max_results}:{extra_hash}"
        data = cache.get(key)
        if data is None:
            data = self.get_sprint_issues(
                sprint_id,
                jql_extra=jql_extra,
                max_results=max_results,
                full=full,
            )
            cache.set(key, data, self.SPRINT_ISSUES_CACHE_TTL_SECONDS)
        return data

    def get_sprint_issues(
        self,
        sprint_id: int,
        *,
        jql_extra: str = "",
        max_results: int = 1000,
        full: bool = False,
    ) -> List[Dict[str, Any]]:
        jql = f"sprint = {int(sprint_id)}"
        if jql_extra.strip():
            jql = f"({jql}) AND ({jql_extra.strip()})"

        return self.run_jql(jql, max_results=max_results, full=full)

    def run_jql(self, jql: str, *, max_results: int = 1000, full: bool = True) -> List[Dict[str, Any]]:
        issues = self._search_issues(jql, max_results=max_results, detailed=full)
        serializer = self._ticket_info_detail_from_issue if full else self._ticket_info_from_issue
        return [serializer(issue) for issue in issues]

    def get_issue(self, issue_key: str, full: bool = True) -> Dict[str, Any]:
        issue = self._with_retries(
            self.jira.issue,
            issue_key,
            fields=self._issue_fields(full),
            expand="changelog" if full else None,
        )
        return self._ticket_info_detail_from_issue(issue) if full else self._ticket_info_from_issue(issue)

    def get_url_jql(self, jql: str) -> str:
        params = {"jql": jql}
        encoded = urllib.parse.urlencode(params)
        return f"{self.cfg.jira_url}issues/?{encoded}"

    def get_issue_url(self, issue_key: str) -> str:
        return f"{self.cfg.jira_url}browse/{issue_key}"

    def get_sprint_report(self, sprint_id: int, jql_extra: str = "", max_results: int = 1000) -> Dict[str, Any]:
        sprint = self.get_sprint(sprint_id)
        tickets = self.get_cached_sprint_issues(
            sprint_id,
            jql_extra=jql_extra,
            max_results=max_results,
        )
        return {"sprint": sprint, "tickets": tickets}

    def ticket_info(self, issue_key: str) -> Dict[str, Any]:
        return self.get_issue(issue_key, full=False)

    def _ticket_info_from_issue(self, issue: Any) -> Dict[str, Any]:
        fields = issue.fields
        return {
            "key": issue.key,
            "summary": getattr(fields, "summary", None),
            "project": getattr(getattr(fields, "project", None), "key", None),
            "issueType": getattr(getattr(fields, "issuetype", None), "name", None),
            "state": getattr(getattr(fields, "status", None), "name", None),
            "status_category": _status_category_key(fields),
            "statusCategory": getattr(getattr(getattr(fields, "status", None), "statusCategory", None), "name", None),
            "assignee": self._serialize_assignee(getattr(fields, "assignee", None)),
            "labels": list(getattr(fields, "labels", None) or []),
            "url": self.get_issue_url(issue.key),
        }

    def _ticket_info_detail_from_issue(self, issue: Any) -> Dict[str, Any]:
        fields = issue.fields
        timetracking = getattr(fields, "timetracking", None)
        original_estimate_sec = getattr(timetracking, "originalEstimateSeconds", None) if timetracking else None
        remaining_sec = getattr(timetracking, "remainingEstimateSeconds", None) if timetracking else None
        spent_sec = getattr(timetracking, "timeSpentSeconds", None) if timetracking else None

        return {
            "key": issue.key,
            "summary": getattr(fields, "summary", None),
            "project": getattr(getattr(fields, "project", None), "key", None),
            "issueType": getattr(getattr(fields, "issuetype", None), "name", None),
            "state": getattr(getattr(fields, "status", None), "name", None),
            "status_category": _status_category_key(fields),
            "statusCategory": getattr(getattr(getattr(fields, "status", None), "statusCategory", None), "name", None),
            "assignee": self._serialize_assignee(getattr(fields, "assignee", None)),
            "labels": list(getattr(fields, "labels", None) or []),
            "estimation": {
                "story_points": self._get_story_points(issue),
                "original_estimate_hours": _to_hours(original_estimate_sec),
            },
            "remaining_hours": _to_hours(remaining_sec),
            "spent_hours": _to_hours(spent_sec),
            "kanban_metrics": self.metrics.compute(issue),
            "history": self.metrics.history(issue),
            "url": self.get_issue_url(issue.key),
        }

    def _detect_story_points_field_id(self) -> Optional[str]:
        try:
            fields = self._with_retries(self.jira.fields)
            heuristic_match = None
            for field in fields:
                name = (field.get("name") or "").strip().lower()
                field_id = field.get("id")
                if not field_id:
                    continue
                if name in {"story points", "story point estimate"}:
                    return field_id
                if not heuristic_match and "story" in name and "point" in name:
                    heuristic_match = field_id
            return heuristic_match
        except Exception:  # noqa: BLE001
            return None

    def _get_story_points(self, issue: Any) -> Optional[float]:
        field_id = self._story_points_field_id
        if not field_id:
            return None
        try:
            value = getattr(issue.fields, field_id, None)
            if value is None:
                return None
            return float(value)
        except Exception:  # noqa: BLE001
            return None

    def update_issue_fields(self, issue_key: str, fields: dict[str, Any]) -> Dict[str, Any]:
        issue = self._with_retries(self.jira.issue, issue_key)
        self._with_retries(issue.update, fields=fields)
        return self.get_issue(issue_key, full=False)

    def update_issue_labels(
        self,
        issue_key: str,
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
        current_labels: list[str] | None = None,
    ) -> Dict[str, Any]:
        labels = current_labels
        if labels is None:
            issue = self._with_retries(self.jira.issue, issue_key, fields="labels")
            labels = list(getattr(issue.fields, "labels", None) or [])

        next_labels = [label for label in labels if label not in set(remove_labels or [])]
        for label in add_labels or []:
            if label not in next_labels:
                next_labels.append(label)

        return self.update_issue_fields(issue_key, {"labels": next_labels})

    def throughput_7d(self, days: int = THROUGHPUT_DEFAULT_DAYS):
        if not self.cfg.jira_project_id:
            return []
        query = (
            f"project = {self.cfg.jira_project_id} "
            f"AND issuetype in standardIssueTypes() "
            f"AND statusCategory = Done "
            f"AND resolved >= -{int(days)}d "
            "ORDER BY resolved DESC"
        )
        return self.run_jql(query, full=False)

    def get_sprint_issues_with_worklogs(self, sprint_id: int, max_results: int = 1000) -> List[Dict[str, Any]]:
        """
        Every issue of the sprint (sub-tasks included, since that is where time is often logged)
        with its status changes and all its worklogs.
        """
        return self._issues_with_worklogs(f"sprint = {int(sprint_id)} ORDER BY key ASC", max_results)

    def get_issues_with_worklogs_by(
        self, usernames: List[str], since: date, max_results: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Every issue, in any sprint or none, on which one of `usernames` logged work and that was
        updated since `since`. Logging work updates the issue, so this covers every worklog of the
        period; the caller keeps the worklogs it wants by date.
        """
        # No worklogDate: Jira Server scans every worklog for it and the search times out.
        authors = ", ".join(quote_value(username) for username in usernames)
        jql = f'worklogAuthor in ({authors}) AND updated >= "{since:%Y-%m-%d}" ORDER BY key ASC'
        return self._issues_with_worklogs(jql, max_results)

    def _issues_with_worklogs(self, jql: str, max_results: int) -> List[Dict[str, Any]]:
        # Worklogs and changelogs make each issue heavy: one request for hundreds of issues
        # outlasts the read timeout. Small pages keep every request short.
        issues = []
        while len(issues) < max_results:
            page = self._with_retries(
                self.jira.search_issues,
                jql,
                startAt=len(issues),
                maxResults=min(self.WORKLOG_SEARCH_PAGE_SIZE, max_results - len(issues)),
                fields="summary,status,assignee,created,timetracking,worklog",
                expand="changelog",
            )
            issues.extend(page)
            total = getattr(page, "total", None)
            if not page or (total is not None and len(issues) >= total):
                break
        out: List[Dict[str, Any]] = []
        for issue in issues:
            fields = issue.fields
            worklog_page = getattr(fields, "worklog", None)
            worklogs = list(getattr(worklog_page, "worklogs", None) or [])
            # Search results only embed the first 20 worklogs of an issue.
            if (getattr(worklog_page, "total", 0) or 0) > len(worklogs):
                worklogs = self._with_retries(self.jira.worklogs, issue.key)

            timetracking = getattr(fields, "timetracking", None)
            histories = getattr(getattr(issue, "changelog", None), "histories", None) or []
            out.append(
                {
                    "key": issue.key,
                    "summary": getattr(fields, "summary", None) or "",
                    "state": getattr(getattr(fields, "status", None), "name", None),
                    "created": getattr(fields, "created", None),
                    "assignee": self._serialize_assignee(getattr(fields, "assignee", None)),
                    "original_estimate_hours": _to_hours(
                        getattr(timetracking, "originalEstimateSeconds", None) if timetracking else None
                    ),
                    "status_changes": [
                        {
                            "at": getattr(history, "created", None),
                            "from": getattr(item, "fromString", None),
                            "to": getattr(item, "toString", None),
                        }
                        for history in histories
                        for item in (getattr(history, "items", None) or [])
                        if getattr(item, "field", "") == "status"
                    ],
                    "worklogs": [
                        {
                            "author": self._serialize_assignee(getattr(worklog, "author", None)),
                            "started": getattr(worklog, "started", None),
                            "hours": _to_hours(getattr(worklog, "timeSpentSeconds", None)) or 0.0,
                            # Exact time: summing the rounded hours drifts (3 x 20 min = 0.99 h).
                            "seconds": getattr(worklog, "timeSpentSeconds", None) or 0,
                        }
                        for worklog in worklogs
                    ],
                    "url": self.get_issue_url(issue.key),
                }
            )
        return out

    def create_issue(
            self,
            *,
            project_key: str,
            summary: str,
            issue_type: str = "Task",
            description: str = "",
            assignee_account_id: str | None = None,
    ) -> Dict[str, Any]:
        payload = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }

        if description:
            payload["description"] = description

        if assignee_account_id:
            payload["assignee"] = {
                "accountId": assignee_account_id,
            }
        if issue_type == "Epic":
            payload["customfield_10102"] = summary

        issue = self._with_retries(
            self.jira.create_issue,
            fields=payload,
        )

        return self.get_issue(issue.key)
