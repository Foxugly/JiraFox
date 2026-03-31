from django.test import SimpleTestCase

from customuser.models import CustomUser


class CustomUserModelTests(SimpleTestCase):
    def test_string_representation_uses_username(self):
        user = CustomUser(username="alice")
        self.assertEqual(str(user), "alice")
