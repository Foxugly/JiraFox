from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from JiraFox.forms import UserSettingsForm
from JiraFox.services.dashboard import (
    build_dashboard_bottlenecks,
    build_dashboard_items,
    build_dashboard_kpis,
    get_current_sprint_with_issues,
)
from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_config_service import get_connected_jira_for_user
from jiramodule.services.jql import JqlBuilder
from sprint.views import build_weighted_throughput_distribution_from_issues, build_wia_data_from_issues


@require_POST
def set_language_preference(request):
    language = request.POST.get("language")
    valid_languages = {code for code, _name in settings.LANGUAGES}
    if language not in valid_languages:
        language = settings.LANGUAGE_CODE

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"

    if request.user.is_authenticated and getattr(request.user, "language", None) != language:
        request.user.language = language
        request.user.save(update_fields=["language"])

    translation.activate(language)
    request.session[settings.LANGUAGE_COOKIE_NAME] = language
    response = redirect(next_url)
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language, samesite="Lax")
    return response


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
                **build_wia_data_from_issues(issues),
                "maxYPad": 10,
                "open_jira_url": jira.get_url_jql(f"sprint={sprint['id']}"),
            },
            "bottlenecks": build_dashboard_bottlenecks(issues),
            "weightedThroughput": build_weighted_throughput_distribution_from_issues(issues),
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


class QueryView(LoginRequiredMixin, TemplateView):
    template_name = "query.html"
    DEFAULT_MAX_RESULTS = 50
    MAX_RESULTS_LIMIT = 500
    PROJECT_KEY = "BUSOPENLAM"
    epics = [
        (
            "M0 — CM Dependencies, Planning & OpenLAMA MVP",
            """M0.1 — Gouvernance et planning CM (MVP, API, Testing)
M0.2 — OpenLAMA Ready for Solidaris (MVP, IAM, accès) (To be Define)
M0.3 — API CM (To be Define)
M0.4 — Initial Load : Mapping
M0.5 — Environnements de testing CM/Solidaris (Start testing)""",
        ),
        ("M1 — Digitalisation Logopédie – Solidaris Internal (Mapping - Business)",
         """M1.1 — Business Requirements & Mapping Agreement & UAT
M1.2 — Adaptation nomenclature AKLO/B5/CLO
M1.3 — 306 Mapping Validation""",
         ),
        (
            "M2 — Solidaris–OpenLAMA & Legacy Integration - IT",
            """M2.1 — Legacy Analysis & Integration Design
M2.1.1 — Architecture DP / Legacy & Exchanges
M2.1.2 — AKLO / Legacy Functional Analysis & Mapping
M2.1.3 — OpenLAMA → AKLO / Legacy Development
M2.1.4 — Testing
M2.1.5 — Prod
M2.1.6 — 306
M2.2 — Développements API DP
M2.2.1 — API Annexes
M2.2.2 — API Courriers
M2.2.3 — API Retour OpenLAMA → Solidaris → AKLO/Legacy (To be define if folder used)
M2.2.4 — Testing technique API et intégration/CM
M2.2.5 — Monitoring infrastructure & dashboard""",
        ),
        (
            "M3 — CIN Agreement",
            """M3.1 — Testing Preparation
M3.2 — Testing Agreement CIN (To be define)""",
        ),
        (
            "M4 — Initial Load",
            """M4.1 — Adaptation du fichier
M4.2 — Sample Load /CM
M4.3 — Full Initial Load""",
        ),
        (
            "M5 — Annexes (Ready for UAT & EtoE testing)",
            """M5.1 — Business Requirements Annexes
M5.2 — Intégration (création) Courrier Irisnext
M5.3 — Intégration Irisnext
M5.4 — Iged
M5.5 — SquanSequel
M5.6 — Ticketing (306)
M5.7 — Testing
M5.8 — Production""",
        ),
        (
            "M6 — Courriers (Ready for UAT & EtoE testing)",
            """M6.1 — Business Requirements Courriers
M6.2 — Intégration Irisnext
M6.3 — Iged
M6.4 — SquanSequel
M6.5 — Ticketing
M6.6 — Testing
M6.7 — Production""",
        ),
        (
            "M7 — Plan C",
            """M7.1 — eAttest
7.1.1 — Analyse FA
7.1.2 — Développement
7.1.3 — Testing
7.1.4 — Prod
7.1.5 — 306
M7.2 — eFact
7.2.1 — Analyse FA
7.2.2 — Développement
7.2.3 — Testing
7.2.4 — Prod
7.2.5 — 306""",
        ),
        (
            "M8 — Software Houses E2E & Final UAT",
            """M8.1 — UAT Testing – Solidaris/Business (To be define)
M8.2 — Software Houses Testing Preparation (To be define)
M8.3 — Agreement E2E Testing
M8.4 — eAttest E2E Testing (annexe/courrier)
M8.5 — eFact E2E Testing (annexe/courrier)
M8.6 — Regression & Final Validation""",
        ),
        (
            "M9 — Operational Readiness",
            "",
        ),
        (
            "M10 — Go-Live",
            "",
        ),
        (
            "M11 — Post-Production",
            "",
        ),
    ]

    def _current_jira_config(self):
        return JiraConfiguration.objects.filter(user=self.request.user, current=True).select_related("team").first()

    def _query_presets(self, jira):
        sprint = jira.get_current_sprint()
        sprint_id = sprint.get("id") if sprint else None
        project_id = getattr(jira.cfg, "jira_project_id", None)
        presets = []
        if sprint_id:
            presets.extend(
                [
                    {
                        "name": _("Current sprint issues"),
                        "jql": JqlBuilder.sprint(sprint_id).standard_issue_types().order_by("status ASC").build(),
                    },
                    {
                        "name": _("Current sprint Done"),
                        "jql": JqlBuilder.sprint(sprint_id).standard_issue_types().status_category("Done").order_by("resolved DESC").build(),
                    },
                    {
                        "name": _("Current sprint WIP"),
                        "jql": JqlBuilder.sprint(sprint_id).standard_issue_types().status_category_not("Done").order_by("updated DESC").build(),
                    },
                    {
                        "name": _("Fastlane"),
                        "jql": JqlBuilder.sprint(sprint_id).standard_issue_types().labels_in(["fastlane", "Fastlane"]).order_by("status ASC").build(),
                    },
                ]
            )
        if project_id:
            presets.append(
                {
                    "name": _("Recently resolved"),
                    "jql": JqlBuilder.project(project_id).standard_issue_types().status_category("Done").resolved_since_days(7).order_by("resolved DESC").build(),
                }
            )
        return presets

    def _store_query_history(self, jql: str):
        history = self.request.session.get("query_history", [])
        history = [item for item in history if item != jql]
        history.insert(0, jql)
        self.request.session["query_history"] = history[:8]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jira = get_connected_jira_for_user(self.request.user)
        jql = (self.request.GET.get("jql") or "").strip()
        try:
            requested_max_results = int(self.request.GET.get("max_results") or self.DEFAULT_MAX_RESULTS)
        except (TypeError, ValueError):
            requested_max_results = self.DEFAULT_MAX_RESULTS
        max_results = min(self.MAX_RESULTS_LIMIT, max(1, requested_max_results))
        full = self.request.GET.get("full") == "1"

        context["todo"] = [
            {
                "summary": summary,
                "description": description,
            }
            for summary, description in self.epics
        ]
        context["current_jira_config"] = self._current_jira_config()
        context["presets"] = self._query_presets(jira)
        context["query_history"] = self.request.session.get("query_history", [])
        context["jql"] = jql
        context["max_results"] = max_results
        context["full"] = full
        context["jira_url_jql"] = jira.get_url_jql(jql) if jql else ""

        if jql:
            try:
                context["issues"] = jira.run_jql(jql, max_results=max_results, full=full)
                context["result_count"] = len(context["issues"])
                self._store_query_history(jql)
            except Exception as exc:  # noqa: BLE001
                context["error"] = str(exc)

        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") != "create_epics":
            return redirect("query")

        jira = get_connected_jira_for_user(request.user)
        created = 0
        skipped = 0
        for summary, description in self.epics:
            existing = jira._search_issues(
                f'project = "{self.PROJECT_KEY}" '
                f'AND issuetype = Epic '
                f'AND summary ~ "\\"{summary}\\""',
                max_results = 1,
            )
            if existing:
                skipped += 1
                continue
            jira.create_issue(project_key=self.PROJECT_KEY, summary=summary, description=description, issue_type="Epic")
            created += 1
        if created:
            messages.success(
                request,
                f"{created} epic(s) créée(s) avec succès."
            )
        if skipped:
            messages.warning(
                request,
                f"{skipped} epic(s) déjà existante(s) ont été ignorée(s)."
            )
        return redirect("query")
