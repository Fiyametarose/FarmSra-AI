from django.contrib.auth import get_user_model
from django.contrib.auth import authenticate
from django.test import TestCase
from django.urls import reverse


class AuthenticationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            "farmer@example.com", "safe-test-password", first_name="Amina"
        )

    def test_login_is_case_insensitive(self):
        response = self.client.post(
            reverse("login"),
            {"email": "FARMER@EXAMPLE.COM", "password": "safe-test-password"},
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_backend_supports_django_admin_username_argument(self):
        administrator = self.User.objects.create_superuser(
            "admin@example.com", "safe-admin-password"
        )
        authenticated = authenticate(
            username="ADMIN@EXAMPLE.COM", password="safe-admin-password"
        )
        self.assertEqual(authenticated, administrator)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("welcome"))

    def test_public_registration_always_creates_farmer(self):
        response = self.client.post(
            reverse("register"),
            {
                "full_name": "Tadesse Bekele",
                "email": "NEW@Example.com",
                "password1": "a-secure-test-password-42",
                "password2": "a-secure-test-password-42",
                "country": "ET",
                "region": "Oromia",
                "subcounty": "East Shewa",
                "ward": "Ada",
                "town": "Bishoftu",
                "locality": "",
                "terms_accepted": "on",
                "role": self.User.Role.EXPERT,
            },
        )
        user = self.User.objects.get(email="new@example.com")
        self.assertEqual(user.role, self.User.Role.FARMER)
        self.assertRedirects(response, reverse("dashboard"))

    def test_expert_login_routes_to_dashboard(self):
        expert = self.User.objects.create_user(
            "expert@example.com",
            "safe-expert-password",
            role=self.User.Role.EXPERT,
        )
        response = self.client.post(
            reverse("login"),
            {"email": expert.email, "password": "safe-expert-password"},
        )
        self.assertRedirects(
            response,
            reverse("expert-dashboard"),
            fetch_redirect_response=False,
        )

    def test_staff_administrator_login_routes_to_admin(self):
        administrator = self.User.objects.create_superuser(
            "administrator@example.com", "safe-admin-password"
        )
        response = self.client.post(
            reverse("login"),
            {"email": administrator.email, "password": "safe-admin-password"},
        )
        self.assertRedirects(
            response,
            reverse("admin:index"),
            fetch_redirect_response=False,
        )
