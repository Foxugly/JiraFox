from jiramodule.selectors import get_current_project_for_user


def projects(request):
    """The user's projects and the current one, for the project switch in the header."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    user_projects = list(user.projects.all())
    current = get_current_project_for_user(user) if user_projects else None
    return {"user_projects": user_projects, "current_project": current}
