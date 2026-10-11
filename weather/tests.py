from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch, MagicMock
from users.models import User
import socket
import json
from django.core.cache import cache

class WeatherTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.farmer = User.objects.create_user(
            email="farmer@test.com",
            password="test",
            role=User.Role.FARMER,
            town="Eldoret",
            country="KE",
            region="Uasin Gishu"
        )
        self.expert = User.objects.create_user(
            email="expert@test.com",
            password="test",
            role=User.Role.EXPERT
        )

    def test_weather_requires_login(self):
        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 302)

    def test_weather_requires_farmer_role(self):
        self.client.force_login(self.expert)
        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 403)

    @patch('weather.services.urllib.request.urlopen')
    def test_geocode_single_match(self, mock_urlopen):
        self.client.force_login(self.farmer)

        # Mock geocode response
        geo_response = MagicMock()
        geo_response.read.return_value = json.dumps({
            "results": [
                {"id": 1, "name": "Eldoret", "country_code": "KE", "latitude": 0.5, "longitude": 35.2}
            ]
        }).encode('utf-8')
        geo_mock = MagicMock()
        geo_mock.__enter__.return_value = geo_response

        # Mock weather response
        weather_response = MagicMock()
        weather_response.read.return_value = json.dumps({
            "current": {"temperature_2m": 25.0, "weather_code": 0},
            "daily": {"time": [], "weather_code": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}
        }).encode('utf-8')
        weather_mock = MagicMock()
        weather_mock.__enter__.return_value = weather_response

        mock_urlopen.side_effect = [geo_mock, weather_mock]

        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/widget.html')
        self.assertIn('weather', response.context)
        self.assertEqual(response.context['weather']['temperature'], 25.0)

    @patch('weather.services.urllib.request.urlopen')
    def test_geocode_multiple_match_region_resolution(self, mock_urlopen):
        self.client.force_login(self.farmer)

        geo_response = MagicMock()
        geo_response.read.return_value = json.dumps({
            "results": [
                {"id": 1, "name": "Eldoret", "country_code": "KE", "admin1": "Nairobi", "latitude": 1.0, "longitude": 1.0},
                {"id": 2, "name": "Eldoret", "country_code": "KE", "admin1": "Uasin Gishu", "latitude": 0.5, "longitude": 35.2}
            ]
        }).encode('utf-8')
        geo_mock = MagicMock()
        geo_mock.__enter__.return_value = geo_response

        weather_response = MagicMock()
        weather_response.read.return_value = json.dumps({
            "current": {"temperature_2m": 22.0, "weather_code": 0},
            "daily": {}
        }).encode('utf-8')
        weather_mock = MagicMock()
        weather_mock.__enter__.return_value = weather_response

        mock_urlopen.side_effect = [geo_mock, weather_mock]

        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/widget.html')
        # Ensure the region logic picked the right one and fetched weather
        self.assertIn('weather', response.context)

    @patch('weather.services.urllib.request.urlopen')
    def test_geocode_multiple_match_no_region_resolution(self, mock_urlopen):
        self.farmer.region = "Rift Valley"
        self.farmer.save()
        self.client.force_login(self.farmer)

        geo_response = MagicMock()
        geo_response.read.return_value = json.dumps({
            "results": [
                {"id": 1, "name": "Eldoret", "country_code": "KE", "admin1": "Nairobi"},
                {"id": 2, "name": "Eldoret", "country_code": "KE", "admin1": "Uasin Gishu"}
            ]
        }).encode('utf-8')
        geo_mock = MagicMock()
        geo_mock.__enter__.return_value = geo_response

        mock_urlopen.side_effect = [geo_mock]

        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/options.html')
        self.assertEqual(len(response.context['options']), 2)

    @patch('weather.services.urllib.request.urlopen')
    def test_weather_timeout(self, mock_urlopen):
        self.client.force_login(self.farmer)
        mock_urlopen.side_effect = socket.timeout("timeout")

        response = self.client.get(reverse('weather:widget'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')
        self.assertEqual(response.context.get('error'), 'Location not found')

    @patch('weather.services.urllib.request.urlopen')
    def test_manual_weather_search(self, mock_urlopen):
        self.client.force_login(self.farmer)

        geo_response = MagicMock()
        geo_response.read.return_value = json.dumps({
            "results": [
                {"id": 10, "name": "Nairobi", "country_code": "KE", "admin1": "Nairobi Area", "latitude": -1.28, "longitude": 36.82}
            ]
        }).encode('utf-8')
        geo_mock = MagicMock()
        geo_mock.__enter__.return_value = geo_response
        mock_urlopen.return_value = geo_mock

        response = self.client.get(reverse('weather:widget') + '?search=Nairobi')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/options.html')
        self.assertEqual(len(response.context['options']), 1)
        self.assertEqual(response.context['options'][0]['name'], 'Nairobi')

    @patch('weather.services.urllib.request.urlopen')
    def test_coordinate_validation_invalid_values(self, mock_urlopen):
        self.farmer.town = ""
        self.farmer.save()
        self.client.force_login(self.farmer)

        # Non-numeric
        response = self.client.get(reverse('weather:widget') + '?lat=abc&lon=def&name=Test')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')

        # Latitude out of bounds
        response = self.client.get(reverse('weather:widget') + '?lat=95.0&lon=35.0&name=Test')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')

        # Longitude out of bounds
        response = self.client.get(reverse('weather:widget') + '?lat=0.0&lon=185.0&name=Test')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')

        # Non-finite (NaN / inf)
        response = self.client.get(reverse('weather:widget') + '?lat=nan&lon=35.0&name=Test')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')

        response = self.client.get(reverse('weather:widget') + '?lat=0.0&lon=inf&name=Test')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/search.html')

    @patch('weather.services.urllib.request.urlopen')
    def test_coordinate_validation_valid_values(self, mock_urlopen):
        self.client.force_login(self.farmer)

        weather_response = MagicMock()
        weather_response.read.return_value = json.dumps({
            "current": {"time": "2026-10-11T10:00", "temperature_2m": 21.5, "weather_code": 1, "precipitation": 1.2},
            "daily": {
                "time": ["2026-10-11", "2026-10-12"],
                "weather_code": [1, 0],
                "temperature_2m_max": [25.0, 26.0],
                "temperature_2m_min": [14.0, 15.0],
                "precipitation_sum": [1.2, 0.5]
            }
        }).encode('utf-8')
        weather_mock = MagicMock()
        weather_mock.__enter__.return_value = weather_response
        mock_urlopen.return_value = weather_mock

        response = self.client.get(reverse('weather:widget') + '?lat=-1.28&lon=36.82&name=Nairobi')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'weather/widget.html')
        self.assertEqual(response.context['town'], 'Nairobi')
        self.assertEqual(response.context['weather']['temperature'], 21.5)
        self.assertEqual(response.context['weather']['precipitation'], 1.2)
        self.assertEqual(response.context['weather']['forecast'][0]['precip'], 0.5)
        self.assertEqual(response.context['weather']['timestamp'], '2026-10-11T10:00')
