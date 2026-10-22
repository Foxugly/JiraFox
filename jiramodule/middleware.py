import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from jira.exceptions import JIRAError
from requests import RequestException

from jiramodule.exceptions import (
    JiraConnectionFailed,
    JiraNotConfigured,
    JiraResourceNotFound,
    JiraUnavailable,
    describe_jira_error,
)

logger = logging.getLogger(__name__)


def _as_jira_unavailable(exc: Exception) -> JiraUnavailable | None:
    if isinstance(exc, JiraUnavailable):
        return exc
    if isinstance(exc, JIRAError) and exc.status_code == 404:
        return JiraResourceNotFound(_("This item does not exist in Jira."), detail=describe_jira_error(exc))
    if isinstance(exc, (JIRAError, RequestException)):
        return JiraConnectionFailed(_("Jira returned an error."), detail=describe_jira_error(exc))
    return None


def _wants_json(request) -> bool:
    return (
        "/api/" in request.path
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


class JiraErrorMiddleware:
    """Turns Jira configuration / connection failures into a readable page (or JSON for API calls)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        error = _as_jira_unavailable(exception)
        if error is None:
            return None

        if not isinstance(error, JiraNotConfigured):
            logger.warning("Jira error on %s: %s", request.path, error.detail or error.message)

        if _wants_json(request):
            return JsonResponse(
                {"ok": False, "error": error.message, "detail": error.detail},
                status=error.status_code,
            )
        return render(
            request,
            "jira/jira_unavailable.html",
            {
                "error": error,
                "not_configured": isinstance(error, JiraNotConfigured),
                "not_found": isinstance(error, JiraResourceNotFound),
            },
            status=error.status_code,
        )
