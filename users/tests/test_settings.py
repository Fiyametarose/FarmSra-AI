from django.test import TestCase
from django.urls import reverse
from users.models import User

class SettingsTests(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(
            email="farmer@test.com",
            password="pwd",
            first_name="John",
            last_name="Doe",
            role=User.Role.FARMER,
            country="KE",
            region="Nairobi",
            subcounty="Westlands",
            ward="Parklands",
            town="Nairobi",
        )
        self.client.login(email="farmer@test.com", password="pwd")

    def test_account_settings_update(self):
        url = reverse("settings-account")
        response = self.client.post(url, {
            "full_name": "Jane Smith",
            "email": "new@test.com",
        })
        self.assertRedirects(response, url)
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.first_name, "Jane")
        self.assertEqual(self.farmer.last_name, "Smith")
        self.assertEqual(self.farmer.email, "new@test.com")

    def test_account_settings_email_unique(self):
        User.objects.create_user(email="other@test.com", password="pwd", role=User.Role.FARMER)
        url = reverse("settings-account")
        response = self.client.post(url, {
            "full_name": "John Doe",
            "email": "other@test.com",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email address already exists.")

    def test_location_settings_update(self):
        url = reverse("settings-location")
        response = self.client.post(url, {
            "country": "ET",
            "region": "Addis Ababa",
            "subcounty": "Bole",
            "ward": "Bole",
            "town": "Addis Ababa",
            "locality": "Village",
        })
        self.assertRedirects(response, url)
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.country, "ET")
        self.assertEqual(self.farmer.region, "Addis Ababa")
