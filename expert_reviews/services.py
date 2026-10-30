import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse

from notifications.models import Notification
from users.models import User

logger = logging.getLogger(__name__)


def notify(recipient, event_type, event_key, metadata, subject=None, message=None, path=None):
    notification, created = Notification.objects.get_or_create(
        recipient=recipient,
        event_type=event_type,
        event_key=event_key,
        defaults={"metadata": metadata},
    )
    if created and subject and recipient.email:
        link = f"{settings.APP_BASE_URL}{path}" if path else settings.APP_BASE_URL

        def deliver():
            try:
                send_mail(subject, f"{message}\n\nSign in to FarmSra AI: {link}", settings.DEFAULT_FROM_EMAIL, [recipient.email])
            except Exception:
                logger.exception("Transactional email delivery failed for notification %s", notification.id)

        transaction.on_commit(deliver)
    return notification


def notify_assignment(referral, assignment_key):
    path = reverse("expert-case-detail", args=[referral.id])
    notify(
        referral.assigned_expert, Notification.EventType.EXPERT_ASSIGNED,
        assignment_key, {"referral_id": str(referral.id), "diagnosis_id": str(referral.diagnosis_id)},
        "FarmSra AI case assignment", "A crop-health case has been assigned to you.", path,
    )
    notify(
        referral.diagnosis.farmer, Notification.EventType.FARMER_ASSIGNED,
        assignment_key, {"referral_id": str(referral.id), "diagnosis_id": str(referral.diagnosis_id)},
        "FarmSra AI expert assignment", "Your expert-review request has been assigned.",
        reverse("case-detail", args=[referral.diagnosis_id]),
    )


def notify_coordinators(referral):
    for coordinator in User.objects.filter(role=User.Role.ADMINISTRATOR, is_staff=True, is_active=True):
        notify(
            coordinator, Notification.EventType.COORDINATOR_REQUEST,
            f"referral_{referral.id}_coordinator_request",
            {"referral_id": str(referral.id), "diagnosis_id": str(referral.diagnosis_id)},
            "FarmSra AI referral awaiting assignment",
            "A farmer requested any available expert. Sign in to assign the request.",
            reverse("admin:expert_reviews_expertreferral_change", args=[referral.id]),
        )
