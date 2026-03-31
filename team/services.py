from django.db import transaction

from dev.models import Dev
from team.models import Team, TeamDev

ALLOWED_INT_FIELDS = {"workload", "holidays", "fastlane", "project_meetings", "other_meetings"}


def parse_dev_int_update(field: str | None, value: str | None) -> tuple[str, int]:
    if field not in ALLOWED_INT_FIELDS:
        raise ValueError("Invalid field")

    try:
        parsed_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid integer value") from exc

    return field, parsed_value


def serialize_dev(dev: Dev) -> dict:
    return {
        "id": dev.id,
        "name": dev.name,
        "workload": dev.workload,
        "holidays": dev.holidays,
        "fastlane": dev.fastlane,
        "project_meetings": dev.project_meetings,
        "other_meetings": dev.other_meetings,
    }


def create_dev_for_team(*, team: Team, name: str) -> TeamDev:
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Name required")

    with transaction.atomic():
        dev = Dev.objects.create(name=clean_name)
        order = TeamDev.objects.filter(team=team).count()
        return TeamDev.objects.create(team=team, dev=dev, order=order)


def parse_teamdev_order(raw_ids: list[str]) -> list[int]:
    try:
        return [int(raw_id) for raw_id in raw_ids]
    except ValueError as exc:
        raise ValueError("Invalid ids") from exc


def reorder_teamdevs_for_team(*, team: Team, ids: list[int]) -> list[int]:
    existing = set(TeamDev.objects.filter(team=team, id__in=ids).values_list("id", flat=True))
    if set(ids) != existing:
        raise ValueError("Mismatched teamdev ids")

    with transaction.atomic():
        for index, teamdev_id in enumerate(ids):
            TeamDev.objects.filter(id=teamdev_id, team=team).update(order=index)

    return ids
