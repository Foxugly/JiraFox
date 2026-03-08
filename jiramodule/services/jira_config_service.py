from jiramodule.selectors import get_current_jira_config_for_user
from .jira_client import JiraManager


def get_connected_jira_for_user(user) -> JiraManager:
    cfg = get_current_jira_config_for_user(user)
    jira = JiraManager(cfg)
    jira.connect()
    return jira