from django.contrib import admin

from dev.models import Dev


@admin.register(Dev)
class DevAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "workload", "holidays", "fastlane")
    search_fields = ("name",)
