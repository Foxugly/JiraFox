from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext as _

from customuser.models import CustomUser
from jiramodule.models import JiraConfiguration
from jiramodule.services.bulk_operations import (
    apply_bulk_label_preview,
    build_bulk_label_preview,
    parse_labels,
)
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jql import JqlBuilder


class JqlBuilderTests(TestCase):
    def test_builds_common_sprint_query(self):
        jql = (
            JqlBuilder.sprint(2420)
            .standard_issue_types()
            .status_category("Done")
            .order_by("resolved DESC")
            .build()
        )

        self.assertEqual(
            jql,
            '(sprint = 2420) AND (issuetype in standardIssueTypes()) AND (statusCategory = "Done") ORDER BY resolved DESC',
        )

    def test_project_key_is_quoted(self):
        self.assertEqual(JqlBuilder.project("BUSOPENLAM").build(), '(project = "BUSOPENLAM")')


class JiraManagerSerializationTests(TestCase):
    def test_ticket_info_includes_status_category(self):
        user = CustomUser.objects.create_user(username="jane", password="secret")
        cfg = JiraConfiguration.objects.create(user=user, jira_url="https://jira.example/")
        manager = JiraManager(cfg)

        category = type("Category", (), {"name": "Done"})()
        status = type("Status", (), {"name": "Closed", "statusCategory": category})()
        fields = type(
            "Fields",
            (),
            {
                "summary": "Issue summary",
                "project": type("Project", (), {"key": "ABC"})(),
                "issuetype": type("IssueType", (), {"name": "Task"})(),
                "status": status,
                "labels": ["old"],
                "assignee": None,
            },
        )()
        issue = type("Issue", (), {"key": "ABC-1", "fields": fields})()

        result = manager._ticket_info_from_issue(issue)

        self.assertEqual(result["state"], "Closed")
        self.assertEqual(result["statusCategory"], "Done")
        self.assertEqual(result["labels"], ["old"])


class BulkOperationsServiceTests(TestCase):
    def test_parse_labels_deduplicates_commas_and_newlines(self):
        self.assertEqual(parse_labels("fastlane, ready\nfastlane"), ["fastlane", "ready"])

    def test_bulk_label_preview_builds_change_plan(self):
        jira = SimpleNamespace(
            run_jql=lambda jql, max_results=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "state": "Open",
                    "statusCategory": "In Progress",
                    "url": "https://jira.example/browse/ABC-1",
                    "labels": ["old", "keep"],
                }
            ]
        )

        preview = build_bulk_label_preview(
            jira,
            jql="project = ABC",
            add_labels_raw="new",
            remove_labels_raw="old",
            limit=25,
        )

        self.assertEqual(preview.items[0]["next_labels"], ["keep", "new"])
        self.assertTrue(preview.items[0]["changed"])

    def test_apply_bulk_label_preview_calls_jira_update(self):
        calls = []
        jira = SimpleNamespace(
            update_issue_labels=lambda issue_key, **kwargs: calls.append((issue_key, kwargs))
        )
        payload = {
            "add_labels": ["new"],
            "remove_labels": ["old"],
            "items": [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "current_labels": ["old"],
                    "next_labels": ["new"],
                    "changed": True,
                }
            ],
        }

        results = apply_bulk_label_preview(jira, payload)

        self.assertTrue(results[0]["ok"])
        self.assertEqual(calls[0][0], "ABC-1")


class BulkOperationsViewsTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="bulk", password="secret")
        self.client.force_login(self.user)

    @patch("jiramodule.views.get_connected_jira_for_user")
    def test_bulk_operations_page_renders(self, mock_get_jira):
        response = self.client.get(reverse("jira_bulk_operations"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "jira/bulk_operations.html")
        self.assertContains(response, _("Available operations"))

    @patch("jiramodule.views.get_connected_jira_for_user")
    def test_bulk_labels_preview_and_apply(self, mock_get_jira):
        calls = []
        mock_get_jira.return_value = SimpleNamespace(
            run_jql=lambda jql, max_results=None, full=None: [
                {
                    "key": "ABC-1",
                    "summary": "Issue title",
                    "state": "Open",
                    "statusCategory": "In Progress",
                    "url": "https://jira.example/browse/ABC-1",
                    "labels": ["old"],
                }
            ],
            update_issue_labels=lambda issue_key, **kwargs: calls.append((issue_key, kwargs)),
        )

        preview_response = self.client.post(
            reverse("jira_bulk_labels_preview"),
            {
                "jql": "project = ABC",
                "add_labels": "new",
                "remove_labels": "old",
                "limit": "10",
            },
            follow=True,
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "ABC-1")

        apply_response = self.client.post(reverse("jira_bulk_labels_apply"), follow=True)

        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(calls[0][0], "ABC-1")
        self.assertContains(apply_response, _("Updated"))


class JiraConfigurationModelTests(TestCase):
    def test_set_as_current_clears_previous_current_config(self):
        user = CustomUser.objects.create_user(username="alice", password="secret")
        old_config = JiraConfiguration.objects.create(user=user, jira_url="https://jira.old", current=True)
        new_config = JiraConfiguration.objects.create(user=user, jira_url="https://jira.new", current=False)

        new_config.set_as_current()

        old_config.refresh_from_db()
        new_config.refresh_from_db()
        self.assertFalse(old_config.current)
        self.assertTrue(new_config.current)

    def test_mark_test_result_truncates_error(self):
        user = CustomUser.objects.create_user(username="bob", password="secret")
        config = JiraConfiguration.objects.create(user=user, jira_url="https://jira.local")

        config.mark_test_result(ok=False, error="x" * 6000)
        config.refresh_from_db()

        self.assertFalse(config.last_test_ok)
        self.assertEqual(len(config.last_test_error), 5000)
