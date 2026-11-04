import importlib
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from advisory.context import build_case_context
from diagnoses.models import DiagnosisCase
from users.models import User
from weather.models import WeatherContext, WeatherSnapshot
from weather.services import (
    capture_automatic_case_weather,
    persist_weather_snapshot,
    refresh_case_weather,
)
from weather.test_dashboard import observation


class WeatherHistoryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.farmer = User.objects.create_user(
            email="hist@test.com", password="test", role=User.Role.FARMER, town="Eldoret", country="KE",
        )
        self.other = User.objects.create_user(email="hist2@test.com", password="test", role=User.Role.FARMER)
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image="cases/h.jpg", location="Eldoret",
            status=DiagnosisCase.Status.COMPLETED, predicted_class="Tomato_Early_blight",
            model_confidence=0.9, completed_at=timezone.now(),
        )
        self.first = timezone.now().replace(microsecond=0) - timedelta(days=1)

    def values(self, observed_at, temperature=22):
        return {
            "location_name": "Eldoret", "location_source": "explicit", "latitude": 0.5,
            "longitude": 35.2, "provider": "Open-Meteo", "observed_at": observed_at,
            "temperature": temperature,
        }

    def test_replacing_current_weather_appends_history_without_duplicates(self):
        persist_weather_snapshot(self.case, self.values(self.first, 20))
        persist_weather_snapshot(self.case, self.values(self.first, 20), replace=True)
        self.assertEqual(WeatherSnapshot.objects.filter(diagnosis=self.case).count(), 1)
        later = self.first + timedelta(hours=20)
        persist_weather_snapshot(self.case, self.values(later, 25), replace=True)
        temperatures = list(WeatherSnapshot.objects.filter(diagnosis=self.case).values_list("temperature", flat=True))
        self.assertEqual(temperatures, [20, 25])
        self.assertEqual(WeatherContext.objects.get(diagnosis=self.case).temperature, 25)

    def test_viewing_case_and_repeated_initial_capture_create_no_extra_snapshot(self):
        self.client.force_login(self.farmer)
        self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertEqual(WeatherSnapshot.objects.count(), 0)
        persist_weather_snapshot(self.case, self.values(self.first))
        self.client.post(reverse("weather:capture-case-weather", args=[self.case.id]))
        self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertEqual(WeatherSnapshot.objects.count(), 1)

    def test_migration_backfill_preserves_existing_context_as_snapshot(self):
        WeatherContext.objects.create(diagnosis=self.case, **self.values(self.first, 18))
        migration = importlib.import_module("weather.migrations.0003_weather_snapshots")
        migration.backfill_snapshots(apps, None)
        migration.backfill_snapshots(apps, None)
        snapshot = WeatherSnapshot.objects.get(diagnosis=self.case)
        self.assertEqual((snapshot.temperature, snapshot.observed_at), (18, self.first))

    @patch("weather.services.get_weather_by_coords")
    @patch("weather.services.resolve_location")
    def test_reassessment_refresh_adds_observation_and_keeps_earlier(self, resolve, weather):
        resolve.return_value = ({"name": "Eldoret", "lat": 0.5, "lon": 35.2}, None)
        persist_weather_snapshot(self.case, self.values(self.first, 20))
        new_time = timezone.now().replace(microsecond=0)
        weather.return_value = observation(new_time, 26)
        self.assertTrue(refresh_case_weather(self.case))
        self.assertFalse(refresh_case_weather(self.case))
        self.assertEqual(WeatherSnapshot.objects.filter(diagnosis=self.case).count(), 2)
        self.assertEqual(WeatherContext.objects.get(diagnosis=self.case).temperature, 26)
        self.assertTrue(weather.call_args.kwargs["force_refresh"])
        DiagnosisCase.objects.filter(pk=self.case.pk).update(completed_at=self.first - timedelta(days=2))
        self.case = DiagnosisCase.objects.get(pk=self.case.pk)
        context = build_case_context(self.farmer, self.case)["weather"]
        self.assertEqual(context["observed_at"], new_time.isoformat())
        self.assertEqual(context["timing"], "later_observation")
        self.assertIsNotNone(context["snapshot_id"])
        self.assertEqual(context["previous_observations"][0]["temperature"], 20)

    @patch("weather.services.get_weather_by_coords", return_value=None)
    @patch("weather.services.resolve_location")
    def test_refresh_failure_keeps_last_valid_observation(self, resolve, weather):
        resolve.return_value = ({"name": "Eldoret", "lat": 0.5, "lon": 35.2}, None)
        persist_weather_snapshot(self.case, self.values(self.first, 20))
        self.assertFalse(refresh_case_weather(self.case))
        current = WeatherContext.objects.get(diagnosis=self.case)
        self.assertEqual((current.temperature, current.observed_at), (20, self.first))
        self.assertEqual(WeatherSnapshot.objects.count(), 1)

    @patch("weather.services.fetch_case_weather", side_effect=RuntimeError("boom"))
    def test_weather_failures_never_raise(self, _fetch):
        capture_automatic_case_weather(self.case)
        self.assertFalse(refresh_case_weather(self.case))
        self.assertFalse(WeatherContext.objects.filter(diagnosis=self.case).exists())

    @patch("weather.services.get_weather_by_coords")
    @patch("weather.services.resolve_location")
    def test_case_location_is_preferred_and_gps_source_kept(self, resolve, weather):
        resolve.return_value = ({"name": "1.0, 35.0", "lat": 1.0, "lon": 35.0}, None)
        weather.return_value = observation(timezone.now())
        self.case.location = "1.0, 35.0"
        self.case.location_source = DiagnosisCase.LocationSource.BROWSER_GEOLOCATION
        self.case.save()
        capture_automatic_case_weather(self.case)
        self.assertEqual(resolve.call_args.args[0], "1.0, 35.0")
        self.assertEqual(
            WeatherContext.objects.get(diagnosis=self.case).location_source,
            WeatherContext.LocationSource.DEVICE_GPS,
        )
        self.assertEqual(WeatherSnapshot.objects.filter(diagnosis=self.case).count(), 1)

    def test_case_weather_is_owner_only(self):
        persist_weather_snapshot(self.case, self.values(self.first))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("case-detail", args=[self.case.id])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("weather:capture-case-weather", args=[self.case.id])).status_code, 404
        )
        self.client.force_login(self.farmer)
        self.assertContains(self.client.get(reverse("case-detail", args=[self.case.id])), "Latest saved observation")
