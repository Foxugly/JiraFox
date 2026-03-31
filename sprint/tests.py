from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

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
