from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import DiagnosisCase


def claim_case(case_id, farmer_id):
    claimed_at = timezone.now()
    updated = DiagnosisCase.objects.filter(
        id=case_id,
        farmer_id=farmer_id,
        status=DiagnosisCase.Status.PENDING,
    ).update(
        status=DiagnosisCase.Status.PROCESSING,
        failure_message="",
        updated_at=claimed_at,
    )
    return claimed_at if updated == 1 else None


def complete_case(case_id, farmer_id, claimed_at, prediction):
    return DiagnosisCase.objects.filter(
        id=case_id,
        farmer_id=farmer_id,
        status=DiagnosisCase.Status.PROCESSING,
        updated_at=claimed_at,
    ).update(
        status=DiagnosisCase.Status.COMPLETED,
        predicted_class=prediction.class_name,
        predicted_class_index=prediction.class_index,
        model_confidence=prediction.confidence,
        model_identifier=prediction.model_identifier,
        updated_at=timezone.now(),
    )


def fail_case(case_id, farmer_id, claimed_at, message):
    return DiagnosisCase.objects.filter(
        id=case_id,
        farmer_id=farmer_id,
        status=DiagnosisCase.Status.PROCESSING,
        updated_at=claimed_at,
    ).update(
        status=DiagnosisCase.Status.FAILED,
        failure_message=message,
        updated_at=timezone.now(),
    )


def stale_before():
    return timezone.now() - timedelta(seconds=settings.PROCESSING_STALE_AFTER_SECONDS)


def recover_stale_case(case_id, farmer_id):
    return DiagnosisCase.objects.filter(
        id=case_id,
        farmer_id=farmer_id,
        status=DiagnosisCase.Status.PROCESSING,
        updated_at__lte=stale_before(),
    ).update(
        status=DiagnosisCase.Status.PENDING,
        failure_message="",
        updated_at=timezone.now(),
    )
