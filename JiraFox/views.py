# views.py
from collections import defaultdict
from statistics import median

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_GET

from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jira_config_service import get_connected_jira_for_user
from sprint.views import build_wia_data


@require_GET
@login_required
def dashboard_summary_api(request):
    jira = get_connected_jira_for_user(request.user)

    sprint = jira.get_current_sprint(jira.cfg.jira_board_id)
    issues = jira.get_cached_sprint_issues(
        sprint["id"],
        jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
        full=True
    )

    # -----------------------
    # WIA
    # -----------------------
    wia_data = build_wia_data(jira, sprint["id"])
    # -----------------------
    # KPIs
    # -----------------------
    wip_items = [i for i in issues if i["state"] != "Done"]
    aging_wip = [i for i in wip_items if (i["kanban_metrics"]["current_wip_age_days"] or 0) > 10]

    cycle_times = [
        i["kanban_metrics"]["cycle_time_days"]
        for i in issues
        if i["kanban_metrics"]["cycle_time_days"]
    ]

    lead_times = [
        i["kanban_metrics"]["lead_time_days"]
        for i in issues
        if i["kanban_metrics"]["lead_time_days"]
    ]
    throughput_7d = len(jira.throughput_7d())

    flow_eff_pct = None

    if cycle_times and lead_times:
        median_cycle = median(cycle_times)
        median_lead = median(lead_times)

        if median_lead > 0:
            flow_eff_pct = round((median_cycle / median_lead) * 100, 1)

    aging_wip_pct = None
    if wip_items:
        aging_wip_pct = round((len(aging_wip) / len(wip_items)) * 100, 1)

    kpis = {
        "wip": len(wip_items),
        "aging_wip": len(aging_wip),
        "cycle_p50_days": round(median(cycle_times), 1) if cycle_times else None,
        "lead_p50_days": round(median(lead_times), 1) if lead_times else None,
        "throughput_7d": throughput_7d,
        "flow_eff_pct": flow_eff_pct,
        "aging_wip_pct" : aging_wip_pct
    }

    # -----------------------
    # Bottlenecks
    # -----------------------
    status_stats = defaultdict(list)

    for i in issues:
        status_stats[i["state"]].append(
            i["kanban_metrics"]["current_wip_age_days"] or 0
        )

    bottlenecks = []

    for status, values in status_stats.items():
        bottlenecks.append({
            "status": status,
            "count": len(values),
            "avgAge": round(sum(values) / len(values), 1),
            "maxAge": max(values)
        })

    bottlenecks.sort(key=lambda x: x["avgAge"], reverse=True)

    return JsonResponse({
        "kpis": kpis,
        "wia": {
            **wia_data,
            "maxYPad": 10,
            "open_jira_url": jira.get_url_jql(f"sprint={sprint['id']}")
        },
        "bottlenecks": bottlenecks,
        "links": {
            "open_jira_wia": jira.get_url_jql(f"sprint={sprint['id']}"),
            "open_jira_bottlenecks": jira.get_url_jql(f"sprint={sprint['id']}"),
            "open_jira_aging": jira.get_url_jql(f"sprint={sprint['id']} ORDER BY updated DESC"),
        }
    })


@require_GET
@login_required
def dashboard_items_api(request):
    jira = get_connected_jira_for_user(request.user)

    sprint = jira.get_current_sprint(jira.cfg.jira_board_id)
    issues = jira.get_cached_sprint_issues(
        sprint["id"],
        jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
        full=True
    )

    items = []

    for i in issues:
        items.append({
            "key": i["key"],
            "title": i["summary"],
            "status": i["state"],
            "ageDays": i["kanban_metrics"]["current_wip_age_days"],
            "assignee": (i["assignee"] or {}).get("displayName"),
            "url": i["url"],
            "detail_url": f"/issue/{i['key']}/"
        })

    return JsonResponse({"items": items})



class DashboardView(LoginRequiredMixin, View):
    template_name = "dashboard.html"

    def get(self, request):
        jira = get_connected_jira_for_user(request.user)
        sprint = jira.get_current_sprint()
        if not sprint:
            raise Http404("Sprint not found")
        context = {
            "sprint": sprint,
        }
        return render(request, self.template_name, context)

class HomeView(View):
    template_name = "home.html"
    def get(self, request):
        context = {}
        return render(request, self.template_name, context)