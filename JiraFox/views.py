# views.py
from collections import defaultdict
from statistics import median

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_GET

from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_client import JiraManager
from sprint.views import build_wia_data


@require_GET
@login_required
def dashboard_summary_api(request):
    try:
        cfg = JiraConfiguration.objects.get(user=request.user, current=True)
    except JiraConfiguration.DoesNotExist:
        raise Http404("No current Jira configuration for this user.")
    jira = JiraManager(cfg)
    jira.connect()

    sprint = jira.get_current_sprint(cfg.jira_board_id)
    issues = jira.get_sprint_issues(
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

    kpis = {
        "wip": len(wip_items),
        "aging_wip": len(aging_wip),
        "cycle_p50_days": round(median(cycle_times), 1) if cycle_times else None,
        "lead_p50_days": round(median(lead_times), 1) if lead_times else None,
        "throughput_7d": 0,  # à améliorer si tu veux calcul réel
        "flow_eff_pct": None,
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
    cfg = JiraConfiguration.objects.get(user=request.user, current=True)
    jira = JiraManager(cfg)
    jira.connect()

    sprint = jira.get_current_sprint(cfg.jira_board_id)
    issues = jira.get_sprint_issues(
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


class HomeView(View):
    template_name = "home.html"

    def get(self, request):

        try:
            cfg = JiraConfiguration.objects.get(user=request.user, current=True)
        except JiraConfiguration.DoesNotExist:
            raise Http404("No Jira configuration found.")

        jira = JiraManager(cfg)
        jira.connect()

        sprint = jira.get_current_sprint()

        if not sprint:
            raise Http404("Sprint not found")

        context = {
            "sprint": sprint,
        }

        return render(request, self.template_name, context)
