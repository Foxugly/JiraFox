from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from jira.exceptions import JIRAError

from customuser.models import CustomUser
from jiramodule.exceptions import JiraNotConfigured, describe_jira_error, is_transient_jira_error
from jiramodule.forms import JiraConfigurationForm
from jiramodule.models import JiraConfiguration
from jiramodule.selectors import get_current_jira_config_for_user
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jira_config_management import save_jira_config_form
from jiramodule.services.jira_config_service import get_connected_jira_for_user


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


class JiraConfigurationSelectionTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="carol", password="secret")

    def test_missing_current_config_raises_not_configured(self):
        with self.assertRaises(JiraNotConfigured):
            get_current_jira_config_for_user(self.user)

    def test_several_current_configs_raise_not_configured(self):
        JiraConfiguration.objects.create(user=self.user, jira_url="https://a.example/", current=True)
        JiraConfiguration.objects.create(user=self.user, jira_url="https://b.example/", current=True)
        with self.assertRaises(JiraNotConfigured):
            get_current_jira_config_for_user(self.user)

    def test_incomplete_config_lists_missing_fields(self):
        JiraConfiguration.objects.create(user=self.user, jira_url="https://a.example/", current=True)
        with self.assertRaises(JiraNotConfigured) as ctx:
            get_connected_jira_for_user(self.user)
        self.assertEqual(ctx.exception.missing_fields, ["jira_token", "jira_board_id"])

    def test_saving_form_as_current_unsets_previous_current(self):
        old = JiraConfiguration.objects.create(user=self.user, jira_url="https://old.example/", current=True)
        form = JiraConfigurationForm(data={"jira_url": "https://new.example/", "jira_token": "t", "current": "on"})
        self.assertTrue(form.is_valid(), form.errors)

        new = save_jira_config_form(form=form, user=self.user)

        old.refresh_from_db()
        self.assertFalse(old.current)
        self.assertTrue(new.current)

    def test_edit_form_keeps_token_when_left_empty_and_never_renders_it(self):
        config = JiraConfiguration.objects.create(user=self.user, jira_url="https://a.example/", jira_token="s3cr3t-token")
        form = JiraConfigurationForm(data={"jira_url": "https://a.example/", "jira_token": ""}, instance=config)
        self.assertNotIn("s3cr3t-token", str(form["jira_token"]))
        self.assertTrue(form.is_valid(), form.errors)

        saved = save_jira_config_form(form=form, user=self.user)

        self.assertEqual(saved.jira_token, "s3cr3t-token")


class JiraErrorDescriptionTests(SimpleTestCase):
    def test_description_never_contains_request_headers(self):
        request = SimpleNamespace(headers={"Authorization": "Bearer s3cr3t-token"}, text="")
        exc = JIRAError(text="Unauthorized", status_code=401, url="https://jira.example/rest", request=request)

        self.assertIn("s3cr3t-token", str(exc))  # the library's own message leaks it
        self.assertNotIn("s3cr3t-token", describe_jira_error(exc))
        self.assertIn("401", describe_jira_error(exc))

    def test_only_transient_errors_are_retryable(self):
        self.assertFalse(is_transient_jira_error(JIRAError(status_code=401)))
        self.assertFalse(is_transient_jira_error(JIRAError(status_code=404)))
        self.assertTrue(is_transient_jira_error(JIRAError(status_code=503)))
        self.assertTrue(is_transient_jira_error(requests.ConnectionError()))


class JiraManagerConnectionTests(TestCase):
    def setUp(self):
        cache.clear()
        user = CustomUser.objects.create_user(username="dave", password="secret")
        self.config = JiraConfiguration.objects.create(
            user=user, jira_url="https://jira.example/", jira_token="tok", jira_board_id=1, current=True
        )

    def _fake_client(self):
        client = MagicMock()
        client._version = (9, 12, 0)
        client.deploymentType = "Server"
        client.fields.return_value = [{"id": "customfield_10002", "name": "Story Points"}]
        return client

    @patch("jiramodule.services.jira_client.JIRA")
    def test_second_connect_reuses_cached_metadata(self, mock_jira_cls):
        mock_jira_cls.return_value = self._fake_client()

        JiraManager(self.config).connect()
        second = JiraManager(self.config)
        second.connect()

        self.assertEqual(mock_jira_cls.return_value.myself.call_count, 1)
        self.assertFalse(mock_jira_cls.call_args.kwargs["get_server_info"])
        self.assertEqual(mock_jira_cls.call_args.kwargs["timeout"], JiraManager.JIRA_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(second._story_points_field_id, "customfield_10002")

    @patch("jiramodule.services.jira_client.time.sleep")
    @patch("jiramodule.services.jira_client.JIRA")
    def test_authentication_errors_are_not_retried(self, mock_jira_cls, mock_sleep):
        client = self._fake_client()
        client.myself.side_effect = JIRAError(status_code=401)
        mock_jira_cls.return_value = client

        with self.assertRaises(JIRAError):
            JiraManager(self.config).connect()

        self.assertEqual(client.myself.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("jiramodule.services.jira_client.time.sleep")
    @patch("jiramodule.services.jira_client.JIRA")
    def test_network_errors_are_retried(self, mock_jira_cls, mock_sleep):
        client = self._fake_client()
        client.myself.side_effect = [requests.ConnectionError(), {"name": "dave"}]
        mock_jira_cls.return_value = client

        JiraManager(self.config).connect()

        self.assertEqual(client.myself.call_count, 2)


class JiraErrorPageTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(username="erin", password="secret")
        self.client.force_login(self.user)

    def test_page_without_config_shows_configuration_page(self):
        response = self.client.get(reverse("sprint-list"))

        self.assertEqual(response.status_code, 409)
        self.assertTemplateUsed(response, "jira/jira_unavailable.html")
        self.assertContains(response, reverse("jira_config_list"), status_code=409)

    def test_api_without_config_returns_json(self):
        response = self.client.get(reverse("dashboard-items-api"))

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])

    @patch("jiramodule.services.jira_config_service.JiraManager.connect")
    def test_connection_failure_shows_error_page_without_secrets(self, mock_connect):
        request = SimpleNamespace(headers={"Authorization": "Bearer s3cr3t-token"}, text="")
        mock_connect.side_effect = JIRAError(text="Unauthorized", status_code=401, request=request)
        JiraConfiguration.objects.create(
            user=self.user, jira_url="https://jira.example/", jira_token="s3cr3t-token", jira_board_id=1, current=True
        )

        response = self.client.get(reverse("sprint-list"))

        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "jira/jira_unavailable.html")
        self.assertContains(response, "401", status_code=503)
        self.assertNotContains(response, "s3cr3t-token", status_code=503)


class JiraManagerQueryTests(TestCase):
    def test_throughput_counts_every_done_category_status(self):
        user = CustomUser.objects.create_user(username="frank", password="secret")
        config = JiraConfiguration.objects.create(user=user, jira_url="https://jira.example/", jira_project_id=7)
        manager = JiraManager(config)

        with patch.object(manager, "run_jql", return_value=[]) as run_jql:
            manager.throughput_7d()

        jql = run_jql.call_args.args[0]
        self.assertIn("statusCategory = Done", jql)
        self.assertNotIn("status = Done", jql)

    def test_serialized_issue_carries_the_status_category_key(self):
        user = CustomUser.objects.create_user(username="gina", password="secret")
        manager = JiraManager(JiraConfiguration.objects.create(user=user, jira_url="https://jira.example/"))
        status = SimpleNamespace(name="Closed", statusCategory=SimpleNamespace(key="done", name="Terminé"))
        issue = SimpleNamespace(key="ABC-1", fields=SimpleNamespace(status=status))

        self.assertEqual(manager._ticket_info_from_issue(issue)["status_category"], "done")
