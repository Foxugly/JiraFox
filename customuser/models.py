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

    def __str__(self) -> str:
        return self.get_username()
