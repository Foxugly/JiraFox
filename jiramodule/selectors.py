from django.shortcuts import get_object_or_404
from .models import JiraConfiguration


def get_current_jira_config_for_user(user) -> JiraConfiguration:
    return get_object_or_404(
        JiraConfiguration,
        user=user,
        current=True,
    )