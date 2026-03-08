from django.contrib import admin
from .models import JiraConfiguration

@admin.register(JiraConfiguration)
class JiraConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "jira_url",
        "jira_board_id",
        "jira_project_id",
        "current",
        "last_test_ok",
        "last_tested_at",
    )
    list_filter = ("current", "last_test_ok", "is_active")
    search_fields = ("jira_url", "jira_email", "user__username")