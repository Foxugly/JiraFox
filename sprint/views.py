from django.http import HttpResponse, request, JsonResponse
from django.views import View
from django.views.generic import ListView, TemplateView
from django.shortcuts import get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin

from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_client import JiraManager
from jiramodule.status import CANONICAL_ORDER, STATUS_EXCLUDED
from .report import create_xlsx_bytes


class SprintListView(LoginRequiredMixin, ListView):
    template_name = "sprint/sprint_list.html"
    context_object_name = "sprints"
    paginate_by = 10  # Bootstrap friendly

    def get_queryset(self):
        # 1. config Jira courante pour l’utilisateur
        jc = get_object_or_404(
            JiraConfiguration,
            user=self.request.user,
            current=True)

        # 2. connexion Jira
        jm = JiraManager(jc)
        jm.connect()

        # 3. récupération des sprints
        # sprints = jm.list_sprints_for_current_board("active,future, closed")

        selected_states = self.request.GET.getlist("state")
        if not selected_states:
            selected_states = ["active", "future"]

        states = ",".join(selected_states)
        sprints = jm.list_sprints_for_current_board(states)

        # 4. tri (souvent pratique)
        return sorted(
            sprints,
            key=lambda s: (s.get("state"), s.get("startDate") or ""),
            reverse=True,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["board_id"] = (
            JiraConfiguration.objects
            .filter(user=self.request.user, current=True)
            .values_list("jira_board_id", flat=True)
            .first()
        )
        return ctx


class SprintDetailView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")

        cfg = get_object_or_404(
            JiraConfiguration,
            user=self.request.user,
            current=True
        )

        jira = JiraManager(cfg)
        jira.connect()
        ctx["sprint"] = jira.get_sprint(sprint_id)
        ctx["issues"] = jira.get_sprint_issues(sprint_id, jql_extra=jira.JIRA_ONLY_STANDARD_TYPES)
        ctx["jira_sprint_url"] = jira.get_url_jql(f"sprint={sprint_id}")
        return ctx


class SprintKanbanView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_kanban.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")

        cfg = get_object_or_404(
            JiraConfiguration,
            user=self.request.user,
            current=True
        )

        jira = JiraManager(cfg)
        jira.connect()
        ctx["sprint"] = jira.get_sprint(sprint_id)
        # ctx["issues"] = jira.get_sprint_issues(sprint_id)
        return ctx


class SprintReportView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_report.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")

        cfg = get_object_or_404(
            JiraConfiguration,
            user=self.request.user,
            current=True
        )

        jira = JiraManager(cfg)
        jira.connect()
        ################ STATUSES ################
        ctx["statuses"] = []

        for name, data in sorted(jira.get_dict_statuses().items(), key=lambda item: item[1]["order"]):
            statuses = ",".join([f'"{s["name"]}"' for s in data['statuses']])
            jql = f"sprint = {sprint_id} and status in ({statuses}) and {jira.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
            d = {"name": name, "jql": jql, "url_jql": jira.get_url_jql(jql)}
            ctx["statuses"].append(d)

        fastlane_jql = f"sprint = {sprint_id} and labels in ('fastlane', 'Fastlane') and {jira.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC"
        d = {"name": "Fastlane", "jql": fastlane_jql, "url_jql": jira.get_url_jql(fastlane_jql)}
        ctx["statuses"].append(d)
        ctx["sprint"] = jira.get_sprint(sprint_id)
        return ctx


class SprintXLSExportView(View):

    def get(self, request, sprint_id):
        # Connexion Jira
        cfg = JiraConfiguration.objects.get(user=request.user, current=True)
        jira = JiraManager(cfg)
        jira.connect()

        content = create_xlsx_bytes(jira, sprint_id)

        # Prépare la réponse HTTP

        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response['Content-Disposition'] = f'attachment; filename="sprint_{sprint_id}.xlsx"'
        return response


def get_api_sprint_kanban_issues(request, sprint_id: int) -> JsonResponse:
    cfg = JiraConfiguration.objects.get(user=request.user, current=True)
    jira = JiraManager(cfg)
    jira.connect()
    data_issues = {}
    for i in jira.get_sprint_issues(sprint_id, jql_extra=jira.JIRA_ONLY_STANDARD_TYPES):
        print(i)
        state = i["state"]
        print(state)
        if state in data_issues.keys():
            data_issues[state].append(i)
        else:
            data_issues[state] = [i, ]
    data = []
    for state, list_issues in data_issues.items():
        data.append({"name": state, "issues": list_issues})
    print(data)
    return JsonResponse(data, safe=False)


class SprintWiaView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_wia.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")

        cfg = get_object_or_404(
            JiraConfiguration,
            user=self.request.user,
            current=True
        )

        jira = JiraManager(cfg)
        jira.connect()
        ctx["sprint"] = jira.get_sprint(sprint_id)
        # ctx["issues"] = jira.get_sprint_issues(sprint_id)
        return ctx


from django.http import JsonResponse, Http404
from django.views.decorators.http import require_GET


# CANONICAL_ORDER = [...]  # ta liste complète ordonnée PBI lifecycle

@require_GET
def get_api_sprint_wia_issues(request, sprint_id: int) -> JsonResponse:
    try:
        cfg = JiraConfiguration.objects.get(user=request.user, current=True)
    except JiraConfiguration.DoesNotExist:
        raise Http404("No current Jira configuration for this user.")

    jira = JiraManager(cfg)
    jira.connect()

    mapped_items: list[dict] = []

    for i in jira.get_sprint_issues(
            sprint_id,
            jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
            full=True
    ):
        status = (i.get("state") or "").strip() or "Unknown"
        if status not in STATUS_EXCLUDED:
            # current_wip_age_hours peut être float / int / None selon la source
            age_hours = i.get("kanban_metrics", {}).get("current_wip_age_hours") or 0
            cycle_time_hours = i.get("kanban_metrics", {}).get("cycle_time_hours") or 0
            first_in_progress = i.get("kanban_metrics", {}).get("first_in_progress") or 0
            first_in_progress_display = i.get("kanban_metrics", {}).get("first_in_progress_display") or 0
            try:
                age_hours = float(age_hours)
            except (TypeError, ValueError):
                age_hours = 0
            try:
                cycle_time_hours = float(cycle_time_hours)
            except (TypeError, ValueError):
                cycle_time_hours = 0
            # 8h = 1 jour (comme tu fais), on force >= 0
            age_days = max(0, int(age_hours // 8))
            cycle_time_days = max(0, int(cycle_time_hours // 8))

            mapped_items.append({
                "key": i.get("key"),
                "status": status,
                "ageDays": age_days,
                "cycleTime" : cycle_time_days,
                "first_in_progress": first_in_progress,
                "first_in_progress_display": first_in_progress_display,
                "title": i.get("summary") or "",
                "url": i.get("url") or "",
            })
    present_statuses = {it["status"] for it in mapped_items}
    ordered_known = [s for s in CANONICAL_ORDER if s in present_statuses]
    unknown = sorted(present_statuses - set(CANONICAL_ORDER))
    statuses = ordered_known + unknown
    data = {"statuses": statuses, "items": mapped_items}
    return JsonResponse(data)
