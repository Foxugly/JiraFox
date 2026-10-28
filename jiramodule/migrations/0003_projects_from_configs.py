from django.db import migrations

PROJECT_FIELDS = ("jira_url", "jira_board_id", "jira_project_id", "jira_recurrent_epic_key")


def configs_to_projects(apps, schema_editor):
    """
    Each Jira configuration becomes a membership of its owner in a project carrying the board,
    project and epic. Configurations sharing a team share its project; the current one fills it.
    """
    JiraConfiguration = apps.get_model("jiramodule", "JiraConfiguration")
    Team = apps.get_model("team", "Team")
    User = apps.get_model("customuser", "CustomUser")

    filled = set()
    for config in JiraConfiguration.objects.order_by("-current", "-last_tested_at", "-id"):
        team = config.team
        if team is None:
            label = f"Board {config.jira_board_id}" if config.jira_board_id else "Jira"
            team = Team.objects.create(name=f"{label} ({config.user.username})")
            config.team = team
            config.save(update_fields=["team"])
        if team.pk not in filled:
            for field in PROJECT_FIELDS:
                setattr(team, field, getattr(config, field))
            team.save(update_fields=list(PROJECT_FIELDS))
            filled.add(team.pk)
        team.members.add(config.user_id)
        if config.current:
            User.objects.filter(pk=config.user_id, current_project__isnull=True).update(current_project=team)


class Migration(migrations.Migration):
    dependencies = [
        ("jiramodule", "0002_remove_jira_email"),
        ("team", "0002_projects"),
        ("customuser", "0002_projects"),
    ]

    operations = [migrations.RunPython(configs_to_projects, migrations.RunPython.noop)]
