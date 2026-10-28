from django.conf import settings
from django.db import models
from django.utils import timezone


class JiraConfiguration(models.Model):
    """A user's Jira connection: the URL and their personal token. Boards and projects live in projects."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jira_configs")
    jira_url = models.URLField("JIRA URL", blank=True, help_text="https://jira.sm-ms.lan/")
    jira_token = models.CharField("JIRA API token", max_length=255, blank=True)
    is_active = models.BooleanField(default=False)
    last_test_ok = models.BooleanField(default=False, db_index=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-last_test_ok", "-last_tested_at", "-id")

    def __str__(self):
        return f"{self.user} - {self.jira_url or 'No URL'}"

    def mark_test_result(self, ok: bool, error: str = "") -> None:
        self.last_test_ok = bool(ok)
        self.last_tested_at = timezone.localtime(timezone.now())
        self.last_test_error = (error or "")[:5000]
        self.save(update_fields=["last_test_ok", "last_tested_at", "last_test_error"])
