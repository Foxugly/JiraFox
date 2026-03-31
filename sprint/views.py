from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.http import require_GET
from django.views.generic import ListView, TemplateView
from JiraFox.services.issue_data import build_sprint_issue_item, get_issue_status
from jiramodule.services.jira_config_service import get_connected_jira_for_user
from jiramodule.status import CANONICAL_ORDER, STATUS_EXCLUDED
from team.models import TeamDev
from .report import create_xlsx_bytes


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


class SprintListView(LoginRequiredMixin, ListView):
    template_name = "sprint/sprint_list.html"
    context_object_name = "sprints"
    paginate_by = 10  # Bootstrap friendly

    def get_queryset(self):
        jira = get_connected_jira_for_user(self.request.user)
        selected_states = self.request.GET.getlist("state")
        if not selected_states:
            selected_states = ["active", "future"]
        states = ",".join(selected_states)
        sprints = jira.list_sprints_for_current_board(states)
        return sorted(
            sprints,
            key=lambda s: (s.get("state"), s.get("startDate") or ""),
            reverse=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jira = get_connected_jira_for_user(self.request.user)
        context["board_id"] = jira.cfg.jira_board_id
        return context

class SprintDetailView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]
        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        context["jira_sprint_url"] = jira.get_url_jql(f"sprint={sprint_id}")
        return context


@require_GET
@login_required
def get_api_sprint_issues(request, sprint_id: int) -> JsonResponse:
    jira = get_connected_jira_for_user(request.user)
    issues = jira.get_cached_sprint_issues(
        sprint_id,
        jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
        full=True,
    )
    return JsonResponse({"items": [build_sprint_issue_item(issue) for issue in issues]})


class SprintKanbanView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_kanban.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]

        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        # ctx["issues"] = jira.get_sprint_issues(sprint_id)
        return context


class SprintReportView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]
        jira  = get_connected_jira_for_user(self.request.user)
        context["statuses"] = []
        for name, data in sorted(jira.get_dict_statuses().items(), key=lambda item: item[1]["order"]):
            statuses = ",".join([f'"{s["name"]}"' for s in data['statuses']])
            jql = f"sprint = {sprint_id} and status in ({statuses}) and {jira.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
            d = {"name": name, "jql": jql, "url_jql": jira.get_url_jql(jql)}
            context["statuses"].append(d)

        fastlane_jql = f"sprint = {sprint_id} and labels in ('fastlane', 'Fastlane') and {jira.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
        d = {"name": "Fastlane", "jql": fastlane_jql, "url_jql": jira.get_url_jql(fastlane_jql)}
        context["statuses"].append(d)
        context["sprint"] = jira.get_sprint(sprint_id)
        return context


class SprintDetailXLSExportView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_xls_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")
        jira = get_connected_jira_for_user(self.request.user)
        team = jira.cfg.team
        context["team"] = team
        context["sprint"] = jira.get_sprint(sprint_id)
        context["teamdevs"] = TeamDev.objects.filter(team=team).select_related("dev").order_by("order", "id")
        # Champs affichés en lignes
        context["dev_fields"] = [
            ("workload", "Workload"),
            ("holidays", "Holidays"),
            ("fastlane", "Fastlane"),
            ("project_meetings", "Project meetings"),
            ("other_meetings", "Other meetings"),
        ]

        return context


class SprintXLSExportView(LoginRequiredMixin, View):

    def get(self, request, sprint_id):
        jira = get_connected_jira_for_user(request.user)
        content = create_xlsx_bytes(jira, sprint_id)
        # Prépare la réponse HTTP
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="sprint_{sprint_id}.xlsx"'
        return response


@require_GET
@login_required
def get_api_sprint_kanban_issues(request, sprint_id: int) -> JsonResponse:
    jira = get_connected_jira_for_user(request.user)
    data_issues = {}
    for i in jira.get_cached_sprint_issues(sprint_id, jql_extra=jira.JIRA_ONLY_STANDARD_TYPES):
        state = get_issue_status(i)
        if state in data_issues.keys():
            data_issues[state].append(i)
        else:
            data_issues[state] = [i, ]
    data = []
    for state, list_issues in data_issues.items():
        data.append({"name": state, "issues": list_issues})
    return JsonResponse(data, safe=False)


class SprintWiaView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_wia.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")

        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        return context


def build_wia_data(jira, sprint_id: int) -> dict:
    mapped_items: list[dict] = []

    issues = jira.get_cached_sprint_issues(
        sprint_id,
        jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
        full=True,
    )

    for i in issues:
        status = (i.get("state") or "").strip() or "Unknown"
        if status in STATUS_EXCLUDED:
            continue

        km = i.get("kanban_metrics") or {}
        age_hours = _to_float(km.get("current_wip_age_hours"), 0.0)
        cycle_hours = _to_float(km.get("cycle_time_hours"), 0.0)

        age_days = max(0, int(age_hours // 8))
        cycle_days = max(0, int(cycle_hours // 8))

        mapped_items.append({
            "key": i.get("key"),
            "status": status,
            "ageDays": age_days,
            "cycleTime": cycle_days,
            "first_in_progress": km.get("first_in_progress") or None,
            "first_in_progress_display": km.get("first_in_progress_display") or "",
            "title": i.get("summary") or "",
            "url": i.get("url") or "",
        })

    present_statuses = {it["status"] for it in mapped_items}
    ordered_known = [s for s in CANONICAL_ORDER if s in present_statuses]
    unknown = sorted(present_statuses - set(CANONICAL_ORDER))

    return {
        "statuses": ordered_known + unknown,
        "items": mapped_items,
    }


@require_GET
@login_required
def get_api_sprint_wia_issues(request, sprint_id: int) -> JsonResponse:
    jira = get_connected_jira_for_user(request.user)
    data = build_wia_data(jira, sprint_id)
    return JsonResponse(data)
