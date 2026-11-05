from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET
from django.views.generic import ListView, TemplateView
from JiraFox.services.issue_data import build_sprint_issue_item, get_issue_status, is_issue_done
from jiramodule.services.jira_config_service import get_connected_jira_for_user, require_config_fields
from jiramodule.status import CANONICAL_ORDER, STATUS_EXCLUDED
from jiramodule.utils.datetime_utils import hours_to_business_days
from team.models import TeamDev
from .logged_report import create_logged_xlsx_bytes, sprint_window
from .models import SprintPeriod
from .report import create_xlsx_bytes
from .analysis import MAX_RISKS, PROVIDERS, Analysis, AnalysisParseError, Risk, build_dossier, risky_keys
from .pptx_report import build_deck
from .report_data import (
    build_report_data, closed_sprints, load_sprint_scope, sprint_warning_lines, sprints_in_range, trend_for,
)

def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _get_sprint_standard_issues(jira, sprint_id: int, *, full: bool = True):
    if hasattr(jira, "get_sprint_standard_issues"):
        return jira.get_sprint_standard_issues(sprint_id, full=full)
    return jira.get_cached_sprint_issues(
        sprint_id,
        jql_extra=jira.JIRA_ONLY_STANDARD_TYPES,
        full=full,
    )


class SprintListView(LoginRequiredMixin, ListView):
    template_name = "sprint/sprint_list.html"
    context_object_name = "sprints"
    paginate_by = 10  # Bootstrap friendly

    def _get_jira(self):
        if not hasattr(self, "_jira"):
            self._jira = get_connected_jira_for_user(self.request.user)
        return self._jira

    def get_queryset(self):
        jira = self._get_jira()
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
        jira = self._get_jira()
        context["board_id"] = jira.cfg.jira_board_id
        return context

class SprintDetailView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]
        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        return context


@require_GET
@login_required
def get_api_sprint_issues(request, sprint_id: int) -> JsonResponse:
    jira = get_connected_jira_for_user(request.user)
    issues = _get_sprint_standard_issues(jira, sprint_id, full=True)
    return JsonResponse({"items": [build_sprint_issue_item(issue) for issue in issues]})


class SprintKanbanView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_kanban.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]

        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        return context


class SprintReportView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]
        jira  = get_connected_jira_for_user(self.request.user)
        context["statuses"] = []
        # statusCategory avoids building "status in ()" when a category has no status.
        for name in ("To Do", "In Progress", "Done"):
            jql = f'sprint = {sprint_id} and statusCategory = "{name}" and {jira.JIRA_ONLY_STANDARD_TYPES} ORDER BY status ASC'
            context["statuses"].append({"name": name, "jql": jql, "url_jql": jira.get_url_jql(jql)})

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
        require_config_fields(jira.cfg, ["team"])
        team = jira.cfg.team
        context["team"] = team
        context["sprint"] = jira.get_sprint(sprint_id)
        context["teamdevs"] = TeamDev.objects.filter(team=team).select_related("dev").order_by("order", "id")
        period = SprintPeriod.objects.filter(team=team, sprint_id=sprint_id).first()
        jira_start, jira_end = sprint_window(context["sprint"], timezone.now())
        context["period"] = period
        context["period_jira"] = {"start": jira_start, "end": jira_end}
        context["period_start"] = _datetime_local(period.start if period else jira_start)
        context["period_end"] = _datetime_local(period.end if period else jira_end)
        # Champs affichés en lignes
        context["dev_fields"] = [
            ("workload", "Workload"),
            ("holidays", "Holidays"),
            ("fastlane", "Fastlane"),
            ("project_meetings", "Project meetings"),
            ("other_meetings", "Other meetings"),
        ]

        return context


def _datetime_local(value) -> str:
    """Value of an <input type="datetime-local">, in the local time zone."""
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M") if value else ""


def _parse_datetime_local(value: str | None):
    parsed = parse_datetime((value or "").strip())
    if parsed is None:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


class SprintPeriodView(LoginRequiredMixin, View):
    """Saves (or resets to Jira's dates) the bounds the logged sheet uses for this sprint."""

    def post(self, request, sprint_id):
        jira = get_connected_jira_for_user(request.user)
        require_config_fields(jira.cfg, ["team"])
        team = jira.cfg.team

        if request.POST.get("action") == "reset":
            SprintPeriod.objects.filter(team=team, sprint_id=sprint_id).delete()
            messages.success(request, _("Sprint bounds reset to the Jira dates."))
            return redirect("sprint_xls_report", sprint_id=sprint_id)

        start = _parse_datetime_local(request.POST.get("start"))
        end = _parse_datetime_local(request.POST.get("end"))
        if not start or not end or start >= end:
            messages.error(request, _("Enter a start and an end, the start before the end."))
        else:
            SprintPeriod.objects.update_or_create(
                team=team, sprint_id=sprint_id, defaults={"start": start, "end": end}
            )
            messages.success(request, _("Sprint bounds saved."))
        return redirect("sprint_xls_report", sprint_id=sprint_id)


class SprintXLSExportView(LoginRequiredMixin, View):

    def get(self, request, sprint_id):
        jira = get_connected_jira_for_user(request.user)
        require_config_fields(jira.cfg, ["jira_project_id"])
        return xlsx_response(create_xlsx_bytes(jira, sprint_id), f"sprint_{sprint_id}.xlsx")


class SprintLoggedXLSExportView(LoginRequiredMixin, View):

    def get(self, request, sprint_id):
        jira = get_connected_jira_for_user(request.user)
        require_config_fields(jira.cfg, ["team"])
        return xlsx_response(create_logged_xlsx_bytes(jira, sprint_id), f"sprint_{sprint_id}_logged.xlsx")


def xlsx_response(content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
@login_required
def get_api_sprint_kanban_issues(request, sprint_id: int) -> JsonResponse:
    jira = get_connected_jira_for_user(request.user)
    data_issues = {}
    for i in _get_sprint_standard_issues(jira, sprint_id, full=False):
        data_issues.setdefault(get_issue_status(i), []).append(build_sprint_issue_item(i))
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
        context["sprint_id"] = sprint_id
        return context


class SprintWeightedThroughputDistributionView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_weighted_throughput_distribution.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")
        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        context.update(build_weighted_throughput_distribution(jira, sprint_id))
        return context


class SprintScopeFlowView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_scope_flow.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs["sprint_id"]
        jira = get_connected_jira_for_user(self.request.user)
        require_config_fields(jira.cfg, ["jira_board_id"])
        loaded = load_sprint_scope(jira, jira.get_sprint(sprint_id))
        scope = loaded["scope"]
        context.update(loaded["warnings"])
        context["sprint"] = loaded["sprint"]
        context["scope"] = scope
        context["sprint_start"] = loaded["start"]
        completion = scope["completion"]
        context["completion"] = [
            (_("Planned issues closed"), completion["planned"], "planned"),
            (_("Added issues closed"), completion["added"], "added"),
            (_("All issues closed"), completion["all"], "all"),
        ]
        context["jira_browse_url"] = jira.get_issue_url("")
        stability = context["scope"]["stability"]
        context["stability"] = [
            (_("Planned issues unchanged"), stability["planned"], "planned"),
            (_("Added issues unchanged"), stability["added"], "added"),
            (_("All issues unchanged"), stability["all"], "all"),
        ]
        return context


def _format_story_points(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def build_weighted_throughput_distribution(jira, sprint_id: int) -> dict:
    issues = _get_sprint_standard_issues(jira, sprint_id, full=True)
    return build_weighted_throughput_distribution_from_issues(issues)


def build_weighted_throughput_distribution_from_issues(issues: list[dict]) -> dict:
    counts_by_points: dict[float, int] = {}
    done_count = 0

    for issue in issues:
        if not is_issue_done(issue):
            continue

        done_count += 1
        story_points = (issue.get("estimation") or {}).get("story_points")
        if story_points is None or story_points == "":
            points = 0.0
        else:
            try:
                points = float(story_points)
            except (TypeError, ValueError):
                points = 0.0

        counts_by_points[points] = counts_by_points.get(points, 0) + 1

    sorted_points = sorted(counts_by_points)
    labels = [_format_story_points(points) for points in sorted_points]
    counts = [counts_by_points[points] for points in sorted_points]

    return {
        "distribution": {
            "labels": labels,
            "counts": counts,
        },
        "done_count": done_count,
        "estimated_done_count": sum(counts),
    }


def build_wia_data(jira, sprint_id: int) -> dict:
    issues = _get_sprint_standard_issues(jira, sprint_id, full=True)
    return build_wia_data_from_issues(issues)


def build_wia_data_from_issues(issues: list[dict]) -> dict:
    mapped_items: list[dict] = []
    for i in issues:
        status = (i.get("state") or "").strip() or "Unknown"
        if status in STATUS_EXCLUDED:
            continue

        km = i.get("kanban_metrics") or {}
        age_hours = _to_float(km.get("current_wip_age_hours"), 0.0)
        cycle_hours = _to_float(km.get("cycle_time_hours"), 0.0)

        age_days = max(0, int(hours_to_business_days(age_hours) or 0))
        cycle_days = max(0, int(hours_to_business_days(cycle_hours) or 0))

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


PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SESSION_KEY = "management_report"


def _ids(values) -> list[int]:
    ids = []
    for value in values:
        if str(value).strip().isdigit() and int(value) not in ids:
            ids.append(int(value))
    return ids


class SprintTrendsView(LoginRequiredMixin, TemplateView):
    """Closed sprints side by side: pick a range or sprints, see the trends, start the report."""

    template_name = "sprint/sprint_trends.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jira = get_connected_jira_for_user(self.request.user)
        require_config_fields(jira.cfg, ["jira_board_id"])
        closed = closed_sprints(jira)
        start = parse_date(self.request.GET.get("start") or "")
        end = parse_date(self.request.GET.get("end") or "")
        picked = _ids(self.request.GET.getlist("sprint"))
        if picked:
            selected = [sprint for sprint in closed if sprint["id"] in picked]
        elif start and end:
            selected = sprints_in_range(closed, start, end)
        else:
            selected = []
        selected_ids = {sprint["id"] for sprint in selected}
        context.update(
            closed=[{**sprint, "selected": sprint["id"] in selected_ids} for sprint in reversed(closed)],
            start=start, end=end,
            requested=bool(picked or (start and end)),
        )
        if selected:
            data = build_report_data(jira, selected)
            context["rows"] = [
                {**entry, "warning_lines": sprint_warning_lines(entry["warnings"]),
                 "scope_errors": entry["warnings"]["scope_errors"]}
                for entry in data["sprints"]
            ]
            context["snapshot"] = data["snapshot"]
            context["focus"] = data["focus"]
            context["report_query"] = "&amp;".join(f"sprint={sprint['id']}" for sprint in selected)
            metrics = [entry["metrics"] for entry in data["sprints"]]
            context["chart_data"] = {
                "labels": [entry["sprint"].get("name") or str(entry["sprint"]["id"]) for entry in data["sprints"]],
                "throughput": [m["throughput"] for m in metrics],
                "cycle_median": [m["cycle_median"] for m in metrics],
                "cycle_p85": [m["cycle_p85"] for m in metrics],
                "percent_planned_closed": [m["percent_planned_closed"] for m in metrics],
                "percent_added": [m["percent_added"] for m in metrics],
            }
        return context


def _report_sprints(jira, source) -> tuple[list[dict], dict | None]:
    """The sprints of a report: with `focus`, the focus and its trend; else the explicit `sprint` list."""
    focus_ids = _ids([source.get("focus") or ""])
    if focus_ids:
        focus = jira.get_sprint(focus_ids[0])
        return trend_for(focus, closed_sprints(jira)) + [focus], focus
    return [jira.get_sprint(sprint_id) for sprint_id in _ids(source.getlist("sprint"))], None


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _edited_analysis(post) -> Analysis:
    """The analysis as the user edited it: these values, not the raw parse, go in the deck."""
    risks = []
    for index in range(MAX_RISKS):
        title = (post.get(f"risk_title_{index}") or "").strip()
        text = (post.get(f"risk_text_{index}") or "").strip()
        if title or text:
            risks.append(Risk(title, text))
    return Analysis(_lines(post.get("situation")), _lines(post.get("trend")), risks)


class ManagementReportView(LoginRequiredMixin, View):
    """Analysis (optional, by AI) then download of the management PowerPoint."""

    template_name = "sprint/management_report.html"

    def get(self, request):
        return self._respond(request, request.GET)

    def post(self, request):
        return self._respond(request, request.POST)

    def _respond(self, request, source):
        jira = get_connected_jira_for_user(request.user)
        require_config_fields(jira.cfg, ["jira_board_id"])
        sprints, focus = _report_sprints(jira, source)
        if not sprints:
            messages.error(request, _("Pick at least one sprint to build a report."))
            return redirect("sprint-trends")
        data = build_report_data(jira, sprints, focus=focus)
        ids = [sprint["id"] for sprint in sprints]
        focus_id = focus["id"] if focus else None
        provider = PROVIDERS.get(source.get("provider") or "copilot", PROVIDERS["copilot"])
        action = request.POST.get("action") if request.method == "POST" else None
        context = {
            "ids": ids, "request_ids": _ids(source.getlist("sprint")), "focus_id": focus_id, "data": data,
            "provider": provider, "providers": list(PROVIDERS.values()),
        }

        stored = request.session.get(SESSION_KEY) or {}
        matches = stored.get("sprints") == ids and stored.get("focus") == focus_id
        stored_analysis = stored.get("analysis") if matches else None

        if action == "generate":
            if not provider.needs_prompt:
                return self._deck(jira, data, None)
            if stored_analysis is None:
                context["warning"] = _("The analysis does not match these sprints: paste the answer again.")
            else:
                return self._deck(jira, data, _edited_analysis(request.POST))
        elif action == "parse":
            context["answer"] = request.POST.get("answer", "")
            try:
                parsed = provider.parse(context["answer"])
            except AnalysisParseError as exc:
                context["parse_error"] = str(exc)
            else:
                stored_analysis = parsed.to_session()
                request.session[SESSION_KEY] = {"sprints": ids, "focus": focus_id, "analysis": stored_analysis}

        if provider.needs_prompt:
            context["dossier"] = build_dossier(data, jira.get_descriptions(risky_keys(data)))
            context["prompt_text"] = provider.prompt(context["dossier"])
        if stored_analysis is not None:
            analysis = Analysis.from_session(stored_analysis)
            risks = analysis.risks + [Risk("", "")] * (MAX_RISKS - len(analysis.risks))
            context["analysis_form"] = {
                "situation": "\n".join(analysis.situation), "trend": "\n".join(analysis.trend), "risks": risks,
            }
        return render(request, self.template_name, context)

    @staticmethod
    def _deck(jira, data, analysis) -> HttpResponse:
        project = getattr(jira.cfg, "project", None)
        template = getattr(project, "report_template", None)
        template_path = template.path if template else None
        response = HttpResponse(build_deck(data, analysis, template_path), content_type=PPTX_CONTENT_TYPE)
        name = slugify(getattr(project, "name", "") or "") or "sprint"
        response["Content-Disposition"] = (
            f'attachment; filename="rapport-direction-{name}-{timezone.localdate():%Y-%m-%d}.pptx"'
        )
        return response
