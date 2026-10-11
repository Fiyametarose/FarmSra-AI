from django.test import TestCase, Client
from django.urls import reverse
from users.models import User
from notifications.models import Notification

class DashboardIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.farmer = User.objects.create_user(
            email="farmer@test.com",
            password="test",
            role=User.Role.FARMER,
            town="Eldoret",
            country="KE"
        )
        self.expert = User.objects.create_user(
            email="expert@test.com",
            password="test",
            role=User.Role.EXPERT
        )

    def test_dashboard_weather_controls(self):
        self.client.force_login(self.farmer)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Check if weather button and container exist
        self.assertContains(response, 'id="load-weather-btn"')
        self.assertContains(response, 'id="weather-container"')
        self.assertContains(response, 'loadWeather()')

    def test_notification_counts_displayed(self):
        self.client.force_login(self.farmer)
        # 1 unread notification
        Notification.objects.create(recipient=self.farmer, event_type=Notification.EventType.FARMER_ASSIGNED)

        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Should display notification count badge with '1'
        self.assertContains(response, '<span class="badge"')
        self.assertContains(response, '1</span>')

        # Expert notification count
        self.client.force_login(self.expert)
        Notification.objects.create(recipient=self.expert, event_type=Notification.EventType.EXPERT_ASSIGNED)
        response = self.client.get(reverse('expert-dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span class="badge"')
        self.assertContains(response, '1</span>')

    def test_language_persistence(self):
        # Set language to Swahili
        response = self.client.post(reverse('set_language'), {
            'language': 'sw',
            'next': '/dashboard/'
        })
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.farmer)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Lang attribute should be sw
        self.assertContains(response, '<html lang="sw">')
        # Dashbaord title translated to swahili
        self.assertContains(response, 'Dashibodi')
