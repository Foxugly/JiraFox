from django.db import models

from dev.models import Dev


class Team(models.Model):
    name = models.CharField("Team Name", max_length=100)
    devs = models.ManyToManyField(Dev, through="TeamDev", related_name="teams")

    def __str__(self):
        return self.name


class TeamDev(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="teamdevs")
    dev = models.ForeignKey(Dev, on_delete=models.CASCADE, related_name="teamdevs")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order",)
        unique_together = ("team", "dev")

    def __str__(self):
        return f"{self.team.name} - {self.dev.name} (order {self.order})"
