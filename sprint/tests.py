from django.test import SimpleTestCase

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
