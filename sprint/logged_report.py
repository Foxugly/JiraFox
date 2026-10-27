"""
"Logged sheet": one tab per team developer listing, for each sprint ticket the developer worked on
(time logged during the sprint) or is assigned to:

    Ticket | Planned (h) | Status at sprint start | Logged (h) | Status at sprint end

with a final Total row summing the planned and logged hours. A last "Unassigned" tab lists the time
logged during the sprint by people matching no team developer (one row per ticket and author), so
name mismatches between Dev.name and Jira are visible instead of silently dropped.
"""
from collections import defaultdict
import re
from datetime import datetime
from io import BytesIO
from typing import Iterable

from dateutil import parser as date_parser
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Border, Font, PatternFill, Side

from jiramodule.services.jira_client import JiraManager
from team.models import TeamDev

HEADERS = ("Ticket", "Planned (h)", "Status at sprint start", "Logged (h)", "Status at sprint end")
UNASSIGNED_HEADERS = HEADERS + ("Logged by",)
UNASSIGNED_SHEET_TITLE = "Unassigned"
CREATED_DURING_SPRINT = "Created during sprint"
SHEET_TITLE_MAX_LENGTH = 31
INVALID_SHEET_TITLE_CHARS = re.compile(r"[\[\]:*?/\\]")


def parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.isoparse(value)
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _exact_names(person: dict) -> set[str]:
    names = {(person.get("displayName") or "").strip(), (person.get("name") or "").strip()}
    return {name.casefold() for name in names if name}


def _first_name(person: dict) -> str | None:
    display_name = (person.get("displayName") or "").strip()
    return display_name.split(", ", 1)[1].casefold() if ", " in display_name else None


def person_matches_dev(person: dict | None, dev_name: str) -> bool:
    """
    Same rule as the capacity sheet: Jira display names look like "Last, First" and Dev.name holds
    the first name. The full display name and the Jira username are accepted too.
    """
    if not person or not dev_name:
        return False
    target = dev_name.strip().casefold()
    return target in _exact_names(person) or target == _first_name(person)


def resolve_dev(person: dict | None, dev_names: list[str]) -> str | None:
    """
    The one team developer a worklog author is, so that no time is counted twice. An exact match
    (username or full display name) wins over a first-name match; an author whose first name fits
    several developers is ambiguous and left unassigned, where it shows up instead of being doubled.
    """
    if not person:
        return None
    exact = _exact_names(person)
    first_name = _first_name(person)
    exact_matches = [name for name in dev_names if name.strip().casefold() in exact]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if exact_matches:
        return None
    first_name_matches = [name for name in dev_names if first_name and name.strip().casefold() == first_name]
    return first_name_matches[0] if len(first_name_matches) == 1 else None


def status_at(issue: dict, moment: datetime) -> str | None:
    """Status of the issue at `moment`, rebuilt from its changelog. None if it did not exist yet."""
    created = parse_jira_datetime(issue.get("created"))
    if created and created > moment:
        return None

    changes = sorted(
        (
            (when, change.get("from"), change.get("to"))
            for change in issue.get("status_changes") or []
            if (when := parse_jira_datetime(change.get("at")))
        ),
        key=lambda change: change[0],
    )
    passed = [change for change in changes if change[0] <= moment]
    if passed:
        return passed[-1][2]
    if changes:
        return changes[0][1]
    return issue.get("state")


def sprint_window(sprint: dict, now: datetime) -> tuple[datetime | None, datetime]:
    start = parse_jira_datetime(sprint.get("startDate"))
    end = parse_jira_datetime(sprint.get("completeDate")) or parse_jira_datetime(sprint.get("endDate"))
    return start, min(end, now) if end else now


def sprint_worklogs(issue: dict, start: datetime | None, end: datetime) -> list[dict]:
    if start is None:
        # A sprint that never started has no logged time; without a lower bound the whole
        # history of its (carried-over) tickets would be counted.
        return []
    worklogs = []
    for worklog in issue.get("worklogs") or []:
        started = parse_jira_datetime(worklog.get("started"))
        if started is None or started < start or started > end:
            continue
        worklogs.append(worklog)
    return worklogs


def worklog_seconds(worklog: dict) -> float:
    seconds = worklog.get("seconds")
    return float(seconds) if seconds is not None else float(worklog.get("hours") or 0) * 3600


def seconds_to_hours(seconds: float) -> float:
    return round(seconds / 3600, 2)


def logged_hours(
    issue: dict, dev_name: str, start: datetime | None, end: datetime, dev_names: list[str] | None = None
) -> float:
    dev_names = dev_names if dev_names is not None else [dev_name]
    total = sum(
        worklog_seconds(worklog)
        for worklog in sprint_worklogs(issue, start, end)
        if resolve_dev(worklog.get("author"), dev_names) == dev_name
    )
    return seconds_to_hours(total)


def issue_row(issue: dict, hours: float, start: datetime | None, end: datetime) -> dict:
    start_status = status_at(issue, start) if start else issue.get("state")
    return {
        "ticket": f"{issue.get('key', '')} - {issue.get('summary', '')}",
        "url": issue.get("url") or "",
        "planned_hours": float(issue.get("original_estimate_hours") or 0),
        "start_status": start_status or CREATED_DURING_SPRINT,
        "logged_hours": hours,
        "end_status": status_at(issue, end) or "",
    }


def build_dev_rows(
    issues: Iterable[dict], dev_name: str, start: datetime | None, end: datetime, dev_names: list[str] | None = None
) -> list[dict]:
    rows = []
    for issue in issues:
        hours = logged_hours(issue, dev_name, start, end, dev_names)
        if hours <= 0 and not person_matches_dev(issue.get("assignee"), dev_name):
            continue
        rows.append(issue_row(issue, hours, start, end))
    return rows


def author_label(author: dict | None) -> str:
    if not author:
        return "Unknown"
    display_name = author.get("displayName") or ""
    username = author.get("name") or ""
    if display_name and username:
        return f"{display_name} ({username})"
    return display_name or username or "Unknown"


def build_unassigned_rows(
    issues: Iterable[dict], dev_names: list[str], start: datetime | None, end: datetime
) -> list[dict]:
    """Time logged during the sprint by authors matching no team developer, or several of them."""
    rows = []
    for issue in issues:
        seconds_by_author: dict[str, float] = defaultdict(float)
        for worklog in sprint_worklogs(issue, start, end):
            author = worklog.get("author")
            if resolve_dev(author, dev_names):
                continue
            seconds_by_author[author_label(author)] += worklog_seconds(worklog)
        for index, (author, seconds) in enumerate(sorted(seconds_by_author.items())):
            row = {**issue_row(issue, seconds_to_hours(seconds), start, end), "author": author}
            if index:
                # Planned time belongs to the ticket: count it once, not once per author.
                row["planned_hours"] = None
            rows.append(row)
    return rows


def sheet_title(name: str, used: set[str]) -> str:
    base = INVALID_SHEET_TITLE_CHARS.sub("-", name).strip().strip("'") or "Dev"
    base = base[:SHEET_TITLE_MAX_LENGTH]
    title, suffix = base, 2
    while title.casefold() in used:
        tail = f" ({suffix})"
        title = f"{base[:SHEET_TITLE_MAX_LENGTH - len(tail)]}{tail}"
        suffix += 1
    used.add(title.casefold())
    return title


class LoggedWorkbookBuilder:
    header_fill = PatternFill(start_color="ff6565", end_color="ff6565", fill_type="solid")
    total_fill = PatternFill(start_color="ff8f8f", end_color="ff8f8f", fill_type="solid")
    border = Border(*(Side(style="thin"),) * 4)
    column_widths = (70, 13, 26, 13, 26, 34)

    def __init__(self, *, sprint: dict, issues: list[dict], dev_names: list[str], now: datetime | None = None):
        self.sprint = sprint
        self.issues = issues
        self.dev_names = dev_names
        self.start, self.end = sprint_window(sprint, now or timezone.now())
        self.workbook = Workbook()

    def build(self) -> bytes:
        self.workbook.remove(self.workbook.active)
        used_titles: set[str] = set()
        if not self.dev_names:
            worksheet = self.workbook.create_sheet("No developers")
            worksheet["A1"] = "The team has no developer: add them on the capacity sheet page."
        for dev_name in self.dev_names:
            worksheet = self.workbook.create_sheet(sheet_title(dev_name, used_titles))
            self._populate(worksheet, build_dev_rows(self.issues, dev_name, self.start, self.end, self.dev_names))

        worksheet = self.workbook.create_sheet(sheet_title(UNASSIGNED_SHEET_TITLE, used_titles))
        self._populate(
            worksheet,
            build_unassigned_rows(self.issues, self.dev_names, self.start, self.end),
            headers=UNASSIGNED_HEADERS,
        )

        buffer = BytesIO()
        self.workbook.save(buffer)
        return buffer.getvalue()

    def _populate(self, worksheet, rows: list[dict], headers: tuple[str, ...] = HEADERS) -> None:
        for column, header in enumerate(headers, start=1):
            cell = worksheet.cell(row=1, column=column, value=header)
            cell.font = Font(bold=True)
            cell.fill = self.header_fill
            cell.border = self.border
        worksheet.freeze_panes = "A2"

        for row_index, row in enumerate(rows, start=2):
            values = (row["ticket"], row["planned_hours"], row["start_status"], row["logged_hours"], row["end_status"])
            if len(headers) > len(HEADERS):
                values += (row["author"],)
            for column, value in enumerate(values, start=1):
                worksheet.cell(row=row_index, column=column, value=value).border = self.border
            if row["url"]:
                worksheet.cell(row=row_index, column=1).hyperlink = row["url"]

        total_row = len(rows) + 2
        last_data_row = total_row - 1
        totals = {
            1: "Total",
            2: f"=SUM(B2:B{last_data_row})" if rows else 0,
            4: f"=SUM(D2:D{last_data_row})" if rows else 0,
        }
        for column in range(1, len(headers) + 1):
            cell = worksheet.cell(row=total_row, column=column, value=totals.get(column))
            cell.font = Font(bold=True)
            cell.fill = self.total_fill
            cell.border = self.border

        for column, width in enumerate(self.column_widths[: len(headers)], start=1):
            worksheet.column_dimensions[chr(ord("A") + column - 1)].width = width


def create_logged_xlsx_bytes(jira: JiraManager, sprint_id: int) -> bytes:
    dev_names = [
        teamdev.dev.name
        for teamdev in TeamDev.objects.filter(team=jira.cfg.team).select_related("dev").order_by("order", "id")
    ]
    return LoggedWorkbookBuilder(
        sprint=jira.get_sprint(sprint_id),
        issues=jira.get_sprint_issues_with_worklogs(sprint_id),
        dev_names=dev_names,
    ).build()
