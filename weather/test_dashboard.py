import json
import socket
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import User


def observation(timestamp, temperature=22.0):
    return {
        "provider": "Open-Meteo", "timestamp": timestamp.isoformat(),
        "temperature": temperature, "humidity": 70, "precipitation": 1.0,
        "wind_speed": 8.0, "condition": "Clear sky", "weather_code": 0,
        "forecast": [{"date": "2026-11-05", "temp_max": 25, "temp_min": 14, "precip": 0, "condition": "Clear sky"}],
        "fetched_at": timezone.now().isoformat(), "timezone_abbreviation": "EAT",
    }


class AutomaticDashboardWeatherTests(TestCase):
    def setUp(self):
        cache.clear()
        self.farmer = User.objects.create_user(
            email="auto@test.com", password="test", role=User.Role.FARMER,
            town="Eldoret", country="KE", region="Uasin Gishu",
        )
        self.client.force_login(self.farmer)
        self.url = reverse("weather:widget")

    def test_dashboard_loads_weather_automatically_without_gps_prompt(self):
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("addEventListener('DOMContentLoaded', () => loadWeather())", html)
        self.assertEqual(html.count("getCurrentPosition"), 1)
        self.assertIn('onclick="refreshWeather()"', html)
        start = html.index("function useDeviceLocation")
        self.assertIn("getCurrentPosition", html[start:html.index("function fetchWeatherManual")])

    @patch("weather.views.get_weather_by_coords")
    @patch("weather.views.geocode_town")
    def test_profile_location_is_used_with_source_and_update_details(self, geocode, weather):
        geocode.return_value = [{"name": "Eldoret", "admin1": "Uasin Gishu", "country": "Kenya", "lat": 0.5, "lon": 35.2}]
        weather.return_value = observation(timezone.now())
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "weather/widget.html")
        self.assertContains(response, "Farm profile")
        self.assertContains(response, "Last successful update")
        self.assertContains(response, "Observed")
        self.assertContains(response, "3-Day Forecast")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertTrue(weather.call_args.kwargs["allow_stale"])

    def test_missing_location_state_offers_search_and_explicit_gps(self):
        self.farmer.town = ""
        self.farmer.save()
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "weather/search.html")
        self.assertTrue(response.context["missing_location"])
        self.assertContains(response, "useDeviceLocation()")
        self.assertContains(response, "only after you press this button")

    @patch("weather.views.get_weather_by_coords")
    def test_manual_selection_is_saved_and_reused_then_reset(self, weather):
        weather.return_value = observation(timezone.now())
        response = self.client.post(self.url, {"lat": "-1.28", "lon": "36.82", "name": "Nairobi", "source": "selected"})
        self.assertContains(response, "Selected location")
        self.assertContains(response, "Nairobi")
        again = self.client.get(self.url)
        self.assertContains(again, "Nairobi")
        self.assertEqual(weather.call_args.args[:2], (-1.28, 36.82))
        self.client.post(self.url, {"reset": "1"})
        self.assertNotIn("weather_location", self.client.session)

    def test_post_requires_csrf_and_valid_coordinates(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.farmer)
        self.assertEqual(strict.post(self.url, {"lat": "1", "lon": "1", "name": "X"}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"lat": "99", "lon": "1", "name": "X"}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"lat": "1", "lon": "1", "name": ""}).status_code, 400)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    @patch("weather.views.get_weather_by_coords")
    def test_device_location_is_rounded_and_labeled_unverified(self, weather):
        weather.return_value = observation(timezone.now())
        response = self.client.post(self.url, {
            "lat": "-1.286389", "lon": "36.817223", "name": "Device location", "source": "device_gps",
        })
        self.assertContains(response, "Browser coordinates (unverified)")
        self.assertEqual(weather.call_args.args[:2], (-1.29, 36.82))
        self.assertEqual(self.client.session["weather_location"][:2], [-1.29, 36.82])

    @patch("weather.views.get_weather_by_coords")
    def test_refresh_requests_new_data_from_selected_location(self, weather):
        weather.return_value = observation(timezone.now())
        self.client.get(self.url, {"lat": "1.0", "lon": "35.0", "name": "Kitale", "source": "selected", "refresh": "1"})
        self.assertTrue(weather.call_args.kwargs["force_refresh"])

    @patch("weather.views.get_weather_by_coords", return_value=None)
    def test_provider_error_state_without_earlier_data(self, weather):
        response = self.client.get(self.url, {"lat": "1.0", "lon": "35.0", "name": "Kitale"})
        self.assertContains(response, "currently unavailable from the provider")
        self.assertContains(response, "refreshWeather()")
        self.assertNotContains(response, "Last successful update")

    @patch("weather.services.urllib.request.urlopen")
    def test_stale_data_is_flagged_and_only_returned_when_allowed(self, urlopen):
        from weather.services import get_weather_by_coords
        payload = {
            "current": {"temperature_2m": 20, "relative_humidity_2m": 60, "precipitation": 0,
                        "wind_speed_10m": 4, "weather_code": 0, "time": "2026-11-04T10:00"},
            "daily": {"time": []}, "utc_offset_seconds": 10800, "timezone_abbreviation": "EAT",
        }
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        opened = MagicMock()
        opened.__enter__.return_value = response
        urlopen.return_value = opened
        fresh = get_weather_by_coords(0.5, 35.2, "Eldoret")
        self.assertFalse(fresh.get("stale"))
        self.assertIn("fetched_at", fresh)
        cache.delete("weather_0.5_35.2")
        urlopen.side_effect = socket.timeout("down")
        self.assertIsNone(get_weather_by_coords(0.5, 35.2, "Eldoret"))
        stale = get_weather_by_coords(0.5, 35.2, "Eldoret", allow_stale=True)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["fetched_at"], fresh["fetched_at"])
        widget = self.client.get(self.url, {"lat": "0.5", "lon": "35.2", "name": "Eldoret"})
        self.assertContains(widget, "last successful observation")

    @patch("weather.services.urllib.request.urlopen")
    def test_cache_hit_and_rapid_refresh_do_not_call_provider_again(self, urlopen):
        from weather.services import get_weather_by_coords
        cached = observation(timezone.now())
        cache.set("weather_0.5_35.2", cached, 1800)
        self.assertEqual(get_weather_by_coords(0.5, 35.2, "x"), cached)
        self.assertEqual(get_weather_by_coords(0.5, 35.2, "x", force_refresh=True), cached)
        urlopen.assert_not_called()
