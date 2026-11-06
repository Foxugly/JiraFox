import base64
from datetime import date, datetime, timezone as dt_timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from pptx.enum.shapes import MSO_SHAPE_TYPE
from jira.exceptions import JIRAError
from requests.exceptions import Timeout

from dev.models import Dev
from sprint.analysis import (
    PROVIDERS, Analysis, AnalysisParseError, Risk, build_dossier, risky_keys,
)
from sprint.report_data import (
    build_report_data,
    sprint_warning_lines,
    sprints_in_range,
    trend_for,
)
from sprint.scope_flow import START_NO_STATUS, build_scope_flow, consistency_errors
from sprint.report_metrics import (
    age_in_status_days,
    cycle_time_days,
    kanban_snapshot,
    percentile,
    sprint_metrics,
    went_back,
)
from sprint.logged_report import (
    HEADERS,
    UNASSIGNED_HEADERS,
    DevIdentity,
    LoggedWorkbookBuilder,
    build_dev_rows,
    build_unassigned_rows,
    closed_ratio,
    planned_assignee_at,
    planned_hours_at,
    logged_hours,
    person_matches_dev,
    resolve_dev,
    sheet_title,
    sprint_window,
    status_at,
)
from sprint.capacity import reset_sprint_capacities, sprint_capacities
from sprint.models import SprintCapacity, SprintPeriod
from sprint.report import SprintWorkbookBuilder
from team.models import Team, TeamDev

from JiraFox.services.dashboard import (
    build_dashboard_bottlenecks,
    build_dashboard_items,
    build_dashboard_kpis,
)
from sprint.views import build_weighted_throughput_distribution


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
    def test_sprint_kanban_api_gives_assignee_names_per_status(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_cached_sprint_issues=lambda sprint_id, jql_extra=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "state": "In Progress",
                    "assignee": {"displayName": "Jane Doe", "accountId": "42"},
                    "url": "https://jira.example/browse/ABC-1",
                },
                {"key": "ABC-2", "summary": "Unassigned", "state": "In Progress", "assignee": None},
            ],
        )

        response = self.client.get(reverse("sprint-api-kanban", args=[12]))

        self.assertEqual(response.status_code, 200)
        [column] = response.json()
        self.assertEqual(column["name"], "In Progress")
        self.assertEqual([i["assignee"] for i in column["issues"]], ["Jane Doe", ""])
        self.assertEqual(column["issues"][0]["url"], "https://jira.example/browse/ABC-1")

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
        "id": "101",
        "key": "ABC-1",
        "summary": "Login page",
        "state": "Done",
        "created": "2026-02-20T10:00:00.000+0100",
        "assignee": ALICE,
        "original_estimate_hours": 8.0,
        # 8h estimated, 4h logged before the sprint: 4h left to plan at the sprint start.
        "remaining_estimate_seconds": 0,
        "estimate_changes": [
            {"at": "2026-02-27T09:00:00.000+0100", "from": "28800", "to": "14400"},
            {"at": "2026-03-04T09:00:00.000+0100", "from": "14400", "to": "0"},
        ],
        "assignee_changes": [],
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
        "id": "102",
        "key": "ABC-2",
        "summary": "Hotfix",
        "state": "In Progress",
        "created": "2026-03-05T10:00:00.000+0100",
        "assignee": BOB,
        "original_estimate_hours": None,
        "remaining_estimate_seconds": 7200,
        "estimate_changes": [],
        "assignee_changes": [],
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
        content = LoggedWorkbookBuilder(sprint=SPRINT, issues=ISSUES, devs=["Alice", "Bob"], now=NOW).build()
        workbook = load_workbook(BytesIO(content))

        self.assertEqual(workbook.sheetnames, ["Alice", "Bob", "Unassigned", "Period"])
        alice = [[cell.value for cell in row] for row in workbook["Alice"].iter_rows()]
        self.assertEqual(alice[0], list(HEADERS))
        self.assertEqual(alice[1], ["ABC-1 - Login page", 4.0, "Ready", 5.5, "=B2-D2", "Done", "Yes"])
        self.assertEqual(alice[2], ["Total", "=SUM(B2:B2)", None, "=SUM(D2:D2)", "=SUM(E2:E2)", None, None])
        self.assertEqual(alice[3], ["Outside sprint", None, None, '=SUMIF(G2:G2,"No",D2:D2)', None, None, None])

        bob = [[cell.value for cell in row] for row in workbook["Bob"].iter_rows()]
        # ABC-1 because Bob logged on it (planned for Alice), ABC-2 because it is assigned to him.
        self.assertEqual(bob[1], ["ABC-1 - Login page", None, "Ready", 1.0, "=B2-D2", "Done", "Yes"])
        self.assertEqual(bob[2], ["ABC-2 - Hotfix", 2.0, "Created during sprint", 0.0, "=B3-D3", "In Progress", "Yes"])
        self.assertEqual(bob[3][:4], ["Total", "=SUM(B2:B3)", None, "=SUM(D2:D3)"])

        unassigned = [[cell.value for cell in row] for row in workbook["Unassigned"].iter_rows()]
        self.assertEqual(unassigned[0], list(UNASSIGNED_HEADERS))
        self.assertEqual(
            unassigned[1], ["ABC-1 - Login page", None, "Ready", 2.0, "Done", "Extern, Carl (CEXTERN)"]
        )
        self.assertEqual(unassigned[2][:4], ["Total", "=SUM(B2:B2)", None, "=SUM(D2:D2)"])

    def test_unassigned_rows_group_by_ticket_and_author(self):
        start, end = sprint_window(SPRINT, NOW)

        rows = build_unassigned_rows(ISSUES, ["Alice"], start, end)

        self.assertEqual(
            [(row["ticket"], row["author"], row["planned_hours"], row["logged_hours"]) for row in rows],
            [
                # Planned for Alice: her planned time is on her tab, not here.
                ("ABC-1 - Login page", "Dupont, Bob (BDUPONT)", None, 1.0),
                ("ABC-1 - Login page", "Extern, Carl (CEXTERN)", None, 2.0),
            ],
        )
        # Planned for someone outside the team: shown here, on the ticket's first row only.
        rows = build_unassigned_rows([{**ISSUES[0], "assignee": CARL}], ["Alice"], start, end)
        self.assertEqual([row["planned_hours"] for row in rows], [4.0, None])

    def test_closed_tickets_share_counts_the_dev_tickets_at_the_sprint_end(self):
        start, end = sprint_window(SPRINT, NOW)
        categories = {"Done": "done", "In Progress": "indeterminate", "To Do": "new"}
        devs = [DevIdentity("Alice"), DevIdentity("Bob")]
        issues = [
            ISSUES[0],  # Alice, Done
            {**ISSUES[1], "assignee": ALICE},  # Alice, In Progress
            {**ISSUES[1], "assignee": ALICE, "state": "To Do"},  # not started: left out
            {**ISSUES[0], "subtask": True},  # sub-task: left out
            # Handed over to Bob before the end: his.
            {**ISSUES[0], "assignee_changes": [{"at": "2026-03-11T09:00:00.000+0100", "from": ALICE, "to": BOB}]},
        ]

        self.assertEqual(closed_ratio(issues, devs[0], devs, end, categories), (1, 2))
        self.assertEqual(closed_ratio(issues, devs[1], devs, end, categories), (1, 1))

    def test_planned_time_is_the_remaining_estimate_at_sprint_start_for_the_dev_assigned_then(self):
        start, end = sprint_window(SPRINT, NOW)
        # Planned for Bob, handed over to Alice during the sprint.
        issue = {
            **ISSUES[0],
            "assignee_changes": [{"at": "2026-03-05T09:00:00.000+0100", "from": BOB, "to": ALICE}],
            "worklogs": [],
        }

        self.assertEqual(planned_hours_at(issue, start), 4.0)
        self.assertEqual(planned_assignee_at(issue, start), BOB)
        bob = build_dev_rows([issue], "Bob", start, end, ["Alice", "Bob"])
        alice = build_dev_rows([issue], "Alice", start, end, ["Alice", "Bob"])
        self.assertEqual([(row["planned_hours"], row["logged_hours"]) for row in bob], [(4.0, 0)])
        self.assertEqual([(row["planned_hours"], row["logged_hours"]) for row in alice], [(None, 0)])

        # Unassigned and not estimated at the start: the first assignee and estimate given count.
        issue = {
            **ISSUES[0],
            "assignee_changes": [{"at": "2026-03-05T09:00:00.000+0100", "from": None, "to": ALICE}],
            "estimate_changes": [{"at": "2026-03-05T09:00:00.000+0100", "from": None, "to": "10800"}],
        }
        self.assertEqual(planned_hours_at(issue, start), 3.0)
        self.assertEqual(planned_assignee_at(issue, start), ALICE)

    def test_sheet_titles_are_valid_and_unique(self):
        used: set[str] = set()

        self.assertEqual(sheet_title("A/B", used), "A-B")
        self.assertEqual(sheet_title("a/b", used), "a-b (2)")
        self.assertLessEqual(len(sheet_title("x" * 40, used)), 31)

    def test_logged_hours_are_summed_before_rounding(self):
        start, end = sprint_window(SPRINT, NOW)
        issue = {
            **ISSUES[1],
            "worklogs": [
                {"author": ALICE, "started": f"2026-03-0{day}T09:00:00.000+0100", "hours": 0.33, "seconds": 1200}
                for day in (3, 4, 5)
            ],
        }

        self.assertEqual(logged_hours(issue, "Alice", start, end), 1.0)

    def test_sprint_not_started_has_no_logged_time(self):
        sprint = {**SPRINT, "startDate": None}
        start, end = sprint_window(sprint, NOW)

        self.assertEqual(logged_hours(ISSUES[0], "Alice", start, end), 0)
        self.assertEqual(build_unassigned_rows(ISSUES, ["Alice"], start, end), [])

    def test_each_worklog_is_attributed_to_one_developer_only(self):
        alice_bis = {"displayName": "Durand, Alice", "name": "ADURAND"}
        self.assertEqual(resolve_dev(ALICE, ["Alice", "AMARTIN"]), DevIdentity("AMARTIN"))  # exact match wins
        self.assertIsNone(resolve_dev(alice_bis, ["Alice", "alice"]))  # ambiguous first name
        self.assertEqual(resolve_dev(alice_bis, ["Alice", "Bob"]), DevIdentity("Alice"))
        self.assertIsNone(resolve_dev(CARL, ["Alice", "Bob"]))

        start, end = sprint_window(SPRINT, NOW)
        dev_names = ["Alice", "AMARTIN"]
        self.assertEqual(logged_hours(ISSUES[0], "AMARTIN", start, end, dev_names), 5.5)
        self.assertEqual(logged_hours(ISSUES[0], "Alice", start, end, dev_names), 0)

    def test_jira_username_matches_exactly_and_beats_names(self):
        alice = DevIdentity("Alice", jira_username="amartin")
        alice_bis = {"displayName": "Durand, Alice", "name": "ADURAND"}

        self.assertTrue(person_matches_dev(ALICE, alice))
        # With a Jira ID, a namesake is no longer taken for the developer...
        self.assertFalse(person_matches_dev(alice_bis, alice))
        self.assertIsNone(resolve_dev(alice_bis, [alice, "Bob"]))
        # ...and the ID wins over another developer matching by name.
        self.assertEqual(resolve_dev(ALICE, [alice, "Martin, Alice"]), alice)

    def test_time_logged_outside_the_sprint_is_listed_without_planned_time(self):
        start, end = sprint_window(SPRINT, NOW)
        support = {
            **ISSUES[1],
            "key": "SUP-9",
            "summary": "Support",
            "original_estimate_hours": 5.0,
            "assignee": BOB,
            "worklogs": [
                {"author": ALICE, "started": "2026-03-04T14:00:00.000+0100", "seconds": 5400},
                {"author": ALICE, "started": "2026-03-20T10:00:00.000+0100", "seconds": 3600},  # after the sprint
            ],
        }

        rows = build_dev_rows(ISSUES, "Alice", start, end, ["Alice", "Bob"], [support])
        self.assertEqual(
            [(row["ticket"], row["planned_hours"], row["logged_hours"], row["in_sprint"]) for row in rows],
            [("ABC-1 - Login page", 4.0, 5.5, True), ("SUP-9 - Support", None, 1.5, False)],
        )
        # Bob is the assignee but logged nothing on it: an outside ticket only shows up with logged time.
        self.assertEqual(len(build_dev_rows(ISSUES, "Bob", start, end, ["Alice", "Bob"], [support])), 2)

    def test_period_set_by_hand_replaces_the_jira_dates(self):
        period = SprintPeriod(
            start=datetime(2026, 3, 2, 0, 0, tzinfo=dt_timezone.utc), end=datetime(2026, 3, 4, 0, 0, tzinfo=dt_timezone.utc)
        )
        start, end = sprint_window(SPRINT, NOW, period)

        self.assertEqual((start, end), (period.start, period.end))
        self.assertEqual(logged_hours(ISSUES[0], "Alice", start, end), 3.0)

        content = LoggedWorkbookBuilder(sprint=SPRINT, issues=ISSUES, devs=["Alice"], period=period, now=NOW).build()
        rows = [[cell.value for cell in row] for row in load_workbook(BytesIO(content))["Period"].iter_rows()]
        self.assertEqual(rows[3], ["Bounds", "Set by hand"])

    def test_dev_tabs_and_unassigned_add_up_to_all_time_logged_in_the_sprint(self):
        start, end = sprint_window(SPRINT, NOW)
        dev_names = ["Alice", "Bob"]

        per_dev = sum(row["logged_hours"] for name in dev_names for row in build_dev_rows(ISSUES, name, start, end, dev_names))
        unassigned = sum(row["logged_hours"] for row in build_unassigned_rows(ISSUES, dev_names, start, end))

        self.assertEqual(per_dev + unassigned, 3.0 + 2.5 + 1.0 + 1.5 + 0.5)


class LoggedSheetViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secret")
        self.client.force_login(self.user)
        self.team = Team.objects.create(name="Core")
        TeamDev.objects.create(team=self.team, dev=Dev.objects.create(name="Alice"), order=0)

    def _fake_jira(
        self, team, worklogs=(), outside_issues=(), worklog_error=None, scope_report=None, removed_issues=(),
        report_error=None,
    ):
        self.worklog_windows, self.issue_id_queries, self.key_queries = [], [], []

        def get_sprint_scope_report(sprint_id):
            if report_error:
                raise report_error
            return scope_report or {"completedIssues": [{"key": "ABC-1"}]}

        def get_issues_with_worklogs_by_keys(keys):
            self.key_queries.append(list(keys))
            return [dict(issue) for issue in removed_issues if issue["key"] in keys]

        def get_worklogs_between(start, end):
            self.worklog_windows.append((start, end))
            if worklog_error:
                raise worklog_error
            return list(worklogs)

        def get_issues_by_ids(issue_ids):
            self.issue_id_queries.append(sorted(issue_ids))
            return [dict(issue) for issue in outside_issues if issue["id"] in issue_ids]

        return SimpleNamespace(
            cfg=SimpleNamespace(team=team, jira_project_id=7),
            get_sprint=lambda sprint_id: {**SPRINT, "url": "https://jira.example/issues/?jql=sprint%3D2446"},
            get_sprint_issues_with_worklogs=lambda sprint_id: ISSUES,
            get_worklogs_between=get_worklogs_between,
            get_issues_by_ids=get_issues_by_ids,
            get_sprint_scope_report=get_sprint_scope_report,
            get_issues_with_worklogs_by_keys=get_issues_with_worklogs_by_keys,
            status_category_keys=lambda: {"Done": "done", "In Progress": "indeterminate", "To Do": "new"},
        )

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_is_downloaded(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="sprint_2446_logged.xlsx"', response["Content-Disposition"])
        self.assertEqual(load_workbook(BytesIO(response.content)).sheetnames, ["Alice", "Unassigned", "Period"])
        # The period's worklogs are read once, between the sprint bounds.
        self.assertEqual(
            [(start.isoformat(), end.isoformat()) for start, end in self.worklog_windows],
            [("2026-03-02T08:00:00+01:00", "2026-03-13T18:00:00+01:00")],
        )
        self.assertEqual(self.issue_id_queries, [])
        self.assertEqual(self.key_queries, [])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_includes_tickets_moved_out_before_the_sprint_closed(self, mock_get_jira):
        # Moved to the next sprint before this one closed: no longer in "sprint = 2446", still in Jira's report.
        moved = {**ISSUES[0], "id": "107", "key": "ABC-7", "summary": "Moved on", "state": "In Progress",
                 "status_changes": [], "worklogs": []}
        report = {"completedIssues": [{"key": "ABC-1"}], "puntedIssues": [{"key": "ABC-7"}]}
        mock_get_jira.return_value = self._fake_jira(self.team, scope_report=report, removed_issues=[moved])

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(self.key_queries, [["ABC-7"]])
        alice = [[cell.value for cell in row] for row in load_workbook(BytesIO(response.content))["Alice"].iter_rows()]
        self.assertEqual(alice[2], ["ABC-7 - Moved on", 4.0, "In Progress", 0, "=B3-D3", "In Progress", "Yes"])
        # ABC-1 closed, ABC-7 started but not closed.
        self.assertEqual(alice[-1][:3], ["Closed tickets", 0.5, "1 closed / 2 started"])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_survives_a_sprint_report_failure(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team, report_error=Timeout("read timed out"))

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        period = [[cell.value for cell in row] for row in load_workbook(BytesIO(response.content))["Period"].iter_rows()]
        self.assertEqual(period[-1][0], "Not fetched")
        self.assertIn("moved out of the sprint", period[-1][1])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_adds_time_logged_outside_the_sprint(self, mock_get_jira):
        # Alice has no Jira ID: her worklogs are matched by name, like the sprint's.
        worklogs = [
            {"issue_id": "900", "author": ALICE, "started": "2026-03-04T14:00:00.000+0100", "seconds": 7200},
            # On a sprint ticket: already counted with the sprint, not fetched again.
            {"issue_id": "101", "author": ALICE, "started": "2026-03-04T15:00:00.000+0100", "seconds": 3600},
            # Someone outside the team: ignored.
            {"issue_id": "901", "author": CARL, "started": "2026-03-04T15:00:00.000+0100", "seconds": 3600},
        ]
        support = {**ISSUES[1], "id": "900", "key": "SUP-9", "summary": "Support", "worklogs": []}
        mock_get_jira.return_value = self._fake_jira(self.team, worklogs=worklogs, outside_issues=[support])

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(self.issue_id_queries, [["900"]])
        alice = [[cell.value for cell in row] for row in load_workbook(BytesIO(response.content))["Alice"].iter_rows()]
        self.assertEqual([row[0] for row in alice[1:3]], ["ABC-1 - Login page", "SUP-9 - Support"])
        self.assertEqual(alice[2][1:], [None, "Created during sprint", 2.0, "=B3-D3", "In Progress", "No"])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_survives_a_worklog_timeout(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team, worklog_error=Timeout("read timed out"))

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        period = [[cell.value for cell in row] for row in workbook["Period"].iter_rows()]
        self.assertEqual(period[-1][0], "Not fetched")
        self.assertIn("timeout", period[-1][1])

    @patch("sprint.views.get_connected_jira_for_user")
    def test_sprint_bounds_are_saved_and_reset_per_sprint(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)
        url = reverse("sprint-period", args=[2446])

        response = self.client.post(url, {"start": "2026-03-02T00:00", "end": "2026-03-13T23:59"})

        self.assertRedirects(response, reverse("sprint_xls_report", args=[2446]), fetch_redirect_response=False)
        period = SprintPeriod.objects.get(team=self.team, sprint_id=2446)
        self.assertEqual(timezone.localtime(period.start).strftime("%Y-%m-%d %H:%M"), "2026-03-02 00:00")
        self.assertFalse(SprintPeriod.objects.filter(sprint_id=2447).exists())

        page = self.client.get(reverse("sprint_xls_report", args=[2446]))
        self.assertContains(page, 'value="2026-03-02T00:00"')

        self.client.post(url, {"start": "2026-03-13T00:00", "end": "2026-03-02T00:00"})  # end before start
        self.assertEqual(SprintPeriod.objects.get(sprint_id=2446).end, period.end)

        self.client.post(url, {"action": "reset"})
        self.assertFalse(SprintPeriod.objects.filter(sprint_id=2446).exists())

    @patch("sprint.views.get_connected_jira_for_user")
    def test_logged_sheet_without_team_explains_what_is_missing(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(None)

        response = self.client.get(reverse("sprint-logged-xls-export", args=[2446]))

        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "jira/jira_unavailable.html")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_capacity_page_shows_logged_sheet_button(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)
        self.user.language = "en"
        self.user.save(update_fields=["language"])

        response = self.client.get(reverse("sprint_xls_report", args=[2446]))

        self.assertContains(response, reverse("sprint-logged-xls-export", args=[2446]))
        self.assertContains(response, "Generate logged sheet")
        self.assertContains(response, reverse("sprint-capacity-update", args=[SprintCapacity.objects.get().pk]))
        self.assertNotContains(response, 'data-field="jira_username"')
        self.assertContains(response, reverse("sprint-period", args=[2446]))

    @patch("sprint.views.get_connected_jira_for_user")
    def test_capacity_page_is_translated(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)
        expected = {
            "fr": ("Générer le rapport des temps prestés", "Enregistrer les bornes", "Repartir du template"),
            "nl": ("Urenrapport genereren", "Grenzen opslaan", "Opnieuw vanaf het sjabloon"),
        }
        for language, texts in expected.items():
            with self.subTest(language=language):
                self.user.language = language
                self.user.save(update_fields=["language"])

                response = self.client.get(reverse("sprint_xls_report", args=[2446]))

                for text in texts:
                    self.assertContains(response, text)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_open_in_jira_links_to_the_sprint(self, mock_get_jira):
        mock_get_jira.return_value = self._fake_jira(self.team)

        response = self.client.get(reverse("sprint_xls_report", args=[2446]))

        self.assertContains(response, 'href="https://jira.example/issues/?jql=sprint%3D2446"')

    def test_weighted_throughput_distribution_groups_done_by_story_points(self):
        jira = SimpleNamespace(
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_cached_sprint_issues=lambda sprint_id, jql_extra=None, full=None: [
                {"state": "Done", "status_category": "done", "estimation": {"story_points": 3}},
                {"state": "Done", "status_category": "done", "estimation": {"story_points": 3}},
                {"state": "Done", "status_category": "done", "estimation": {"story_points": 5}},
                {"state": "Done", "status_category": "done", "estimation": {"story_points": None}},
                {"state": "Closed", "status_category": "done", "estimation": {"story_points": 8}},
                {"state": "In Progress", "status_category": "indeterminate", "estimation": {"story_points": 8}},
            ],
        )

        result = build_weighted_throughput_distribution(jira, 12)

        self.assertEqual(result["distribution"], {"labels": ["0", "3", "5", "8"], "counts": [1, 2, 1, 1]})
        self.assertEqual(result["done_count"], 5)
        self.assertEqual(result["estimated_done_count"], 5)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_weighted_throughput_distribution_page_renders(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_sprint=lambda sprint_id: {
                "id": sprint_id,
                "name": "Sprint 12",
                "state": "active",
                "startDate": "2026-03-01T08:00:00.000+0000",
                "endDate": "2026-03-14T18:00:00.000+0000",
            },
            get_url_jql=lambda jql: f"https://jira.example/issues/?jql={jql}",
            get_cached_sprint_issues=lambda sprint_id, jql_extra=None, full=None: [
                {"state": "Done", "estimation": {"story_points": 3}},
            ],
        )

        response = self.client.get(reverse("sprint-weighted-throughput-distribution", args=[12]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "sprint/sprint_weighted_throughput_distribution.html")
        self.assertContains(response, "Distribution du débit par pondération")


SCOPE_REPORT = {
    "completedIssues": [
        {"key": "BKL1-1", "summary": "Planned and done", "statusId": "10002", "assigneeName": "Martin, Alice"},
        {"key": "BKL1-2", "summary": "Added and done", "statusId": "10002"},
        {"key": "BKL1-3", "summary": "Planned and canceled", "statusId": "10009"},
    ],
    "issuesNotCompletedInCurrentSprint": [],
    "puntedIssues": [
        {"key": "BKL1-4", "summary": "Planned, carried over", "statusId": "3"},
        {"key": "BKL1-5", "summary": "Added, carried over", "statusId": "3"},
    ],
    "issuesCompletedInAnotherSprint": [{"key": "BKL1-6", "summary": "Done elsewhere", "statusId": "10002"}],
    "issueKeysAddedDuringSprint": {"BKL1-2": True, "BKL1-5": True},
    "entityData": {
        "statuses": {
            "10002": {"statusName": "Done", "status": {"statusCategory": {"key": "done"}}},
            "10009": {"statusName": "Canceled", "status": {"statusCategory": {"key": "done"}}},
            "3": {"statusName": "In Progress", "status": {"statusCategory": {"key": "indeterminate"}}},
        }
    },
}


class CapacitySheetTests(TestCase):
    def test_capacity_sheet_can_be_filtered_by_developer(self):
        team = Team.objects.create(name="Core")
        TeamDev.objects.create(team=team, dev=Dev.objects.create(name="Alice"), order=0)
        TeamDev.objects.create(team=team, dev=Dev.objects.create(name="Bob"), order=1)
        jira = SimpleNamespace(
            cfg=SimpleNamespace(team=team, jira_board_id=1, jira_project_id=7, jira_recurrent_epic_key="ABC-0"),
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_next_sprint=lambda board_id: {"id": 2447},
            run_jql=lambda jql, history=False: [ISSUES[0]] if "Epic Link" in jql else [],
        )

        workbook = load_workbook(BytesIO(SprintWorkbookBuilder(jira, 2446).build()))

        sheet = workbook["Capacity sheet"]
        self.assertEqual(sheet.auto_filter.ref, f"A1:F{sheet.max_row}")
        self.assertEqual(workbook["Estimation VS Reality"].auto_filter.ref[:4], "A1:E")
        ticket = next(row for row in sheet.iter_rows(values_only=True) if row[1] and row[1].startswith("ABC-1"))
        self.assertEqual(ticket[0], "RECURRENTS")

    def test_capacity_sheet_has_a_decision_list_on_each_ticket(self):
        team = Team.objects.create(name="Core")
        TeamDev.objects.create(team=team, dev=Dev.objects.create(name="Alice"), order=0)
        TeamDev.objects.create(team=team, dev=Dev.objects.create(name="Bob"), order=1)
        jira = SimpleNamespace(
            cfg=SimpleNamespace(team=team, jira_board_id=1, jira_project_id=7, jira_recurrent_epic_key="ABC-0"),
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_next_sprint=lambda board_id: {"id": 2447},
            run_jql=lambda jql, history=False: [ISSUES[0]] if "Epic Link" in jql or "ORDER BY" in jql else [],
        )

        workbook = load_workbook(BytesIO(SprintWorkbookBuilder(jira, 2446).build()))

        sheet = workbook["Capacity sheet"]
        self.assertEqual(sheet["F1"].value, "Decision")
        ticket_rows = [
            cell.row for cell in sheet["B"] if isinstance(cell.value, str) and cell.value.startswith("ABC-1")
        ]
        self.assertEqual(len(ticket_rows), 3)  # RECURRENTS, BKL and NEW BKL
        (validation,) = sheet.data_validations.dataValidation
        self.assertEqual(validation.type, "list")
        self.assertEqual(validation.formula1, '"Incoming,Next,Backlog,Cancelled"')
        self.assertEqual(sorted(str(validation.sqref).split()), sorted(f"F{row}" for row in ticket_rows))
        self.assertEqual(workbook["Estimation VS Reality"].data_validations.dataValidation, [])
        self.assertIsNone(workbook["Estimation VS Reality"]["F1"].value)


class SprintCapacityTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Core")
        self.alice = Dev.objects.create(name="Alice", workload=36, holidays=0, fastlane=7)
        self.bob = Dev.objects.create(name="Bob", workload=18)
        TeamDev.objects.create(team=self.team, dev=self.bob, order=1)
        TeamDev.objects.create(team=self.team, dev=self.alice, order=0)

    def test_a_sprint_starts_from_a_copy_of_the_templates_in_column_order(self):
        capacities = sprint_capacities(self.team, 41)

        self.assertEqual([capacity.dev for capacity in capacities], [self.alice, self.bob])
        self.assertEqual([capacity.workload for capacity in capacities], [36, 18])
        self.assertEqual(SprintCapacity.objects.count(), 2)
        sprint_capacities(self.team, 41)
        self.assertEqual(SprintCapacity.objects.count(), 2)

    def test_each_sprint_keeps_its_own_hours_and_the_template_is_untouched(self):
        sprint_41 = sprint_capacities(self.team, 41)[0]
        sprint_41.holidays = 24
        sprint_41.save()

        self.assertEqual(sprint_capacities(self.team, 42)[0].holidays, 0)
        self.assertEqual(sprint_capacities(self.team, 41)[0].holidays, 24)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.holidays, 0)

    def test_a_template_change_reaches_new_sprints_and_reset_sprints_only(self):
        sprint_capacities(self.team, 41)
        self.alice.fastlane = 3
        self.alice.save()

        self.assertEqual(sprint_capacities(self.team, 41)[0].fastlane, 7)
        self.assertEqual(sprint_capacities(self.team, 42)[0].fastlane, 3)
        reset_sprint_capacities(self.team, 41)
        self.assertEqual(sprint_capacities(self.team, 41)[0].fastlane, 3)

    def test_a_developer_added_later_gets_the_template_and_a_removed_one_leaves_the_sheet(self):
        sprint_capacities(self.team, 41)
        carl = Dev.objects.create(name="Carl", holidays=8)
        TeamDev.objects.create(team=self.team, dev=carl, order=2)
        TeamDev.objects.filter(dev=self.bob).delete()

        capacities = sprint_capacities(self.team, 41)

        self.assertEqual([capacity.dev for capacity in capacities], [self.alice, carl])
        self.assertEqual(capacities[1].holidays, 8)

    def test_capacity_sheet_writes_the_sprint_hours(self):
        capacity = sprint_capacities(self.team, 2446)[0]
        capacity.holidays = 16
        capacity.save()
        jira = SimpleNamespace(
            cfg=SimpleNamespace(team=self.team, jira_board_id=1, jira_project_id=7, jira_recurrent_epic_key="ABC-0"),
            JIRA_ONLY_STANDARD_TYPES="issuetype in standardIssueTypes()",
            get_next_sprint=lambda board_id: {"id": 2447},
            run_jql=lambda jql, history=False: [],
        )

        sheet = load_workbook(BytesIO(SprintWorkbookBuilder(jira, 2446).build()))["Capacity sheet"]

        rows = {row[1]: row[2:4] for row in sheet.iter_rows(values_only=True) if row[1]}
        self.assertEqual(rows["Holidays"], (16, 0))
        self.assertEqual(rows["Capacity"], (36, 18))

    def test_only_a_project_member_edits_a_sprint_hour(self):
        member = get_user_model().objects.create_user(username="member", password="secret")
        intruder = get_user_model().objects.create_user(username="intruder", password="secret")
        self.team.members.add(member)
        capacity = sprint_capacities(self.team, 41)[0]
        url = reverse("sprint-capacity-update", args=[capacity.pk])

        self.client.force_login(intruder)
        self.assertEqual(self.client.post(url, {"field": "holidays", "value": "8"}).status_code, 404)
        self.client.force_login(member)
        self.assertEqual(self.client.post(url, {"field": "name", "value": "Mallory"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"field": "holidays", "value": "8"}).status_code, 200)

        capacity.refresh_from_db()
        self.assertEqual(capacity.holidays, 8)
        self.alice.refresh_from_db()
        self.assertEqual((self.alice.holidays, self.alice.name), (0, "Alice"))

    def test_sprint_hours_are_listed_in_the_admin(self):
        capacity = sprint_capacities(self.team, 41)[0]
        self.client.force_login(get_user_model().objects.create_superuser(username="admin", password="secret"))

        listing = self.client.get(reverse("admin:sprint_sprintcapacity_changelist"))
        change = self.client.get(reverse("admin:sprint_sprintcapacity_change", args=[capacity.pk]))

        self.assertContains(listing, "Alice")
        self.assertEqual(change.status_code, 200)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_reset_button_copies_the_templates_again(self, mock_get_jira):
        user = get_user_model().objects.create_user(username="member", password="secret")
        self.client.force_login(user)
        mock_get_jira.return_value = SimpleNamespace(cfg=SimpleNamespace(team=self.team))
        capacity = sprint_capacities(self.team, 41)[0]
        capacity.holidays = 24
        capacity.save()

        response = self.client.post(reverse("sprint-capacity-reset", args=[41]))

        self.assertRedirects(response, reverse("sprint_xls_report", args=[41]), fetch_redirect_response=False)
        self.assertEqual(sprint_capacities(self.team, 41)[0].holidays, 0)


class SprintScopeFlowTests(TestCase):
    def test_flows_split_planned_and_added_issues(self):
        scope = build_scope_flow(SCOPE_REPORT, lambda key: f"https://jira.example/browse/{key}")

        flows = {(flow["from"], flow["to"]): flow["flow"] for flow in scope["flows"]}
        self.assertEqual(
            flows,
            {
                ("origin:planned", "outcome:done"): 2,
                ("origin:added", "outcome:done"): 1,
                ("origin:planned", "outcome:removed"): 1,
                ("origin:added", "outcome:removed"): 1,
                ("outcome:done", "end:Done"): 2,
                ("outcome:done", "end:Canceled"): 1,
                ("outcome:removed", "end:In Progress"): 2,
            },
        )
        # BKL1-6 was done in another sprint: left out of every figure, only listed.
        self.assertEqual(scope["totals"], {"planned": 3, "added": 2})
        self.assertNotIn("BKL1-6", [issue["key"] for issue in scope["issues"]])
        self.assertEqual(
            scope["done_elsewhere"],
            [{"key": "BKL1-6", "summary": "Done elsewhere", "url": "https://jira.example/browse/BKL1-6"}],
        )
        self.assertEqual(scope["status_categories"]["In Progress"], "indeterminate")
        self.assertFalse(scope["has_history"])
        first = scope["issues"][0]
        self.assertEqual((first["key"], first["origin"], first["assignee"]), ("BKL1-1", "planned", "Martin, Alice"))

    def test_consistency_errors_find_nothing_in_figures_that_add_up(self):
        start = datetime(2026, 3, 2, tzinfo=dt_timezone.utc)
        end = datetime(2026, 3, 13, tzinfo=dt_timezone.utc)
        history = {"state": "In Progress", "created": "2026-02-01T10:00:00.000+0000", "status_changes": []}
        for histories in (None, {"BKL1-1": history, "BKL1-4": history}):
            with self.subTest(histories=bool(histories)):
                scope = build_scope_flow(
                    SCOPE_REPORT, lambda key: key, histories=histories, start=start, end=end, sprint_id=2446
                )
                self.assertEqual(consistency_errors(scope), [])

    def test_consistency_errors_flag_figures_that_do_not_add_up(self):
        scope = build_scope_flow(SCOPE_REPORT, lambda key: key)
        scope["completion"]["planned"]["total"] -= 1
        scope["flows"][0]["flow"] += 1

        errors = consistency_errors(scope)

        self.assertEqual(len(errors), 2)
        self.assertIn("planned", errors[0])
        self.assertEqual(errors[1], "Le diagramme de flux ne tombe pas juste.")

    def test_completion_rates_count_closed_out_of_started_issues(self):
        scope = build_scope_flow(SCOPE_REPORT, lambda key: key)

        # Removed but in progress at the end: started, not closed. Done in another sprint: left out.
        self.assertEqual(
            scope["completion"],
            {
                "planned": {"done": 2, "started": 3, "percent": 67, "total": 3, "percent_total": 67, "not_started": []},
                "added": {"done": 1, "started": 2, "percent": 50, "total": 2, "percent_total": 50, "not_started": []},
                "all": {"done": 3, "started": 5, "percent": 60, "total": 5, "percent_total": 60, "not_started": []},
            },
        )

        # Still To Do at the end: not started, so left out of the started rate but kept in the total.
        histories = {"BKL1-5": {"state": "To Do", "created": "2026-03-05T10:00:00.000+0000", "status_changes": []}}
        scope = build_scope_flow(
            SCOPE_REPORT, lambda key: key, histories=histories, start=datetime(2026, 3, 2, tzinfo=dt_timezone.utc),
            end=datetime(2026, 3, 13, tzinfo=dt_timezone.utc), status_categories={"To Do": "new"},
        )
        self.assertEqual(
            scope["completion"]["added"],
            {"done": 1, "started": 1, "percent": 100, "total": 2, "percent_total": 50, "not_started": ["BKL1-5"]},
        )

        # A To Do-like status listed as started: BKL1-5 then counts as started.
        histories = {"BKL1-5": {"state": "Pending", "created": "2026-03-05T10:00:00.000+0000", "status_changes": []}}
        scope = build_scope_flow(
            SCOPE_REPORT, lambda key: key, histories=histories, start=datetime(2026, 3, 2, tzinfo=dt_timezone.utc),
            end=datetime(2026, 3, 13, tzinfo=dt_timezone.utc), status_categories={"Pending": "new"},
            started_statuses=["pending"],
        )
        self.assertEqual(scope["status_categories"]["Pending"], "indeterminate")
        self.assertEqual(scope["completion"]["added"]["started"], 2)
        self.assertEqual(scope["completion"]["added"]["not_started"], [])
        self.assertEqual(consistency_errors(scope), [])

    def test_planned_issue_created_after_the_start_enters_with_its_status_at_creation(self):
        # Jira counts BKL1-1 as planned, but it was created on 4 March, after the 2 March start date.
        histories = {"BKL1-1": {"state": "Done", "created": "2026-03-04T10:00:00.000+0000", "status_changes": [
            {"at": "2026-03-06T10:00:00.000+0000", "from": "Open", "to": "Done"},
        ]}}
        scope = build_scope_flow(
            SCOPE_REPORT, lambda key: key, histories=histories, start=datetime(2026, 3, 2, tzinfo=dt_timezone.utc),
            end=datetime(2026, 3, 13, tzinfo=dt_timezone.utc), sprint_id=2446,
        )

        issue = next(issue for issue in scope["issues"] if issue["key"] == "BKL1-1")
        self.assertTrue(issue["created_after_start"])
        self.assertIsNone(issue["status_start"])
        self.assertEqual((issue["status_entry"], issue["moved"]), ("Open", True))
        self.assertFalse(next(issue for issue in scope["issues"] if issue["key"] == "BKL1-3")["created_after_start"])
        self.assertEqual(consistency_errors(scope), [])

    def test_statuses_at_sprint_start_and_end_come_from_the_changelog(self):
        start = datetime(2026, 3, 2, 8, tzinfo=dt_timezone.utc)
        end = datetime(2026, 3, 13, 18, tzinfo=dt_timezone.utc)
        histories = {
            # Planned, To Do at the start, done during the sprint.
            "BKL1-1": {"state": "Done", "created": "2026-02-01T10:00:00.000+0000", "status_changes": [
                {"at": "2026-03-05T10:00:00.000+0000", "from": "To Do", "to": "Done"},
            ]},
            # Planned, already In Progress at the start; reopened after the sprint end.
            "BKL1-4": {"state": "In Progress", "created": "2026-02-01T10:00:00.000+0000", "status_changes": [
                {"at": "2026-02-20T10:00:00.000+0000", "from": "To Do", "to": "In Progress"},
                {"at": "2026-03-10T10:00:00.000+0000", "from": "In Progress", "to": "In Test"},
                {"at": "2026-03-20T10:00:00.000+0000", "from": "In Test", "to": "In Progress"},
            ]},
            # Added during the sprint: no start status.
            "BKL1-5": {"state": "In Progress", "created": "2026-03-04T10:00:00.000+0000", "status_changes": []},
        }

        scope = build_scope_flow(
            SCOPE_REPORT, lambda key: key, histories=histories, start=start, end=end,
            status_categories={"To Do": "new", "In Test": "indeterminate"},
        )

        issues = {issue["key"]: issue for issue in scope["issues"]}
        self.assertEqual((issues["BKL1-1"]["status_start"], issues["BKL1-1"]["status_end"]), ("To Do", "Done"))
        self.assertEqual(
            (issues["BKL1-4"]["status_start"], issues["BKL1-4"]["status_end"], issues["BKL1-4"]["status_current"]),
            ("In Progress", "In Test", "In Progress"),
        )
        self.assertIsNone(issues["BKL1-5"]["status_start"])
        # No history for BKL1-3: its current status stands in for the end status.
        self.assertEqual(issues["BKL1-3"]["status_end"], "Canceled")
        flows = {(flow["from"], flow["to"]): flow["flow"] for flow in scope["flows"]}
        self.assertEqual(flows[("start:To Do", "origin:planned")], 1)
        self.assertEqual(flows[("start:In Progress", "origin:planned")], 1)
        # Planned without a known start status (no history for BKL1-3): "No status".
        self.assertEqual(flows[(START_NO_STATUS, "origin:planned")], 1)
        self.assertEqual(scope["columns"][START_NO_STATUS], 0)
        self.assertNotIn((START_NO_STATUS, "origin:added"), flows)
        self.assertEqual(flows[("outcome:removed", "end:In Test")], 1)
        self.assertEqual(scope["columns"]["start:To Do"], 0)
        self.assertEqual(scope["columns"]["end:In Test"], 3)
        self.assertTrue(scope["has_history"])

    def test_stability_compares_the_status_on_entering_the_sprint_with_the_end(self):
        start = datetime(2026, 3, 2, 8, tzinfo=dt_timezone.utc)
        end = datetime(2026, 3, 13, 18, tzinfo=dt_timezone.utc)
        histories = {
            # Planned: To Do at the start, Done at the end.
            "BKL1-1": {"state": "Done", "created": "2026-02-01T10:00:00.000+0000", "status_changes": [
                {"at": "2026-03-05T10:00:00.000+0000", "from": "To Do", "to": "Done"},
            ]},
            # Added without a Sprint change in its changelog: entered when it was created.
            "BKL1-2": {"state": "Done", "created": "2026-03-03T10:00:00.000+0000", "status_changes": [
                {"at": "2026-03-09T10:00:00.000+0000", "from": "To Do", "to": "Done"},
            ]},
            # Planned: In Progress at both ends, but it went to In Test and back meanwhile.
            "BKL1-4": {"state": "In Progress", "created": "2026-02-01T10:00:00.000+0000", "status_changes": [
                {"at": "2026-02-20T10:00:00.000+0000", "from": "To Do", "to": "In Progress"},
                {"at": "2026-03-10T10:00:00.000+0000", "from": "In Progress", "to": "In Test"},
                {"at": "2026-03-11T10:00:00.000+0000", "from": "In Test", "to": "In Progress"},
            ]},
            # Added on 6 March, already In Progress, never moved after.
            "BKL1-5": {
                "state": "In Progress",
                "created": "2026-03-01T10:00:00.000+0000",
                "status_changes": [{"at": "2026-03-04T10:00:00.000+0000", "from": "To Do", "to": "In Progress"}],
                "sprint_changes": [
                    {"at": "2026-03-01T10:00:00.000+0000", "from": None, "to": "2440"},
                    {"at": "2026-03-06T10:00:00.000+0000", "from": "2440", "to": "2440, 2446"},
                ],
            },
            # Done before the sprint: same status, but not a stuck issue.
            "BKL1-6": {"state": "Done", "created": "2026-02-01T10:00:00.000+0000", "status_changes": [
                {"at": "2026-02-15T10:00:00.000+0000", "from": "To Do", "to": "Done"},
            ]},
        }

        scope = build_scope_flow(
            SCOPE_REPORT, lambda key: key, histories=histories, start=start, end=end,
            status_categories={"To Do": "new", "In Test": "indeterminate"}, sprint_id=2446,
        )

        issues = {issue["key"]: issue for issue in scope["issues"]}
        entries = {key: (issue["status_entry"], issue["status_end"], issue["moved"]) for key, issue in issues.items()}
        self.assertEqual(
            entries,
            {
                "BKL1-1": ("To Do", "Done", True),
                "BKL1-2": ("To Do", "Done", True),
                "BKL1-3": (None, "Canceled", None),
                "BKL1-4": ("In Progress", "In Progress", True),
                "BKL1-5": ("In Progress", "In Progress", False),
            },
        )
        # An unknown entry status (BKL1-3, planned) counts as changed.
        self.assertEqual(
            scope["stability"],
            {
                "planned": {"same": 1, "still": 0, "back": 1, "compared": 3, "percent": 33},
                "added": {"same": 1, "still": 1, "back": 0, "compared": 2, "percent": 50},
                "all": {"same": 2, "still": 1, "back": 1, "compared": 5, "percent": 40},
            },
        )
        self.assertEqual(scope["status_order"], ["To Do", "In Progress", "Done", "Canceled"])

    def _login_and_fake_jira(self, mock_get_jira, **overrides):
        user = get_user_model().objects.create_user(username="alice", password="secret")
        self.client.force_login(user)
        fake = dict(
            cfg=SimpleNamespace(jira_board_id=638),
            get_sprint=lambda sprint_id: {**SPRINT, "name": "Sprint 12", "url": "https://jira.example/"},
            get_sprint_scope_report=lambda sprint_id: SCOPE_REPORT,
            get_issue_url=lambda key: f"https://jira.example/browse/{key}",
            get_status_histories=lambda keys: {},
            status_category_keys=lambda: {},
            get_sprint_issue_keys=lambda sprint_id: {"BKL1-1", "BKL1-2", "BKL1-4", "BKL1-5", "BKL1-6"},
        )
        fake.update(overrides)
        mock_get_jira.return_value = SimpleNamespace(**fake)
        return user

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_renders(self, mock_get_jira):
        requested = []
        user = self._login_and_fake_jira(
            mock_get_jira, get_status_histories=lambda keys: requested.extend(keys) or {}
        )

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chartjs-chart-sankey")
        self.assertContains(response, "BKL1-5")
        self.assertEqual(requested,["BKL1-1", "BKL1-2", "BKL1-3", "BKL1-4", "BKL1-5"])

        user.language = "fr"
        user.save(update_fields=["language"])
        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))
        self.assertContains(response, "Périmètre du sprint")
        self.assertContains(response, "Non terminés")
        self.assertNotContains(response, "Retirés")
        self.assertContains(response, "Tickets prévus clôturés")
        self.assertContains(response, "2 clôturés / 3 commencés")
        self.assertContains(response, "2 clôturés / 3 au total")
        self.assertContains(response, "Évolution des statuts")
        # No history: nothing to compare yet.
        self.assertContains(response, "historique des statuts est nécessaire")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_shows_unchanged_statuses_with_the_history(self, mock_get_jira):
        history = {"state": "In Progress", "created": "2026-02-01T10:00:00.000+0000", "status_changes": []}
        self._login_and_fake_jira(mock_get_jira, get_status_histories=lambda keys: {"BKL1-4": history})

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertContains(response, "stabilityMatrixBody")
        self.assertContains(response, "const TOTAL = \"Total\"")
        # Planned issues: BKL1-4 only has a history, the 2 others count as changed ("No status").
        self.assertContains(response, "1 au même statut qu'à l'entrée / 3 tickets")
        self.assertContains(response, "1 jamais changés · 0 changés puis revenus")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_warns_about_issues_missing_from_the_report_or_without_history(self, mock_get_jira):
        history = {"state": "In Progress", "created": "2026-02-01T10:00:00.000+0000", "status_changes": []}
        self._login_and_fake_jira(
            mock_get_jira,
            get_status_histories=lambda keys: {key: history for key in keys if key != "BKL1-3"},
            # BKL1-7 is in the sprint but left out of the report by the board's filter.
            get_sprint_issue_keys=lambda sprint_id: {"BKL1-1", "BKL1-7"},
        )

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertEqual(response.context["missing_from_report"], ["BKL1-7"])
        self.assertEqual(response.context["without_history"], ["BKL1-3"])
        self.assertEqual(response.context["scope_errors"], [])
        self.assertContains(response, "1 ticket du sprint manque dans le rapport de sprint de Jira")
        self.assertContains(response, 'href="https://jira.example/browse/BKL1-7"')
        self.assertContains(response, "1 ticket n'a pas d'historique de statut")
        self.assertNotContains(response, "alert-danger")
        # BKL1-3 is planned but has no history: no "created after the start" alert for it.
        self.assertNotContains(response, "créé après la date de début du sprint")
        self.assertContains(response, "1 ticket du sprint était déjà terminé en dehors de celui-ci")
        self.assertContains(response, 'href="https://jira.example/browse/BKL1-6"')

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_lists_late_planned_issues_and_links_the_not_started_ones(self, mock_get_jira):
        late = {"state": "To Do", "created": "2026-03-04T10:00:00.000+0000", "status_changes": []}
        self._login_and_fake_jira(
            mock_get_jira,
            get_status_histories=lambda keys: {"BKL1-4": late},
            status_category_keys=lambda: {"To Do": "new"},
        )

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertEqual([issue["key"] for issue in response.context["planned_created_late"]], ["BKL1-4"])
        self.assertContains(response, "1 ticket compté comme prévu par Jira a été créé après la date de début du sprint")
        self.assertContains(response, 'href="https://jira.example/browse/BKL1-4"')
        self.assertContains(response, 'class="scope-not-started" data-group="planned"')

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_says_when_the_report_coverage_cannot_be_checked(self, mock_get_jira):
        def broken(sprint_id):
            raise TypeError("argument of type 'NoneType' is not iterable")

        self._login_and_fake_jira(mock_get_jira, get_sprint_issue_keys=broken)

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Impossible de vérifier que le rapport de sprint de Jira contient tous les tickets du sprint")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_falls_back_to_current_statuses_when_history_times_out(self, mock_get_jira):
        def timeout(keys):
            raise Timeout("read timed out")

        self._login_and_fake_jira(mock_get_jira, get_status_histories=timeout)

        response = self.client.get(reverse("sprint-scope-flow", args=[2446]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alert-warning")
        self.assertFalse(response.context["scope"]["has_history"])


def _metric_history(created, state, *changes):
    return {
        "state": state,
        "created": created,
        "status_changes": [{"at": at, "from": old, "to": new} for at, old, new in changes],
    }


METRIC_CATEGORIES = {"To Do": "new", "In Progress": "indeterminate", "Pending": "new", "Done": "done"}


class ReportMetricsTests(SimpleTestCase):
    def _scope(self, statuses, added=()):
        """Scope of issues K<n> whose status is `statuses[n]` = (name, category)."""
        names = list(statuses.values())
        report = {
            "completedIssues": [],
            "issuesNotCompletedInCurrentSprint": [],
            "issueKeysAddedDuringSprint": {f"K{n}": [] for n in added},
            "entityData": {
                "statuses": {
                    str(i): {"statusName": name, "status": {"statusCategory": {"key": cat}}}
                    for i, (name, cat) in enumerate(names)
                }
            },
        }
        for i, (n, (name, cat)) in enumerate(statuses.items()):
            bucket = "completedIssues" if cat == "done" else "issuesNotCompletedInCurrentSprint"
            report[bucket].append({"key": f"K{n}", "summary": f"Issue {n}", "statusId": i})
        return build_scope_flow(report, lambda key: f"https://jira/{key}")

    def test_sprint_metrics_counts_the_planned_and_added_issues(self):
        scope = self._scope(
            {1: ("Done", "done"), 2: ("Done", "done"), 3: ("In Progress", "indeterminate"), 4: ("Done", "done")},
            added=[4],
        )

        metrics = sprint_metrics(scope, None, None)

        self.assertEqual((metrics["planned"], metrics["added"]), (3, 1))
        self.assertEqual((metrics["closed"], metrics["throughput"]), (3, 3))
        self.assertEqual(metrics["percent_planned_closed"], 67)
        self.assertEqual(metrics["percent_added"], 25)
        self.assertEqual((metrics["wip_end"], metrics["not_started"]), (1, 0))
        self.assertIsNone(metrics["cycle_median"])

    def test_sprint_metrics_separates_not_started_from_wip(self):
        scope = self._scope({1: ("To Do", "new"), 2: ("In Progress", "indeterminate"), 3: ("Done", "done")})

        metrics = sprint_metrics(scope, None, None)

        self.assertEqual((metrics["not_started"], metrics["wip_end"], metrics["closed"]), (1, 1, 1))

    def test_wip_end_leaves_out_the_issues_removed_from_the_sprint(self):
        scope = self._scope({1: ("In Progress", "indeterminate"), 2: ("In Progress", "indeterminate")})
        scope["issues"][1]["outcome"] = "removed"

        self.assertEqual(sprint_metrics(scope, None, None)["wip_end"], 1)

    def test_kanban_snapshot_takes_the_creation_status_before_any_change(self):
        # Created straight in progress, moved back to To Do after the snapshot date.
        scope = self._scope({1: ("To Do", "new")})
        scope["status_categories"].update(METRIC_CATEGORIES)
        histories = {
            "K1": _metric_history(
                "2026-03-02T09:00:00.000+0100",
                "To Do",
                ("2026-03-20T09:00:00.000+0100", "In Progress", "To Do"),
            ),
        }
        at = datetime(2026, 3, 4, 9, tzinfo=timezone.get_default_timezone())

        snapshot = kanban_snapshot(scope, histories, at)

        self.assertEqual(snapshot["wip"], 1)
        self.assertAlmostEqual(snapshot["oldest"][0]["age_days"], 16 / 7.2, places=2)

    def test_sprint_metrics_takes_the_cycle_times_of_the_closed_issues(self):
        end = datetime(2026, 3, 13, tzinfo=dt_timezone.utc)
        scope = self._scope({1: ("Done", "done"), 2: ("Done", "done")})
        scope["status_categories"].update(METRIC_CATEGORIES)
        histories = {
            key: _metric_history(
                "2026-03-01T08:00:00.000+0100",
                "Done",
                ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
                (f"2026-03-0{day}T09:00:00.000+0100", "In Progress", "Done"),
            )
            for key, day in (("K1", 3), ("K2", 4))  # from Monday 09:00: 1 and 2 business days
        }

        metrics = sprint_metrics(scope, histories, end)

        # 8 h = 1.11 days and 16 h = 2.22 days (7.2 h working day): median 1.665, p85 2.035.
        self.assertAlmostEqual(metrics["cycle_median"], 1.67, places=1)
        self.assertAlmostEqual(metrics["cycle_p85"], 2.04, places=1)

    def test_cycle_time_starts_at_the_first_started_status(self):
        history = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "Done",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),  # a Monday
            ("2026-03-04T09:00:00.000+0100", "In Progress", "Done"),
        )
        end = datetime(2026, 3, 13, tzinfo=dt_timezone.utc)

        self.assertAlmostEqual(cycle_time_days(history, METRIC_CATEGORIES, end), 16 / 7.2, places=2)

        # "Pending" is To Do-like for Jira, yet listed as started: it starts the clock.
        pending = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "Done",
            ("2026-03-02T09:00:00.000+0100", "To Do", "Pending"),
            ("2026-03-04T09:00:00.000+0100", "Pending", "Done"),
        )
        self.assertEqual(cycle_time_days(pending, METRIC_CATEGORIES, end), 0.0)  # the clock starts at Done
        self.assertAlmostEqual(
            cycle_time_days(pending, {**METRIC_CATEGORIES, "Pending": "indeterminate"}, end), 16 / 7.2, places=2
        )

    def test_cycle_time_is_none_without_a_done_transition(self):
        history = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "In Progress",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
        )

        self.assertIsNone(cycle_time_days(history, METRIC_CATEGORIES, datetime(2026, 3, 13, tzinfo=dt_timezone.utc)))

    def test_cycle_time_ignores_a_done_transition_after_the_end(self):
        history = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "Done",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
            ("2026-03-20T09:00:00.000+0100", "In Progress", "Done"),
        )

        self.assertIsNone(cycle_time_days(history, METRIC_CATEGORIES, datetime(2026, 3, 13, tzinfo=dt_timezone.utc)))

    def test_percentile_interpolates(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(percentile([4, 3, 2, 1], 85), 3.55)
        self.assertIsNone(percentile([], 50))

    def test_empty_sprint_gives_gaps_not_zeros(self):
        scope = self._scope({})

        metrics = sprint_metrics(scope, None, None)

        self.assertIsNone(metrics["percent_planned_closed"])
        self.assertIsNone(metrics["percent_added"])
        self.assertIsNone(metrics["cycle_median"])
        self.assertIsNone(metrics["cycle_p85"])

    def test_kanban_snapshot_is_rebuilt_at_the_given_date(self):
        scope = self._scope({1: ("Done", "done"), 2: ("In Progress", "indeterminate"), 3: ("To Do", "new")})
        histories = {
            # Done now, but still in progress at the snapshot date.
            "K1": _metric_history(
                "2026-02-01T08:00:00.000+0100",
                "Done",
                ("2026-02-02T09:00:00.000+0100", "To Do", "In Progress"),
                ("2026-04-01T09:00:00.000+0100", "In Progress", "Done"),
            ),
            "K2": _metric_history(
                "2026-02-01T08:00:00.000+0100",
                "In Progress",
                ("2026-03-09T09:00:00.000+0100", "To Do", "In Progress"),
            ),
            "K3": _metric_history("2026-02-01T08:00:00.000+0100", "To Do"),
        }
        at = datetime(2026, 3, 13, 9, 0, tzinfo=timezone.get_default_timezone())

        snapshot = kanban_snapshot(scope, histories, at, limit=1)

        self.assertEqual(snapshot["wip"], 2)
        self.assertEqual(snapshot["aging"], 1)  # K1 for weeks, K2 for 4 business days
        self.assertEqual(snapshot["aging_percent"], 50)
        self.assertEqual([item["key"] for item in snapshot["oldest"]], ["K1"])
        self.assertEqual(snapshot["oldest"][0]["status"], "In Progress")
        self.assertEqual(snapshot["oldest"][0]["url"], "https://jira/K1")
        self.assertGreater(snapshot["oldest"][0]["age_days"], 10)
        self.assertEqual(len(kanban_snapshot(scope, histories, at)["oldest"]), 2)

    def test_went_back_detects_a_return_to_to_do(self):
        back = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "To Do",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
            ("2026-03-03T09:00:00.000+0100", "In Progress", "To Do"),
        )
        forward = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "Done",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
            ("2026-03-03T09:00:00.000+0100", "In Progress", "Done"),
        )
        reopened = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "In Progress",
            ("2026-03-02T09:00:00.000+0100", "To Do", "Done"),
            ("2026-03-03T09:00:00.000+0100", "Done", "In Progress"),
        )
        end = datetime(2026, 3, 13, tzinfo=dt_timezone.utc)

        self.assertTrue(went_back(reopened, METRIC_CATEGORIES, end))
        self.assertTrue(went_back(back, METRIC_CATEGORIES, end))
        self.assertFalse(went_back(forward, METRIC_CATEGORIES, end))
        self.assertFalse(went_back(back, METRIC_CATEGORIES, datetime(2026, 3, 2, 12, tzinfo=dt_timezone.utc)))

    def test_age_in_status_counts_from_the_last_change(self):
        history = _metric_history(
            "2026-03-01T08:00:00.000+0100",
            "In Progress",
            ("2026-03-02T09:00:00.000+0100", "To Do", "In Progress"),
        )
        at = datetime(2026, 3, 4, 9, tzinfo=timezone.get_default_timezone())

        self.assertAlmostEqual(age_in_status_days(history, at), 16 / 7.2, places=2)


class ReportDataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def _sprint(sprint_id, start, end, state="closed"):
        return {
            "id": sprint_id, "name": f"Sprint {sprint_id}", "state": state,
            "startDate": f"{start}T08:00:00.000+01:00", "endDate": f"{end}T18:00:00.000+01:00",
            "completeDate": None,
        }

    @staticmethod
    def _jira(calls=None, **overrides):
        calls = calls if calls is not None else []

        def scope_report(sprint_id):
            calls.append(("scope", sprint_id))
            return SCOPE_REPORT

        fake = dict(
            cfg=SimpleNamespace(pk=1),
            get_sprint_scope_report=scope_report,
            get_issue_url=lambda key: f"https://jira.example/browse/{key}",
            get_status_histories=lambda keys: {},
            status_category_keys=lambda: {},
            get_sprint_issue_keys=lambda sprint_id: {"BKL1-1", "BKL1-2", "BKL1-4", "BKL1-5", "BKL1-6"},
        )
        fake.update(overrides)
        return SimpleNamespace(**fake)

    def test_sprints_in_range_keeps_overlapping_closed_sprints(self):
        sprints = [
            self._sprint(1, "2026-01-05", "2026-01-16"),
            self._sprint(2, "2026-01-19", "2026-01-30"),
            self._sprint(3, "2026-02-02", "2026-02-13"),
            self._sprint(4, "2026-02-16", "2026-02-27", state="active"),
            self._sprint(5, "2026-03-02", "2026-03-13", state="future"),
        ]

        # Edges are inclusive: sprint 1 ends on the 16th, sprint 3 starts on the 2nd.
        kept = sprints_in_range(sprints, date(2026, 1, 16), date(2026, 2, 2))

        self.assertEqual([sprint["id"] for sprint in kept], [1, 2, 3])
        self.assertEqual(sprints_in_range(sprints, date(2026, 2, 16), date(2026, 3, 31)), [])

    def test_trend_for_takes_the_five_previous_closed_sprints(self):
        closed = [self._sprint(i, f"2026-01-{i + 1:02d}", f"2026-01-{i + 1:02d}") for i in range(1, 8)]

        self.assertEqual([s["id"] for s in trend_for(closed[6], closed)], [2, 3, 4, 5, 6])
        self.assertEqual([s["id"] for s in trend_for(closed[2], closed)], [1, 2])
        self.assertEqual(trend_for(closed[0], closed), [])

    def test_build_report_data_marks_an_active_sprint_in_progress(self):
        sprint = self._sprint(2446, "2026-03-02", "2026-03-13", state="active")

        data = build_report_data(self._jira(), [sprint])

        self.assertTrue(data["in_progress"])
        self.assertEqual(data["title_period"], "Sprint 2446 (en cours)")
        self.assertEqual(data["focus"]["id"], 2446)
        self.assertEqual(data["project_name"], "")
        entry = data["sprints"][0]
        self.assertEqual(entry["metrics"]["planned"], 3)
        self.assertEqual(entry["categories"], entry["scope"]["status_categories"])
        self.assertEqual(data["totals"]["planned"], 3)
        self.assertEqual(data["totals"]["added"], 2)

    def test_build_report_data_titles_several_sprints_and_sums_them(self):
        sprints = [self._sprint(1, "2026-01-05", "2026-01-16"), self._sprint(2, "2026-01-19", "2026-01-30")]

        data = build_report_data(self._jira(), sprints)

        self.assertFalse(data["in_progress"])
        self.assertEqual(data["title_period"], "du 05/01/2026 au 30/01/2026")
        self.assertEqual(data["focus"]["id"], 2)
        self.assertFalse(data["single"])
        self.assertEqual(data["totals"]["planned"], 6)
        self.assertEqual(data["totals"]["closed"], 6)
        planned_closed = sum(entry["scope"]["completion"]["planned"]["done"] for entry in data["sprints"])
        self.assertEqual(data["totals"]["planned_closed"], planned_closed)
        self.assertEqual(data["totals"]["percent_planned_closed"], round(100 * planned_closed / 6))

    def test_build_report_data_with_a_focus_is_a_single_sprint_report(self):
        trend = self._sprint(1, "2026-01-05", "2026-01-16")
        focus = self._sprint(2, "2026-01-19", "2026-01-30", state="active")

        data = build_report_data(self._jira(), [trend, focus], focus=focus)

        self.assertTrue(data["single"])
        self.assertTrue(data["in_progress"])
        self.assertEqual(data["title_period"], "Sprint 2 (en cours)")
        self.assertEqual(len(data["sprints"]), 2)  # the trend sprints stay for slides 4 and 5
        focus_metrics = data["sprints"][1]["metrics"]
        self.assertEqual(data["totals"]["planned"], focus_metrics["planned"])
        self.assertEqual(data["totals"]["closed"], focus_metrics["closed"])
        self.assertEqual(data["totals"]["percent_planned_closed"], focus_metrics["percent_planned_closed"])

    def test_build_report_data_single_closed_focus_has_no_en_cours(self):
        focus = self._sprint(2, "2026-01-19", "2026-01-30")

        data = build_report_data(self._jira(), [self._sprint(1, "2026-01-05", "2026-01-16"), focus], focus=focus)

        self.assertEqual((data["title_period"], data["in_progress"]), ("Sprint 2", False))

    def test_build_report_data_survives_a_history_error(self):
        def failing(keys):
            raise JIRAError(status_code=500, text="boom")

        sprint = self._sprint(1, "2026-01-05", "2026-01-16")

        data = build_report_data(self._jira(get_status_histories=failing), [sprint])

        entry = data["sprints"][0]
        self.assertTrue(entry["warnings"]["history_error"])
        self.assertIsNone(entry["histories"])
        self.assertIsNone(entry["metrics"]["cycle_median"])
        self.assertEqual(entry["metrics"]["planned"], 3)

    def test_build_report_data_caches_closed_sprints_only(self):
        calls = []
        closed = self._sprint(1, "2026-01-05", "2026-01-16")
        active = self._sprint(2, "2026-01-19", "2026-01-30", state="active")
        jira = self._jira(calls)

        build_report_data(jira, [closed, active])
        build_report_data(jira, [closed, active])

        self.assertEqual(calls, [("scope", 1), ("scope", 2), ("scope", 2)])

    def test_cache_depends_on_the_started_statuses(self):
        calls = []
        closed = self._sprint(1, "2026-01-05", "2026-01-16")

        build_report_data(self._jira(calls, cfg=SimpleNamespace(pk=1, started_statuses=["Pending"])), [closed])
        build_report_data(self._jira(calls, cfg=SimpleNamespace(pk=1, started_statuses=["Pending"])), [closed])
        build_report_data(self._jira(calls, cfg=SimpleNamespace(pk=1, started_statuses=["Review"])), [closed])

        self.assertEqual(calls, [("scope", 1), ("scope", 1)])

    def test_sprint_warning_lines_write_one_sentence_per_warning(self):
        lines = sprint_warning_lines(
            {
                "planned_created_late": [{"key": "BKL1-1"}, {"key": "BKL1-2"}],
                "missing_from_report": ["BKL1-7"],
                "without_history": [],
                "history_error": "Jira ne répond pas",
            }
        )

        self.assertEqual(lines[0], "2 tickets prévus créés après le début du sprint : BKL1-1, BKL1-2")
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("BKL1-7" in line for line in lines))
        self.assertTrue(any("Jira ne répond pas" in line for line in lines))


class AnalysisTests(SimpleTestCase):
    CATEGORIES = {"A faire": "new", "En cours": "indeterminate", "Termine": "done"}
    GOOD = (
        "Intro ignoree\n## OÙ EN EST L'ÉQUIPE\n- Une\n* Deux\n• Trois\n1. Quatre\n"
        "## TENDANCE\n- Cinq\n## RISQUES\n### Risque A\nTexte A.\n### Risque B\nTexte B\nsuite.\n"
        "### Risque C\nTexte C.\n### Risque D\nTexte D.\n"
    )

    @staticmethod
    def _issue(key, summary, assignee, origin="planned", outcome="done", start="A faire", end="Termine"):
        return {
            "key": key, "summary": summary, "type": "Story", "assignee": assignee, "origin": origin,
            "outcome": outcome, "status_start": start, "status_end": end,
        }

    def _data(self):
        issues = [
            self._issue("BKL1-1", "Page de login", "Alice"),
            self._issue("BKL1-2", "Export PDF", "Bob", outcome="not_done", end="En cours"),
            self._issue("BKL1-3", "Idee en attente", "", outcome="not_done", end="A faire"),
        ]
        histories = {
            "BKL1-1": {"created": "2026-01-01T08:00:00.000+01:00", "status_changes": []},
            "BKL1-2": {
                "created": "2026-01-01T08:00:00.000+01:00",
                "status_changes": [
                    {"at": "2026-01-06T09:00:00.000+01:00", "from": "A faire", "to": "En cours"},
                    {"at": "2026-01-07T09:00:00.000+01:00", "from": "En cours", "to": "A faire"},
                    {"at": "2026-01-08T09:00:00.000+01:00", "from": "A faire", "to": "En cours"},
                ],
            },
            "BKL1-3": {"created": "2026-01-01T08:00:00.000+01:00", "status_changes": []},
        }
        sprint = {
            "id": 1, "name": "Sprint 1", "state": "closed",
            "startDate": "2026-01-05T08:00:00.000+01:00", "endDate": "2026-01-16T18:00:00.000+01:00",
            "completeDate": None,
        }
        metrics = {
            "planned": 3, "added": 0, "closed": 1, "not_started": 1, "throughput": 1, "cycle_median": 2.0,
            "cycle_p85": 3.0, "wip_end": 1, "percent_planned_closed": 33, "percent_added": 0,
        }
        entry = {
            "sprint": sprint, "metrics": metrics, "warnings": {}, "histories": histories,
            "categories": self.CATEGORIES,
            "scope": {
                "issues": issues, "status_categories": self.CATEGORIES, "totals": {"planned": 3, "added": 0},
                "done_elsewhere": [{"key": "BKL1-8", "summary": "Fini ailleurs", "url": ""}],
            },
        }
        return {
            "title_period": "Sprint 1", "in_progress": False, "project_name": "Backlog", "sprints": [entry],
            "focus": sprint, "snapshot": None, "totals": {},
        }

    def test_parse_reads_the_three_sections(self):
        analysis = PROVIDERS["copilot"].parse(self.GOOD)

        self.assertEqual(analysis.situation, ["Une", "Deux", "Trois", "Quatre"])
        self.assertEqual(analysis.trend, ["Cinq"])
        self.assertEqual(analysis.risks[0], Risk("Risque A", "Texte A."))
        self.assertEqual(analysis.risks[1].text, "Texte B suite.")

    def test_parse_tolerates_case_accents_and_hash_count(self):
        text = "# Ou en est l'equipe  \n- Un\n### tendance\n- Deux\n#### RISQUES\n## Titre\nExplication."
        analysis = PROVIDERS["copilot"].parse(text)

        self.assertEqual((analysis.situation, analysis.trend), (["Un"], ["Deux"]))
        self.assertEqual(analysis.risks, [Risk("Titre", "Explication.")])

    def test_parse_keeps_at_most_three_risks(self):
        self.assertEqual(len(PROVIDERS["copilot"].parse(self.GOOD).risks), 3)

    def test_parse_without_sections_raises_a_clear_error(self):
        with self.assertRaises(AnalysisParseError) as raised:
            PROVIDERS["copilot"].parse("## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux")
        self.assertIn("RISQUES", str(raised.exception))
        with self.assertRaises(AnalysisParseError) as raised:
            PROVIDERS["copilot"].parse("Bonjour")
        self.assertIn("TENDANCE", str(raised.exception))

    def test_prompt_embeds_the_dossier_and_the_rules(self):
        prompt = PROVIDERS["copilot"].prompt("DOSSIER-X")

        self.assertIn("DOSSIER-X", prompt)
        for expected in ("## OÙ EN EST L'ÉQUIPE", "## TENDANCE", "## RISQUES", "### Titre du risque", "5 à 7", "3 risques"):
            self.assertIn(expected, prompt)

    def test_no_ai_provider_needs_no_prompt_and_parses_to_an_empty_analysis(self):
        provider = PROVIDERS["none"]

        self.assertFalse(provider.needs_prompt)
        self.assertTrue(PROVIDERS["copilot"].needs_prompt)
        self.assertEqual(provider.parse("peu importe"), Analysis([], [], []))

    def test_risky_keys_are_not_started_or_went_back(self):
        self.assertEqual(sorted(risky_keys(self._data())), ["BKL1-2", "BKL1-3"])
        self.assertEqual(len(risky_keys(self._data(), limit=1)), 1)

    def test_dossier_has_titles_assignees_and_descriptions_of_risky_issues_only(self):
        descriptions = {"BKL1-2": "Contexte de l'export", "BKL1-1": "NE DOIT PAS APPARAITRE", "BKL1-3": "x" * 600}
        dossier = build_dossier(self._data(), descriptions)

        self.assertIn("BKL1-1 | Story | Page de login | Alice | A faire → Termine | prévu | clôturé |", dossier)
        self.assertIn("BKL1-2 | Story | Export PDF | Bob | A faire → En cours | prévu | non terminé |", dossier)
        self.assertIn("BKL1-8 — Fini ailleurs", dossier)
        self.assertIn("revenu en arrière: oui", dossier)
        self.assertIn("revenu en arrière: non", dossier)
        self.assertIn("Contexte de l'export", dossier)
        self.assertNotIn("NE DOIT PAS APPARAITRE", dossier)
        self.assertNotIn("x" * 501, dossier)
        self.assertIn("Sprint 1", dossier)

    def test_dossier_never_contains_the_token(self):
        data = self._data()
        data["cfg"] = SimpleNamespace(jira_token="SECRET")

        self.assertNotIn("SECRET", build_dossier(data, {}))

    def test_analysis_round_trips_through_the_session(self):
        analysis = Analysis(["a"], ["b"], [Risk("t", "x")])

        stored = analysis.to_session()

        self.assertEqual(stored, {"situation": ["a"], "trend": ["b"], "risks": [{"title": "t", "text": "x"}]})
        self.assertEqual(Analysis.from_session(stored), analysis)

    def test_parse_accepts_bold_bare_and_hashed_bold_headings(self):
        text = (
            "**OÙ EN EST L'ÉQUIPE**\n- Un\n## **TENDANCE**\n- Deux\nRisques :\n"
            "**Risque A**\n- Detail A\n### Risque B\nTexte B.\n"
        )
        analysis = PROVIDERS["copilot"].parse(text)

        self.assertEqual((analysis.situation, analysis.trend), (["Un"], ["Deux"]))
        self.assertEqual(analysis.risks, [Risk("Risque A", "Detail A"), Risk("Risque B", "Texte B.")])
        self.assertEqual(PROVIDERS["copilot"].parse("TENDANCE\n- a\nOu en est l'equipe\n- b\nRISQUES").trend, ["a"])

    def test_parse_accepts_a_heading_ending_with_a_question_mark(self):
        text = "## Où en est l'équipe ?\n- Un\n## Tendance :\n- Deux\n## Risques ?\n### Dette\nTexte."

        self.assertEqual(PROVIDERS["copilot"].parse(text).situation, ["Un"])

    def test_parse_reads_risks_copied_as_plain_text(self):
        # Copilot's rendered answer, copied: no "#", no "**", one paragraph per line.
        text = (
            "Où en est l'équipe\nLe sprint avance.\nTendance\nStable.\nRisques\nVoici les risques :\n"
            "Dette technique\nLe module X ralentit tout.\nExport PDF\nLivraison incertaine.\n\n"
            "Recrutement\n\nUn départ non remplacé.\n"
        )

        analysis = PROVIDERS["copilot"].parse(text)

        self.assertEqual((analysis.situation, analysis.trend), (["Le sprint avance."], ["Stable."]))
        self.assertEqual(
            analysis.risks,
            [
                Risk("Dette technique", "Le module X ralentit tout."),
                Risk("Export PDF", "Livraison incertaine."),
                Risk("Recrutement", "Un départ non remplacé."),
            ],
        )

    def test_parse_reads_risks_written_as_bullets(self):
        head = "## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n"
        parse = PROVIDERS["copilot"].parse

        self.assertEqual(
            parse(head + "- **Dette** : le module X ralentit.\n- Export PDF : livraison incertaine.\n").risks,
            [Risk("Dette", "le module X ralentit."), Risk("Export PDF", "livraison incertaine.")],
        )
        self.assertEqual(
            parse(head + "1. **Dette**\nLe module X ralentit.\n2. **Export PDF :**\nLivraison incertaine.\n").risks,
            [Risk("Dette", "Le module X ralentit."), Risk("Export PDF", "Livraison incertaine.")],
        )
        # Under a marked title, a "x : y" bullet is part of the explanation.
        self.assertEqual(parse(head + "### Dette\n- Impact : retard\n").risks, [Risk("Dette", "Impact : retard")])

    def test_parse_keeps_a_multi_paragraph_explanation_under_a_marked_title(self):
        head = "## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n"

        self.assertEqual(
            PROVIDERS["copilot"].parse(head + "### Dette\nPremière phrase.\n\nDeuxième paragraphe.\n### Export\nTexte.\n").risks,
            [Risk("Dette", "Première phrase. Deuxième paragraphe."), Risk("Export", "Texte.")],
        )

    def test_parse_keeps_sub_bullets_in_the_explanation(self):
        head = "## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n"

        self.assertEqual(
            PROVIDERS["copilot"].parse(head + "- **Dette** : le module.\n  - détail un\n  - détail deux\n- **Export** : texte.\n").risks,
            [Risk("Dette", "le module. détail un détail deux"), Risk("Export", "texte.")],
        )

    def test_parse_reads_no_risk_as_an_empty_list(self):
        head = "## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n"
        for sentence in ("Aucun risque identifié.", "Pas de risque", "Aucun risque majeur.", "**AUCUN RISQUE IDENTIFIE**"):
            with self.subTest(sentence):
                self.assertEqual(PROVIDERS["copilot"].parse(head + sentence + "\n").risks, [])

    def test_parse_reads_consecutive_lines_after_a_bare_title_as_its_explanation(self):
        head = "## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n"

        self.assertEqual(
            PROVIDERS["copilot"].parse(head + "Dette technique\nLigne un.\nLigne deux.\n\nExport\nTexte.\n").risks,
            [Risk("Dette technique", "Ligne un. Ligne deux."), Risk("Export", "Texte.")],
        )

    def test_parse_refuses_risks_it_cannot_read_and_empty_sections(self):
        parse = PROVIDERS["copilot"].parse
        with self.assertRaises(AnalysisParseError) as raised:
            parse("## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\nVoici les risques :\n")
        self.assertIn("RISQUES", str(raised.exception))
        with self.assertRaises(AnalysisParseError) as raised:
            parse("## OÙ EN EST L'ÉQUIPE\n## TENDANCE\n- Deux\n## RISQUES\n")
        self.assertIn("OÙ EN EST L'ÉQUIPE", str(raised.exception))
        # An empty RISQUES section is no risk to show, not an error.
        self.assertEqual(parse("## OÙ EN EST L'ÉQUIPE\n- Un\n## TENDANCE\n- Deux\n## RISQUES\n").risks, [])


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
SUBSTITUTE = "texte de substitution"


def _fixture_template(path, skip=()):
    """A 6-slide template whose shapes are named like the Solidaris ones, but those in `skip`."""
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    blank = prs.slide_layouts[6]
    row = [0]

    def box(shapes, name, text=SUBSTITUTE):
        if name in skip:
            return None
        row[0] += 1
        shape = shapes.add_textbox(Emu(400000), Emu(300000 * row[0]), Emu(4000000), Emu(300000))
        shape.name, shape.text_frame.text = name, text
        return shape

    def picture(slide, name):
        slide.shapes.add_picture(BytesIO(TINY_PNG), Emu(0), Emu(0), Emu(100000), Emu(100000)).name = name

    def titled(names, groups=()):
        row[0] = 0
        slide = prs.slides.add_slide(blank)
        box(slide.shapes, "Titre 1", "Titre layout")
        box(slide.shapes, "Titre 1", "- Sous-titre -")
        for name in names:
            box(slide.shapes, name)
        for group_names in groups:
            group = slide.shapes.add_group_shape()
            for name in group_names:
                box(group.shapes, name)
        return slide

    title = prs.slides.add_slide(blank)
    for name in ("Rectangle 4", "Rectangle 5", "Rectangle 3", "Espace réservé de la date 3"):
        box(title.shapes, name)
    picture(title, "Graphique 8")
    columns = titled(["ZoneTexte 29", "ZoneTexte 55"], [["Rectangle 31"], ["Rectangle 58"]])
    for name in ("Rectangle 51", "Rectangle 54"):  # the empty column backgrounds of the real template
        box(columns.shapes, name, "")
    titled(["Rectangle 16", "ZoneTexte 58"], [["Rectangle 24", "ZoneTexte 58"]])
    picture(titled(["Rectangle 14", "Rectangle 18", "ZoneTexte 19", "ZoneTexte 58"], [["Rectangle 21"]]), "Graphique 4")
    titled([], [["Rectangle 4", "Rectangle 6"], ["Rectangle 18", "Rectangle 20"], ["Rectangle 22", "Rectangle 24"]])
    box(prs.slides.add_slide(blank).shapes, "Rectangle 3", "Merci.")
    prs.save(path)


class PptxReportTests(SimpleTestCase):
    DISCLAIMER = "Texte généré par IA à partir des métriques JiraFox — à relire avant diffusion"

    @staticmethod
    def _entry(sprint_id, name, metrics, issues=(), warnings=None):
        sprint = {
            "id": sprint_id, "name": name, "state": "closed",
            "startDate": "2026-10-01T08:00:00.000+01:00", "endDate": "2026-10-14T18:00:00.000+01:00",
            "completeDate": None,
        }
        categories = {"A faire": "new", "En cours": "indeterminate", "Termine": "done"}
        return {
            "sprint": sprint, "metrics": metrics, "warnings": warnings or {}, "histories": {}, "categories": categories,
            "scope": {"issues": list(issues), "status_categories": categories},
        }

    @staticmethod
    def _metrics(**overrides):
        metrics = {
            "planned": 10, "added": 2, "closed": 7, "not_started": 1, "throughput": 7, "cycle_median": 2.5,
            "cycle_p85": 4.0, "wip_end": 4, "percent_planned_closed": 60, "percent_added": 17,
        }
        return {**metrics, **overrides}

    @staticmethod
    def _issue(key, summary, outcome, status, assignee="Alice Martin"):
        return {
            "key": key, "summary": summary, "assignee": assignee, "origin": "planned", "outcome": outcome,
            "status_end": status, "status_start": "A faire",
        }

    def _data(self, *, warnings=None):
        first = self._entry(1, "Sprint 11", self._metrics(cycle_median=None, cycle_p85=None, percent_added=None, added=0))
        issues = [
            self._issue("BKL1-1", "Page de login", "done", "Termine"),
            self._issue("BKL1-2", "Export PDF", "not_done", "En cours", assignee="Bob Dupont"),
            self._issue("BKL1-3", "", "not_done", "A faire"),  # an empty cell must not break the table
        ]
        focus = self._entry(2, "Sprint 12", self._metrics(), issues, warnings)
        return {
            "title_period": "Sprint 12", "in_progress": False, "project_name": "Backlog", "sprints": [first, focus],
            "focus": focus["sprint"],
            "snapshot": {
                "wip": 3, "aging": 1, "aging_percent": 33,
                "oldest": [{"key": "BKL1-2", "summary": "Export PDF", "status": "En cours", "age_days": 12.5, "url": ""}],
            },
            "single": False,
            "totals": {
                "planned": 20, "added": 2, "closed": 14, "not_started": 2, "planned_closed": 12,
                "percent_planned_closed": 60,
            },
        }

    @staticmethod
    def _analysis():
        return Analysis(["Situation un", "Situation deux"], ["Tendance un"], [Risk("Risque A", "Explication A")])

    @staticmethod
    def _texts(slide):
        def walk(shapes):
            for shape in shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    yield from walk(shape.shapes)
                elif shape.has_text_frame:
                    yield shape.text_frame.text
                elif getattr(shape, "has_table", False):
                    yield "\n".join(cell.text for row in shape.table.rows for cell in row.cells)

        return list(walk(slide.shapes))

    def _build(self, data=None, analysis=None, template=None):
        from pptx import Presentation

        from sprint.pptx_report import build_deck

        return Presentation(BytesIO(build_deck(data or self._data(), analysis, template)))

    def test_neutral_deck_has_nine_slides_with_ai_and_seven_without(self):
        self.assertEqual(len(self._build(analysis=self._analysis()).slides), 9)
        self.assertEqual(len(self._build().slides), 7)

    def test_charts_are_native(self):
        slides = self._build(analysis=self._analysis()).slides
        charts = {
            number: [shape.chart for shape in slides[number - 1].shapes if shape.has_chart] for number in (3, 4, 5)
        }

        self.assertTrue(all(charts.values()))
        progress = {series.name: series.values for series in charts[3][0].plots[0].series}
        # Several sprints: slide 3 shows the sums over the period.
        self.assertEqual(progress, {"Prévus": (20.0,), "Ajoutés": (2.0,), "Clôturés": (14.0,), "Non commencés": (2.0,)})
        flow = {series.name: series.values for chart in charts[4] for plot in chart.plots for series in plot.series}
        self.assertEqual(flow["Débit"], (7.0, 7.0))
        self.assertEqual(flow["Temps de cycle médian"], (None, 2.5))
        predictability = {series.name: series.values for series in charts[5][0].plots[0].series}
        self.assertEqual(predictability["% des prévus clôturés"], (60.0, 60.0))
        self.assertEqual(predictability["% ajoutés"], (None, 17.0))

    def test_ai_text_and_disclaimer_on_slides_2_and_7(self):
        slides = self._build(analysis=self._analysis()).slides

        synthesis, risks = "\n".join(self._texts(slides[1])), "\n".join(self._texts(slides[6]))
        self.assertIn("Situation deux", synthesis)
        self.assertIn("Tendance un", synthesis)
        self.assertIn("Risque A", risks)
        self.assertIn("Explication A", risks)
        for slide in (slides[1], slides[6]):
            self.assertEqual(self._texts(slide)[-1], self.DISCLAIMER)

    def test_risks_slide_is_left_out_without_risks(self):
        deck = self._build(analysis=Analysis(["Situation un"], ["Tendance un"], []))

        self.assertEqual(len(deck.slides), 8)
        everything = "\n".join(text for slide in deck.slides for text in self._texts(slide))
        self.assertNotIn("Aucun risque", everything)
        self.assertNotIn("Risques", [self._texts(slide)[0] for slide in deck.slides if self._texts(slide)])

    def test_synthesis_slide_keeps_at_most_seven_bullets(self):
        analysis = Analysis([f"S{n}" for n in range(1, 7)], ["T1", "T2", "T3"], [Risk("R", "x")])

        slide = self._build(analysis=analysis).slides[1]

        text = "\n".join(self._texts(slide))
        for kept in ("S1", "S6", "T1"):
            self.assertIn(f"• {kept}", text)
        for cut in ("T2", "T3"):
            self.assertNotIn(f"• {cut}", text)
        notes = slide.notes_slide.notes_text_frame.text
        self.assertIn("T2", notes)
        self.assertIn("T3", notes)

    def test_annex_leaves_out_the_issues_removed_from_the_sprint(self):
        data = self._data()
        data["sprints"][1]["scope"]["issues"].append(self._issue("BKL1-7", "Sorti du sprint", "removed", "En cours"))

        annex = "\n".join(self._texts(self._build(data).slides[5]))

        self.assertIn("BKL1-2", annex)
        self.assertNotIn("BKL1-7", annex)

    def test_warnings_go_to_the_speaker_notes_not_the_slide(self):
        deck = self._build(self._data(warnings={"without_history": ["BKL1-9"]}), self._analysis())

        warning = "1 ticket sans historique de statut : BKL1-9"
        self.assertIn(warning, deck.slides[2].notes_slide.notes_text_frame.text)
        for slide in deck.slides:
            self.assertFalse(any(warning in text for text in self._texts(slide)))

    def test_template_deck_reuses_the_example_slides(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "modele.pptx")
            _fixture_template(path)
            deck = self._build(analysis=self._analysis(), template=path)

        self.assertEqual(len(deck.slides), 9)
        everything = "\n".join(text for slide in deck.slides for text in self._texts(slide))
        self.assertNotIn(SUBSTITUTE, everything.lower())
        self.assertNotIn("Titre layout", everything)
        self.assertIn("Rapport d'équipe", self._texts(deck.slides[0]))
        self.assertTrue(any(shape.name == "Graphique 8" for shape in deck.slides[0].shapes))
        self.assertTrue(any(shape.name == "Graphique 4" for shape in deck.slides[2].shapes))
        self.assertIn("Merci.", self._texts(deck.slides[-1]))

    def test_missing_template_shape_is_named_in_the_notes(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "modele.pptx")
            _fixture_template(path, skip={"ZoneTexte 19"})
            deck = self._build(template=path)

        self.assertIn("« ZoneTexte 19 » absente", deck.slides[0].notes_slide.notes_text_frame.text)

    def test_neutral_deck_has_no_missing_shape(self):
        self.assertFalse(self._build().slides[0].has_notes_slide)

    def test_several_sprints_progress_shows_the_period_sums(self):
        texts = self._texts(self._build().slides[1])

        self.assertIn("- 2 sprints, Sprint 12 -", texts)
        self.assertIn("60\u00a0%", texts)

    def test_single_report_covers_the_focus_sprint_only(self):
        data = self._data()
        data.update(single=True, totals={**data["sprints"][1]["metrics"], "planned_closed": 6})
        slides = self._build(data).slides

        self.assertIn("- Sprint 12 -", self._texts(slides[1]))
        annex = "\n".join(self._texts(slides[5]))
        self.assertIn("BKL1-1", annex)
        self.assertFalse(annex.startswith("Sprint"))  # no sprint column
        self.assertNotIn("Sprint 11", annex)
        self.assertEqual(len(next(shape for shape in slides[5].shapes if shape.has_table).table.columns), 3)

    def test_kanban_slide_without_history_shows_dashes_and_says_why(self):
        data = self._data()
        data["snapshot"] = None
        slide = self._build(data).slides[4]

        self.assertIn("WIP : -\nVieillissants : -", self._texts(slide))
        self.assertIn("Instantané indisponible", slide.notes_slide.notes_text_frame.text)

    def test_missing_template_falls_back_to_neutral_and_says_so(self):
        deck = self._build(template="introuvable/modele.pptx")

        self.assertEqual(len(deck.slides), 7)
        self.assertIn("mise en page neutre", deck.slides[0].notes_slide.notes_text_frame.text)

    def test_annex_has_no_assignee(self):
        annex = "\n".join(self._texts(self._build().slides[5]))

        self.assertIn("BKL1-1", annex)
        self.assertIn("Export PDF", annex)
        self.assertIn("En cours", annex)
        self.assertIn("Sprint 12", annex)  # several sprints: a sprint column
        self.assertNotIn("Alice", annex)
        self.assertNotIn("Bob", annex)

    def test_active_sprint_title_says_en_cours(self):
        data = self._data()
        data.update(title_period="Sprint 12 (en cours)", in_progress=True)

        texts = self._texts(self._build(data).slides[0])

        self.assertIn("Sprint 12 (en cours)", texts)
        self.assertIn("Backlog", texts)
        self.assertIn(f"Direction • {timezone.localdate():%d/%m/%Y}", texts)


class ManagementReportViewTests(TestCase):
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ANSWER = (
        "## OÙ EN EST L'ÉQUIPE\n- Le sprint avance bien\n- Deux tickets restent\n"
        "## TENDANCE\n- Stable\n## RISQUES\n### Export PDF\nLivraison incertaine.\n"
    )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @staticmethod
    def _sprint(sprint_id, start, end, state="closed"):
        return {
            "id": sprint_id, "name": f"Sprint {sprint_id}", "state": state,
            "startDate": f"{start}T08:00:00.000+01:00", "endDate": f"{end}T18:00:00.000+01:00",
            "completeDate": None, "url": "https://jira.example/",
        }

    def _login(self, mock_get_jira, **overrides):
        sprints = {
            1: self._sprint(1, "2026-01-05", "2026-01-16"),
            2: self._sprint(2, "2026-01-19", "2026-01-30"),
            3: self._sprint(3, "2026-02-02", "2026-02-13"),
            4: self._sprint(4, "2026-02-16", "2026-02-27", state="active"),
        }
        language = overrides.pop("language", "fr")
        user = get_user_model().objects.create_user(username="alice", password="secret", language=language)
        self.client.force_login(user)
        fake = dict(
            cfg=SimpleNamespace(pk=1, jira_board_id=638, project=SimpleNamespace(name="Mon Projet", report_template=None)),
            list_sprints=lambda board, state=None: [s for s in sprints.values() if s["state"] == (state or s["state"])],
            get_sprint=lambda sprint_id: sprints[int(sprint_id)],
            get_sprint_scope_report=lambda sprint_id: SCOPE_REPORT,
            get_issue_url=lambda key: f"https://jira.example/browse/{key}",
            get_status_histories=lambda keys: {},
            status_category_keys=lambda: {},
            get_sprint_issue_keys=lambda sprint_id: {"BKL1-1", "BKL1-2", "BKL1-4", "BKL1-5", "BKL1-6"},
            get_descriptions=lambda keys: {key: "Une description" for key in keys},
        )
        fake.update(overrides)
        mock_get_jira.return_value = SimpleNamespace(**fake)
        return user

    @patch("sprint.views.get_connected_jira_for_user")
    def test_trends_page_lists_the_sprints_of_the_range(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.get(reverse("sprint-trends"), {"start": "2026-01-01", "end": "2026-01-31"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sprint 1")
        self.assertContains(response, "Sprint 2")
        self.assertContains(response, 'value="1" checked')
        self.assertNotContains(response, 'value="3" checked')
        self.assertContains(response, "Rapport direction")
        self.assertContains(response, "sprint=1&amp;sprint=2")
        self.assertContains(response, "trendsChart")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_trends_page_without_closed_sprint_says_so(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.get(reverse("sprint-trends"), {"start": "2025-01-01", "end": "2025-01-31"})

        self.assertContains(response, "Aucun sprint clôturé sur cette période")
        self.assertNotContains(response, "/sprint/management-report/")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_trends_page_shows_scope_errors_in_red(self, mock_get_jira):
        self._login(mock_get_jira)

        with patch("sprint.report_data.consistency_errors", return_value=["Les chiffres divergent"]):
            response = self.client.get(reverse("sprint-trends"), {"sprint": ["1"]})

        self.assertContains(response, '<div class="alert alert-danger')
        self.assertContains(response, "Les chiffres divergent", count=1)  # in the red alert only

    @patch("sprint.views.get_connected_jira_for_user")
    def test_trends_chart_shows_the_wip_at_the_sprint_end(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.get(reverse("sprint-trends"), {"sprint": ["1", "2"]})

        self.assertEqual(len(response.context["chart_data"]["wip_end"]), 2)
        self.assertContains(response, "d.wip_end")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_report_page_shows_the_warnings_of_each_sprint(self, mock_get_jira):
        self._login(mock_get_jira, get_sprint_issue_keys=lambda sprint_id: {"BKL1-1", "BKL1-99"})

        with patch("sprint.report_data.consistency_errors", return_value=["Les chiffres divergent"]):
            response = self.client.get(reverse("sprint-management-report"), {"sprint": ["1", "2"]})

        self.assertContains(response, '<div class="alert alert-danger')
        self.assertContains(response, "Les chiffres divergent", count=2)  # once per sprint
        self.assertContains(response, "BKL1-99")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_speaker_notes_are_french_whatever_the_ui_language(self, mock_get_jira):
        from django.utils.translation import gettext
        from pptx import Presentation

        self._login(mock_get_jira, language="en")

        def errors(scope):
            return [gettext("Closed rate (%(group)s) does not add up.") % {"group": "all"}]

        with patch("sprint.report_data.consistency_errors", side_effect=errors):
            response = self.client.post(
                reverse("sprint-management-report"), {"action": "generate", "provider": "none", "sprint": ["1"]}
            )

        deck = Presentation(BytesIO(response.content))
        self.assertIn("Le taux de clôture (all) ne tombe pas juste.", deck.slides[1].notes_slide.notes_text_frame.text)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_report_page_shows_the_full_dossier_before_copying(self, mock_get_jira):
        requested = []
        self._login(
            mock_get_jira,
            status_category_keys=lambda: {"In Progress": "new", "Done": "done"},  # carried-over issues: not started
            get_descriptions=lambda keys: requested.extend(keys) or {key: "Une description" for key in keys},
        )

        response = self.client.get(reverse("sprint-management-report"), {"sprint": ["1", "2"]})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Projet : Mon Projet", response.context["dossier"])
        self.assertTrue(requested)
        # One source: the textarea holds the prompt and the whole dossier, which « Copier » copies.
        self.assertContains(response, "<textarea")
        self.assertContains(response, "readonly")
        self.assertContains(response, "DOSSIER")
        self.assertContains(response, "## Sprint 1")
        self.assertContains(response, "## Sprint 2")
        self.assertContains(response, "navigator.clipboard")

    @patch("sprint.views.get_connected_jira_for_user")
    def test_parse_error_keeps_the_answer_and_explains(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.post(
            reverse("sprint-management-report"),
            {"action": "parse", "provider": "copilot", "sprint": ["1", "2"], "answer": "Du texte sans titres"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Réponse inutilisable")
        self.assertContains(response, "Du texte sans titres")
        self.assertContains(response, '<div class="alert alert-danger')

    @patch("sprint.views.get_connected_jira_for_user")
    def test_generate_returns_a_pptx(self, mock_get_jira):
        from pptx import Presentation

        self._login(mock_get_jira)
        url = reverse("sprint-management-report")
        parsed = self.client.post(
            url, {"action": "parse", "provider": "copilot", "sprint": ["1", "2"], "answer": self.ANSWER}
        )
        self.assertContains(parsed, "Le sprint avance bien")

        response = self.client.post(
            url,
            {
                "action": "generate", "provider": "copilot", "sprint": ["1", "2"],
                "situation": "Texte édité A\nTexte édité B", "trend": "Stable",
                "risk_title_0": "Export PDF", "risk_text_0": "Risque édité",
                "risk_title_1": "", "risk_text_1": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], self.PPTX)
        self.assertRegex(
            response["Content-Disposition"], r'filename="rapport-direction-mon-projet-\d{4}-\d{2}-\d{2}\.pptx"'
        )
        deck = Presentation(BytesIO(response.content))
        text = " ".join(
            shape.text_frame.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame
        )
        self.assertIn("Texte édité A", text)
        self.assertIn("Risque édité", text)
        self.assertNotIn("Deux tickets restent", text)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_generate_without_ai_needs_no_answer(self, mock_get_jira):
        from pptx import Presentation

        self._login(mock_get_jira)

        response = self.client.post(
            reverse("sprint-management-report"), {"action": "generate", "provider": "none", "sprint": ["1", "2"]}
        )

        self.assertEqual(response["Content-Type"], self.PPTX)
        Presentation(BytesIO(response.content))

    @patch("sprint.views.get_connected_jira_for_user")
    def test_generate_without_a_matching_analysis_asks_again(self, mock_get_jira):
        self._login(mock_get_jira)
        url = reverse("sprint-management-report")
        self.client.post(url, {"action": "parse", "provider": "copilot", "sprint": ["1", "2"], "answer": self.ANSWER})

        response = self.client.post(
            url, {"action": "generate", "provider": "copilot", "sprint": ["1", "3"], "situation": "x"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alert-warning")
        self.assertNotEqual(response["Content-Type"], self.PPTX)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_single_sprint_report_uses_the_focus_and_its_trend(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.get(reverse("sprint-management-report"), {"sprint": "3", "focus": "3"})

        self.assertEqual(response.status_code, 200)
        dossier = response.context["dossier"]
        self.assertIn("Période : Sprint 3", dossier)
        for name in ("Sprint 1", "Sprint 2", "Sprint 3"):
            self.assertIn(f"## {name}", dossier)

    @patch("sprint.views.get_connected_jira_for_user")
    def test_scope_page_links_to_the_report(self, mock_get_jira):
        self._login(mock_get_jira)

        response = self.client.get(reverse("sprint-scope-flow", args=[3]))

        self.assertContains(response, "Rapport direction")
        self.assertContains(response, "sprint=3&amp;focus=3")
