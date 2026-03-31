from django.test import SimpleTestCase

from team.services import parse_dev_int_update, parse_teamdev_order


class TeamServicesTests(SimpleTestCase):
    def test_parse_dev_int_update_returns_clean_values(self):
        field, value = parse_dev_int_update("workload", "12")
        self.assertEqual(field, "workload")
        self.assertEqual(value, 12)

    def test_parse_dev_int_update_rejects_unknown_field(self):
        with self.assertRaisesMessage(ValueError, "Invalid field"):
            parse_dev_int_update("unknown", "12")

    def test_parse_teamdev_order_rejects_invalid_ids(self):
        with self.assertRaisesMessage(ValueError, "Invalid ids"):
            parse_teamdev_order(["1", "oops"])
