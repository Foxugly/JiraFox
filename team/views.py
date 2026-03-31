from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from dev.models import Dev
from team.models import Team, TeamDev
from team.services import (
    create_dev_for_team,
    parse_dev_int_update,
    parse_teamdev_order,
    reorder_teamdevs_for_team,
    serialize_dev,
)


@login_required
@require_POST
def update_dev_field(request, dev_id: int):
    dev = get_object_or_404(Dev, pk=dev_id)

    try:
        field, int_value = parse_dev_int_update(
            request.POST.get("field"),
            request.POST.get("value"),
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    setattr(dev, field, int_value)
    dev.save(update_fields=[field])
    return JsonResponse({"status": "ok", "dev_id": dev.id, "field": field, "value": int_value})


@login_required
@require_POST
def create_dev_in_team(request, team_id: int):
    team = get_object_or_404(Team, pk=team_id)

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
    teamdev = get_object_or_404(TeamDev, pk=teamdev_id)
    teamdev.delete()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def reorder_teamdevs(request, team_id: int):
    team = get_object_or_404(Team, pk=team_id)
    raw_ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")

    try:
        ids = parse_teamdev_order(raw_ids)
        reordered_ids = reorder_teamdevs_for_team(team=team, ids=ids)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    return JsonResponse({"status": "ok", "ids": reordered_ids})
