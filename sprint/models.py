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
