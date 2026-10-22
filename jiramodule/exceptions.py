from django.utils.translation import gettext as _
from jira.exceptions import JIRAError
from requests import RequestException
from requests.exceptions import ConnectionError as RequestsConnectionError, SSLError, Timeout


class JiraUnavailable(Exception):
    """Base class for every "Jira cannot be used right now" situation shown to the user."""

    status_code = 503

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class JiraNotConfigured(JiraUnavailable):
    status_code = 409

    def __init__(self, message: str, *, missing_fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing_fields = missing_fields or []


class JiraConnectionFailed(JiraUnavailable):
    pass


class JiraResourceNotFound(JiraUnavailable):
    status_code = 404


def describe_jira_error(exc: Exception) -> str:
    """
    Human-readable, secret-free summary of a Jira/requests error.

    Never use str(JIRAError): it embeds the request headers, Authorization token included.
    """
    if isinstance(exc, JIRAError):
        status = exc.status_code
        if status == 401:
            return _("Jira rejected the credentials (HTTP 401). Check the API token.")
        if status == 403:
            return _("Jira denied access (HTTP 403). Check the account permissions or a pending CAPTCHA.")
        if status == 404:
            return _("Jira resource not found (HTTP 404).")
        text = (exc.text or "").strip()[:500]
        if status:
            return _("Jira returned HTTP %(status)s: %(text)s") % {"status": status, "text": text or "-"}
        return text or _("Unknown Jira error.")
    if isinstance(exc, SSLError):
        return _("TLS/SSL error while connecting to Jira (certificate not trusted?).")
    if isinstance(exc, Timeout):
        return _("Jira did not answer in time (timeout).")
    if isinstance(exc, RequestsConnectionError):
        return _("Unable to reach the Jira server (network error or wrong URL).")
    if isinstance(exc, RequestException):
        return _("HTTP error while talking to Jira.")
    if isinstance(exc, JiraUnavailable):
        return exc.message
    return str(exc)[:500] or exc.__class__.__name__


def is_transient_jira_error(exc: Exception) -> bool:
    """Only network hiccups, throttling and 5xx are worth retrying; 4xx never are."""
    if isinstance(exc, JIRAError):
        return exc.status_code is not None and (exc.status_code == 429 or exc.status_code >= 500)
    return isinstance(exc, (RequestsConnectionError, Timeout)) and not isinstance(exc, SSLError)
