from django.views import View
from django.shortcuts import render
from django.http import Http404
from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_client import JiraManager


# Create your views here.
class IssueDetailView(View):

    template_name = "issue/issue_detail.html"

    def get(self, request, jira_key: str):

        try:
            cfg = JiraConfiguration.objects.get(user=request.user, current=True)
        except JiraConfiguration.DoesNotExist:
            raise Http404("No Jira configuration found.")

        jira = JiraManager(cfg)
        jira.connect()

        issue = jira.get_issue(jira_key)

        if not issue:
            raise Http404("Issue not found")

        context = {
            "issue": issue,
        }

        return render(request, self.template_name, context)