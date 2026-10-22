import logging

from django.utils.translation import gettext as _

from jiramodule.exceptions import JiraConnectionFailed, JiraNotConfigured, describe_jira_error
from jiramodule.models import JiraConfiguration
from jiramodule.selectors import get_current_jira_config_for_user
from .jira_client import JiraManager

logger = logging.getLogger(__name__)

# Needed by every Jira page; jira_project_id and team are only required by some reports.
REQUIRED_FIELDS = ("jira_url", "jira_token", "jira_board_id")


def require_config_fields(cfg: JiraConfiguration, field_names) -> None:
    missing = [name for name in field_names if not getattr(cfg, name)]
    if missing:
        labels = [str(JiraConfiguration._meta.get_field(name).verbose_name) for name in missing]
        raise JiraNotConfigured(
            _("The current Jira configuration is incomplete: %(fields)s.") % {"fields": ", ".join(labels)},
            missing_fields=missing,
        )


def get_connected_jira_for_user(user) -> JiraManager:
    cfg = get_current_jira_config_for_user(user)
    require_config_fields(cfg, REQUIRED_FIELDS)
    jira = JiraManager(cfg)
    try:
        jira.connect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Jira connection failed for cfg=%s user=%s: %s", cfg.pk, user.pk, describe_jira_error(exc))
        raise JiraConnectionFailed(
            _("Unable to connect to Jira at %(url)s.") % {"url": cfg.jira_url},
            detail=describe_jira_error(exc),
        ) from exc
    return jira
