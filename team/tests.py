from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from dev.models import Dev
from jiramodule.models import JiraConfiguration
from team.models import Team, TeamDev
from team.services import parse_dev_update, parse_teamdev_order


class TeamServicesTests(SimpleTestCase):
    def test_parse_dev_update_returns_clean_values(self):
        field, value = parse_dev_update("workload", "12")
        self.assertEqual(field, "workload")
        self.assertEqual(value, 12)

    def test_parse_dev_update_rejects_unknown_field(self):
        with self.assertRaisesMessage(ValueError, "Invalid field"):
            parse_dev_update("unknown", "12")

    def test_parse_dev_update_accepts_the_jira_username(self):
        self.assertEqual(parse_dev_update("jira_username", " TDPTAV "), ("jira_username", "TDPTAV"))

    def test_parse_teamdev_order_rejects_invalid_ids(self):
        with self.assertRaisesMessage(ValueError, "Invalid ids"):
            parse_teamdev_order(["1", "oops"])


class TeamViewsAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner", password="secret")
        self.intruder = get_user_model().objects.create_user(username="intruder", password="secret")
        self.team = Team.objects.create(name="Core")
        JiraConfiguration.objects.create(user=self.owner, team=self.team)
        self.dev = Dev.objects.create(name="Jane")
        self.teamdev = TeamDev.objects.create(team=self.team, dev=self.dev, order=0)

    def test_owner_can_update_dev(self):
        self.client.force_login(self.owner)

        response = self.client.post(reverse("update_dev", args=[self.dev.id]), {"field": "holidays", "value": "8"})

        self.assertEqual(response.status_code, 200)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.holidays, 8)

    def test_other_user_cannot_touch_the_team(self):
        self.client.force_login(self.intruder)

        responses = [
            self.client.post(reverse("update_dev", args=[self.dev.id]), {"field": "holidays", "value": "8"}),
            self.client.post(reverse("create_dev", args=[self.team.id]), {"name": "Mallory"}),
            self.client.post(reverse("delete_dev_from_team", args=[self.teamdev.id])),
            self.client.post(reverse("reorder_teamdevs", args=[self.team.id]), {"ids[]": [self.teamdev.id]}),
        ]

        self.assertEqual([response.status_code for response in responses], [404, 404, 404, 404])
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.holidays, 0)
        self.assertTrue(TeamDev.objects.filter(pk=self.teamdev.pk).exists())
        self.assertFalse(Dev.objects.filter(name="Mallory").exists())
