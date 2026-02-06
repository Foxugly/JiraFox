
from django.views.generic import ListView, TemplateView
from django.shortcuts import get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin

from jiramodule.models import JiraConfiguration, JiraManager


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
        #sprints = jm.list_sprints_for_current_board("active,future, closed")

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
        ctx["jira_url"] = cfg.jira_url
        ctx["sprint"] = jira.get_sprint(sprint_id)
        ctx["issues"] = jira.get_sprint_issues(sprint_id, jql_extra=jira.JIRA_ONLY_STANDARD_TYPES)
        ctx["jira_sprint_url"] =  jira.get_url_jql(f"sprint={sprint_id}")
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
        ctx["issues"] = jira.get_sprint_issues(sprint_id)
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
        ctx["sprint"] = jira.get_sprint(sprint_id)
        ctx["issues"] = jira.get_sprint_issues(sprint_id)
        return ctx