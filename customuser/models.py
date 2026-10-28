from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    language = models.CharField(
        "language",
        max_length=8,
        choices=settings.LANGUAGES,
        default=getattr(settings, "LANGUAGE_CODE", "en"),
    )
    # The project the pages read from Jira; chosen in the header among the user's projects.
    current_project = models.ForeignKey(
        "team.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    def __str__(self) -> str:
        return self.get_username()
