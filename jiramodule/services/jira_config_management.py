import logging

from django.db import transaction
from django.shortcuts import get_object_or_404

from jiramodule.exceptions import describe_jira_error
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
    with transaction.atomic():
        config = form.save(commit=False)
        config.user = user
        config.is_active = False
        config.save()
    return config


def test_jira_configuration(config: JiraConfiguration, *, user_pk: int | None = None) -> tuple[bool, str]:
    try:
        jira = JiraManager(config)
        jira.connect(force=True)
        jira.server_info()
        ok = True
        error = ""
    except Exception as exc:  # noqa: BLE001
        ok = False
        # str(JIRAError) contains the request headers (Bearer token): never store, log or return it.
        error = describe_jira_error(exc)
        logger.warning("jira_config_test failed for cfg=%s user=%s: %s", config.pk, user_pk, error)

    config.mark_test_result(ok=ok, error=error)

    return ok, error
