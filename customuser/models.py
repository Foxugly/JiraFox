from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from jiramodule.models import JiraConfiguration


class CustomUser(AbstractUser):
    language = models.CharField(
        "language",
        max_length=8,
        choices=settings.LANGUAGES,
        default=getattr(settings, "LANGUAGE_CODE", "en"),
    )
