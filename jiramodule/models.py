import urllib.parse
from datetime import datetime, time, timedelta
from typing import Optional, Tuple, List, Any, Dict

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.utils import timezone
from django.utils.formats import date_format
from jira import JIRA


class JiraConfiguration(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jira_configs")

    jira_url = models.URLField(
        "JIRA URL",
        blank=True,
        help_text="https://jira.sm-ms.lan/"
    )

    jira_email = models.EmailField(
        "JIRA API token",
        max_length=255,
        blank=True,
    )

    jira_token = models.CharField(
        "JIRA API token",
        max_length=255,
        blank=True,
    )
    jira_board_id = models.PositiveIntegerField(
        "JIRA board ID",
        null=True,
        blank=True,
    )

    jira_project_id = models.PositiveIntegerField(
        "JIRA project ID",
        null=True,
        blank=True,
    )

    jira_recurrent_epic_key = models.CharField(
        "JIRA recurrent epic key",
        max_length=50,
        blank=True,
    )
    is_active = models.BooleanField(default=False)
    current = models.BooleanField(default=False)
    last_test_ok = models.BooleanField(default=False, db_index=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True)

    def set_as_current(self):
        with transaction.atomic():
            JiraConfiguration.objects.filter(user=self.user, current=True).update(current=False)
            self.current = True
            self.save(update_fields=["current"])

    def __str__(self):
        return f"{self.jira_email} on {self.jira_url}"

    def mark_test_result(self, ok: bool, error: str = "") -> None:
        self.last_test_ok = bool(ok)
        self.last_tested_at = timezone.localtime(timezone.now())
        self.last_test_error = (error or "")[:5000]  # sécurité
        self.save(update_fields=["last_test_ok", "last_tested_at", "last_test_error"])

    def save(self, *args, **kwargs):
        user = kwargs.pop("user", None)

        if self.pk is None:
            # création
            if self.user_id is None:
                if user is None:
                    raise ValueError(
                        "JiraConfiguration.save() requires a user on creation"
                    )
                self.user = user

        super().save(*args, **kwargs)


