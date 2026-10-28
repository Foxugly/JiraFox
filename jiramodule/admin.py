from django.contrib import admin
from .models import JiraConfiguration

@admin.register(JiraConfiguration)
class JiraConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "jira_url",
        "last_test_ok",
        "last_tested_at",
    )
    list_filter = ("last_test_ok", "is_active")
    search_fields = ("jira_url", "user__username")
