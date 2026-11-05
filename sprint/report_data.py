"""
Gathers the Jira data of the sprints of a management report. `load_sprint_scope` is also what the
scope page shows; `build_report_data` shapes it for the AI dossier and the PowerPoint.
"""

from datetime import date

from django.core.cache import cache
from django.utils import timezone
from jira.exceptions import JIRAError
from requests import RequestException

from jiramodule.exceptions import JiraUnavailable, describe_jira_error

from .logged_report import parse_jira_datetime, sprint_window
from .report_metrics import kanban_snapshot, sprint_metrics
from .scope_flow import OUTCOMES as SCOPE_OUTCOMES, build_scope_flow, consistency_errors

CACHE_SECONDS = 24 * 3600
TREND_COUNT = 5


def load_sprint_scope(jira, sprint: dict) -> dict:
    """Scope flow of one sprint with its status histories and the warnings to show next to it."""
    sprint_id = sprint["id"]
    report = jira.get_sprint_scope_report(sprint_id)
    # Jira's own dates: the hand-set period only bounds the logged sheet.
    start, end = sprint_window(sprint, timezone.now())
    warnings = {
        "scope_errors": [], "planned_created_late": [], "done_elsewhere": [], "missing_from_report": [],
        "missing_check_error": None, "without_history": [], "history_error": None,
    }
    histories, categories = None, None
    if start:
        keys = [issue.get("key") for bucket, _ in SCOPE_OUTCOMES for issue in report.get(bucket) or []]
        try:
            histories = jira.get_status_histories(keys)
            categories = jira.status_category_keys()
        except (JIRAError, RequestException, JiraUnavailable) as exc:
            # The diagram still works with current statuses: say what is missing.
            warnings["history_error"] = describe_jira_error(exc)
            histories = None
    scope = build_scope_flow(
        report, jira.get_issue_url, histories=histories, start=start, end=end, status_categories=categories,
        sprint_id=sprint_id, started_statuses=getattr(jira.cfg, "started_statuses", ()),
    )
    warnings["scope_errors"] = consistency_errors(scope)
    warnings["planned_created_late"] = [
        {**issue, "created_at": parse_jira_datetime(issue["created"])}
        for issue in scope["issues"]
        if issue["created_after_start"]
    ]
    warnings["done_elsewhere"] = scope["done_elsewhere"]
    report_keys = {issue["key"] for issue in scope["issues"]}
    elsewhere_keys = {issue["key"] for issue in scope["done_elsewhere"]}
    if histories is not None:
        warnings["without_history"] = sorted(report_keys - set(histories))
    try:
        # Jira's sprint report only holds the issues of the board's filter.
        warnings["missing_from_report"] = sorted(jira.get_sprint_issue_keys(sprint_id) - report_keys - elsewhere_keys)
    except Exception as exc:  # noqa: BLE001
        # An extra check only: whatever goes wrong, the figures are still shown, with a warning.
        warnings["missing_check_error"] = describe_jira_error(exc)
    return {
        "sprint": sprint, "start": start, "end": end, "scope": scope, "histories": histories, "warnings": warnings,
    }


def _end_of(sprint: dict):
    return parse_jira_datetime(sprint.get("completeDate")) or parse_jira_datetime(sprint.get("endDate"))


def closed_sprints(jira) -> list[dict]:
    """The board's closed sprints, oldest first."""
    sprints = jira.list_sprints(jira.cfg.jira_board_id, state="closed")
    return sorted(sprints, key=lambda sprint: _end_of(sprint) or timezone.now())


def sprints_in_range(sprints: list[dict], start: date, end: date) -> list[dict]:
    """Closed sprints whose [start, end] overlaps the range, edges included."""
    kept = []
    for sprint in sprints:
        if sprint.get("state") != "closed":
            continue
        sprint_start, sprint_end = parse_jira_datetime(sprint.get("startDate")), _end_of(sprint)
        if sprint_start and sprint_end and sprint_start.date() <= end and sprint_end.date() >= start:
            kept.append(sprint)
    return kept


def trend_for(sprint: dict, closed: list[dict], count: int = TREND_COUNT) -> list[dict]:
    """The `count` closed sprints before `sprint`, oldest first."""
    ids = [candidate["id"] for candidate in closed]
    if sprint["id"] in ids:
        previous = closed[:ids.index(sprint["id"])]
    else:
        sprint_end = _end_of(sprint) or timezone.now()
        previous = [candidate for candidate in closed if (_end_of(candidate) or timezone.now()) < sprint_end]
    return previous[-count:] if count else []


def _loaded_sprint(jira, sprint: dict) -> dict:
    """`load_sprint_scope`, cached for a day when the sprint is closed and nothing went wrong."""
    if sprint.get("state") != "closed":
        return load_sprint_scope(jira, sprint)
    key = f"report-sprint:{getattr(jira.cfg, 'pk', None)}:{sprint['id']}"
    loaded = cache.get(key)
    if loaded is None:
        loaded = load_sprint_scope(jira, sprint)
        warnings = loaded["warnings"]
        if not warnings["history_error"] and not warnings["missing_check_error"]:
            cache.set(key, loaded, CACHE_SECONDS)
    return loaded


def _title_period(loaded: list[dict], in_progress: bool) -> str:
    if len(loaded) == 1:
        sprint = loaded[0]["sprint"]
        name = sprint.get("name") or f"Sprint {sprint['id']}"
        return f"{name} (en cours)" if in_progress else name
    first, last = loaded[0]["start"], loaded[-1]["end"]
    return f"du {first:%d/%m/%Y} au {last:%d/%m/%Y}" if first else f"jusqu'au {last:%d/%m/%Y}"


def _totals(entries: list[dict]) -> dict:
    """Sums over `entries`; the key figure is recomputed from the sums, not averaged."""
    totals = {
        key: sum(entry["metrics"][key] for entry in entries) for key in ("planned", "added", "closed", "not_started")
    }
    totals["planned_closed"] = sum(entry["scope"]["completion"]["planned"]["done"] for entry in entries)
    planned = totals["planned"]
    totals["percent_planned_closed"] = round(100 * totals["planned_closed"] / planned) if planned else None
    return totals


def build_report_data(jira, sprints: list[dict], *, focus: dict | None = None) -> dict:
    """
    An explicit `focus` makes a single-sprint report: the other sprints only give the trend, the
    title and the totals are the focus sprint's. Without it, every sprint counts.
    """
    single = focus is not None
    loaded = [_loaded_sprint(jira, sprint) for sprint in sprints]
    entries = [
        {
            "sprint": item["sprint"],
            "metrics": sprint_metrics(item["scope"], item["histories"], item["end"]),
            "warnings": item["warnings"],
            "scope": item["scope"],
            "histories": item["histories"],
            "categories": item["scope"]["status_categories"],
        }
        for item in loaded
    ]
    focus = focus or sprints[-1]
    focus_item = next(item for item in loaded if item["sprint"]["id"] == focus["id"])
    if single:
        in_progress = focus.get("state") == "active"
        name = focus.get("name") or f"Sprint {focus['id']}"
        title_period = f"{name} (en cours)" if in_progress else name
    else:
        in_progress = len(sprints) == 1 and sprints[0].get("state") == "active"
        title_period = _title_period(loaded, in_progress)
    focus_entry = next(entry for entry in entries if entry["sprint"]["id"] == focus["id"])
    # Without histories there is nothing to rebuild: no snapshot rather than a misleading empty one.
    snapshot = (
        kanban_snapshot(focus_item["scope"], focus_item["histories"], focus_item["end"])
        if focus_item["histories"] is not None
        else None
    )
    project = getattr(jira.cfg, "project", None)
    return {
        "title_period": title_period,
        "in_progress": in_progress,
        "single": single,
        "project_name": getattr(project, "name", "") or "",
        "sprints": entries,
        "focus": focus,
        "snapshot": snapshot,
        "totals": _totals([focus_entry] if single else entries),
    }


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def sprint_warning_lines(warnings: dict) -> list[str]:
    """One French sentence per non-empty warning, for the speaker notes and the multi-sprint page."""
    lines = []
    late = [issue["key"] for issue in warnings.get("planned_created_late") or []]
    if late:
        lines.append(
            f"{len(late)} {_plural(len(late), 'ticket prévu créé', 'tickets prévus créés')} "
            f"après le début du sprint : {', '.join(late)}"
        )
    missing = warnings.get("missing_from_report") or []
    if missing:
        lines.append(
            f"{len(missing)} {_plural(len(missing), 'ticket du sprint manque', 'tickets du sprint manquent')} "
            f"dans le rapport de sprint de Jira : {', '.join(missing)}"
        )
    elsewhere = [issue["key"] for issue in warnings.get("done_elsewhere") or []]
    if elsewhere:
        lines.append(
            f"{len(elsewhere)} {_plural(len(elsewhere), 'ticket déjà terminé', 'tickets déjà terminés')} "
            f"en dehors du sprint : {', '.join(elsewhere)}"
        )
    without = warnings.get("without_history") or []
    if without:
        lines.append(
            f"{len(without)} {_plural(len(without), 'ticket sans', 'tickets sans')} historique de statut : "
            f"{', '.join(without)}"
        )
    if warnings.get("history_error"):
        lines.append(f"Historique des statuts indisponible : {warnings['history_error']}")
    if warnings.get("missing_check_error"):
        lines.append(f"Contrôle des tickets manquants impossible : {warnings['missing_check_error']}")
    lines.extend(warnings.get("scope_errors") or [])
    return lines
