"""
Sprint scope flow: the status of the planned issues at the sprint start, where the sprint's issues
came from (planned at the start or added during the sprint), how they ended (Jira's sprint report
buckets) and their status at the sprint end.

Built from Jira's sprint report, which already knows which issues were added after the start;
statuses at the start and end are rebuilt from each issue's changelog.
"""

from datetime import datetime

from .logged_report import status_at

PLANNED = "planned"
ADDED = "added"
ORIGINS = (PLANNED, ADDED)

# Jira sprint report buckets, in display order.
OUTCOMES = (
    ("completedIssues", "done"),
    ("issuesNotCompletedInCurrentSprint", "not_done"),
    ("puntedIssues", "removed"),
    ("issuesCompletedInAnotherSprint", "done_elsewhere"),
)

# Completion rates: planned issues, added issues, all of them.
COMPLETION_GROUPS = (PLANNED, ADDED, "all")

# Diagram columns, left to right.
COLUMNS = {"start": 0, "origin": 1, "outcome": 2, "end": 3}


def build_scope_flow(
    report: dict,
    issue_url,
    histories: dict[str, dict] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_categories: dict[str, str] | None = None,
) -> dict:
    """
    Nodes are keyed "start:<status>", "origin:<x>", "outcome:<x>" and "end:<status>" so a status
    never collides with an outcome. Flows are ticket counts: start status -> planned (planned
    issues only), origin -> outcome, then outcome -> status at the sprint end.

    Without `histories` (or for an issue missing from it) the end status is the current one and
    the start status is unknown.
    """
    added_keys = set((report.get("issueKeysAddedDuringSprint") or {}).keys())
    statuses = (report.get("entityData") or {}).get("statuses") or {}
    categories = dict(status_categories or {})
    histories = histories or {}

    issues = []
    for bucket, outcome in OUTCOMES:
        for raw in report.get(bucket) or []:
            status = statuses.get(str(raw.get("statusId"))) or {}
            current = status.get("statusName") or str(raw.get("statusId") or "?")
            category = ((status.get("status") or {}).get("statusCategory") or {}).get("key")
            if category:
                categories.setdefault(current, category)

            key = raw.get("key")
            origin = ADDED if key in added_keys else PLANNED
            history = histories.get(key)
            status_start = status_at(history, start) if history and start and origin == PLANNED else None
            status_end = (status_at(history, end) if history and end else None) or current
            issues.append(
                {
                    "key": key,
                    "summary": raw.get("summary") or "",
                    "assignee": raw.get("assigneeName") or "",
                    "origin": origin,
                    "outcome": outcome,
                    "status_start": status_start,
                    "status_end": status_end,
                    "status_current": current,
                    "url": issue_url(key),
                }
            )

    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        steps = [
            (f"origin:{issue['origin']}", f"outcome:{issue['outcome']}"),
            (f"outcome:{issue['outcome']}", f"end:{issue['status_end']}"),
        ]
        if issue["status_start"]:
            steps.insert(0, (f"start:{issue['status_start']}", f"origin:{issue['origin']}"))
        for step in steps:
            counts[step] = counts.get(step, 0) + 1

    completion = completion_rates(issues, categories)
    flows = [{"from": source, "to": target, "flow": count} for (source, target), count in counts.items()]
    nodes = {node for flow in flows for node in (flow["from"], flow["to"])}
    return {
        "flows": flows,
        "columns": {node: COLUMNS[node.split(":", 1)[0]] for node in nodes},
        "status_categories": categories,
        "issues": issues,
        "totals": {origin: sum(1 for issue in issues if issue["origin"] == origin) for origin in ORIGINS},
        "completion": completion,
        "has_history": bool(histories),
    }


def completion_rates(issues: list[dict], status_categories: dict[str, str]) -> dict[str, dict]:
    """
    Per group: issues closed in the sprint out of the issues started by its end (not "To Do"),
    whatever Jira's bucket: an issue removed from the sprint still counts by its status at the end.
    Issues completed in another sprint are left out.
    """
    rates = {}
    for group in COMPLETION_GROUPS:
        done = started = 0
        for issue in issues:
            if group != "all" and issue["origin"] != group or issue["outcome"] == "done_elsewhere":
                continue
            category = status_categories.get(issue["status_end"])
            if issue["outcome"] == "done" or category == "done":
                done += 1
            elif category == "new":
                continue
            started += 1
        rates[group] = {"done": done, "started": started, "percent": round(100 * done / started) if started else None}
    return rates
