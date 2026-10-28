from django.contrib import admin

from .models import Team, TeamDev


class TeamDevInline(admin.TabularInline):
    model = TeamDev
    extra = 0
    autocomplete_fields = ("dev",)
    ordering = ("order",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "jira_url", "jira_board_id", "jira_project_id")
    search_fields = ("name",)
    filter_horizontal = ("members",)
    inlines = [TeamDevInline]


@admin.register(TeamDev)
class TeamDevAdmin(admin.ModelAdmin):
    list_display = ("team", "dev", "order")
    list_filter = ("team",)
    autocomplete_fields = ("team", "dev")
    ordering = ("team", "order")
