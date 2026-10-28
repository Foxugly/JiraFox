from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from dev.models import Dev
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
        self.team.members.add(self.owner)
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


class ProjectPagesTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="secret")
        self.bob = user_model.objects.create_user(username="bob", password="secret")
        self.carl = user_model.objects.create_user(username="carl", password="secret")
        self.core = Team.objects.create(name="Core", jira_url="https://jira.example/", jira_board_id=638)
        self.core.members.add(self.alice, self.bob)
        self.ops = Team.objects.create(name="Ops", jira_url="https://jira.example/", jira_board_id=12)
        self.ops.members.add(self.alice)
        self.secret = Team.objects.create(name="Secret")
        self.secret.members.add(self.carl)
        self.client.force_login(self.alice)

    def test_header_lists_my_projects_and_switches_the_current_one(self):
        response = self.client.get(reverse("project_list"))

        self.assertContains(response, 'name="project_id"')
        self.assertContains(response, ">Core</option>")
        self.assertContains(response, ">Ops</option>")
        self.assertNotContains(response, ">Secret</option>")

        response = self.client.post(reverse("project_set_current"), {"project_id": self.ops.id, "next": "/sprint/"})

        self.assertRedirects(response, "/sprint/", fetch_redirect_response=False)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.current_project, self.ops)

    def test_cannot_switch_to_or_open_a_project_i_am_not_a_member_of(self):
        responses = [
            self.client.post(reverse("project_set_current"), {"project_id": self.secret.id}),
            self.client.get(reverse("project_edit", args=[self.secret.id])),
            self.client.get(reverse("project_devs", args=[self.secret.id])),
            self.client.post(reverse("project_delete", args=[self.secret.id])),
        ]

        self.assertEqual([response.status_code for response in responses], [404, 404, 404, 404])
        self.assertTrue(Team.objects.filter(pk=self.secret.pk).exists())

    def test_creating_a_project_makes_me_its_member_and_switches_to_it(self):
        response = self.client.post(
            reverse("project_create"),
            {"name": "New", "jira_url": "https://jira.example/", "jira_board_id": "5", "jira_project_id": "",
             "jira_recurrent_epic_key": ""},
        )

        project = Team.objects.get(name="New")
        self.assertRedirects(response, reverse("project_edit", args=[project.id]))
        self.assertEqual(list(project.members.all()), [self.alice])
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.current_project, project)

    def test_members_are_added_and_removed_but_never_the_last_one(self):
        self.client.post(reverse("project_add_member", args=[self.ops.id]), {"user_id": self.carl.id})
        self.assertEqual(set(self.ops.members.all()), {self.alice, self.carl})

        self.client.post(reverse("project_remove_member", args=[self.ops.id, self.carl.id]))
        self.assertEqual(list(self.ops.members.all()), [self.alice])

        # Alice is the last member: leaving would orphan the project.
        self.client.post(reverse("project_remove_member", args=[self.ops.id, self.alice.id]))
        self.assertEqual(list(self.ops.members.all()), [self.alice])

        # On Core she can leave, Bob stays.
        response = self.client.post(reverse("project_remove_member", args=[self.core.id, self.alice.id]))
        self.assertRedirects(response, reverse("project_list"))
        self.assertEqual(list(self.core.members.all()), [self.bob])

    def test_devs_page_lists_the_project_developers_for_any_member(self):
        TeamDev.objects.create(team=self.core, dev=Dev.objects.create(name="Jane"), order=0)
        self.client.force_login(self.bob)

        response = self.client.get(reverse("project_devs", args=[self.core.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane")
        self.assertContains(response, reverse("create_dev", args=[self.core.id]))
        # Bob, a member, may edit the shared developers.
        dev = Dev.objects.get(name="Jane")
        self.client.post(reverse("update_dev", args=[dev.id]), {"field": "holidays", "value": "4"})
        dev.refresh_from_db()
        self.assertEqual(dev.holidays, 4)
