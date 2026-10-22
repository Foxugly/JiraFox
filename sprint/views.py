from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.http import require_GET
from django.views.generic import ListView, TemplateView
from JiraFox.services.issue_data import build_sprint_issue_item, get_issue_status
from jiramodule.services.jira_config_service import get_connected_jira_for_user, require_config_fields
from jiramodule.status import CANONICAL_ORDER, STATUS_EXCLUDED
from jiramodule.utils.datetime_utils import hours_to_business_days
from team.models import TeamDev
from .logged_report import create_logged_xlsx_bytes
from .report import create_xlsx_bytes

DONE_STATUS_NAMES = {
    "resolved",
    "completed",
    "issue completed",
    "closed",
    "done",
}


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
        context["jira_sprint_url"] = jira.get_url_jql(f"sprint={sprint_id}")
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
        state = get_issue_status(i)
        if state in data_issues:
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
        context["sprint_id"] = sprint_id
        return context


class SprintWeightedThroughputDistributionView(LoginRequiredMixin, TemplateView):
    template_name = "sprint/sprint_weighted_throughput_distribution.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sprint_id = kwargs.get("sprint_id")
        jira = get_connected_jira_for_user(self.request.user)
        context["sprint"] = jira.get_sprint(sprint_id)
        context["jira_sprint_url"] = jira.get_url_jql(f"sprint={sprint_id}")
        context.update(build_weighted_throughput_distribution(jira, sprint_id))
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
        if (issue.get("statusCategory") or "").strip() != "Done" and (issue.get("state") or "").strip().lower() not in DONE_STATUS_NAMES:
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

        age_days = max(0, int(hours_to_business_days(age_hours, day_hours=7.2) or 0))
        cycle_days = max(0, int(hours_to_business_days(cycle_hours, day_hours=7.2) or 0))

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
