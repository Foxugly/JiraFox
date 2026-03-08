from django.shortcuts import render

# Create your views here.
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.db import transaction

from dev.models import Dev
from .models import Team, TeamDev


ALLOWED_INT_FIELDS = {"workload", "holidays", "fastlane", "project_meetings", "other_meetings"}


@login_required
@require_POST
def update_dev_field(request, dev_id: int):
    """
    Met à jour un champ integer d'un Dev (AJAX inline).
    body: field, value
    """
    dev = get_object_or_404(Dev, pk=dev_id)
    field = request.POST.get("field")
    value = request.POST.get("value")

    if field not in ALLOWED_INT_FIELDS:
        return HttpResponseBadRequest("Invalid field")

    try:
        int_value = int(value)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid integer value")

    setattr(dev, field, int_value)
    dev.save(update_fields=[field])

    return JsonResponse({"status": "ok", "dev_id": dev.id, "field": field, "value": int_value})


@login_required
@require_POST
def create_dev_in_team(request, team_id: int):
    """
    Crée un Dev + l'associe à la Team via TeamDev, en fin d'ordre.
    body: name
    """
    team = get_object_or_404(Team, pk=team_id)
    name = (request.POST.get("name") or "").strip()
    if not name:
        return HttpResponseBadRequest("Name required")

    with transaction.atomic():
        dev = Dev.objects.create(name=name)
        last_order = TeamDev.objects.filter(team=team).count()
        td = TeamDev.objects.create(team=team, dev=dev, order=last_order)

    return JsonResponse({
        "status": "ok",
        "teamdev_id": td.id,
        "dev": {
            "id": dev.id,
            "name": dev.name,
            "workload": dev.workload,
            "holidays": dev.holidays,
            "fastlane": dev.fastlane,
            "project_meetings": dev.project_meetings,
            "other_meetings": dev.other_meetings,
        }
    })


@login_required
@require_POST
def delete_dev_from_team(request, teamdev_id: int):
    """
    Supprime le lien TeamDev (n'efface PAS le Dev globalement).
    """
    td = get_object_or_404(TeamDev, pk=teamdev_id)
    td.delete()
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def reorder_teamdevs(request, team_id: int):
    """
    Réordonne les colonnes (TeamDev.order) pour une team donnée.
    body: ids[]=<teamdev_id> (dans le nouvel ordre de gauche à droite)
    """
    team = get_object_or_404(Team, pk=team_id)

    # Accepte ids[] ou ids (fallback)
    ids = request.POST.getlist("ids[]")
    if not ids:
        ids = request.POST.getlist("ids")

    try:
        ids = [int(_id) for _id in ids]
    except ValueError:
        return HttpResponseBadRequest("Invalid ids")

    # Vérif que tous appartiennent à la team
    existing = set(TeamDev.objects.filter(team=team, id__in=ids).values_list("id", flat=True))
    if set(ids) != existing:
        return HttpResponseBadRequest("Mismatched teamdev ids")

    with transaction.atomic():
        for idx, td_id in enumerate(ids):
            TeamDev.objects.filter(id=td_id, team=team).update(order=idx)

    return JsonResponse({"status": "ok", "ids": ids})