from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from team.forms import ProjectForm
from team.models import TeamDev
from team.services import (
    create_dev_for_team,
    create_project,
    remove_project_member,
    devs_for_user,
    parse_dev_update,
    parse_teamdev_order,
    reorder_teamdevs_for_team,
    serialize_dev,
    teamdevs_for_user,
    teams_for_user,
)


@login_required
@require_POST
def update_dev_field(request, dev_id: int):
    dev = get_object_or_404(devs_for_user(request.user), pk=dev_id)

    try:
        field, value = parse_dev_update(
            request.POST.get("field"),
            request.POST.get("value"),
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    setattr(dev, field, value)
    dev.save(update_fields=[field])
    return JsonResponse({"status": "ok", "dev_id": dev.id, "field": field, "value": value})


@login_required
@require_POST
def create_dev_in_team(request, team_id: int):
    team = get_object_or_404(teams_for_user(request.user), pk=team_id)

    try:
        teamdev = create_dev_for_team(team=team, name=request.POST.get("name"))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return JsonResponse(
        {
            "status": "ok",
            "teamdev_id": teamdev.id,
            "dev": serialize_dev(teamdev.dev),
        }
    )


@login_required
@require_POST
def delete_dev_from_team(request, teamdev_id: int):
    teamdev = get_object_or_404(teamdevs_for_user(request.user), pk=teamdev_id)
    teamdev.delete()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def reorder_teamdevs(request, team_id: int):
    team = get_object_or_404(teams_for_user(request.user), pk=team_id)
    raw_ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")

    try:
        ids = parse_teamdev_order(raw_ids)
        reordered_ids = reorder_teamdevs_for_team(team=team, ids=ids)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return JsonResponse({"status": "ok", "ids": reordered_ids})


def _member_project_or_404(user, project_id: int):
    return get_object_or_404(teams_for_user(user), pk=project_id)


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return reverse(fallback)


@login_required
def project_list(request):
    projects = teams_for_user(request.user).prefetch_related("members").annotate(dev_count=Count("teamdevs"))
    return render(request, "team/project_list.html", {"projects": projects})


@login_required
def project_create(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = create_project(user=request.user, form=form)
            messages.success(request, _("Project created."))
            return redirect("project_edit", project_id=project.id)
    else:
        account = request.user.jira_configs.first()
        form = ProjectForm(initial={"jira_url": account.jira_url if account else ""})
    return render(request, "team/project_form.html", {"form": form, "mode": "create"})


@login_required
def project_edit(request, project_id: int):
    project = _member_project_or_404(request.user, project_id)
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, _("Project saved."))
            return redirect("project_edit", project_id=project.id)
    else:
        form = ProjectForm(instance=project)
    return render(
        request,
        "team/project_form.html",
        {
            "form": form,
            "mode": "edit",
            "project": project,
            "members": project.members.order_by("username"),
            "candidates": get_user_model().objects.exclude(projects=project).order_by("username"),
        },
    )


@login_required
@require_POST
def project_delete(request, project_id: int):
    project = _member_project_or_404(request.user, project_id)
    name = project.name
    project.delete()
    messages.success(request, _("Project %(name)s deleted.") % {"name": name})
    return redirect("project_list")


@login_required
@require_POST
def project_add_member(request, project_id: int):
    project = _member_project_or_404(request.user, project_id)
    member = get_user_model().objects.filter(pk=request.POST.get("user_id")).first()
    if member is None:
        messages.error(request, _("Choose a user to add."))
    else:
        project.members.add(member)
        messages.success(request, _("%(user)s added to the project.") % {"user": member.username})
    return redirect("project_edit", project_id=project.id)


@login_required
@require_POST
def project_remove_member(request, project_id: int, user_id: int):
    project = _member_project_or_404(request.user, project_id)
    member = get_object_or_404(project.members, pk=user_id)
    try:
        remove_project_member(project=project, member=member)
    except ValueError:
        messages.error(request, _("A project keeps at least one member: delete it instead."))
        return redirect("project_edit", project_id=project.id)
    if member == request.user:
        messages.success(request, _("You left the project %(name)s.") % {"name": project.name})
        return redirect("project_list")
    messages.success(request, _("%(user)s removed from the project.") % {"user": member.username})
    return redirect("project_edit", project_id=project.id)


@login_required
@require_POST
def project_set_current(request):
    project = get_object_or_404(teams_for_user(request.user), pk=request.POST.get("project_id") or 0)
    request.user.current_project = project
    request.user.save(update_fields=["current_project"])
    return redirect(_safe_next(request, "home"))


@login_required
def project_devs(request, project_id: int):
    project = _member_project_or_404(request.user, project_id)
    return render(
        request,
        "team/project_devs.html",
        {
            "project": project,
            "team": project,
            "teamdevs": TeamDev.objects.filter(team=project).select_related("dev").order_by("order", "id"),
        },
    )
