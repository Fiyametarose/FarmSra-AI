from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase


class UserManagerTests(TestCase):
    def setUp(self):
        self.User = get_user_model()

    def test_create_user_uses_email_and_defaults_to_farmer(self):
        user = self.User.objects.create_user("Farmer@EXAMPLE.com", "test-password")

        self.assertEqual(user.email, "farmer@example.com")
        self.assertEqual(user.role, self.User.Role.FARMER)
        self.assertTrue(user.check_password("test-password"))
        self.assertFalse(user.is_staff)

    def test_create_user_requires_email(self):
        with self.assertRaisesMessage(ValueError, "email address"):
            self.User.objects.create_user("", "test-password")

    def test_email_uniqueness_is_case_insensitive(self):
        self.User.objects.create_user("farmer@example.com", "test-password")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.User.objects.create_user("FARMER@example.com", "test-password")

    def test_create_superuser_is_an_administrator(self):
        user = self.User.objects.create_superuser("expert@example.com", "test-password")

        self.assertEqual(user.role, self.User.Role.ADMINISTRATOR)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_create_superuser_rejects_expert_role(self):
        with self.assertRaisesMessage(ValueError, "administrator role"):
            self.User.objects.create_superuser(
                "farmer@example.com",
                "test-password",
                role=self.User.Role.EXPERT,
            )

    def test_expert_does_not_implicitly_receive_admin_privileges(self):
        expert = self.User.objects.create_user(
            "expert@example.com", "test-password", role=self.User.Role.EXPERT
        )
        self.assertFalse(expert.is_staff)
        self.assertFalse(expert.is_superuser)
