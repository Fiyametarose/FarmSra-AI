import tempfile
import uuid
from PIL import Image

from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError

from users.models import User
from diagnoses.models import DiagnosisCase
from .models import ExpertReferral, ExpertReview
from notifications.models import Notification


class ExpertReviewsTests(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(
            email="farmer@example.com",
            password="testpassword",
            role=User.Role.FARMER,
            locality="Test Farm"
        )
        self.expert = User.objects.create_user(
            email="expert@example.com",
            password="testpassword",
            role=User.Role.EXPERT
        )
        self.expert2 = User.objects.create_user(
            email="expert2@example.com",
            password="testpassword",
            role=User.Role.EXPERT
        )
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="testpassword",
            role=User.Role.ADMINISTRATOR,
            is_staff=True,
            is_superuser=True
        )

        from django.core.files.uploadedfile import SimpleUploadedFile
        import io
        image = Image.new("RGB", (10, 10), color="green")
        img_io = io.BytesIO()
        image.save(img_io, format="JPEG")
        img_file = SimpleUploadedFile("test_image.jpg", img_io.getvalue(), content_type="image/jpeg")

        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=img_file,
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_healthy",
            model_confidence=0.9
        )

    def test_farmer_consent_required(self):
        self.client.login(email="farmer@example.com", password="testpassword")
        # Missing consent
        response = self.client.post(reverse("request-review", args=[self.case.id]), {})
        self.assertEqual(response.status_code, 400)

        # Invalid consent
        response = self.client.post(reverse("request-review", args=[self.case.id]), {"consent": "false"})
        self.assertEqual(response.status_code, 400)

        # Valid consent
        response = self.client.post(reverse("request-review", args=[self.case.id]), {"consent": "on"})
        self.assertRedirects(response, reverse("case-detail", args=[self.case.id]))
        self.assertTrue(ExpertReferral.objects.filter(diagnosis=self.case).exists())

    def test_farmer_cannot_request_review_for_pending_case(self):
        pending_case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            status=DiagnosisCase.Status.PENDING
        )
        self.client.login(email="farmer@example.com", password="testpassword")
        response = self.client.post(reverse("request-review", args=[pending_case.id]), {"consent": "on"})
        self.assertEqual(response.status_code, 403)

    def test_expert_cannot_request_review(self):
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.post(reverse("request-review", args=[self.case.id]), {"consent": "on"})
        self.assertEqual(response.status_code, 403)

    def test_farmer_can_revoke_review(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        self.client.login(email="farmer@example.com", password="testpassword")
        response = self.client.post(reverse("revoke-review", args=[self.case.id]))
        self.assertRedirects(response, reverse("case-detail", args=[self.case.id]))

        referral.refresh_from_db()
        self.assertEqual(referral.state, ExpertReferral.State.REVOKED)
        self.assertIsNotNone(referral.revoked_at)

    def test_duplicate_active_referrals_prevented(self):
        # Create an active referral
        ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )

        # Attempt to create another active referral
        with self.assertRaises(IntegrityError):
            ExpertReferral.objects.create(
                diagnosis=self.case,
                state=ExpertReferral.State.REQUESTED
            )

    def test_expert_can_view_assigned_case(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expert_reviews/case_detail.html")
        self.assertContains(response, "Review Case")

    def test_expert_cannot_view_unassigned_case(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertEqual(response.status_code, 404)

    def test_expert_b_cannot_view_expert_a_case(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert2@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertEqual(response.status_code, 404)

    def test_expert_cannot_view_revoked_case(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REVOKED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertEqual(response.status_code, 403)

    def test_expert_can_submit_review(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.post(reverse("expert-submit-review", args=[referral.id]), {
            "outcome": "agrees",
            "findings": "Looks good.",
            "next_steps": "Continue current treatment."
        })
        self.assertRedirects(response, reverse("expert-dashboard"))

        referral.refresh_from_db()
        self.assertEqual(referral.state, ExpertReferral.State.REVIEWED)

        review = ExpertReview.objects.get(referral=referral)
        self.assertEqual(review.expert, self.expert)
        self.assertEqual(review.outcome, "agrees")

    def test_invalid_review_submission(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        # Missing required findings
        response = self.client.post(reverse("expert-submit-review", args=[referral.id]), {
            "outcome": "agrees"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outcome and findings are required")

        referral.refresh_from_db()
        self.assertEqual(referral.state, ExpertReferral.State.ASSIGNED)
        self.assertFalse(ExpertReview.objects.filter(referral=referral).exists())

    def test_duplicate_review_submission_does_not_overwrite(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        # Create an existing review
        ExpertReview.objects.create(
            referral=referral,
            expert=self.expert,
            outcome="alternative",
            findings="Original finding."
        )
        # referral state was not updated here manually, but view expects ASSIGNED if re-submitting

        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.post(reverse("expert-submit-review", args=[referral.id]), {
            "outcome": "agrees",
            "findings": "New finding."
        })
        # Should redirect but not overwrite
        self.assertRedirects(response, reverse("expert-dashboard"))

        review = ExpertReview.objects.get(referral=referral)
        self.assertEqual(review.outcome, "alternative")

    def test_expert_can_view_image(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-image", args=[referral.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_unauthorized_users_cannot_view_expert_image(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="farmer@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-image", args=[referral.id]))
        self.assertEqual(response.status_code, 403)

    def test_farmer_email_and_location_not_in_expert_responses(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")

        # Dashboard
        response = self.client.get(reverse("expert-dashboard"))
        self.assertNotContains(response, self.farmer.email)
        self.assertNotContains(response, self.farmer.locality)

        # Case Detail
        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertNotContains(response, self.farmer.email)
        self.assertNotContains(response, self.farmer.locality)

    def test_csrf_enforced_on_consent(self):
        client = Client(enforce_csrf_checks=True)
        client.login(email="farmer@example.com", password="testpassword")
        response = client.post(reverse("request-review", args=[self.case.id]), {"consent": "on"})
        self.assertEqual(response.status_code, 403) # Forbidden due to missing CSRF token

    def test_admin_assignment_to_active_expert(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        self.client.login(email="admin@example.com", password="testpassword")
        response = self.client.post(
            reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]),
            {
                "diagnosis": self.case.id,
                "state": ExpertReferral.State.ASSIGNED,
                "assigned_expert": self.expert.id,
                "consent_granted_at_0": "2023-01-01",
                "consent_granted_at_1": "00:00:00",
            }
        )
        self.assertEqual(response.status_code, 302)
        referral.refresh_from_db()
        self.assertEqual(referral.state, ExpertReferral.State.ASSIGNED)
        self.assertEqual(referral.assigned_expert, self.expert)

    def test_admin_cannot_assign_revoked_referral(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REVOKED
        )
        self.client.login(email="admin@example.com", password="testpassword")
        response = self.client.post(
            reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]),
            {
                "diagnosis": self.case.id,
                "state": ExpertReferral.State.ASSIGNED,
                "assigned_expert": self.expert.id
            }
        )
        self.assertEqual(response.status_code, 200) # Form shows errors
        self.assertContains(response, "A revoked request cannot be reopened")

    def test_admin_cannot_assign_inactive_expert(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        inactive_expert = User.objects.create_user(
            email="inactive_expert@example.com",
            password="testpassword",
            role=User.Role.EXPERT,
            is_active=False
        )
        self.client.login(email="admin@example.com", password="testpassword")
        response = self.client.post(
            reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]),
            {
                "diagnosis": self.case.id,
                "state": ExpertReferral.State.ASSIGNED,
                "assigned_expert": inactive_expert.id
            }
        )
        self.assertEqual(response.status_code, 200) # Form shows errors
        self.assertContains(response, "Select a valid choice. That choice is not one of the available choices.")

    def test_farmer_views_submitted_review_and_retains_after_revocation(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REVIEWED,
            assigned_expert=self.expert
        )
        ExpertReview.objects.create(
            referral=referral,
            expert=self.expert,
            outcome="agrees",
            findings="Looks good."
        )
        self.client.login(email="farmer@example.com", password="testpassword")
        response = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response, "Review by Expert")
        self.assertContains(response, "Looks good.")

        # Revoke consent
        self.client.post(reverse("revoke-review", args=[self.case.id]))
        response_after = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(response_after, "Consent Revoked")
        self.assertContains(response_after, "Looks good.") # Still visible

    def test_expert_loses_access_after_revocation(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REVOKED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")

        response = self.client.get(reverse("expert-case-detail", args=[referral.id]))
        self.assertEqual(response.status_code, 403)

        response_img = self.client.get(reverse("expert-case-image", args=[referral.id]))
        self.assertEqual(response_img.status_code, 403)

    def test_fresh_consent_after_revocation(self):
        referral1 = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REVOKED
        )
        self.client.login(email="farmer@example.com", password="testpassword")
        response = self.client.post(reverse("request-review", args=[self.case.id]), {"consent": "on"})
        self.assertRedirects(response, reverse("case-detail", args=[self.case.id]))

        active_referrals = ExpertReferral.objects.filter(diagnosis=self.case).exclude(state=ExpertReferral.State.REVOKED)
        self.assertEqual(active_referrals.count(), 1)
        self.assertNotEqual(active_referrals.first().id, referral1.id)

    def test_inactive_expert_denied_workspace_access(self):
        inactive_expert = User.objects.create_user(
            email="inactive2_expert@example.com",
            password="testpassword",
            role=User.Role.EXPERT,
            is_active=False
        )
        # client.login fails for inactive users
        login_success = self.client.login(email="inactive2_expert@example.com", password="testpassword")
        self.assertFalse(login_success)
        response = self.client.get(reverse("expert-dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_expert_settings_cannot_change_role_or_staff(self):
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.post(reverse("expert-settings"), {
            "full_name": "NewName Expert",
            "email": "expert@example.com",
            "role": User.Role.ADMINISTRATOR,
            "is_staff": True
        })
        self.assertRedirects(response, reverse("expert-settings"))
        self.expert.refresh_from_db()
        self.assertEqual(self.expert.first_name, "NewName")
        self.assertEqual(self.expert.role, User.Role.EXPERT)
        self.assertFalse(self.expert.is_staff)

    def test_missing_expert_image_handled_safely(self):
        import os
        os.remove(self.case.image.path)

        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.ASSIGNED,
            assigned_expert=self.expert
        )
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("expert-case-image", args=[referral.id]))
        self.assertEqual(response.status_code, 404)

    def test_admin_deletion_disabled(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        review = ExpertReview.objects.create(
            referral=referral,
            expert=self.expert,
            outcome="agrees",
            findings="Test"
        )
        self.client.login(email="admin@example.com", password="testpassword")

        # Test Referral deletion denied
        response = self.client.get(reverse("admin:expert_reviews_expertreferral_delete", args=[referral.id]))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("admin:expert_reviews_expertreferral_delete", args=[referral.id]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)

        # Test Review deletion denied
        response = self.client.get(reverse("admin:expert_reviews_expertreview_delete", args=[review.id]))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse("admin:expert_reviews_expertreview_delete", args=[review.id]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)

    def test_expert_reassignment_notifications(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            state=ExpertReferral.State.REQUESTED
        )
        self.client.login(email="admin@example.com", password="testpassword")

        # 1. Initial assignment to expert
        response = self.client.post(reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]), {
            "state": ExpertReferral.State.REQUESTED,
            "assigned_expert": self.expert.id
        })
        self.assertEqual(response.status_code, 302)
        referral.refresh_from_db()
        self.assertEqual(referral.assigned_expert, self.expert)
        self.assertEqual(referral.state, ExpertReferral.State.ASSIGNED)

        # Expert notification created
        expert_notes = Notification.objects.filter(recipient=self.expert, event_type=Notification.EventType.EXPERT_ASSIGNED)
        self.assertEqual(expert_notes.count(), 1)
        # Farmer notification created
        farmer_notes = Notification.objects.filter(recipient=self.farmer, event_type=Notification.EventType.FARMER_ASSIGNED)
        self.assertEqual(farmer_notes.count(), 1)

        # 2. Saving again without changing expert should NOT create duplicate notifications
        response = self.client.post(reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]), {
            "state": ExpertReferral.State.ASSIGNED,
            "assigned_expert": self.expert.id
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.expert, event_type=Notification.EventType.EXPERT_ASSIGNED).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.farmer, event_type=Notification.EventType.FARMER_ASSIGNED).count(), 1)

        # 3. Reassign to expert2
        response = self.client.post(reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]), {
            "state": ExpertReferral.State.ASSIGNED,
            "assigned_expert": self.expert2.id
        })
        self.assertEqual(response.status_code, 302)
        referral.refresh_from_db()
        self.assertEqual(referral.assigned_expert, self.expert2)

        # New expert2 notified
        self.assertEqual(Notification.objects.filter(recipient=self.expert2, event_type=Notification.EventType.EXPERT_ASSIGNED).count(), 1)
        # Farmer notified of update
        self.assertEqual(Notification.objects.filter(recipient=self.farmer, event_type=Notification.EventType.FARMER_ASSIGNED).count(), 2)

        # 4. Check inbox: expert 1's old notification should NOT show actionable link
        self.client.login(email="expert@example.com", password="testpassword")
        response = self.client.get(reverse("notifications:inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["notifications"][0].is_reassigned)
        self.assertFalse(response.context["notifications"][0].is_actionable)

        # Expert 2's notification SHOULD be actionable
        self.client.login(email="expert2@example.com", password="testpassword")
        response = self.client.get(reverse("notifications:inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["notifications"][0].is_reassigned)
        self.assertTrue(response.context["notifications"][0].is_actionable)
