from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext as _

from jiramodule.models import JiraConfiguration


class PublicPagesTests(TestCase):
    def test_home_page_renders(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home.html")

    def test_about_page_renders(self):
        response = self.client.get(reverse("about"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "about.html")

    def test_password_reset_page_stays_public(self):
        response = self.client.get(reverse("account_reset_password"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/password_reset.html")

    def test_login_page_renders(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login.html")

    def test_signup_page_renders(self):
        response = self.client.get(reverse("account_signup"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup.html")

    def test_password_reset_done_page_renders(self):
        response = self.client.get(reverse("account_reset_password_done"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/password_reset_done.html")


class AuthPagesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secret123!")
        self.client.force_login(self.user)

    def test_password_change_page_renders_for_authenticated_user(self):
        response = self.client.get(reverse("account_change_password"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/password_change.html")

    def test_settings_page_renders_for_authenticated_user(self):
        JiraConfiguration.objects.create(
            user=self.user,
            jira_url="https://jira.example",
            jira_board_id=42,
            jira_project_id=7,
            current=True,
        )

        response = self.client.get(reverse("settings"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "settings.html")
        self.assertContains(response, "jira.example")

    def test_settings_post_updates_user_profile(self):
        response = self.client.post(
            reverse("settings"),
            {
                "first_name": "Alice",
                "last_name": "Admin",
                "email": "alice@example.com",
                "language": "nl",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Alice")
        self.assertEqual(self.user.last_name, "Admin")
        self.assertEqual(self.user.email, "alice@example.com")
        self.assertEqual(self.user.language, "nl")

    def test_language_switch_updates_user_preference(self):
        response = self.client.post(
            reverse("set_language_preference"),
            {"language": "nl", "next": reverse("settings")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("settings"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, "nl")
        self.assertEqual(response.cookies["django_language"].value, "nl")

    def test_user_language_is_activated_on_request(self):
        self.user.language = "nl"
        self.user.save(update_fields=["language"])

        response = self.client.get(reverse("settings"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "nl")

    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_dashboard_page_renders_with_current_sprint(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            get_current_sprint=lambda: {"id": 12, "name": "Sprint 12", "url": "https://jira.example/sprint/12"},
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard.html")
        self.assertTemplateUsed(response, "dashboard/_toolbar.html")
        self.assertContains(response, "Sprint 12")

    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_dashboard_page_returns_404_without_current_sprint(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(get_current_sprint=lambda: None)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 404)

    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_query_page_renders_presets(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            cfg=SimpleNamespace(jira_project_id="ABC"),
            get_current_sprint=lambda: {"id": 12, "name": "Sprint 12"},
            get_url_jql=lambda query: f"https://jira.example/issues/?jql={query}",
        )

        response = self.client.get(reverse("query"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "query.html")
        self.assertContains(response, _("Current sprint issues"))

    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_query_page_runs_jql(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            cfg=SimpleNamespace(jira_project_id="ABC"),
            get_current_sprint=lambda: {"id": 12, "name": "Sprint 12"},
            get_url_jql=lambda query: f"https://jira.example/issues/?jql={query}",
            run_jql=lambda jql, max_results=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "issueType": "Task",
                    "state": "Closed",
                    "statusCategory": "Done",
                    "assignee": None,
                    "url": "https://jira.example/browse/ABC-1",
                }
            ],
        )

        response = self.client.get(reverse("query"), {"jql": "project = ABC", "max_results": "25"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ABC-1")
        self.assertContains(response, "Issue title")

    @patch("JiraFox.views.get_current_sprint_with_issues")
    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_dashboard_summary_api_returns_json_payload(self, mock_get_jira, mock_get_sprint):
        mock_get_jira.return_value = SimpleNamespace(
            throughput_7d=lambda: [1, 2, 3],
            get_url_jql=lambda query: f"https://jira.example/issues/?jql={query}",
        )
        mock_get_sprint.return_value = (
            {"id": 12, "name": "Sprint 12"},
            [
                {"key": "ABC-1", "state": "In Progress", "kanban_metrics": {"current_wip_age_hours": 36, "current_wip_age_days": 5, "cycle_time_days": 3, "lead_time_days": 4}},
                {"key": "ABC-2", "state": "Done", "statusCategory": "Done", "estimation": {"story_points": 3}},
                {"key": "ABC-3", "state": "Done", "statusCategory": "Done", "estimation": {"story_points": None}},
                {"key": "ABC-4", "state": "Closed", "statusCategory": "Done", "estimation": {"story_points": 5}},
            ],
        )

        response = self.client.get(reverse("dashboard-summary-api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("In Progress", payload["wia"]["statuses"])
        self.assertEqual(payload["weightedThroughput"]["distribution"], {"labels": ["0", "3", "5"], "counts": [1, 1, 1]})
        done_bottleneck = next(item for item in payload["bottlenecks"] if item["status"] == "Done")
        self.assertEqual(done_bottleneck["count"], 3)
        self.assertEqual(done_bottleneck["count"], payload["weightedThroughput"]["done_count"])
        self.assertIn("links", payload)

    @patch("JiraFox.views.get_current_sprint_with_issues")
    @patch("JiraFox.views.get_connected_jira_for_user")
    def test_dashboard_items_api_returns_items(self, mock_get_jira, mock_get_sprint):
        mock_get_jira.return_value = SimpleNamespace()
        mock_get_sprint.return_value = (
            {"id": 12},
            [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "state": "In Progress",
                    "assignee": {"displayName": "Jane Doe"},
                    "url": "https://jira.example/browse/ABC-1",
                    "kanban_metrics": {"current_wip_age_days": 6},
                }
            ],
        )

        response = self.client.get(reverse("dashboard-items-api"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["key"], "ABC-1")
