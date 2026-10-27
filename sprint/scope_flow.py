"""
Sprint scope flow: where the sprint's issues came from (planned at the start or added during the
sprint), how they ended (Jira's sprint report buckets) and their current status.

Built from Jira's sprint report, which already knows which issues were added after the start.
"""

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


def build_scope_flow(report: dict, issue_url) -> dict:
    """
    Nodes are keyed "origin:<x>", "outcome:<x>" and "status:<name>" so a status never collides
    with an outcome. Flows are ticket counts: origin -> outcome, then outcome -> current status.
    """
    added_keys = set((report.get("issueKeysAddedDuringSprint") or {}).keys())
    statuses = (report.get("entityData") or {}).get("statuses") or {}

    issues = []
    for bucket, outcome in OUTCOMES:
        for raw in report.get(bucket) or []:
            status = statuses.get(str(raw.get("statusId"))) or {}
            category = ((status.get("status") or {}).get("statusCategory") or {}).get("key")
            issues.append(
                {
                    "key": raw.get("key"),
                    "summary": raw.get("summary") or "",
                    "assignee": raw.get("assigneeName") or "",
                    "origin": ADDED if raw.get("key") in added_keys else PLANNED,
                    "outcome": outcome,
                    "status": status.get("statusName") or str(raw.get("statusId") or "?"),
                    "status_category": category or "",
                    "url": issue_url(raw.get("key")),
                }
            )

    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        for source, target in (
            (f"origin:{issue['origin']}", f"outcome:{issue['outcome']}"),
            (f"outcome:{issue['outcome']}", f"status:{issue['status']}"),
        ):
            counts[(source, target)] = counts.get((source, target), 0) + 1

    status_categories = {issue["status"]: issue["status_category"] for issue in issues}
    return {
        "flows": [{"from": source, "to": target, "flow": count} for (source, target), count in counts.items()],
        "status_categories": status_categories,
        "issues": issues,
        "totals": {origin: sum(1 for issue in issues if issue["origin"] == origin) for origin in ORIGINS},
    }
