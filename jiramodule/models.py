from typing import Optional, Tuple, List, Any, Dict

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.contrib.auth.models import AbstractUser
from datetime import datetime
from django.utils import timezone
from jira import JIRA
import urllib.parse


def _to_hours(seconds: Optional[int]) -> Optional[float]:
    if seconds is None:
        return 0
    return round(seconds / 3600.0, 2)


def _parse_jira_dt(s: Optional[str]) -> Optional[datetime]:
    """
    Jira timestamps look like: '2026-01-22T13:31:12.345+0100'
    """
    if not s:
        return None
    # try a few common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


class JiraConfiguration(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jira_configs")

    jira_url = models.URLField(
        "JIRA URL",
        blank=True,
        help_text="https://jira.sm-ms.lan/"
    )

    jira_email = models.EmailField(
        "JIRA API token",
        max_length=255,
        blank=True,
    )

    jira_token = models.CharField(
        "JIRA API token",
        max_length=255,
        blank=True,
    )
    jira_board_id = models.PositiveIntegerField(
        "JIRA board ID",
        null=True,
        blank=True,
    )

    jira_project_id = models.PositiveIntegerField(
        "JIRA project ID",
        null=True,
        blank=True,
    )

    jira_recurrent_epic_key = models.CharField(
        "JIRA recurrent epic key",
        max_length=50,
        blank=True,
    )
    is_active = models.BooleanField(default=False)
    current = models.BooleanField(default=False)
    last_test_ok = models.BooleanField(default=False, db_index=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True)

    def set_as_current(self):
        with transaction.atomic():
            JiraConfiguration.objects.filter(user=self.user, current=True).update(current=False)
            self.current = True
            self.save(update_fields=["current"])

    def __str__(self):
        return f"{self.jira_email} on {self.jira_url}"

    def mark_test_result(self, ok: bool, error: str = "") -> None:
        self.last_test_ok = bool(ok)
        self.last_tested_at = timezone.localtime(timezone.now())
        self.last_test_error = (error or "")[:5000]  # sécurité
        self.save(update_fields=["last_test_ok", "last_tested_at", "last_test_error"])

    def save(self, *args, **kwargs):
        user = kwargs.pop("user", None)

        if self.pk is None:
            # création
            if self.user_id is None:
                if user is None:
                    raise ValueError(
                        "JiraConfiguration.save() requires a user on creation"
                    )
                self.user = user

        super().save(*args, **kwargs)


class JiraManager:
    """
    Jira Server/DC helper using token auth (Authorization: Bearer <token>).
    Requires: pip install jira

    Notes:
    - Agile endpoints (boards/sprints) require Jira Software (GreenHopper).
    - 'Story points' depends on your custom field; we auto-detect by name.
    """
    JIRA_ONLY_STANDARD_TYPES = "issuetype in standardIssueTypes()"
    dict_statuses = {'Done': {'order': 2, 'statuses': []}, 'To Do': {'order': 0, 'statuses': []},
                     'In Progress': {'order': 1, 'statuses': []}}

    def __init__(self, jc: JiraConfiguration) -> None:
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

        # force a call
        _ = self._jira.myself()

        # cache story points field id if present
        self._story_points_field_id = self._detect_story_points_field_id()

    def server_info(self) -> dict[str, Any]:
        return self._jira.server_info()

    def get_dict_statuses(self)->dict:
        for s in self.list_statuses():
            self.dict_statuses[s['statusCategory']]['statuses'].append(s)
        return self.dict_statuses
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
        """
        "liste des états" -> all statuses available in Jira instance.
        """
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
            })
        return out

    def get_current_sprint(self, board_id: int, max_results: int = 50) -> Optional[Dict[str, Any]]:
        """
        Find the next sprint on a board with id strictly greater than current_sprint_id.
        Returns None if not found.
        """
        sprints = self.list_sprints(board_id, state="active", max_results=max_results)
        candidates = [s for s in sprints if isinstance(s.get("id"), int)]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s["id"])

    def get_next_sprint(self, board_id: int, max_results: int = 50) -> Optional[Dict[str, Any]]:
        """
        Find the next sprint on a board with id strictly greater than current_sprint_id.
        Returns None if not found.
        """
        current_sprint = self.get_current_sprint(board_id)
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

    def _search_issues(self, jql: str,max_results: int = 1000) -> List[Dict[str, Any]]:
        issues = self.jira.search_issues(
            jql,
            maxResults=max_results,
            fields="summary,status,assignee,timetracking,created,updated,resolutiondate,issuetype,project",
        )
        return issues

    def get_sprint_issues(self, sprint_id: int, *, jql_extra: str = "", max_results: int = 1000) -> List[
        Dict[str, Any]]:
        """
        Get issues in a sprint via JQL.
        """
        jql = f"sprint = {int(sprint_id)}"
        if jql_extra.strip():
            jql = f"({jql}) AND ({jql_extra.strip()})"

        issues = self._search_issues(jql)
        return [self._ticket_info(i.key) for i in issues]

    def run_jql(self, jql: str, *, max_results: int = 1000, full=True) -> List[Dict[str, Any]]:
        """
        Execute a JQL query and return ticket details.
        """
        issues = self._search_issues(jql, max_results=max_results)
        if full :
            return [self._ticket_info_detail(i.key) for i in issues]
        else:
            return [self._ticket_info(i.key) for i in issues]

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
        tickets = self.get_sprint_issues(sprint_id, jql_extra=jql_extra, max_results=max_results)
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
            "url" : self.get_issue_url(issue.key),
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

        metrics = self._kanban_metrics(issue)

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

    def _kanban_metrics(self, issue: Any) -> Dict[str, Any]:
        """
        "ses metriques kanban" — Jira doesn't expose cycle/lead time directly via python-jira.
        We compute pragmatic metrics from changelog:
        - lead_time_hours: created -> resolution (or now)
        - cycle_time_hours: first time moved into an 'In Progress' category -> resolution (or now)
        - time_in_status_hours: dict status -> hours
        - current_wip_age_hours: last status change -> now
        """
        created = _parse_jira_dt(getattr(issue.fields, "created", None))
        resolved = _parse_jira_dt(getattr(issue.fields, "resolutiondate", None))
        tz = timezone.get_default_timezone()
        now = datetime.now(tz)

        end = resolved or now

        lead_time_hours = None
        if created:
            lead_time_hours = round((end - created.astimezone(tz)).total_seconds() / 3600.0, 2)

        # Build status change timeline from changelog
        histories = getattr(getattr(issue, "changelog", None), "histories", None) or []
        changes: List[Tuple[datetime, str, str]] = []  # (when, from, to)

        for h in histories:
            when = _parse_jira_dt(getattr(h, "created", None))
            if not when:
                continue
            for it in getattr(h, "items", []) or []:
                if getattr(it, "field", "") == "status":
                    changes.append((when, getattr(it, "fromString", None), getattr(it, "toString", None)))

        changes.sort(key=lambda x: x[0])

        # Compute time in status
        time_in_status_sec: Dict[str, float] = {}
        last_status = getattr(getattr(issue.fields, "status", None), "name", None)
        # We need the initial status: use first transition's fromString if available
        if changes:
            initial_status = changes[0][1] or last_status
        else:
            initial_status = last_status

        # timeline points: (t, status)
        points: List[Tuple[datetime, str]] = []
        if created and initial_status:
            points.append((created.astimezone(tz), initial_status))

        for when, _from, to in changes:
            if to:
                points.append((when.astimezone(tz), to))

        if not points:
            # no created or no status info
            return {
                "lead_time_hours": lead_time_hours,
                "cycle_time_hours": None,
                "current_wip_age_hours": None,
                "time_in_status_hours": {},
                "notes": "Pas assez d'infos pour calculer (created/status/changelog manquant).",
            }

        # accumulate durations
        for idx in range(len(points)):
            t0, st0 = points[idx]
            t1 = (points[idx + 1][0] if idx + 1 < len(points) else end.astimezone(tz))
            dur = max(0.0, (t1 - t0).total_seconds())
            if st0:
                time_in_status_sec[st0] = time_in_status_sec.get(st0, 0.0) + dur

        time_in_status_hours = {k: round(v / 3600.0, 2) for k, v in time_in_status_sec.items()}

        # Cycle time heuristic: first time status becomes something containing "In Progress"
        # (Your workflow may differ — adjust if needed.)
        first_in_progress: Optional[datetime] = None
        for t, st in points:
            if st and "progress" in st.lower():
                first_in_progress = t
                break

        cycle_time_hours = None
        if first_in_progress:
            cycle_time_hours = round((end.astimezone(tz) - first_in_progress).total_seconds() / 3600.0, 2)

        # current WIP age: time since last status point
        current_wip_age_hours = round((now - points[-1][0]).total_seconds() / 3600.0, 2)

        return {
            "lead_time_hours": lead_time_hours,
            "cycle_time_hours": cycle_time_hours,
            "current_wip_age_hours": current_wip_age_hours,
            "time_in_status_hours": time_in_status_hours,
            "heuristics": {
                "cycle_time_start_rule": "first status containing 'In Progress'",
            },
        }
