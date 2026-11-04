from unittest.mock import MagicMock, patch
from datetime import timedelta
import json
import os

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from django.urls import reverse

from advisory.context import build_case_context
from advisory.models import AdvisoryGenerationJob, AdvisoryResult, AdvisoryVersion
from advisory.workflow import claim_next_job, enqueue_for_case, generate_for_case
from advisory.services import generate_advisory_payload
from diagnoses.inference import Prediction
from diagnoses.models import DiagnosisCase


EVIDENCE = [{
    "chunk_id": "chunk-1", "source_id": "source-1", "title": "Extension guide",
    "publisher": "Extension", "url": "https://example.test/guide", "text": "Rotate crops.",
    "date": "2025", "section": "Management",
}]
SECTIONS = {
    "diagnosis_summary": "Tentative early blight assessment.",
    "immediate_actions": "Remove badly affected debris.",
    "expert_consultation": "Consult an expert if the crop declines.",
}


class AdvisoryWorkflowTests(TestCase):
    def setUp(self):
        self.farmer = get_user_model().objects.create_user(
            "farmer@example.com", "password", preferred_language="en",
            town="Nakuru", country="KE",
        )
        self.other = get_user_model().objects.create_user("other@example.com", "password")
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image="cases/leaf.jpg",
            symptoms="Dark concentric spots", location="Nakuru, Kenya",
            status=DiagnosisCase.Status.COMPLETED, predicted_class="Tomato_Early_blight",
            model_confidence=.91,
        )

    @patch("advisory.workflow.generate_advisory_payload")
    @patch("advisory.workflow.retrieve_evidence", return_value=EVIDENCE)
    def test_success_is_versioned_and_updates_legacy_relation(self, _retrieve, generate):
        generate.return_value = ("### Diagnosis summary\nAdvice", ["chunk-1"], SECTIONS)
        job = generate_for_case(self.case, self.farmer)

        self.assertEqual(job.status, AdvisoryGenerationJob.Status.SUCCEEDED)
        version = AdvisoryVersion.objects.get(diagnosis=self.case)
        self.assertEqual(version.version, 1)
        self.assertEqual(version.context_snapshot["case_location"], "Nakuru, Kenya")
        self.assertEqual(version.source_references[0]["chunk_id"], "chunk-1")
        self.assertEqual(self.case.advisory.content, version.content)
        self.assertTrue(AdvisoryResult.objects.get(diagnosis=self.case).is_generated)

    @patch("advisory.workflow.generate_advisory_payload")
    @patch("advisory.workflow.retrieve_evidence", return_value=EVIDENCE)
    def test_same_context_is_idempotent_but_changed_context_creates_version(self, _retrieve, generate):
        generate.return_value = ("Advice", ["chunk-1"], SECTIONS)
        first = generate_for_case(self.case, self.farmer)
        second = generate_for_case(self.case, self.farmer)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AdvisoryVersion.objects.count(), 1)

        self.case.symptoms = "Spots have spread"
        self.case.save(update_fields=["symptoms"])
        third = generate_for_case(self.case, self.farmer)
        self.assertNotEqual(first.pk, third.pk)
        self.assertEqual(list(AdvisoryVersion.objects.values_list("version", flat=True)), [2, 1])

    @patch("advisory.workflow.generate_advisory_payload")
    @patch("advisory.workflow.retrieve_evidence")
    def test_failed_job_retries_same_version_and_recovers(self, retrieve, generate):
        retrieve.side_effect = [None, EVIDENCE]
        generate.return_value = ("Recovered advice", ["chunk-1"], SECTIONS)
        failed = generate_for_case(self.case, self.farmer)
        self.assertEqual(failed.status, AdvisoryGenerationJob.Status.FAILED)
        self.assertFalse(AdvisoryResult.objects.filter(diagnosis=self.case).exists())

        recovered = generate_for_case(self.case, self.farmer)
        self.assertEqual(recovered.pk, failed.pk)
        self.assertEqual(recovered.status, AdvisoryGenerationJob.Status.SUCCEEDED)
        self.assertEqual(AdvisoryVersion.objects.count(), 1)

    @patch("advisory.workflow.generate_advisory_payload")
    @patch("advisory.workflow.retrieve_evidence", return_value=EVIDENCE)
    def test_saved_preference_controls_full_generated_language(self, _retrieve, generate):
        self.farmer.preferred_language = "sw"
        self.farmer.save(update_fields=["preferred_language"])
        generate.return_value = ("### Muhtasari wa utambuzi\nUshauri", ["chunk-1"], SECTIONS)
        job = generate_for_case(self.case, self.farmer)
        self.assertEqual(job.language, "sw")
        self.assertEqual(job.advisory_version.language, "sw")
        self.assertIn("Muhtasari", job.advisory_version.content)
        self.assertEqual(generate.call_args.args[-1], "sw")

    def test_safety_advisory_is_localized_without_external_generation(self):
        self.case.predicted_class = "Tomato_Spider_mites_Two_spotted_spider_mite"
        self.case.save(update_fields=["predicted_class"])
        self.farmer.preferred_language = "am"
        self.farmer.save(update_fields=["preferred_language"])
        with patch("advisory.workflow.retrieve_evidence") as retrieve:
            job = generate_for_case(self.case, self.farmer)
        retrieve.assert_not_called()
        self.assertEqual(job.status, AdvisoryGenerationJob.Status.SUCCEEDED)
        self.assertEqual(job.advisory_version.language, "am")
        self.assertIn("### የምርመራ ማጠቃለያ", job.advisory_version.content)

    def test_context_and_generation_reject_another_farmer(self):
        with self.assertRaises(PermissionDenied):
            build_case_context(self.other, self.case)
        with self.assertRaises(PermissionDenied):
            generate_for_case(self.case, self.other)

    def test_retry_endpoint_is_owner_only(self):
        self.client.force_login(self.other)
        response = self.client.post(reverse("generate-advisory", args=[self.case.id]))
        self.assertEqual(response.status_code, 403)

    @patch("advisory.workflow.enqueue_for_case")
    @patch("weather.services.capture_automatic_case_weather")
    @patch("diagnoses.views.predict")
    def test_ajax_diagnosis_automatically_captures_weather_and_generates(self, predict, capture, generate):
        pending = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image="cases/new.jpg", location="Nakuru",
        )
        predict.return_value = Prediction("Tomato_Early_blight", 1, .9, "model.keras")
        generate.return_value = MagicMock(status="pending")
        self.client.force_login(self.farmer)
        response = self.client.post(
            reverse("case-process", args=[pending.id]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["advisory_status"], "pending")
        capture.assert_called_once()
        generate.assert_called_once()
        pending.refresh_from_db()
        self.assertEqual(pending.status, DiagnosisCase.Status.COMPLETED)

    def test_analyzing_page_uses_ajax_progressive_workflow(self):
        pending = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image="cases/new.jpg",
        )
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("case-analyzing", args=[pending.id]))
        self.assertContains(response, 'headers: {"X-Requested-With": "XMLHttpRequest"}')
        self.assertContains(response, "Your advisory is ready")
        self.assertNotContains(response, "document.write")
        self.assertContains(response, "window.location.assign")

    def test_enqueue_is_idempotent_and_claim_is_atomic(self):
        first = enqueue_for_case(self.case, self.farmer)
        second = enqueue_for_case(self.case, self.farmer)
        self.assertEqual(first.pk, second.pk)
        claimed = claim_next_job()
        self.assertEqual(claimed.pk, first.pk)
        self.assertIsNone(claim_next_job())

    def test_status_endpoint_is_owner_only(self):
        enqueue_for_case(self.case, self.farmer)
        self.client.force_login(self.other)
        response = self.client.get(reverse("advisory-status", args=[self.case.id]))
        self.assertEqual(response.status_code, 404)

    @override_settings(ADVISORY_JOB_STALE_AFTER_SECONDS=60)
    def test_stale_running_job_is_reclaimed_without_new_version(self):
        job = enqueue_for_case(self.case, self.farmer)
        old = timezone.now() - timedelta(minutes=2)
        AdvisoryGenerationJob.objects.filter(pk=job.pk).update(
            status=AdvisoryGenerationJob.Status.RUNNING, claimed_at=old
        )
        reclaimed = claim_next_job()
        self.assertEqual(reclaimed.pk, job.pk)
        self.assertEqual(reclaimed.attempt_count, 1)
        self.assertEqual(AdvisoryVersion.objects.filter(diagnosis=self.case).count(), 1)

    def test_each_language_keeps_its_own_version(self):
        english = enqueue_for_case(self.case, self.farmer, "en")
        swahili = enqueue_for_case(self.case, self.farmer, "sw")
        self.assertNotEqual(english.pk, swahili.pk)
        self.assertEqual(
            set(AdvisoryVersion.objects.values_list("language", flat=True)),
            {"en", "sw"},
        )

    def test_owner_status_reports_pending_stage(self):
        enqueue_for_case(self.case, self.farmer)
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("advisory-status", args=[self.case.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["stage"], "preparing")

    @patch("advisory.services.genai.Client")
    def test_incomplete_structured_response_is_rejected(self, client):
        response = MagicMock()
        response.text = json.dumps({"diagnosis_summary": "Tentative.", "citations": ["chunk-1"]})
        client.return_value.models.generate_content.return_value = response
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            content, citations, sections = generate_advisory_payload(
                "tomato", "Tomato_Early_blight", EVIDENCE, {}, "en"
            )
        self.assertIsNone(content)
        self.assertIsNone(citations)
        self.assertIsNone(sections)

    @patch("advisory.services.genai.Client")
    def test_unknown_citation_is_rejected(self, client):
        payload = {
            key: "Evidence-supported guidance."
            for key in (
                "diagnosis_summary", "symptoms_to_inspect", "possible_causes",
                "immediate_actions", "prevention", "weather_context", "monitoring",
                "treatment_considerations", "expert_consultation",
            )
        }
        payload["citations"] = ["unknown"]
        response = MagicMock(text=json.dumps(payload))
        client.return_value.models.generate_content.return_value = response
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = generate_advisory_payload(
                "tomato", "Tomato_Early_blight", EVIDENCE, {}, "en"
            )
        self.assertEqual(result, (None, None, None))
