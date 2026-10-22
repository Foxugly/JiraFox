from django.utils.translation import gettext as _

from .exceptions import JiraNotConfigured
from .models import JiraConfiguration


def get_current_jira_config_for_user(user) -> JiraConfiguration:
    configs = list(JiraConfiguration.objects.filter(user=user, current=True).select_related("team")[:2])
    if not configs:
        raise JiraNotConfigured(_("No current Jira configuration. Create one and mark it as current."))
    if len(configs) > 1:
        raise JiraNotConfigured(_("Several Jira configurations are marked as current. Keep only one."))
    return configs[0]
