def get_issue_status(issue: dict, default: str = "Unknown") -> str:
    return (issue.get("state") or "").strip() or default


def get_issue_assignee_name(issue: dict) -> str:
    assignee = issue.get("assignee") or {}
    return assignee.get("displayName") or ""


def get_kanban_metric(issue: dict, metric_name: str, default=None):
    metrics = issue.get("kanban_metrics") or {}
    value = metrics.get(metric_name)
    return default if value is None else value


def build_dashboard_item(issue: dict) -> dict:
    issue_key = issue.get("key")
    return {
        "key": issue_key,
        "title": issue.get("summary") or "",
        "status": get_issue_status(issue),
        "ageDays": get_kanban_metric(issue, "current_wip_age_days"),
        "assignee": get_issue_assignee_name(issue) or None,
        "url": issue.get("url") or "",
        "detail_url": f"/issue/{issue_key}/",
    }


def build_sprint_issue_item(issue: dict) -> dict:
    return {
        "key": issue.get("key"),
        "summary": issue.get("summary") or "",
        "issueType": issue.get("issueType") or "",
        "state": get_issue_status(issue),
        "assignee": get_issue_assignee_name(issue),
        "url": issue.get("url") or "",
    }

