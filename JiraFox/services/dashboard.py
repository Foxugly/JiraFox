from collections import defaultdict
from statistics import median

from JiraFox.services.issue_data import build_dashboard_item, get_issue_status, get_kanban_metric

AGING_WIP_THRESHOLD_DAYS = 10


def get_dashboard_status_bucket(issue: dict) -> str:
    if (issue.get("statusCategory") or "").strip() == "Done":
        return "Done"
    return get_issue_status(issue)


def get_current_sprint_with_issues(jira) -> tuple[dict | None, list[dict]]:
    if hasattr(jira, "get_current_sprint_issues"):
        return jira.get_current_sprint_issues(full=True)

    sprint = jira.get_current_sprint(jira.cfg.jira_board_id)
    if not sprint:
        return None, []
    return sprint, jira.get_cached_sprint_issues(sprint["id"], jql_extra=jira.JIRA_ONLY_STANDARD_TYPES, full=True)


def build_dashboard_kpis(issues: list[dict], throughput_7d: int) -> dict:
    wip_items = [
        issue
        for issue in issues
        if (issue.get("statusCategory") or get_issue_status(issue, default="")) != "Done"
    ]
    aging_wip = [
        issue
        for issue in wip_items
        if (get_kanban_metric(issue, "current_wip_age_days", 0) or 0) > AGING_WIP_THRESHOLD_DAYS
    ]
    cycle_times = [
        value
        for issue in issues
        if (value := get_kanban_metric(issue, "cycle_time_days"))
    ]
    lead_times = [
        value
        for issue in issues
        if (value := get_kanban_metric(issue, "lead_time_days"))
    ]

    flow_eff_pct = None
    if cycle_times and lead_times:
        median_cycle = median(cycle_times)
        median_lead = median(lead_times)
        if median_lead > 0:
            flow_eff_pct = round((median_cycle / median_lead) * 100, 1)

    aging_wip_pct = None
    if wip_items:
        aging_wip_pct = round((len(aging_wip) / len(wip_items)) * 100, 1)

    return {
        "wip": len(wip_items),
        "aging_wip": len(aging_wip),
        "cycle_p50_days": round(median(cycle_times), 1) if cycle_times else None,
        "lead_p50_days": round(median(lead_times), 1) if lead_times else None,
        "throughput_7d": throughput_7d,
        "flow_eff_pct": flow_eff_pct,
        "aging_wip_pct": aging_wip_pct,
    }


def build_dashboard_bottlenecks(issues: list[dict]) -> list[dict]:
    status_stats: dict[str, list[float]] = defaultdict(list)
    for issue in issues:
        status_stats[get_dashboard_status_bucket(issue)].append(
            get_kanban_metric(issue, "current_wip_age_days", 0) or 0
        )

    bottlenecks = [
        {
            "status": status,
            "count": len(values),
            "avgAge": round(sum(values) / len(values), 1),
            "maxAge": max(values),
        }
        for status, values in status_stats.items()
    ]
    bottlenecks.sort(key=lambda item: item["avgAge"], reverse=True)
    return bottlenecks


def build_dashboard_items(issues: list[dict]) -> list[dict]:
    return [build_dashboard_item(issue) for issue in issues]
