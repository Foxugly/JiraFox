from team.models import Team, TeamDev

from .models import CAPACITY_FIELDS, SprintCapacity


def sprint_capacities(team: Team, sprint_id: int) -> list[SprintCapacity]:
    """
    The team's developers' hours for this sprint, in column order. A developer without hours for the sprint yet
    (first look at it, or added to the team since) gets a copy of their template.
    """
    teamdevs = list(TeamDev.objects.filter(team=team).select_related("dev").order_by("order", "id"))
    existing = {
        capacity.dev_id: capacity
        for capacity in SprintCapacity.objects.filter(team=team, sprint_id=sprint_id)
    }
    missing = [
        SprintCapacity(
            team=team,
            sprint_id=sprint_id,
            dev=teamdev.dev,
            **{field: getattr(teamdev.dev, field) for field in CAPACITY_FIELDS},
        )
        for teamdev in teamdevs
        if teamdev.dev_id not in existing
    ]
    if missing:
        # Two tabs opening the sprint at once must not copy the template twice.
        SprintCapacity.objects.bulk_create(missing, ignore_conflicts=True)
        existing = {
            capacity.dev_id: capacity
            for capacity in SprintCapacity.objects.filter(team=team, sprint_id=sprint_id)
        }

    capacities = []
    for teamdev in teamdevs:
        capacity = existing[teamdev.dev_id]
        capacity.dev = teamdev.dev
        capacities.append(capacity)
    return capacities


def reset_sprint_capacities(team: Team, sprint_id: int) -> None:
    """Drops the sprint's hours: the next look at the sprint copies the templates again."""
    SprintCapacity.objects.filter(team=team, sprint_id=sprint_id).delete()
