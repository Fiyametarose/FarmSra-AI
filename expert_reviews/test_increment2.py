from unittest.mock import patch

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from diagnoses.models import DiagnosisCase
from notifications.models import Notification
from users.models import User
from .models import ExpertMessage, ExpertProfile, ExpertReferral


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReferralCommunicationTests(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(email="farmer2@test.com", password="pw", role="farmer")
        self.other_farmer = User.objects.create_user(email="otherfarmer@test.com", password="pw", role="farmer")
        self.expert = User.objects.create_user(email="expert2@test.com", password="pw", role="expert", first_name="Asha", last_name="K")
        self.other_expert = User.objects.create_user(email="otherexpert@test.com", password="pw", role="expert")
        self.admin = User.objects.create_superuser(email="coordinator@test.com", password="pw")
        self.profile = ExpertProfile.objects.create(
            user=self.expert, specialization="Plant pathology", speaks_english=True,
            availability=ExpertProfile.Availability.AVAILABLE,
        )
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", image=SimpleUploadedFile("leaf.jpg", b"image"),
            status=DiagnosisCase.Status.COMPLETED, predicted_class="healthy", model_confidence=.9,
        )

    def test_directory_shows_metadata_without_email(self):
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("agricultural-experts"))
        self.assertContains(response, "Plant pathology")
        self.assertContains(response, "English")
        self.assertNotContains(response, self.expert.email)

    def test_directory_includes_active_expert_without_profile_but_not_as_choice(self):
        incomplete = User.objects.create_user(
            email="incomplete@test.com", password="pw", role="expert", first_name="Incomplete"
        )
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("agricultural-experts"))
        self.assertContains(response, "Incomplete")
        self.assertContains(response, "Profile incomplete")
        self.assertContains(response, "Unknown availability")
        form_response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertNotContains(form_response, f'value="{incomplete.id}"')

    def test_recipient_placeholder_requires_explicit_selection(self):
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, '<option value="" selected>Choose a recipient</option>', html=True)
        response = self.client.post(
            reverse("request-review", args=[self.case.id]), {"recipient": "", "consent": "on"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ExpertReferral.objects.exists())

    def test_farmer_status_copy_matches_referral_state(self):
        self.client.force_login(self.farmer)
        referral = ExpertReferral.objects.create(diagnosis=self.case, state=ExpertReferral.State.REQUESTED)
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, "queued for assignment")
        referral.state = ExpertReferral.State.ASSIGNED
        referral.assigned_expert = self.expert
        referral.save(update_fields=["state", "assigned_expert"])
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, "Your case is assigned to an agricultural expert.")
        self.assertNotContains(response, "queued for assignment")

    def test_specific_expert_is_selected_assigned_and_notified(self):
        self.client.force_login(self.farmer)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("request-review", args=[self.case.id]), {
                "recipient": str(self.expert.id), "consent": "on",
            })
        self.assertEqual(response.status_code, 302)
        referral = ExpertReferral.objects.get(diagnosis=self.case)
        self.assertEqual(referral.selected_expert, self.expert)
        self.assertEqual(referral.assigned_expert, self.expert)
        self.assertEqual(referral.state, ExpertReferral.State.ASSIGNED)
        self.assertTrue(Notification.objects.filter(recipient=self.expert, event_type="expert_assigned").exists())
        self.assertEqual(len(mail.outbox), 2)

    def test_any_expert_stays_unassigned_and_notifies_coordinator(self):
        self.client.force_login(self.farmer)
        self.client.post(reverse("request-review", args=[self.case.id]), {"recipient": "any", "consent": "on"})
        referral = ExpertReferral.objects.get(diagnosis=self.case)
        self.assertIsNone(referral.assigned_expert)
        self.assertEqual(referral.state, ExpertReferral.State.REQUESTED)
        self.assertTrue(Notification.objects.filter(recipient=self.admin, event_type="coordinator_request").exists())

    def test_inactive_or_unavailable_expert_is_rejected(self):
        self.profile.availability = ExpertProfile.Availability.UNAVAILABLE
        self.profile.save()
        self.client.force_login(self.farmer)
        response = self.client.post(reverse("request-review", args=[self.case.id]), {
            "recipient": str(self.expert.id), "consent": "on",
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ExpertReferral.objects.exists())

    def _assigned_referral(self):
        return ExpertReferral.objects.create(
            diagnosis=self.case, recipient_choice="specific", selected_expert=self.expert,
            assigned_expert=self.expert, state=ExpertReferral.State.ASSIGNED,
        )

    def test_messages_are_ordered_notified_and_isolated(self):
        referral = self._assigned_referral()
        self.client.force_login(self.farmer)
        self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "First"})
        self.client.force_login(self.expert)
        self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "Second"})
        self.assertEqual(list(referral.messages.values_list("body", flat=True)), ["First", "Second"])
        self.assertEqual(Notification.objects.filter(event_type="new_message").count(), 2)
        self.client.force_login(self.other_expert)
        self.assertEqual(self.client.get(reverse("expert-case-detail", args=[referral.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "Forged"}).status_code, 403)
        self.client.force_login(self.other_farmer)
        self.assertEqual(self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "Forged"}).status_code, 403)

    def test_revocation_immediately_blocks_expert_but_farmer_retains_history(self):
        referral = self._assigned_referral()
        ExpertMessage.objects.create(referral=referral, sender=self.expert, body="Audit history")
        self.client.force_login(self.farmer)
        self.client.post(reverse("revoke-review", args=[self.case.id]))
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, "Audit history")
        self.client.force_login(self.expert)
        self.assertEqual(self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "No"}).status_code, 403)

    def test_invalid_message_and_csrf(self):
        referral = self._assigned_referral()
        self.client.force_login(self.expert)
        self.assertEqual(self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": " "}).status_code, 400)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.expert)
        self.assertEqual(csrf_client.post(reverse("expert-send-message", args=[referral.id]), {"body": "No token"}).status_code, 403)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    @patch("expert_reviews.services.send_mail", side_effect=OSError("SMTP unavailable"))
    def test_email_failure_does_not_rollback_message(self, send_mail):
        referral = self._assigned_referral()
        self.client.force_login(self.farmer)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("expert-send-message", args=[referral.id]), {"body": "Saved"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ExpertMessage.objects.filter(body="Saved").exists())
