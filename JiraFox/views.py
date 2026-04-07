from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET

from JiraFox.forms import UserSettingsForm
from JiraFox.services.dashboard import (
    build_dashboard_bottlenecks,
    build_dashboard_items,
    build_dashboard_kpis,
    get_current_sprint_with_issues,
)
from jiramodule.services.jira_config_service import get_connected_jira_for_user
from sprint.views import build_wia_data


@require_GET
@login_required
def dashboard_summary_api(request):
    jira = get_connected_jira_for_user(request.user)
    sprint, issues = get_current_sprint_with_issues(jira)
    if not sprint:
        raise Http404("Sprint not found")

    return JsonResponse(
        {
            "kpis": build_dashboard_kpis(issues, throughput_7d=len(jira.throughput_7d())),
            "wia": {
                **build_wia_data(jira, sprint["id"]),
                "maxYPad": 10,
                "open_jira_url": jira.get_url_jql(f"sprint={sprint['id']}"),
            },
            "bottlenecks": build_dashboard_bottlenecks(issues),
            "links": {
                "open_jira_wia": jira.get_url_jql(f"sprint={sprint['id']}"),
                "open_jira_bottlenecks": jira.get_url_jql(f"sprint={sprint['id']}"),
                "open_jira_aging": jira.get_url_jql(f"sprint={sprint['id']} ORDER BY updated DESC"),
            },
        }
    )


@require_GET
@login_required
def dashboard_items_api(request):
    jira = get_connected_jira_for_user(request.user)
    sprint, issues = get_current_sprint_with_issues(jira)
    if not sprint:
        raise Http404("Sprint not found")
    return JsonResponse({"items": build_dashboard_items(issues)})


class DashboardView(LoginRequiredMixin, View):
    template_name = "dashboard.html"

    def get(self, request):
        jira = get_connected_jira_for_user(request.user)
        sprint = jira.get_current_sprint()
        if not sprint:
            raise Http404("Sprint not found")
        return render(request, self.template_name, {"sprint": sprint})


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        return render(request, self.template_name, {})


class AboutView(View):
    template_name = "about.html"

    def get(self, request):
        return render(request, self.template_name, {})


class SettingsView(LoginRequiredMixin, View):
    template_name = "settings.html"

    def get(self, request):
        form = UserSettingsForm(user=request.user)
        current_jira_config = request.user.jira_configs.filter(current=True).select_related("team").first()
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "current_jira_config": current_jira_config,
            },
        )

    def post(self, request):
        form = UserSettingsForm(request.POST, user=request.user)
        current_jira_config = request.user.jira_configs.filter(current=True).select_related("team").first()
        if form.is_valid():
            user = form.save()
            messages.success(request, _("Settings updated."))
            translation.activate(user.language)
            response = redirect("settings")
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, user.language)
            return response
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "current_jira_config": current_jira_config,
            },
        )
