from django.conf import settings
from django.db import models

from dev.models import Dev


class Team(models.Model):
    """
    A project: one Jira board and project, shared by its members (each with their own Jira token),
    with its developers.
    """

    name = models.CharField("Project name", max_length=100)
    jira_url = models.URLField("JIRA URL", blank=True, help_text="https://jira.sm-ms.lan/")
    jira_board_id = models.PositiveIntegerField("JIRA board ID", null=True, blank=True)
    jira_project_id = models.PositiveIntegerField("JIRA project ID", null=True, blank=True)
    jira_recurrent_epic_key = models.CharField("JIRA recurrent epic key", max_length=50, blank=True)
    started_statuses = models.CharField(
        "Statuses counted as started",
        max_length=500,
        blank=True,
        help_text="Comma-separated Jira statuses counted as started even if Jira puts them in To Do.",
    )
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="projects", blank=True)
    devs = models.ManyToManyField(Dev, through="TeamDev", related_name="teams")

    class Meta:
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        ordering = ("name", "id")

    def __str__(self):
        return self.name

    def started_status_list(self) -> list[str]:
        return [name.strip() for name in self.started_statuses.split(",") if name.strip()]


class TeamDev(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="teamdevs")
    dev = models.ForeignKey(Dev, on_delete=models.CASCADE, related_name="teamdevs")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order",)
        unique_together = ("team", "dev")

    def __str__(self):
        return f"{self.team.name} - {self.dev.name} (order {self.order})"
