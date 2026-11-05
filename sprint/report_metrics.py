"""
Figures of the management report, as pure functions of a sprint's scope flow and the status
histories of its issues. "Started" and "closed" follow `scope_flow.completion_rates` everywhere.
"""

from datetime import datetime

from jiramodule.services.kanban_metrics import KanbanMetricsService
from jiramodule.utils.datetime_utils import hours_to_business_days
from JiraFox.services.dashboard import AGING_WIP_THRESHOLD_DAYS

from .logged_report import parse_jira_datetime, status_at

CATEGORY_RANK = {"new": 0, "indeterminate": 1, "done": 2}


def is_started_status(name: str | None, categories: dict[str, str]) -> bool:
    """Not a To Do-like status. An unknown status counts as started, as in `completion_rates`."""
    return categories.get(name) != "new"


def is_closed(issue: dict, categories: dict[str, str]) -> bool:
    """Done for Jira's sprint report, or in a done status at the sprint end."""
    return issue["outcome"] == "done" or categories.get(issue["status_end"]) == "done"


def _is_active(name: str | None, categories: dict[str, str]) -> bool:
    # Stricter than is_started_status: a status never seen by Jira must not start a clock.
    return categories.get(name) in ("indeterminate", "done")


def _changes(history: dict, end: datetime | None) -> list[tuple[datetime, str | None, str | None]]:
    changes = sorted(
        (at, change.get("from"), change.get("to"))
        for change in history.get("status_changes") or []
        if (at := parse_jira_datetime(change.get("at")))
    )
    return [change for change in changes if end is None or change[0] <= end]


def _business_days(start: datetime, end: datetime) -> float:
    # Same scale as the dashboard (kanban_metrics): the default 7.2 h average working day.
    return hours_to_business_days(KanbanMetricsService().business_hours_between(start, end))


def _started_at(history: dict, categories: dict[str, str], end: datetime | None) -> datetime | None:
    """First entry into a started status (the creation date if the issue was created started)."""
    changes = _changes(history, end)
    # The status at creation is the "from" of the first change ever, even one after `end`.
    first = _changes(history, None)
    initial = first[0][1] if first else history.get("state")
    created = parse_jira_datetime(history.get("created"))
    if created and _is_active(initial, categories):
        return created
    return next((at for at, _, new in changes if _is_active(new, categories)), None)


def cycle_time_days(history: dict, categories: dict[str, str], end: datetime | None) -> float | None:
    """Business days from the first entry into a started status to the last entry into a done one."""
    started = _started_at(history, categories, end)
    done = [at for at, _, new in _changes(history, end) if categories.get(new) == "done"]
    if not started or not done or done[-1] < started:
        return None
    return _business_days(started, done[-1])


def percentile(values: list[float], p: int) -> float | None:
    """Percentile `p` (0-100) by linear interpolation between the closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def _percent(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def sprint_metrics(scope: dict, histories: dict | None, end: datetime | None) -> dict:
    categories = scope["status_categories"]
    issues = scope["issues"]
    closed = [issue for issue in issues if is_closed(issue, categories)]
    cycles = [
        days
        for issue in closed
        if (history := (histories or {}).get(issue["key"]))
        if (days := cycle_time_days(history, categories, end)) is not None
    ]
    planned, added = scope["totals"]["planned"], scope["totals"]["added"]
    open_issues = [issue for issue in issues if not is_closed(issue, categories)]
    not_started = [issue for issue in open_issues if not is_started_status(issue["status_end"], categories)]
    # An issue removed from the sprint is no longer the team's work in progress.
    wip_end = [
        issue for issue in open_issues
        if issue["outcome"] != "removed" and is_started_status(issue["status_end"], categories)
    ]
    return {
        "planned": planned,
        "added": added,
        "closed": len(closed),
        "not_started": len(not_started),
        "throughput": len(closed),
        "cycle_median": percentile(cycles, 50),
        "cycle_p85": percentile(cycles, 85),
        "wip_end": len(wip_end),
        "percent_planned_closed": scope["completion"]["planned"]["percent_total"],
        "percent_added": _percent(added, planned + added),
    }


def age_in_status_days(history: dict, at: datetime) -> float | None:
    """Business days since the last status change before `at` (or since creation)."""
    changes = _changes(history, at)
    since = changes[-1][0] if changes else parse_jira_datetime(history.get("created"))
    return _business_days(since, at) if since and since <= at else None


def kanban_snapshot(scope: dict, histories: dict | None, at: datetime, limit: int = 5) -> dict:
    """WIP, ageing share and oldest items as they stood at `at`, rebuilt from the histories."""
    categories = scope["status_categories"]
    items = []
    for issue in scope["issues"]:
        history = (histories or {}).get(issue["key"])
        status = status_at(history, at) if history else None
        if not status or categories.get(status) != "indeterminate":
            continue
        started = _started_at(history, categories, at)
        if started:
            items.append(
                {
                    "key": issue["key"],
                    "summary": issue["summary"],
                    "status": status,
                    "age_days": _business_days(started, at),
                    "url": issue["url"],
                }
            )
    aging = sum(1 for item in items if item["age_days"] > AGING_WIP_THRESHOLD_DAYS)
    items.sort(key=lambda item: item["age_days"], reverse=True)
    return {"wip": len(items), "aging": aging, "aging_percent": _percent(aging, len(items)), "oldest": items[:limit]}


def went_back(history: dict, categories: dict[str, str], end: datetime | None) -> bool:
    """Whether a status change went from a started/done category to a lower one."""
    for _, old, new in _changes(history, end):
        before, after = CATEGORY_RANK.get(categories.get(old)), CATEGORY_RANK.get(categories.get(new))
        if before is not None and after is not None and after < before:
            return True
    return False
