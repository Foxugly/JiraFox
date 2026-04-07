import logging

from django.db import transaction
from django.shortcuts import get_object_or_404

from jiramodule.forms import JiraConfigurationForm
from jiramodule.models import JiraConfiguration
from jiramodule.services.jira_client import JiraManager

logger = logging.getLogger(__name__)


def user_owns_jira_config(user, config: JiraConfiguration) -> bool:
    return user.jira_configs.filter(pk=config.pk).exists()


def get_user_jira_config_or_404(user, pk: int) -> JiraConfiguration:
    return get_object_or_404(JiraConfiguration, pk=pk, user=user)


def build_jira_config_form(*, data=None, instance=None) -> JiraConfigurationForm:
    return JiraConfigurationForm(data=data, instance=instance)


def save_jira_config_form(*, form: JiraConfigurationForm, user) -> JiraConfiguration:
    config = form.save(commit=False)
    config.user = user
    config.is_active = False
    config.save()
    return config

def test_jira_configuration(config: JiraConfiguration, *, user_pk: int | None = None) -> tuple[bool, str]:
    try:
        jira = JiraManager(config)
        jira.connect()
        jira.server_info()
        ok = True
        error = ""
    except Exception as exc:
        ok = False
        error = str(exc)
        logger.exception("jira_config_test failed for cfg=%s user=%s", config.pk, user_pk)

    config.mark_test_result(ok=ok, error=error)

    return ok, error


def set_current_config(*, config: JiraConfiguration, want_current: bool) -> None:
    with transaction.atomic():
        if want_current:
            config.set_as_current()
        else:
            config.current = False
            config.save(update_fields=["current"])
