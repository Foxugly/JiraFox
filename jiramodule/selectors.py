from dataclasses import dataclass

from django.utils.translation import gettext as _

from team.models import Team

from .exceptions import JiraNotConfigured
from .models import JiraConfiguration


@dataclass(frozen=True)
class JiraContext:
    """What a Jira page reads from: the current project, with the user's token for its Jira."""

    project: Team
    account: JiraConfiguration

    @property
    def team(self) -> Team:
        return self.project

    @property
    def jira_url(self) -> str:
        return self.project.jira_url

    @property
    def jira_token(self) -> str:
        return self.account.jira_token

    @property
    def jira_board_id(self):
        return self.project.jira_board_id

    @property
    def jira_project_id(self):
        return self.project.jira_project_id

    @property
    def jira_recurrent_epic_key(self) -> str:
        return self.project.jira_recurrent_epic_key

    @property
    def pk(self) -> str:
        # Cache keys: the same token on two projects must not share cached sprints.
        return f"{self.account.pk}.{self.project.pk}"

    @property
    def user_id(self) -> int:
        return self.account.user_id


def _same_url(left: str, right: str) -> bool:
    return (left or "").strip().rstrip("/").casefold() == (right or "").strip().rstrip("/").casefold()


def get_current_project_for_user(user) -> Team | None:
    """The user's chosen project if they are still a member, else their first project."""
    projects = Team.objects.filter(members=user)
    if user.current_project_id:
        project = projects.filter(pk=user.current_project_id).first()
        if project:
            return project
    return projects.first()


def get_jira_account_for_user(user, jira_url: str) -> JiraConfiguration | None:
    """The user's connection to that Jira, the last one tested OK first."""
    return next((config for config in user.jira_configs.all() if _same_url(config.jira_url, jira_url)), None)


def get_current_jira_context_for_user(user) -> JiraContext:
    project = get_current_project_for_user(user)
    if project is None:
        raise JiraNotConfigured(
            _("You have no project. Create one, or ask a member to add you to theirs."), missing_fields=["project"]
        )
    if not project.jira_url:
        raise JiraNotConfigured(
            _("The project %(project)s has no Jira URL.") % {"project": project.name}, missing_fields=["jira_url"]
        )
    account = get_jira_account_for_user(user, project.jira_url)
    if account is None or not account.jira_token:
        raise JiraNotConfigured(
            _("You have no Jira token for %(url)s. Add it to your Jira connections.") % {"url": project.jira_url},
            missing_fields=["jira_token"],
        )
    return JiraContext(project=project, account=account)
