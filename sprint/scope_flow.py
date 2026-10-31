"""
Sprint scope flow: the status of the planned issues at the sprint start, where the sprint's issues
came from (planned at the start or added during the sprint), how they ended (Jira's sprint report
buckets) and their status at the sprint end.

Built from Jira's sprint report, which already knows which issues were added after the start;
statuses at the start and end are rebuilt from each issue's changelog.
"""

import re
from datetime import datetime

from jiramodule.status import CANONICAL_ORDER

from .logged_report import parse_jira_datetime, status_at

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

# Status order of the stability matrix: by Jira category, then the usual workflow order.
CATEGORY_ORDER = {"new": 0, "indeterminate": 1, "done": 2}


def build_scope_flow(
    report: dict,
    issue_url,
    histories: dict[str, dict] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_categories: dict[str, str] | None = None,
    sprint_id: int | None = None,
) -> dict:
    """
    Nodes are keyed "start:<status>", "origin:<x>", "outcome:<x>" and "end:<status>" so a status
    never collides with an outcome. Flows are ticket counts: start status -> planned (planned
    issues only), origin -> outcome, then outcome -> status at the sprint end.

    Without `histories` (or for an issue missing from it) the end status is the current one and
    the start status is unknown.

    `status_entry` is the status when the issue entered the sprint (its start for planned issues)
    and `moved` whether its status changed between then and the end, for the stability tab.
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
            entered = entered_at(history, origin, sprint_id, start, end) if history and start and end else None
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
                    "status_entry": status_at(history, entered) if entered else None,
                    "moved": status_moved(history, entered, end) if entered else None,
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
        "stability": stability_rates(issues, categories),
        "status_order": status_order(issues, categories),
        "has_history": bool(histories),
    }


def completion_rates(issues: list[dict], status_categories: dict[str, str]) -> dict[str, dict]:
    """
    Per group: issues closed in the sprint out of the issues started by its end (not "To Do"), and
    out of all the group's issues, whatever Jira's bucket: an issue removed from the sprint still
    counts by its status at the end. Issues completed in another sprint are left out.
    """
    rates = {}
    for group in COMPLETION_GROUPS:
        done = started = total = 0
        for issue in issues:
            if group != "all" and issue["origin"] != group or issue["outcome"] == "done_elsewhere":
                continue
            total += 1
            category = status_categories.get(issue["status_end"])
            if issue["outcome"] == "done" or category == "done":
                done += 1
            elif category == "new":
                continue
            started += 1
        rates[group] = {
            "done": done,
            "started": started,
            "percent": _percent(done, started),
            "total": total,
            "percent_total": _percent(done, total),
        }
    return rates


def _percent(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def entered_at(history: dict, origin: str, sprint_id: int | None, start: datetime, end: datetime) -> datetime:
    """
    When the issue entered the sprint: its start for a planned issue; for an added one, the last
    time its Sprint field gained this sprint before the end, else its creation, never before the start.
    """
    if origin == PLANNED:
        return start
    sprint = str(sprint_id)
    additions = [
        at
        for change in history.get("sprint_changes") or []
        if sprint in _sprint_ids(change.get("to")) and sprint not in _sprint_ids(change.get("from"))
        if (at := parse_jira_datetime(change.get("at"))) and at <= end
    ]
    moment = max(additions) if additions else parse_jira_datetime(history.get("created"))
    return max(start, moment) if moment else start


def _sprint_ids(value) -> set[str]:
    # Jira gives the sprints of a Sprint change as a list of ids: "2440, 2446".
    return set(re.findall(r"\d+", str(value or "")))


def status_moved(history: dict, since: datetime, end: datetime) -> bool:
    """Whether the status changed at least once between `since` and `end`."""
    return any(
        (at := parse_jira_datetime(change.get("at"))) and since < at <= end
        for change in history.get("status_changes") or []
    )


def stability_rates(issues: list[dict], status_categories: dict[str, str]) -> dict[str, dict]:
    """
    Per group: issues in the same status on entering the sprint and at its end, out of the issues
    whose entry status is known (done ones included); `still` ones never changed status meanwhile.
    """
    rates = {}
    for group in COMPLETION_GROUPS:
        same = still = compared = 0
        for issue in issues:
            entry = issue["status_entry"]
            if group != "all" and issue["origin"] != group or not entry:
                continue
            compared += 1
            if entry == issue["status_end"]:
                same += 1
                still += not issue["moved"]
        rates[group] = {"same": same, "still": still, "compared": compared, "percent": _percent(same, compared)}
    return rates


def status_order(issues: list[dict], status_categories: dict[str, str]) -> list[str]:
    """The entry and end statuses of the issues, To Do-like first and done-like last."""
    statuses = {issue[side] for issue in issues for side in ("status_entry", "status_end") if issue[side]}
    canonical = {name.casefold(): index for index, name in enumerate(CANONICAL_ORDER)}
    return sorted(
        statuses,
        key=lambda name: (
            CATEGORY_ORDER.get(status_categories.get(name), 1),
            canonical.get(name.casefold(), len(canonical)),
            name,
        ),
    )
