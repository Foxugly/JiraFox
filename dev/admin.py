from django.contrib import admin

from dev.models import Dev


# Register your models here.
@admin.register(Dev)
class DevAdmin(admin.ModelAdmin):
    list_display = ('id', 'name',)
    search_fields = ('name',)
