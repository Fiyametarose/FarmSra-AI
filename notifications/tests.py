from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError
from users.models import User
from .models import Notification
from expert_reviews.models import ExpertReferral
from diagnoses.models import DiagnosisCase
from crops.models import Crop

class NotificationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.farmer = User.objects.create_user(email="farmer@test.com", password="test", role=User.Role.FARMER)
        self.expert = User.objects.create_user(email="expert@test.com", password="test", role=User.Role.EXPERT)
        self.crop = Crop.objects.create(farmer=self.farmer, species="tomato", name="My Tomato")
        self.case = DiagnosisCase.objects.create(farmer=self.farmer, registered_crop=self.crop, image="test.jpg")

    def test_inbox_requires_login(self):
        response = self.client.get(reverse('notifications:inbox'))
        self.assertEqual(response.status_code, 302)

    def test_inbox_access(self):
        self.client.force_login(self.farmer)
        response = self.client.get(reverse('notifications:inbox'))
        self.assertEqual(response.status_code, 200)

    def test_idempotency_event_key(self):
        # Create first notification
        n1, created1 = Notification.objects.get_or_create(
            recipient=self.farmer,
            event_type=Notification.EventType.FARMER_ASSIGNED,
            event_key="case_123",
            defaults={"metadata": {"diagnosis_id": "123"}}
        )
        self.assertTrue(created1)

        # Attempt to create duplicate with same key
        n2, created2 = Notification.objects.get_or_create(
            recipient=self.farmer,
            event_type=Notification.EventType.FARMER_ASSIGNED,
            event_key="case_123",
            defaults={"metadata": {"diagnosis_id": "123"}}
        )
        self.assertFalse(created2)
        self.assertEqual(n1.id, n2.id)

        # Integrity error if we bypass get_or_create
        with self.assertRaises(IntegrityError):
            Notification.objects.create(
                recipient=self.farmer,
                event_type=Notification.EventType.FARMER_ASSIGNED,
                event_key="case_123",
                metadata={"diagnosis_id": "123"}
            )

    def test_mark_read_csrf_and_post_required(self):
        self.client.force_login(self.farmer)
        n = Notification.objects.create(recipient=self.farmer, event_type=Notification.EventType.CONSENT_REVOKED)

        # GET should fail (require_POST)
        response = self.client.get(reverse('notifications:mark-read', args=[n.id]))
        self.assertEqual(response.status_code, 405) # Method Not Allowed

        n.refresh_from_db()
        self.assertFalse(n.is_read())

        # POST should succeed
        response = self.client.post(reverse('notifications:mark-read', args=[n.id]))
        self.assertEqual(response.status_code, 302)
        n.refresh_from_db()
        self.assertTrue(n.is_read())

    def test_mark_all_read(self):
        self.client.force_login(self.farmer)
        Notification.objects.create(recipient=self.farmer, event_type=Notification.EventType.CONSENT_REVOKED)
        Notification.objects.create(recipient=self.farmer, event_type=Notification.EventType.FARMER_ASSIGNED)

        self.assertEqual(self.farmer.notifications.filter(read_at__isnull=True).count(), 2)

        response = self.client.post(reverse('notifications:mark-all-read'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.farmer.notifications.filter(read_at__isnull=True).count(), 0)

    def test_revoked_referral_handling_in_inbox(self):
        self.client.force_login(self.expert)
        referral = ExpertReferral.objects.create(
            diagnosis=self.case,
            assigned_expert=self.expert,
            state=ExpertReferral.State.REVOKED
        )

        Notification.objects.create(
            recipient=self.expert,
            event_type=Notification.EventType.EXPERT_ASSIGNED,
            metadata={"referral_id": str(referral.id)}
        )

        response = self.client.get(reverse('notifications:inbox'))
        self.assertEqual(response.status_code, 200)
        # Should mark is_revoked = True in the view logic
        note = response.context['notifications'][0]
        self.assertTrue(note.is_revoked)

    def test_new_message_link_respects_current_assignment_and_consent(self):
        referral = ExpertReferral.objects.create(
            diagnosis=self.case, assigned_expert=self.expert, state=ExpertReferral.State.ASSIGNED
        )
        Notification.objects.create(
            recipient=self.expert, event_type=Notification.EventType.NEW_MESSAGE,
            event_key="message-1", metadata={"referral_id": str(referral.id)},
        )
        self.client.force_login(self.expert)
        response = self.client.get(reverse('notifications:inbox'))
        self.assertContains(response, reverse('expert-case-detail', args=[referral.id]))

        referral.state = ExpertReferral.State.REVOKED
        referral.save(update_fields=['state'])
        response = self.client.get(reverse('notifications:inbox'))
        self.assertNotContains(response, reverse('expert-case-detail', args=[referral.id]))

    def test_coordinator_request_link_requires_administrator_permission(self):
        referral = ExpertReferral.objects.create(diagnosis=self.case, state=ExpertReferral.State.REQUESTED)
        admin = User.objects.create_superuser(email="coordinator@test.com", password="test")
        for recipient in (admin, self.farmer):
            Notification.objects.create(
                recipient=recipient, event_type=Notification.EventType.COORDINATOR_REQUEST,
                event_key=f"coordinator-{recipient.id}", metadata={"referral_id": str(referral.id)},
            )
        admin_url = reverse('admin:expert_reviews_expertreferral_change', args=[referral.id])
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse('notifications:inbox')), admin_url)
        self.client.force_login(self.farmer)
        self.assertNotContains(self.client.get(reverse('notifications:inbox')), admin_url)


class TranslationCoverageTests(TestCase):
    def tearDown(self):
        from django.utils.translation import deactivate
        deactivate()

    def test_translation_catalogs_amharic(self):
        from django.utils.translation import activate, gettext as _
        activate('am')
        self.assertEqual(_("Notifications"), "ማሳወቂያዎች")
        self.assertEqual(_("Local Weather"), "የአካባቢ አየር ሁኔታ")
        self.assertEqual(_("Check Weather"), "አየር ሁኔታን ያረጋግጡ")
        self.assertEqual(_("English"), "እንግሊዝኛ")
        self.assertEqual(_("Amharic"), "አማርኛ")
        self.assertEqual(_("Swahili"), "ስዋሂሊ")
        self.assertEqual(_("Your request has been assigned to an expert."), "ጥያቄዎ ለባለሙያ ተመድቧል።")
        self.assertEqual(_("You have been assigned a new case."), "አዲስ ጉዳይ ተመድቦልዎታል።")
        self.assertEqual(_("Consent was revoked for a case you were assigned."), "ለተመደቡበት ጉዳይ ፈቃድ ተሰርዟል።")
        self.assertEqual(_("An expert has reviewed your case."), "ባለሙያ ጉዳይዎን ገምግሟል።")
        self.assertEqual(_("Case Reassigned"), "ጉዳዩ በድጋሚ ተመድቧል")

    def test_translation_catalogs_swahili(self):
        from django.utils.translation import activate, gettext as _
        activate('sw')
        self.assertEqual(_("Notifications"), "Arifa")
        self.assertEqual(_("Local Weather"), "Hali ya Hewa ya Ndani")
        self.assertEqual(_("Check Weather"), "Angalia Hali ya Hewa")
        self.assertEqual(_("English"), "Kiingereza")
        self.assertEqual(_("Amharic"), "Kiamhari")
        self.assertEqual(_("Swahili"), "Kiswahili")
        self.assertEqual(_("Your request has been assigned to an expert."), "Ombi lako limekabidhiwa kwa mtaalamu.")
        self.assertEqual(_("You have been assigned a new case."), "Umepewa kesi mpya.")
        self.assertEqual(_("Consent was revoked for a case you were assigned."), "Idhini imebatilishwa kwa kesi uliyopewa.")
        self.assertEqual(_("An expert has reviewed your case."), "Mtaalamu amekagua kesi yako.")
        self.assertEqual(_("Case Reassigned"), "Kesi Imekabidhiwa Tena")

    def test_set_language_redirect(self):
        response = self.client.post(reverse('set_language'), {
            'language': 'am',
            'next': reverse('dashboard')
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies.get('django_language').value, 'am')
