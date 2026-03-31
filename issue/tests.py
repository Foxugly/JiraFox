from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class IssuePagesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bob", password="secret")
        self.client.force_login(self.user)

    @patch("issue.views.get_connected_jira_for_user")
    def test_issue_detail_page_renders(self, mock_get_jira):
        mock_get_jira.return_value = SimpleNamespace(
            get_issue=lambda jira_key: {
                "key": jira_key,
                "project": "BKL1",
                "summary": "Example issue",
                "issueType": "Bug",
                "state": "In Progress",
                "url": f"https://jira.example/browse/{jira_key}",
                "kanban_metrics": {
                    "lead_time_days": 3,
                    "lead_time_hours": 24,
                    "cycle_time_days": 2,
                    "cycle_time_hours": 16,
                    "current_wip_age_days": 1,
                    "current_wip_age_hours": 8,
                    "created_display": "2026-03-20",
                    "first_in_progress_display": "2026-03-21",
                    "time_in_status": [],
                },
                "assignee": {"displayName": "Jane Doe"},
                "estimation": {"story_points": 3, "original_estimate_hours": 8},
                "remaining_hours": 4,
                "spent_hours": 2,
                "history": [],
            }
        )

        response = self.client.get(reverse("issue-detail", args=["BKL1-3071"]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "issue/issue_detail.html")
        self.assertContains(response, "Example issue")
