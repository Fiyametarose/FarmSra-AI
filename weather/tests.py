from django.test import TestCase, Client, TransactionTestCase, override_settings
from django.urls import reverse
from unittest.mock import patch, MagicMock
from users.models import User
import socket
import json
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta, timezone as datetime_timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections

from diagnoses.models import DiagnosisCase
from weather.models import WeatherContext
from weather.services import resolve_location, weather_observed_at
from weather.views import persist_weather_snapshot

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
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop="tomato",
            image="cases/test.jpg",
            location="Eldoret",
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
                {"id": 1, "name": "Eldoret", "country_code": "KE", "admin1": "Uasin Gishu", "latitude": 0.5, "longitude": 35.2}
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
    def test_geocode_does_not_select_a_different_region(self, mock_urlopen):
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
        self.assertTemplateUsed(response, 'weather/search.html')
        self.assertEqual(response.context['error'], 'Location not found')

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

    @patch("weather.views.get_weather_by_coords")
    @patch("weather.views.resolve_location")
    def test_case_snapshot_is_persisted_and_initial_capture_is_idempotent(self, mock_resolve, mock_weather):
        mock_resolve.return_value = ({"name": "Eldoret", "lat": 0.5, "lon": 35.2}, None)
        mock_weather.return_value = {
            "provider": "Open-Meteo", "timestamp": timezone.now().isoformat(),
            "temperature": 22.0, "humidity": 70, "precipitation": 1.0,
            "wind_speed": 8.0, "condition": "Rain", "weather_code": 61,
        }
        self.client.force_login(self.farmer)
        url = reverse("weather:capture-case-weather", args=[self.case.id])
        self.client.post(url)
        snapshot = WeatherContext.objects.get(diagnosis=self.case)
        self.client.post(url, {"location": "Different Town"})
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.location_name, "Eldoret")
        self.assertEqual(mock_weather.call_count, 1)

    @patch("weather.views.get_weather_by_coords", return_value=None)
    @patch("weather.views.resolve_location")
    def test_failed_provider_does_not_create_snapshot(self, mock_resolve, mock_weather):
        mock_resolve.return_value = ({"name": "Eldoret", "lat": 0.5, "lon": 35.2}, None)
        self.client.force_login(self.farmer)
        self.client.post(reverse("weather:capture-case-weather", args=[self.case.id]))
        self.assertFalse(WeatherContext.objects.filter(diagnosis=self.case).exists())
        self.assertTrue(DiagnosisCase.objects.filter(id=self.case.id).exists())

    def test_other_farmer_cannot_capture_case_weather(self):
        other = User.objects.create_user(email="other@test.com", password="test", role=User.Role.FARMER)
        self.client.force_login(other)
        response = self.client.post(reverse("weather:capture-case-weather", args=[self.case.id]))
        self.assertEqual(response.status_code, 404)

    @patch("weather.services.geocode_town")
    def test_ambiguous_location_is_not_silently_selected(self, mock_geocode):
        mock_geocode.return_value = [
            {"name": "Town A", "lat": 1, "lon": 2},
            {"name": "Town B", "lat": 3, "lon": 4},
        ]
        resolved, error = resolve_location("Town")
        self.assertIsNone(resolved)
        self.assertIn("ambiguous", str(error).lower())

    @patch("weather.services.geocode_town")
    def test_multi_part_location_uses_town_region_and_country(self, mock_geocode):
        mock_geocode.return_value = [
            {"name": "Eldoret", "admin1": "Uasin Gishu", "country": "Kenya", "lat": 0.5, "lon": 35.2}
        ]
        resolved, error = resolve_location("Eldoret, Uasin Gishu County, Kenya")
        self.assertIsNone(error)
        self.assertEqual(resolved["name"], "Eldoret")
        mock_geocode.assert_called_once_with("Eldoret", "KE", "Uasin Gishu County")

    @patch("weather.services.geocode_town")
    def test_structured_profile_uses_town_not_first_address_component(self, mock_geocode):
        mock_geocode.return_value = [
            {"name": "Eldoret", "admin1": "Uasin Gishu", "country": "Kenya", "lat": 0.5, "lon": 35.2}
        ]
        location = "Kapsoya, Eldoret, Kimumu Ward, Uasin Gishu, Kenya"
        resolved, error = resolve_location(
            location,
            country_code="KE",
            region="Uasin Gishu",
            search_name="Eldoret",
        )
        self.assertIsNone(error)
        self.assertEqual(resolved["name"], "Eldoret")
        mock_geocode.assert_called_once_with("Eldoret", "KE", "Uasin Gishu")

    def test_malformed_coordinate_pair_is_not_geocoded_as_a_place(self):
        resolved, error = resolve_location("1.25, not-a-longitude")
        self.assertIsNone(resolved)
        self.assertIn("malformed", str(error).lower())

    def test_provider_local_observation_uses_provider_utc_offset(self):
        observed_at = weather_observed_at({
            "timestamp": "2026-10-29T10:00",
            "utc_offset_seconds": 3 * 3600,
        })
        self.assertEqual(observed_at.isoformat(), "2026-10-29T10:00:00+03:00")
        self.assertEqual(
            observed_at.astimezone(datetime_timezone.utc).isoformat(),
            "2026-10-29T07:00:00+00:00",
        )

    def test_naive_provider_time_without_offset_is_rejected(self):
        self.assertIsNone(weather_observed_at({"timestamp": "2026-10-29T10:00"}))

    @override_settings(CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES=30)
    @patch("weather.views.get_weather_by_coords")
    def test_old_case_rejects_automatic_current_weather_capture(self, mock_weather):
        DiagnosisCase.objects.filter(id=self.case.id).update(
            status=DiagnosisCase.Status.COMPLETED,
            updated_at=timezone.now() - timedelta(days=2),
        )
        self.case.refresh_from_db()
        self.client.force_login(self.farmer)
        response = self.client.post(
            reverse("weather:capture-case-weather", args=[self.case.id]),
            {"capture_mode": "automatic"},
        )
        self.assertRedirects(response, reverse("case-detail", args=[self.case.id]))
        mock_weather.assert_not_called()
        self.assertFalse(WeatherContext.objects.filter(diagnosis=self.case).exists())
        detail = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(detail, "Historical weather was not recorded")
        self.assertNotContains(detail, "sessionStorage.setItem")

    @patch("weather.views.get_weather_by_coords", return_value=None)
    @patch("weather.views.resolve_location")
    def test_failed_correction_preserves_location_and_existing_snapshot(self, mock_resolve, mock_weather):
        snapshot = WeatherContext.objects.create(
            diagnosis=self.case, location_name="Eldoret", location_source="explicit",
            latitude=0.5, longitude=35.2, provider="Open-Meteo",
            observed_at=timezone.now(), temperature=22,
        )
        mock_resolve.return_value = ({"name": "Kitale", "lat": 1.0, "lon": 35.0}, None)
        self.client.force_login(self.farmer)
        self.client.post(
            reverse("weather:capture-case-weather", args=[self.case.id]),
            {"location": "Kitale, Trans Nzoia, Kenya", "replace": "1"},
        )
        self.case.refresh_from_db()
        snapshot.refresh_from_db()
        self.assertEqual(self.case.location, "Kitale, Trans Nzoia, Kenya")
        self.assertEqual(snapshot.location_name, "Eldoret")
        self.assertEqual(snapshot.temperature, 22)

    @patch("weather.views.get_weather_by_coords")
    def test_manual_coordinate_text_is_not_labeled_device_gps(self, mock_weather):
        self.case.location = "1.0, 35.0"
        self.case.save(update_fields=["location"])
        mock_weather.return_value = {
            "provider": "Open-Meteo", "timestamp": timezone.now().isoformat(),
            "temperature": 22, "humidity": 70, "precipitation": 0,
            "wind_speed": 5, "condition": "Clear", "weather_code": 0,
        }
        self.client.force_login(self.farmer)
        self.client.post(reverse("weather:capture-case-weather", args=[self.case.id]))
        self.assertEqual(
            WeatherContext.objects.get(diagnosis=self.case).location_source,
            WeatherContext.LocationSource.EXPLICIT,
        )

    @patch("weather.views.get_weather_by_coords")
    def test_browser_coordinate_source_is_recorded_as_unverified(self, mock_weather):
        self.case.location = "1.0, 35.0"
        self.case.location_source = DiagnosisCase.LocationSource.BROWSER_GEOLOCATION
        self.case.save(update_fields=["location", "location_source"])
        mock_weather.return_value = {
            "provider": "Open-Meteo", "timestamp": timezone.now().isoformat(),
            "temperature": 22, "humidity": 70, "precipitation": 0,
            "wind_speed": 5, "condition": "Clear", "weather_code": 0,
        }
        self.client.force_login(self.farmer)
        self.client.post(reverse("weather:capture-case-weather", args=[self.case.id]))
        snapshot = WeatherContext.objects.get(diagnosis=self.case)
        self.assertEqual(snapshot.location_source, WeatherContext.LocationSource.DEVICE_GPS)
        self.assertIn("unverified", snapshot.get_location_source_display().lower())


class ConcurrentWeatherCaptureTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_initial_capture_keeps_one_snapshot(self):
        farmer = User.objects.create_user(
            email="concurrent-weather@test.com", password="test", role=User.Role.FARMER
        )
        case = DiagnosisCase.objects.create(
            farmer=farmer, crop="tomato", image="cases/concurrent.jpg", location="Eldoret"
        )
        barrier = Barrier(2)

        def capture(temperature):
            close_old_connections()
            try:
                thread_case = DiagnosisCase.objects.get(id=case.id)
                barrier.wait()
                return persist_weather_snapshot(thread_case, {
                    "location_name": "Eldoret", "location_source": "explicit",
                    "latitude": 0.5, "longitude": 35.2, "provider": "Open-Meteo",
                    "observed_at": timezone.now(), "temperature": temperature,
                })
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(capture, [20, 21]))

        self.assertEqual(WeatherContext.objects.filter(diagnosis=case).count(), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)
