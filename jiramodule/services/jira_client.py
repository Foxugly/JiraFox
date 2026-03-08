import hashlib
import urllib.parse
from typing import Optional, List, Any, Dict
from django.core.cache import cache

from jira import JIRA
from .kanban_metrics import KanbanMetricsService
from ..models import JiraConfiguration
from ..utils.datetime_utils import _to_hours


class JiraManager:
    JIRA_ONLY_STANDARD_TYPES = "issuetype in standardIssueTypes()"

    def __init__(self, jc: JiraConfiguration) -> None:
        self.metrics = KanbanMetricsService()
        self.cfg = jc
        self._jira: Optional[JIRA] = None
        self._story_points_field_id: Optional[str] = None  # e.g. "customfield_10016"

    @property
    def jira(self) -> JIRA:
        if not self._jira:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._jira

    def connect(self) -> None:
        """
        Connect via token_auth (python-jira).
        """
        if not self.cfg.jira_url:
            raise RuntimeError("jira_url manquant")
        if not self.cfg.jira_token:
            raise RuntimeError("jira_token manquant")

        options: Dict[str, Any] = {
            "server": self.cfg.jira_url,
            "verify": True,  # ou False si tu es en self-signed, mais évite si possible
            "timeout": 30,
        }
        self._jira = JIRA(options=options, token_auth=self.cfg.jira_token)
        _ = self._jira.myself()
        self._story_points_field_id = self._detect_story_points_field_id()

    def server_info(self) -> dict[str, Any]:
        return self._jira.server_info()

    def get_dict_statuses(self) -> dict:
        dict_statuses = {
            "Done": {"order": 2, "statuses": []},
            "To Do": {"order": 0, "statuses": []},
            "In Progress": {"order": 1, "statuses": []},
        }

        for s in self.list_statuses():
            category = s.get("statusCategory")
            if category in dict_statuses:
                dict_statuses[category]["statuses"].append(s)

        return dict_statuses

    # ---------------------------
    # Core listings
    # ---------------------------

    def list_sprints_for_current_board(self, states):
        if not self.cfg.jira_board_id:
            raise RuntimeError("Aucun jira_board_id configuré")

        return self.list_sprints(
            board_id=self.cfg.jira_board_id,
            state=states,
            max_results=500,
        )

    def list_statuses(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for st in self.jira.statuses():
            out.append({
                "id": getattr(st, "id", None),
                "name": getattr(st, "name", None),
                "statusCategory": getattr(getattr(st, "statusCategory", None), "name", None),
            })
        return out

    def list_projects(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in self.jira.projects():
            out.append({
                "id": getattr(p, "id", None),
                "key": getattr(p, "key", None),
                "name": getattr(p, "name", None),
            })
        return out

    def list_views(self) -> List[Dict[str, Any]]:
        """
        "vues" in Jira Software are typically Boards (Scrum/Kanban).
        """
        try:
            boards = self.jira.boards()
        except Exception as e:
            raise RuntimeError(
                "Impossible de lister les vues/boards. "
                "Vérifie que Jira Software (Agile) est installé et que ton compte a les droits."
            ) from e

        out: List[Dict[str, Any]] = []
        for b in boards:
            out.append({
                "id": getattr(b, "id", None),
                "name": getattr(b, "name", None),
                "type": getattr(b, "type", None),  # 'scrum' / 'kanban'
            })
        return out

    def list_sprints(self, board_id: int, *, state: str = "active,future,closed", max_results: int = 50) -> List[
        Dict[str, Any]]:
        """
        List sprints for a given board.
        state can be "active", "future", "closed" or comma-separated.
        """
        sprints = self.jira.sprints(board_id, state=state, maxResults=max_results)
        out: List[Dict[str, Any]] = []
        for s in sprints:
            out.append({
                "id": getattr(s, "id", None),
                "name": getattr(s, "name", None),
                "state": getattr(s, "state", None),
                "startDate": getattr(s, "startDate", None),
                "endDate": getattr(s, "endDate", None),
                "completeDate": getattr(s, "completeDate", None),
                "originBoardId": getattr(s, "originBoardId", None),
                "url": self.get_url_jql(f"sprint={s.id}"),
            })
        return out

    def get_current_sprint(self, board_id: int=0, max_results: int = 50) -> Optional[Dict[str, Any]]:
        """
        Find the next sprint on a board with id strictly greater than current_sprint_id.
        Returns None if not found.
        """
        if board_id == 0:
            board_id = self.cfg.jira_board_id
        sprints = self.list_sprints(board_id, state="active", max_results=max_results)
        candidates = [s for s in sprints if isinstance(s.get("id"), int)]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s["id"])

    def get_next_sprint(self, board_id: int, max_results: int = 50) -> Optional[Dict[str, Any]]:
        current_sprint = self.get_current_sprint(board_id)
        if not current_sprint:
            return None
        current_sprint_id = int(current_sprint.get("id"))
        sprints = self.list_sprints(board_id, state="future", max_results=max_results)
        candidates = [s for s in sprints if isinstance(s.get("id"), int) and s["id"] > current_sprint_id]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s["id"])

    # ---------------------------
    # Sprint details + issues
    # ---------------------------

    def get_sprint(self, sprint_id: int) -> Dict[str, Any]:
        s = self.jira.sprint(sprint_id)
        return {
            "id": getattr(s, "id", None),
            "name": getattr(s, "name", None),
            "state": getattr(s, "state", None),
            "startDate": getattr(s, "startDate", None),
            "endDate": getattr(s, "endDate", None),
            "completeDate": getattr(s, "completeDate", None),
            "originBoardId": getattr(s, "originBoardId", None),
            "goal": getattr(s, "goal", None),
        }

    def _search_issues(self, jql: str, max_results: int = 1000) -> List[Dict[str, Any]]:
        issues = self.jira.search_issues(
            jql,
            maxResults=max_results,
            fields="summary,status,assignee,timetracking,created,updated,resolutiondate,issuetype,project",
        )
        return issues

    def get_cached_sprint_issues(self, sprint_id, jql_extra=JIRA_ONLY_STANDARD_TYPES, full=True):
        extra = (jql_extra or "").strip()
        extra_hash = hashlib.md5(extra.encode()).hexdigest()
        key = f"sprint_issues:{self.cfg.user_id}:{sprint_id}:{int(full)}:{extra_hash}"
        data = cache.get(key)
        if data is None:
            data = self.get_sprint_issues(
                sprint_id,
                jql_extra=jql_extra,
                full=full,
            )
            cache.set(key, data, 60)
        return data

    def get_sprint_issues(self, sprint_id: int, *, jql_extra: str = "", max_results: int = 1000, full=False) -> List[
        Dict[str, Any]]:
        """
        Get issues in a sprint via JQL.
        """
        jql = f"sprint = {int(sprint_id)}"
        if jql_extra.strip():
            jql = f"({jql}) AND ({jql_extra.strip()})"

        issues = self._search_issues(jql)
        if full:
            return [self._ticket_info_detail(i.key) for i in issues]
        else:
            return [self._ticket_info(i.key) for i in issues]

    def run_jql(self, jql: str, *, max_results: int = 1000, full=True) -> List[Dict[str, Any]]:
        """
        Execute a JQL query and return ticket details.
        """
        issues = self._search_issues(jql, max_results=max_results)
        if full:
            return [self._ticket_info_detail(i.key) for i in issues]
        else:
            return [self._ticket_info(i.key) for i in issues]

    def get_issue(self, issue_key: str, full:bool=True) -> Dict[str, Any]:
        return self._ticket_info_detail(issue_key) if full else self._ticket_info(issue_key)

    def get_url_jql(self, jql: str) -> str:
        params = {"jql": jql}
        encoded = urllib.parse.urlencode(params)
        url = f"{self.cfg.jira_url}issues/?{encoded}"
        return url

    def get_issue_url(self, issue_key: str) -> str:
        url = f"{self.cfg.jira_url}browse/{issue_key}"
        return url

    def get_sprint_report(self, sprint_id: int, jql_extra: str = "", max_results: int = 1000) -> Dict[str, Any]:
        """
        Return sprint info + tickets with requested ticket fields.
        """
        sprint = self.get_sprint(sprint_id)
        tickets = self.get_cached_sprint_issues(sprint_id, jql_extra=jql_extra, max_results=max_results)
        return {"sprint": sprint, "tickets": tickets}

    # ---------------------------
    # Ticket info + kanban metrics
    # ---------------------------

    def ticket_info(self, issue_key: str) -> Dict[str, Any]:
        """
        Public method: info for a single ticket.
        """
        return self._ticket_info(issue_key)

    def _ticket_info(self, issue_key: str) -> Dict[str, Any]:
        issue = self.jira.issue(
            issue_key,
            fields="summary,status,assignee,issuetype,project",
            expand="changelog",
        )

        fields = issue.fields

        # status / assignee
        status_name = getattr(getattr(fields, "status", None), "name", None)
        assignee_obj = getattr(fields, "assignee", None)
        assignee = None
        if assignee_obj:
            assignee = {
                "displayName": getattr(assignee_obj, "displayName", None),
                "emailAddress": getattr(assignee_obj, "emailAddress", None),
                "accountId": getattr(assignee_obj, "accountId", None),
                "name": getattr(assignee_obj, "name", None),  # some Server versions
            }

        return {
            "key": issue.key,
            "summary": getattr(fields, "summary", None),
            "project": getattr(getattr(fields, "project", None), "key", None),
            "issueType": getattr(getattr(fields, "issuetype", None), "name", None),
            "state": status_name,
            "assignee": assignee,
            "url": self.get_issue_url(issue.key),
        }

    def _ticket_info_detail(self, issue_key: str) -> Dict[str, Any]:
        issue = self.jira.issue(
            issue_key,
            fields="summary,status,assignee,timetracking,created,updated,resolutiondate,issuetype,project",
            expand="changelog",
        )

        fields = issue.fields
        timetracking = getattr(fields, "timetracking", None)

        # estimation (prefer story points if available, else original estimate)
        sp = self._get_story_points(issue)
        original_estimate_sec = getattr(timetracking, "originalEstimateSeconds", None) if timetracking else None

        # remaining time
        remaining_sec = getattr(timetracking, "remainingEstimateSeconds", None) if timetracking else None
        spent_sec = getattr(timetracking, "timeSpentSeconds", None) if timetracking else None

        # status / assignee
        status_name = getattr(getattr(fields, "status", None), "name", None)
        assignee_obj = getattr(fields, "assignee", None)
        assignee = None
        if assignee_obj:
            assignee = {
                "displayName": getattr(assignee_obj, "displayName", None),
                "emailAddress": getattr(assignee_obj, "emailAddress", None),
                "accountId": getattr(assignee_obj, "accountId", None),
                "name": getattr(assignee_obj, "name", None),  # some Server versions
            }

        metrics = self.metrics.compute(issue)
        history = self.metrics.history(issue)
        return {
            "key": issue.key,
            "summary": getattr(fields, "summary", None),
            "project": getattr(getattr(fields, "project", None), "key", None),
            "issueType": getattr(getattr(fields, "issuetype", None), "name", None),
            "state": status_name,
            "assignee": assignee,
            "estimation": {
                "story_points": sp,
                "original_estimate_hours": _to_hours(original_estimate_sec),
            },
            "remaining_hours": _to_hours(remaining_sec),
            "spent_hours": _to_hours(spent_sec),
            "kanban_metrics": metrics,
            "history": history,
            "url": self.get_issue_url(issue.key),
        }

    def _detect_story_points_field_id(self) -> Optional[str]:
        """
        Try to detect story points custom field id by name.
        Common names: 'Story Points', 'Story Point Estimate', 'Points'
        """
        try:
            for f in self.jira.fields():
                name = (f.get("name") or "").strip().lower()
                fid = f.get("id")
                if not fid:
                    continue
                if name in {"story points", "story point estimate"}:
                    return fid
            # fallback heuristic
            for f in self.jira.fields():
                name = (f.get("name") or "").strip().lower()
                fid = f.get("id")
                if fid and "story" in name and "point" in name:
                    return fid
        except Exception:
            return None
        return None

    def _get_story_points(self, issue: Any) -> Optional[float]:
        fid = self._story_points_field_id
        if not fid:
            return None
        try:
            v = getattr(issue.fields, fid, None)
            if v is None:
                return None
            # Jira can store as int/float
            return float(v)
        except Exception:
            return None

    def throughput_7d(self):
        q = f"project = {self.cfg.jira_project_id} AND issuetype in standardIssueTypes() AND status = Done AND resolved >= -7d ORDER BY resolved DESC"
        return self.run_jql(q, full=False)
