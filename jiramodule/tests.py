from django.test import TestCase

from customuser.models import CustomUser
from jiramodule.models import JiraConfiguration


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
