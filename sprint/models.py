from django.db import models

from team.models import Team


class SprintPeriod(models.Model):
    """
    Bounds of a sprint for the logged sheet, set by hand when Jira's start/end do not match when the
    team actually worked on it. Sprint data itself is always fetched live from Jira.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="sprint_periods")
    sprint_id = models.PositiveIntegerField()
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("team", "sprint_id"), name="one_period_per_team_sprint")]

    def __str__(self):
        return f"{self.team} - sprint {self.sprint_id}: {self.start:%Y-%m-%d %H:%M} -> {self.end:%Y-%m-%d %H:%M}"


# A developer's hours for a sprint; the Dev holds the same fields as the template each sprint starts from.
CAPACITY_FIELDS = ("workload", "holidays", "fastlane", "project_meetings", "other_meetings")


class SprintCapacity(models.Model):
    """
    A developer's hours for one sprint of the capacity sheet. Copied from the developer's template (the
    Dev's own values) the first time the sprint needs them, then edited for this sprint only.
    """

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="sprint_capacities")
    sprint_id = models.PositiveIntegerField()
    dev = models.ForeignKey("dev.Dev", on_delete=models.CASCADE, related_name="sprint_capacities")
    workload = models.DecimalField(max_digits=6, decimal_places=2)
    holidays = models.DecimalField(max_digits=6, decimal_places=2)
    fastlane = models.DecimalField(max_digits=6, decimal_places=2)
    project_meetings = models.DecimalField(max_digits=6, decimal_places=2)
    other_meetings = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("team", "sprint_id", "dev"), name="one_capacity_per_team_sprint_dev")
        ]

    def __str__(self):
        return f"{self.team} - sprint {self.sprint_id}: {self.dev}"
