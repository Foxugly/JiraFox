from django.contrib import admin

from .models import SprintCapacity, SprintPeriod


@admin.register(SprintCapacity)
class SprintCapacityAdmin(admin.ModelAdmin):
    list_display = (
        "team", "sprint_id", "dev", "workload", "holidays", "fastlane", "project_meetings", "other_meetings"
    )
    list_filter = ("team",)
    search_fields = ("dev__name", "sprint_id")
    autocomplete_fields = ("team", "dev")
    ordering = ("team", "-sprint_id", "dev__name")


@admin.register(SprintPeriod)
class SprintPeriodAdmin(admin.ModelAdmin):
    list_display = ("team", "sprint_id", "start", "end")
    list_filter = ("team",)
    search_fields = ("sprint_id",)
    autocomplete_fields = ("team",)
    ordering = ("team", "-sprint_id")
