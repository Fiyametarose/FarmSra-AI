import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from advisory.chat import reply_to_question
from advisory.context import build_case_context
from advisory.models import AdvisoryGenerationJob, AdvisoryVersion, RecommendationAction
from advisory.workflow import execute_job
from diagnoses.inference import Prediction
from diagnoses.models import CaseFollowUp, DiagnosisCase
from diagnoses.tests.test_forms import image_upload

User = get_user_model()
SECTIONS = {
    "diagnosis_summary": "Tentative early blight assessment.",
    "immediate_actions": "Remove badly affected debris.",
    "monitoring": "Check new leaves every three days.",
    "expert_consultation": "Consult an expert if the crop declines.",
}
EVIDENCE = [{
    "chunk_id": "chunk-1", "source_id": "s1", "title": "Guide", "publisher": "Ext",
    "url": "https://example.test/g", "text": "Rotate crops.", "date": "", "section": "",
}]


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FollowUpTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.farmer = User.objects.create_user("farmer@example.com", "pw", preferred_language="en")
        self.other = User.objects.create_user("other@example.com", "pw")
        self.expert = User.objects.create_user("expert@example.com", "pw", role=User.Role.EXPERT)
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image=image_upload(), symptoms="Dark spots",
            status=DiagnosisCase.Status.COMPLETED, predicted_class="Tomato_Early_blight",
            model_confidence=0.9, completed_at=timezone.now(),
        )
        self.version = AdvisoryVersion.objects.create(
            diagnosis=self.case, version=1, language="en", status="succeeded",
            sections=SECTIONS, content="advice", is_generated=True,
            completed_at=timezone.now(),
        )
        self.client.force_login(self.farmer)

    def post_follow_up(self, with_image=False, **extra):
        data = {
            "symptoms": "Spots spreading to new leaves", "notes": "Worse after rain",
            "actions_taken": "Removed leaves",
            "observed_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "request_token": "11111111-1111-1111-1111-111111111111",
            **extra,
        }
        if with_image:
            data["image"] = image_upload(name="followup.png")
        return self.client.post(reverse("case-follow-up", args=[self.case.id]), data)

    # Actions
    def test_detail_creates_not_started_actions_only(self):
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, "Recommended Action Tracking")
        actions = RecommendationAction.objects.filter(advisory_version=self.version)
        self.assertEqual({a.section_key for a in actions}, {"immediate_actions", "monitoring"})
        self.assertEqual({a.status for a in actions}, {"not_started"})

    def test_farmer_updates_action_and_others_cannot(self):
        self.client.get(reverse("case-detail", args=[self.case.id]))
        action = RecommendationAction.objects.get(section_key="immediate_actions")
        url = reverse("update-action", args=[action.id])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"status": "completed"}).status_code, 404)
        self.client.force_login(self.expert)
        self.assertEqual(self.client.post(url, {"status": "completed"}).status_code, 403)
        action.refresh_from_db()
        self.assertEqual(action.status, "not_started")
        self.client.force_login(self.farmer)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {"status": "completed", "farmer_notes": "Done today"})
        action.refresh_from_db()
        self.assertEqual((action.status, action.farmer_notes), ("completed", "Done today"))

    def test_invalid_action_status_rejected(self):
        self.client.get(reverse("case-detail", args=[self.case.id]))
        action = RecommendationAction.objects.get(section_key="monitoring")
        self.client.post(reverse("update-action", args=[action.id]), {"status": "bogus"})
        action.refresh_from_db()
        self.assertEqual(action.status, "not_started")

    # Follow-ups
    def test_follow_up_saved_separately_with_photo_and_is_idempotent(self):
        original_image = self.case.image.name
        self.assertEqual(self.post_follow_up(with_image=True).status_code, 302)
        self.post_follow_up(with_image=True)
        self.assertEqual(self.case.follow_ups.count(), 1)
        follow_up = self.case.follow_ups.get()
        self.assertTrue(follow_up.image.name.startswith(f"cases/{self.farmer.id}/follow-ups/"))
        self.case.refresh_from_db()
        self.assertEqual((self.case.image.name, self.case.symptoms), (original_image, "Dark spots"))
        self.post_follow_up(request_token="22222222-2222-2222-2222-222222222222")
        self.assertEqual(list(self.case.follow_ups.values_list("symptoms", flat=True)),
                         ["Spots spreading to new leaves"] * 2)

    def test_follow_up_validation(self):
        bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
        response = self.client.post(reverse("case-follow-up", args=[self.case.id]), {
            "image": bad, "observed_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "request_token": "33333333-3333-3333-3333-333333333333",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.case.follow_ups.count(), 0)
        empty = self.post_follow_up(symptoms="", notes="", actions_taken="")
        self.assertEqual(empty.status_code, 400)
        future = (timezone.localtime() + timezone.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(self.post_follow_up(observed_at=future).status_code, 400)
        with override_settings(MAX_UPLOAD_SIZE=10):
            self.assertEqual(self.post_follow_up(with_image=True).status_code, 400)

    def test_follow_up_ownership_and_protected_image(self):
        self.post_follow_up(with_image=True)
        follow_up = self.case.follow_ups.get()
        image_url = reverse("follow-up-image", args=[self.case.id, follow_up.id])
        response = self.client.get(image_url)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(image_url).status_code, 404)
        self.assertEqual(self.post_follow_up().status_code, 404)
        self.client.force_login(self.expert)
        self.assertEqual(self.client.get(image_url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(image_url).status_code, 302)

    # Reassessment
    def test_symptoms_only_reassessment_queues_advisory_without_prediction(self):
        self.post_follow_up()
        follow_up = self.case.follow_ups.get()
        url = reverse("case-reassess", args=[self.case.id, follow_up.id])
        self.client.post(url)
        self.client.post(url)
        follow_up.refresh_from_db()
        self.assertIsNotNone(follow_up.reassessment_requested_at)
        self.assertIsNone(follow_up.reassessment)
        self.assertEqual(DiagnosisCase.objects.count(), 1)
        self.assertEqual(AdvisoryGenerationJob.objects.filter(diagnosis=self.case).count(), 1)
        self.assertEqual(AdvisoryVersion.objects.filter(diagnosis=self.case).count(), 2)
        job = AdvisoryGenerationJob.objects.get()
        context = job.advisory_version.context_snapshot
        self.assertEqual(context["reassessment"]["kind"], "symptoms_only")
        self.assertFalse(context["reassessment"]["new_image_classified"])
        self.assertEqual(context["follow_ups"][0]["symptoms"], "Spots spreading to new leaves")
        self.case.refresh_from_db()
        self.assertEqual((self.case.predicted_class, self.case.model_confidence), ("Tomato_Early_blight", 0.9))

    def test_other_farmer_cannot_reassess(self):
        self.post_follow_up()
        follow_up = self.case.follow_ups.get()
        self.client.force_login(self.other)
        response = self.client.post(reverse("case-reassess", args=[self.case.id, follow_up.id]))
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.expert)
        response = self.client.post(reverse("case-reassess", args=[self.case.id, follow_up.id]))
        self.assertEqual(response.status_code, 403)

    @patch("advisory.workflow.enqueue_for_case")
    @patch("weather.services.capture_automatic_case_weather")
    @patch("diagnoses.views.predict")
    def test_photo_reassessment_uses_pipeline_and_preserves_original(self, predict, _weather, enqueue):
        predict.return_value = Prediction("Tomato_Late_blight", 2, 0.77, "tomato.keras")
        self.post_follow_up(with_image=True)
        follow_up = self.case.follow_ups.get()
        url = reverse("case-reassess", args=[self.case.id, follow_up.id])
        response = self.client.post(url)
        child = DiagnosisCase.objects.get(parent_case=self.case)
        self.assertRedirects(response, reverse("case-analyzing", args=[child.id]))
        self.client.post(url)
        self.assertEqual(DiagnosisCase.objects.filter(parent_case=self.case).count(), 1)
        self.assertNotEqual(child.image.name, follow_up.image.name)
        self.assertEqual(child.status, DiagnosisCase.Status.PENDING)
        self.client.post(reverse("case-process", args=[child.id]))
        child.refresh_from_db()
        self.assertEqual((child.status, child.predicted_class), ("completed", "Tomato_Late_blight"))
        enqueue.assert_called_once()
        self.case.refresh_from_db()
        self.assertEqual((self.case.predicted_class, self.case.model_confidence), ("Tomato_Early_blight", 0.9))
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.reassessment, child)
        detail = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(detail, reverse("case-detail", args=[child.id]))
        self.assertNotContains(self.client.get(reverse("history")), reverse("case-detail", args=[child.id]))
        context = build_case_context(self.farmer, child)
        self.assertEqual(context["reassessment"]["kind"], "photo")
        self.assertEqual(context["previous_assessments"][0]["predicted_class"], "Tomato_Early_blight")

    # Preservation and chatbot
    def test_actions_and_versions_survive_new_advisory(self):
        self.client.get(reverse("case-detail", args=[self.case.id]))
        action = RecommendationAction.objects.get(section_key="monitoring")
        self.client.post(reverse("update-action", args=[action.id]), {"status": "in_progress"})
        self.post_follow_up()
        follow_up = self.case.follow_ups.get()
        self.client.post(reverse("case-reassess", args=[self.case.id, follow_up.id]))
        job = AdvisoryGenerationJob.objects.get()
        with patch("advisory.workflow.retrieve_evidence", return_value=EVIDENCE):
            job.status = "running"
            job.claimed_at = timezone.now()
            job.save()
            execute_job(
                job,
                retrieve_func=lambda *a, **k: EVIDENCE,
                generate_func=lambda *a, **k: ("### x", ["chunk-1"], SECTIONS),
            )
        self.assertEqual(AdvisoryVersion.objects.filter(diagnosis=self.case, status="succeeded").count(), 2)
        self.assertEqual(self.version.__class__.objects.get(pk=self.version.pk).sections, SECTIONS)
        detail = self.client.get(reverse("case-detail", args=[self.case.id]))
        action.refresh_from_db()
        self.assertEqual(action.status, "in_progress")
        self.assertContains(detail, "Action history from other advisory versions")
        new_actions = RecommendationAction.objects.filter(advisory_version=job.advisory_version)
        self.assertEqual({a.status for a in new_actions}, {"not_started"})

    @patch("advisory.chat.generate_chat_answer")
    @patch("advisory.chat.retrieve_evidence")
    def test_chat_context_has_authorized_follow_up_information(self, retrieve, generate):
        self.client.get(reverse("case-detail", args=[self.case.id]))
        action = RecommendationAction.objects.get(section_key="monitoring")
        action.status = "completed"
        action.save()
        self.post_follow_up()
        retrieve.return_value = EVIDENCE
        generate.return_value = {
            "answer": "Rotate crops.", "answer_en": "Rotate crops.",
            "insufficient_evidence": False, "citations": ["chunk-1"],
        }
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            reply_to_question(self.farmer, self.case, "What changed?", "en", [])
        context = generate.call_args.args[0]
        self.assertEqual(context["follow_ups"][0]["notes"], "Worse after rain")
        self.assertIn("completed", {a["status"] for a in context["recommended_actions"]})
        self.assertEqual(context["previous_advisories"][0]["version"], 1)
        with self.assertRaises(Exception):
            build_case_context(self.other, self.case)
        self.assertEqual(RecommendationAction.objects.get(pk=action.pk).status, "completed")

    @patch("weather.services.get_weather_by_coords")
    @patch("weather.services.resolve_location")
    def test_symptoms_only_reassessment_records_new_weather_in_advisory_context(self, resolve, weather):
        from weather.models import WeatherContext, WeatherSnapshot
        from weather.services import persist_weather_snapshot
        resolve.return_value = ({"name": "Nakuru", "lat": 0.3, "lon": 36.0}, None)
        self.case.location = "Nakuru"
        self.case.save(update_fields=["location"])
        earlier = timezone.now().replace(microsecond=0) - timezone.timedelta(days=1)
        persist_weather_snapshot(self.case, {
            "location_name": "Nakuru", "location_source": "explicit", "latitude": 0.3,
            "longitude": 36.0, "provider": "Open-Meteo", "observed_at": earlier, "temperature": 19,
        })
        now = timezone.now().replace(microsecond=0)
        weather.return_value = {
            "provider": "Open-Meteo", "timestamp": now.isoformat(), "temperature": 27,
            "humidity": 80, "precipitation": 2, "wind_speed": 3, "condition": "Rain",
            "weather_code": 61, "forecast": [],
        }
        self.post_follow_up()
        follow_up = self.case.follow_ups.get()
        url = reverse("case-reassess", args=[self.case.id, follow_up.id])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(weather.call_count, 1)
        self.assertEqual(WeatherSnapshot.objects.filter(diagnosis=self.case).count(), 2)
        self.assertEqual(WeatherContext.objects.get(diagnosis=self.case).temperature, 27)
        used = AdvisoryGenerationJob.objects.get().advisory_version.context_snapshot["weather"]
        self.assertEqual(used["observed_at"], now.isoformat())
        self.assertEqual(used["previous_observations"][0]["temperature"], 19)

    @patch("weather.services.fetch_case_weather", side_effect=RuntimeError("provider down"))
    def test_weather_failure_does_not_block_symptoms_only_reassessment(self, _fetch):
        self.post_follow_up()
        follow_up = self.case.follow_ups.get()
        self.client.post(reverse("case-reassess", args=[self.case.id, follow_up.id]))
        self.assertEqual(AdvisoryGenerationJob.objects.count(), 1)
        self.assertIsNone(AdvisoryGenerationJob.objects.get().advisory_version.context_snapshot["weather"])
