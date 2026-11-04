from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language

from users.models import normalize_language

from .models import CaseFollowUp, DiagnosisCase


class ReassessmentUnavailable(Exception):
    pass


def request_reassessment(follow_up_id, farmer):
    """Explicitly reassess a case from one follow-up.

    Returns (follow_up, new_case, advisory_job). A photo creates a pending child case that
    the existing classification pipeline processes; otherwise only an advisory job is queued.
    Repeated requests for the same follow-up return the first outcome without new work.
    """
    with transaction.atomic():
        follow_up = (
            CaseFollowUp.objects.select_for_update()
            .select_related("diagnosis")
            .get(pk=follow_up_id, diagnosis__farmer=farmer)
        )
        case = follow_up.diagnosis
        if follow_up.reassessment_requested_at:
            return follow_up, follow_up.reassessment, None
        if case.parent_case_id or case.status != DiagnosisCase.Status.COMPLETED or not case.predicted_class:
            raise ReassessmentUnavailable
        new_case = None
        if follow_up.image:
            with follow_up.image.open("rb") as source:
                data = source.read()
            new_case = DiagnosisCase(
                farmer=farmer,
                registered_crop=case.registered_crop,
                parent_case=case,
                crop=case.crop,
                symptoms=follow_up.symptoms,
                location=case.location,
                location_source=case.location_source,
            )
            new_case.image.save(follow_up.image.name.rsplit("/", 1)[-1], ContentFile(data), save=False)
            new_case.save()
            follow_up.reassessment = new_case
        follow_up.reassessment_requested_at = timezone.now()
        follow_up.save(update_fields=["reassessment", "reassessment_requested_at"])
        job = None
        if new_case is None:
            from advisory.workflow import enqueue_for_case
            job = enqueue_for_case(case, farmer, normalize_language(get_language()))
    return follow_up, new_case, job
