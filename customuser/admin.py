from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from customuser.models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Preferences", {"fields": ("language",)}),)
    list_display = ("username", "email", "is_staff", "is_superuser", "language")
