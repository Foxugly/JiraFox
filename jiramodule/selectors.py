from .models import JiraConfiguration


def get_current_jira_config_for_user(user) -> JiraConfiguration:
    return JiraConfiguration.objects.get(
        user=user,
        current=True,
    )
