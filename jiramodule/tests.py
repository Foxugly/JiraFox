import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from jira import JIRA
from jira.client import ResultList
from jira.exceptions import JIRAError
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils.translation import gettext as _

from customuser.models import CustomUser
from jiramodule.exceptions import JiraNotConfigured, describe_jira_error, is_transient_jira_error
from jiramodule.forms import JiraConfigurationForm
from jiramodule.models import JiraConfiguration
from jiramodule.selectors import get_current_jira_config_for_user
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jira_config_management import save_jira_config_form
from jiramodule.services.jira_config_service import get_connected_jira_for_user
from jiramodule.services.bulk_operations import (
    apply_bulk_label_preview,
    build_bulk_label_preview,
    parse_labels,
)
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jql import JqlBuilder
from sprint.report import SprintWorkbookBuilder


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


class JiraManagerSprintListTests(SimpleTestCase):
    def test_list_sprints_reads_every_page(self):
        # Jira returns at most 50 sprints per page, oldest first.
        def fake_get_json(path, params=None, base=None, use_post=False):
            start = params.get("startAt", 0)
            values = [{"id": i, "name": f"S{i}", "state": "closed"} for i in range(start, min(start + 50, 120))]
            return {"startAt": start, "maxResults": 50, "isLast": start + 50 >= 120, "values": values}

        manager = JiraManager(SimpleNamespace(jira_url="https://jira.example/"))
        manager._jira = JIRA(server="https://jira.example", get_server_info=False)
        with patch.object(JIRA, "_get_json", side_effect=fake_get_json):
            sprints = manager.list_sprints(638, state="closed")

        self.assertEqual(len(sprints), 120)
        self.assertEqual(sprints[-1]["name"], "S119")


class JiraManagerRunJqlTests(SimpleTestCase):
    def _manager(self):
        self.search_kwargs = []
        fields = SimpleNamespace(
            summary="Issue summary",
            project=SimpleNamespace(key="ABC"),
            issuetype=SimpleNamespace(name="Task"),
            status=SimpleNamespace(name="Open", statusCategory=SimpleNamespace(name="To Do", key="new")),
            assignee=None,
            labels=[],
            timetracking=SimpleNamespace(
                originalEstimateSeconds=7200, remainingEstimateSeconds=3600, timeSpentSeconds=3600
            ),
        )

        def search_issues(jql, **kwargs):
            self.search_kwargs.append(kwargs)
            return [SimpleNamespace(key="ABC-1", fields=fields)]

        manager = JiraManager(SimpleNamespace(jira_url="https://jira.example/"))
        manager._jira = SimpleNamespace(search_issues=search_issues)
        return manager

    def test_without_history_skips_the_changelog(self):
        manager = self._manager()

        result = manager.run_jql("project = ABC", history=False)

        self.assertIsNone(self.search_kwargs[0]["expand"])
        self.assertIn("timetracking", self.search_kwargs[0]["fields"])
        self.assertEqual(result[0]["estimation"]["original_estimate_hours"], 2.0)
        self.assertEqual(result[0]["remaining_hours"], 1.0)
        self.assertNotIn("kanban_metrics", result[0])

    def test_capacity_sheet_queries_skip_the_changelog(self):
        manager = self._manager()
        builder = SprintWorkbookBuilder.__new__(SprintWorkbookBuilder)
        builder.jira = manager
        builder._jql_cache = {}

        list(builder._run_jql_cached("project = ABC"))

        self.assertIsNone(self.search_kwargs[0]["expand"])


class JiraManagerStatusHistoryTests(SimpleTestCase):
    def test_status_histories_are_fetched_by_chunks_of_keys(self):
        queries = []

        def search_issues(jql, **kwargs):
            queries.append((jql, kwargs))
            change = SimpleNamespace(field="status", fromString="To Do", toString="Done")
            history = SimpleNamespace(created="2026-03-05T10:00:00.000+0000", items=[change])
            return [
                SimpleNamespace(
                    key=key,
                    fields=SimpleNamespace(status=SimpleNamespace(name="Done"), created="2026-02-01"),
                    changelog=SimpleNamespace(histories=[history]),
                )
                for key in re.findall(r'"([^"]+)"', jql)
            ]

        manager = JiraManager(SimpleNamespace(jira_url="https://jira.example/"))
        manager._jira = SimpleNamespace(search_issues=search_issues)

        result = manager.get_status_histories([f"ABC-{i}" for i in range(60)])

        self.assertEqual(len(result), 60)
        self.assertEqual(len(queries), 3)
        self.assertEqual(queries[0][1]["fields"], "status,created")
        self.assertEqual(queries[0][1]["expand"], "changelog")
        self.assertEqual(
            result["ABC-59"]["status_changes"],
            [{"at": "2026-03-05T10:00:00.000+0000", "from": "To Do", "to": "Done"}],
        )


class JiraManagerWorklogSearchTests(SimpleTestCase):
    def test_issues_with_worklogs_are_fetched_in_light_steps(self):
        issues = [
            SimpleNamespace(key=f"ABC-{i}", fields=SimpleNamespace(timetracking=None, status=None))
            for i in range(120)
        ]
        searches, worklog_calls = [], []

        def search_issues(jql, startAt=0, maxResults=50, **kwargs):
            searches.append((jql, startAt, maxResults, kwargs))
            if jql.startswith("key in"):
                change = SimpleNamespace(field="status", fromString="To Do", toString="Done")
                history = SimpleNamespace(created="2026-03-05T10:00:00.000+0000", items=[change])
                return [
                    SimpleNamespace(key=key, fields=SimpleNamespace(status=None, created=None),
                                    changelog=SimpleNamespace(histories=[history]))
                    for key in re.findall(r'"([^"]+)"', jql)
                ]
            return ResultList(issues[startAt:startAt + maxResults], startAt, maxResults, len(issues))

        def worklogs(key):
            worklog_calls.append(key)
            return [SimpleNamespace(author=None, started="2026-03-04T10:00:00.000+0000", timeSpentSeconds=3600)]

        manager = JiraManager(SimpleNamespace(jira_url="https://jira.example/"))
        manager._jira = SimpleNamespace(search_issues=search_issues, worklogs=worklogs)

        result = manager.get_sprint_issues_with_worklogs(2446)

        self.assertEqual(len(result), 120)
        sprint_pages = [search for search in searches if search[0].startswith("sprint")]
        self.assertEqual([(start, size) for _, start, size, _ in sprint_pages], [(0, 50), (50, 50), (100, 50)])
        # The sprint search stays light: no worklog field, no changelog.
        self.assertNotIn("worklog", sprint_pages[0][3]["fields"])
        self.assertNotIn("expand", sprint_pages[0][3])
        self.assertEqual(len(searches) - len(sprint_pages), 6)  # 120 status histories, 20 per search
        self.assertEqual(len(worklog_calls), 120)
        self.assertEqual(result[0]["status_changes"][0]["to"], "Done")
        self.assertEqual(result[0]["worklogs"][0]["seconds"], 3600)


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
        # The labels are re-read from Jira, never taken from the (possibly stale) preview.
        self.assertIsNone(calls[0][1]["current_labels"])


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
