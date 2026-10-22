from datetime import datetime, timezone as dt_timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from dev.models import Dev
from sprint.logged_report import (
    HEADERS,
    UNASSIGNED_HEADERS,
    LoggedWorkbookBuilder,
    build_unassigned_rows,
    logged_hours,
    person_matches_dev,
    sheet_title,
    sprint_window,
    status_at,
)
from team.models import Team, TeamDev

from JiraFox.services.dashboard import (
    build_dashboard_bottlenecks,
    build_dashboard_items,
    build_dashboard_kpis,
)


class DashboardServicesTests(SimpleTestCase):
    def test_build_dashboard_kpis_handles_empty_and_populated_metrics(self):
        issues = [
            {
                "state": "In Progress",
                "kanban_metrics": {
                    "current_wip_age_days": 12,
                    "cycle_time_days": 6,
                    "lead_time_days": 8,
                },
            },
            {
                "state": "Done",
                "kanban_metrics": {
                    "current_wip_age_days": 2,
                    "cycle_time_days": 4,
                    "lead_time_days": 10,
                },
            },
        ]

        result = build_dashboard_kpis(issues, throughput_7d=5)

        self.assertEqual(result["wip"], 1)
        self.assertEqual(result["aging_wip"], 1)
        self.assertEqual(result["throughput_7d"], 5)
        self.assertEqual(result["cycle_p50_days"], 5.0)
        self.assertEqual(result["lead_p50_days"], 9.0)
        self.assertEqual(result["flow_eff_pct"], 55.6)
        self.assertEqual(result["aging_wip_pct"], 100.0)

    def test_build_dashboard_bottlenecks_sorts_by_average_age(self):
        issues = [
            {"state": "Review", "kanban_metrics": {"current_wip_age_days": 3}},
            {"state": "Review", "kanban_metrics": {"current_wip_age_days": 9}},
            {"state": "Testing", "kanban_metrics": {"current_wip_age_days": 4}},
        ]

        result = build_dashboard_bottlenecks(issues)

        self.assertEqual(result[0]["status"], "Review")
        self.assertEqual(result[0]["avgAge"], 6.0)
        self.assertEqual(result[0]["maxAge"], 9)

    def test_build_dashboard_items_maps_expected_fields(self):
        issues = [
            {
                "key": "ABC-1",
                "summary": "Titre",
                "state": "In Progress",
                "assignee": {"displayName": "Jane Doe"},
                "url": "https://jira.local/browse/ABC-1",
                "kanban_metrics": {"current_wip_age_days": 7},
            }
        ]

        result = build_dashboard_items(issues)

        self.assertEqual(result[0]["key"], "ABC-1")
        self.assertEqual(result[0]["title"], "Titre")
        self.assertEqual(result[0]["status"], "In Progress")
        self.assertEqual(result[0]["ageDays"], 7)
        self.assertEqual(result[0]["assignee"], "Jane Doe")
        self.assertEqual(result[0]["detail_url"], "/issue/ABC-1/")


class SprintPagesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secret")
        self.client.force_login(self.user)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_sprint_list_page_renders(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            cfg=SimpleNamespace(jira_board_id=42),
            list_sprints_for_current_board=lambda states: [
                {
                    "id": 12,
                    "name": "Sprint 12",
                    "state": "active",
                    "startDate": "2026-03-01T08:00:00.000+0000",
                    "endDate": "2026-03-14T18:00:00.000+0000",
                    "url": "https://jira.example/sprint/12",
                }
            ],
        )

        response = self.client.get(reverse("sprint-list"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sprint/sprint_list.html")
        self.assertContains(response, "Sprint 12")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_sprint_issues_api_returns_mapped_items(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_cached_sprint_issues=lambda sprint_id, jql_extra=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "issueType": "Bug",
                    "state": "In Progress",
                    "assignee": {"displayName": "Jane Doe"},
                    "url": "https://jira.example/browse/ABC-1",
                }
            ],
        )

        response = self.client.get(reverse("sprint-api-issues", args=[12]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["items"][0]["key"], "ABC-1")
        self.assertEqual(payload["items"][0]["issueType"], "Bug")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_sprint_wia_api_returns_statuses_and_items(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_cached_sprint_issues=lambda sprint_id, jql_extra=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "state": "In Progress",
                    "url": "https://jira.example/browse/ABC-1",
                    "kanban_metrics": {
                        "current_wip_age_hours": 16,
                        "cycle_time_hours": 8,
                        "first_in_progress": "2026-03-01T08:00:00.000+0000",
                        "first_in_progress_display": "01/03/2026",
                    },
                }
            ],
        )

        response = self.client.get(reverse("sprint-api-wia", args=[12]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["statuses"], ["In Progress"])
        self.assertEqual(payload["items"][0]["key"], "ABC-1")


SPRINT = {
    "id": 2446,
    "startDate": "2026-03-02T08:00:00.000+01:00",
    "endDate": "2026-03-13T18:00:00.000+01:00",
    "completeDate": None,
}
ALICE = {"displayName": "Martin, Alice", "name": "AMARTIN"}
BOB = {"displayName": "Dupont, Bob", "name": "BDUPONT"}
CARL = {"displayName": "Extern, Carl", "name": "CEXTERN"}
ISSUES = [
    {
        "key": "ABC-1",
        "summary": "Login page",
        "state": "Done",
        "created": "2026-02-20T10:00:00.000+0100",
        "assignee": ALICE,
        "original_estimate_hours": 8.0,
        "status_changes": [
            {"at": "2026-02-25T10:00:00.000+0100", "from": "Open", "to": "Ready"},
            {"at": "2026-03-03T09:00:00.000+0100", "from": "Ready", "to": "In Progress"},
            {"at": "2026-03-10T09:00:00.000+0100", "from": "In Progress", "to": "Done"},
        ],
        "worklogs": [
            {"author": ALICE, "started": "2026-03-03T09:00:00.000+0100", "hours": 3.0},
            {"author": ALICE, "started": "2026-03-04T09:00:00.000+0100", "hours": 2.5},
            {"author": ALICE, "started": "2026-02-27T09:00:00.000+0100", "hours": 4.0},  # before the sprint
            {"author": BOB, "started": "2026-03-05T09:00:00.000+0100", "hours": 1.0},
            {"author": CARL, "started": "2026-03-06T09:00:00.000+0100", "hours": 1.5},
            {"author": CARL, "started": "2026-03-09T09:00:00.000+0100", "hours": 0.5},
            {"author": CARL, "started": "2026-03-16T09:00:00.000+0100", "hours": 9.0},  # after the sprint
        ],
        "url": "https://jira.example/browse/ABC-1",
    },
    {
        "key": "ABC-2",
        "summary": "Hotfix",
        "state": "In Progress",
        "created": "2026-03-05T10:00:00.000+0100",
        "assignee": BOB,
        "original_estimate_hours": None,
        "status_changes": [],
        "worklogs": [],
        "url": "https://jira.example/browse/ABC-2",
    },
]
NOW = datetime(2026, 3, 20, 12, 0, tzinfo=dt_timezone.utc)


class LoggedReportTests(SimpleTestCase):
    def test_status_at_rebuilds_status_from_changelog(self):
        start, end = sprint_window(SPRINT, NOW)

        self.assertEqual(status_at(ISSUES[0], start), "Ready")
        self.assertEqual(status_at(ISSUES[0], end), "Done")
        self.assertIsNone(status_at(ISSUES[1], start))  # created during the sprint
        self.assertEqual(status_at(ISSUES[1], end), "In Progress")

    def test_logged_hours_only_counts_the_developer_within_the_sprint(self):
        start, end = sprint_window(SPRINT, NOW)

        self.assertEqual(logged_hours(ISSUES[0], "Alice", start, end), 5.5)
        self.assertEqual(logged_hours(ISSUES[0], "Bob", start, end), 1.0)

    def test_person_matching_accepts_first_name_display_name_and_username(self):
        self.assertTrue(person_matches_dev(ALICE, "alice"))
        self.assertTrue(person_matches_dev(ALICE, "Martin, Alice"))
        self.assertTrue(person_matches_dev(ALICE, "AMARTIN"))
        self.assertFalse(person_matches_dev(ALICE, "Bob"))

    def test_workbook_has_one_tab_per_dev_with_totals(self):
        content = LoggedWorkbookBuilder(sprint=SPRINT, issues=ISSUES, dev_names=["Alice", "Bob"], now=NOW).build()
        workbook = load_workbook(BytesIO(content))

        self.assertEqual(workbook.sheetnames, ["Alice", "Bob", "Unassigned"])
        alice = [[cell.value for cell in row] for row in workbook["Alice"].iter_rows()]
        self.assertEqual(alice[0], list(HEADERS))
        self.assertEqual(alice[1], ["ABC-1 - Login page", 8.0, "Ready", 5.5, "Done"])
        self.assertEqual(alice[2], ["Total", "=SUM(B2:B2)", None, "=SUM(D2:D2)", None])

        bob = [[cell.value for cell in row] for row in workbook["Bob"].iter_rows()]
        # ABC-1 because Bob logged on it, ABC-2 because it is assigned to him.
        self.assertEqual(bob[1], ["ABC-1 - Login page", 8.0, "Ready", 1.0, "Done"])
        self.assertEqual(bob[2], ["ABC-2 - Hotfix", 0.0, "Created during sprint", 0.0, "In Progress"])
        self.assertEqual(bob[3][:4], ["Total", "=SUM(B2:B3)", None, "=SUM(D2:D3)"])

        unassigned = [[cell.value for cell in row] for row in workbook["Unassigned"].iter_rows()]
        self.assertEqual(unassigned[0], list(UNASSIGNED_HEADERS))
        self.assertEqual(
            unassigned[1], ["ABC-1 - Login page", 8.0, "Ready", 2.0, "Done", "Extern, Carl (CEXTERN)"]
        )
        self.assertEqual(unassigned[2][:4], ["Total", "=SUM(B2:B2)", None, "=SUM(D2:D2)"])

    def test_unassigned_rows_group_by_ticket_and_author(self):
        start, end = sprint_window(SPRINT, NOW)

        rows = build_unassigned_rows(ISSUES, ["Alice"], start, end)

        self.assertEqual(
            [(row["ticket"], row["author"], row["logged_hours"]) for row in rows],
            [
                ("ABC-1 - Login page", "Dupont, Bob (BDUPONT)", 1.0),
                ("ABC-1 - Login page", "Extern, Carl (CEXTERN)", 2.0),
            ],
        )

    def test_sheet_titles_are_valid_and_unique(self):
        used: set[str] = set()

        self.assertEqual(sheet_title("A/B", used), "A-B")
        self.assertEqual(sheet_title("a/b", used), "a-b (2)")
        self.assertLessEqual(len(sheet_title("x" * 40, used)), 31)


class LoggedSheetViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secret")
        self.client.force_login(self.user)
        self.team = Team.objects.create(name="Core")
        TeamDev.objects.create(team=self.team, dev=Dev.objects.create(name="Alice"), order=0)

    def _fake_jira(self, team):
        return SimpleNamespace(
            cfg=SimpleNamespace(team=team, jira_project_id=7),
            get_sprint=lambda sprint_id: SPRINT,
            get_sprint_issues_with_worklogs=lambda sprint_id: ISSUES,
        )

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_is_downloaded(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="sprint_2446_logged.xlsx"', response["Content-Disposition"])
        self.assertEqual(load_workbook(BytesIO(response.content)).sheetnames, ["Alice", "Unassigned"])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_without_team_explains_what_is_missing(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(None)

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "jira/jira_unavailable.html")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_capacity_page_shows_logged_sheet_button(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)

        response = self.client.get(reverse("sprint_xls_report", args=[2446]))

        self.assertContains(response, reverse("sprint-logged-xls-export", args=[2446]))
        self.assertContains(response, "Generate logged sheet")
