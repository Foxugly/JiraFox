from django.contrib import admin

from .models import Team, TeamDev


class TeamDevInline(admin.TabularInline):
    model = TeamDev
    extra = 0
    autocomplete_fields = ("dev",)
    ordering = ("order",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('id', 'name',)
    search_fields = ('name',)
    inlines = [TeamDevInline]


@admin.register(TeamDev)
class TeamDevAdmin(admin.ModelAdmin):
    list_display = ("team", "dev", "order")
    list_filter = ("team",)
    autocomplete_fields = ("team", "dev")
    ordering = ("team", "order")
