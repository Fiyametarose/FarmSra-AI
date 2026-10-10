import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from diagnoses.inference import InferenceError, Prediction
from diagnoses.models import DiagnosisCase
from diagnoses.tests.test_forms import image_upload


class DiagnosisWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_root = tempfile.mkdtemp()
        cls.settings_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.settings_override.disable()
        shutil.rmtree(cls.media_root)

    def setUp(self):
        User = get_user_model()
        self.farmer = User.objects.create_user("one@example.com", "test-password")
        self.other = User.objects.create_user("two@example.com", "test-password")
        self.expert = User.objects.create_user(
            "expert@example.com", "test-password", role=User.Role.EXPERT
        )
        self.client.force_login(self.farmer)

    def create_case(self, farmer=None):
        return DiagnosisCase.objects.create(
            farmer=farmer or self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=image_upload(),
            location="Nakuru, Kenya",
        )

    def test_expert_cannot_access_farmer_dashboard(self):
        self.client.force_login(self.expert)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)

    def test_farmer_journey_pages_render(self):
        self.assertContains(self.client.get(reverse("dashboard")), "Start Diagnosis")
        self.assertContains(self.client.get(reverse("select-crop")), "Select a Crop")
        self.assertContains(
            self.client.get(reverse("case-upload", args=["tomato"])),
            "Upload Crop Photo",
        )
        self.assertContains(self.client.get(reverse("history")), "Saved analyses")

    def test_cross_user_case_and_image_are_not_exposed(self):
        case = self.create_case(self.other)
        self.assertEqual(self.client.get(reverse("case-detail", args=[case.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("case-image", args=[case.id])).status_code, 404)

    def test_owner_can_reopen_case_and_private_image(self):
        case = self.create_case()
        self.client.logout()
        self.client.login(email="ONE@EXAMPLE.COM", password="test-password")
        self.assertEqual(self.client.get(reverse("case-detail", args=[case.id])).status_code, 200)
        image_response = self.client.get(reverse("case-image", args=[case.id]))
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Content-Type"], "image/png")
        self.assertEqual(image_response["Content-Disposition"], "inline")
        b"".join(image_response.streaming_content)

    def test_private_image_uses_decoded_mime_type(self):
        expected_types = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }
        for image_format, expected_type in expected_types.items():
            with self.subTest(image_format=image_format):
                extension = "jpg" if image_format == "JPEG" else image_format.lower()
                case = DiagnosisCase.objects.create(
                    farmer=self.farmer,
                    crop=DiagnosisCase.Crop.TOMATO,
                    image=image_upload(image_format, name=f"leaf.{extension}"),
                )
                response = self.client.get(reverse("case-image", args=[case.id]))
                self.assertEqual(response["Content-Type"], expected_type)
                b"".join(response.streaming_content)

    def test_private_image_requires_authentication(self):
        case = self.create_case()
        self.client.logout()
        response = self.client.get(reverse("case-image", args=[case.id]))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('case-image', args=[case.id])}",
        )

    @patch("diagnoses.views.predict")
    def test_successful_result_persists_and_appears_in_history(self, mocked_predict):
        case = self.create_case()
        mocked_predict.return_value = Prediction(
            "Tomato_Early_blight", 1, 0.82, "tomato_mobilenetv2_final.keras"
        )
        response = self.client.post(reverse("case-process", args=[case.id]))
        self.assertRedirects(response, reverse("case-detail", args=[case.id]))
        case.refresh_from_db()
        self.assertEqual(case.status, DiagnosisCase.Status.COMPLETED)
        self.assertEqual(case.predicted_class_index, 1)
        history = self.client.get(reverse("history"))
        self.assertContains(history, case.reference)
        self.assertContains(history, "Early blight")
        self.client.post(reverse("case-process", args=[case.id]))
        mocked_predict.assert_called_once()

    @patch("diagnoses.views.predict", side_effect=InferenceError("Model execution failed."))
    def test_failed_inference_is_saved_as_failure(self, mocked_predict):
        case = self.create_case()
        self.client.post(reverse("case-process", args=[case.id]))
        case.refresh_from_db()
        self.assertEqual(case.status, DiagnosisCase.Status.FAILED)
        self.assertEqual(case.predicted_class, "")
        self.assertIn("failed", case.failure_message.lower())

    @patch("diagnoses.views.predict")
    def test_completed_case_is_not_reprocessed(self, mocked_predict):
        case = self.create_case()
        case.status = DiagnosisCase.Status.COMPLETED
        case.predicted_class = "Tomato_healthy"
        case.save()
        response = self.client.post(reverse("case-process", args=[case.id]))
        self.assertRedirects(response, reverse("case-detail", args=[case.id]))
        mocked_predict.assert_not_called()

    @patch("diagnoses.views.predict")
    def test_processing_case_is_not_processed_again(self, mocked_predict):
        case = self.create_case()
        case.status = DiagnosisCase.Status.PROCESSING
        case.save()
        response = self.client.post(reverse("case-process", args=[case.id]))
        self.assertRedirects(response, reverse("case-analyzing", args=[case.id]))
        mocked_predict.assert_not_called()
        analyzing = self.client.get(reverse("case-analyzing", args=[case.id]))
        self.assertNotContains(analyzing, 'id="process-form"')

    @patch("diagnoses.views.predict")
    def test_failed_case_is_not_silently_reprocessed(self, mocked_predict):
        case = self.create_case()
        case.status = DiagnosisCase.Status.FAILED
        case.failure_message = "Original failure"
        case.save()
        response = self.client.post(reverse("case-process", args=[case.id]))
        self.assertRedirects(response, reverse("case-detail", args=[case.id]))
        mocked_predict.assert_not_called()

    def test_only_stale_processing_case_can_be_recovered(self):
        fresh_case = self.create_case()
        fresh_case.status = DiagnosisCase.Status.PROCESSING
        fresh_case.save()
        self.client.post(reverse("case-recover", args=[fresh_case.id]))
        fresh_case.refresh_from_db()
        self.assertEqual(fresh_case.status, DiagnosisCase.Status.PROCESSING)

        stale_case = self.create_case()
        stale_case.status = DiagnosisCase.Status.PROCESSING
        stale_case.save()
        DiagnosisCase.objects.filter(id=stale_case.id).update(
            updated_at=timezone.now() - timedelta(minutes=16)
        )
        response = self.client.post(reverse("case-recover", args=[stale_case.id]))
        self.assertRedirects(response, reverse("case-analyzing", args=[stale_case.id]))
        stale_case.refresh_from_db()
        self.assertEqual(stale_case.status, DiagnosisCase.Status.PENDING)
