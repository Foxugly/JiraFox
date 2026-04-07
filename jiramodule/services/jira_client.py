import hashlib
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from jira import JIRA

from ..models import JiraConfiguration
from ..utils.datetime_utils import _to_hours
from .kanban_metrics import KanbanMetricsService


class JiraManager:
    JIRA_ONLY_STANDARD_TYPES = "issuetype in standardIssueTypes()"
    JIRA_REQUEST_TIMEOUT_SECONDS = 30
    SPRINT_ISSUES_CACHE_TTL_SECONDS = 300
    THROUGHPUT_DEFAULT_DAYS = 7
    RETRY_ATTEMPTS = 3
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
        last_error = None
        for attempt in range(1, self.RETRY_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.RETRY_ATTEMPTS:
                    raise
                time.sleep(self.RETRY_DELAY_SECONDS)
        raise last_error

    def _issue_fields(self, detailed: bool) -> str:
        fields = [
            "summary",
            "status",
            "assignee",
            "issuetype",
            "project",
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

    def connect(self) -> None:
        if not self.cfg.jira_url:
            raise RuntimeError("Missing jira_url configuration.")
        if not self.cfg.jira_token:
            raise RuntimeError("Missing jira_token configuration.")

        options: Dict[str, Any] = {
            "server": self.cfg.jira_url,
            "verify": True,
            "timeout": self.JIRA_REQUEST_TIMEOUT_SECONDS,
        }
        self._jira = JIRA(options=options, token_auth=self.cfg.jira_token)
        self._with_retries(self.jira.myself)
        self._story_points_field_id = self._detect_story_points_field_id()

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
            max_results=500,
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
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        sprints = self._with_retries(self.jira.sprints, board_id, state=state, maxResults=max_results)
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

    def get_current_sprint(self, board_id: int = 0, max_results: int = 50) -> Optional[Dict[str, Any]]:
        """
        Find the active sprint for the given board.
        Returns None if not found.
        """
        if board_id == 0:
            board_id = self.cfg.jira_board_id
        sprints = self.list_sprints(board_id, state="active", max_results=max_results)
        candidates = [sprint for sprint in sprints if isinstance(sprint.get("id"), int)]
        if not candidates:
            return None
        return min(candidates, key=lambda sprint: sprint["id"])

    def get_next_sprint(self, board_id: int, max_results: int = 50) -> Optional[Dict[str, Any]]:
        current_sprint = self.get_current_sprint(board_id)
        if not current_sprint:
            return None
        current_sprint_id = int(current_sprint.get("id"))
        sprints = self.list_sprints(board_id, state="future", max_results=max_results)
        candidates = [
            sprint
            for sprint in sprints
            if isinstance(sprint.get("id"), int) and sprint["id"] > current_sprint_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda sprint: sprint["id"])

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
        key = f"sprint_issues:{self.cfg.user_id}:{sprint_id}:{int(full)}:{max_results}:{extra_hash}"
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
            "assignee": self._serialize_assignee(getattr(fields, "assignee", None)),
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
            "assignee": self._serialize_assignee(getattr(fields, "assignee", None)),
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

    def throughput_7d(self, days: int = THROUGHPUT_DEFAULT_DAYS):
        query = (
            f"project = {self.cfg.jira_project_id} "
            f"AND issuetype in standardIssueTypes() "
            f"AND status = Done "
            f"AND resolved >= -{int(days)}d "
            "ORDER BY resolved DESC"
        )
        return self.run_jql(query, full=False)
