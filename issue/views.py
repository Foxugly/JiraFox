from django.contrib.auth.mixins import LoginRequiredMixin
from django.views import View
from django.shortcuts import render
from django.http import Http404
from jiramodule.services.jira_config_service import get_connected_jira_for_user


class IssueDetailView(LoginRequiredMixin, View):
    template_name = "issue/issue_detail.html"

    def get(self, request, jira_key: str):
        jira = get_connected_jira_for_user(request.user)
        issue = jira.get_issue(jira_key)
        if not issue:
            raise Http404("Issue not found")
        context = {
            "issue": issue,
        }
        return render(request, self.template_name, context)