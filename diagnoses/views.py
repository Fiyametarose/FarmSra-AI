from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.translation import get_language
from django.urls import reverse
from datetime import timedelta

from users.models import User, normalize_language
from expert_reviews.models import ExpertMessage, ExpertReferral
from expert_reviews.forms import ExpertMessageForm, RequestReviewForm

from django.contrib import messages
from django.http import Http404
from django.utils.translation import gettext as _

from .followups import ReassessmentUnavailable, request_reassessment
from .forms import CaseFollowUpForm, DiagnosisUploadForm
from .inference import InferenceError, ModelUnavailableError, predict
from .models import CaseFollowUp, DiagnosisCase
from .processing import (
    claim_case,
    complete_case,
    fail_case,
    recover_stale_case,
    stale_before,
)


IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def farmer_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.FARMER:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def owned_case(request, case_id):
    return get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)


@farmer_required
def dashboard(request):
    recent_cases = request.user.diagnosis_cases.filter(
        status=DiagnosisCase.Status.COMPLETED, parent_case__isnull=True
    )[:3]
    recent_crops = request.user.registered_crops.all()[:3]
    crop_count = request.user.registered_crops.count()
    total_diagnoses_count = request.user.diagnosis_cases.filter(parent_case__isnull=True).count()
    return render(request, "diagnoses/dashboard.html", {
        "recent_cases": recent_cases,
        "recent_crops": recent_crops,
        "crop_count": crop_count,
        "total_diagnoses_count": total_diagnoses_count,
    })


@farmer_required
def select_crop(request):
    return render(request, "diagnoses/select_crop.html", {"crops": DiagnosisCase.Crop})


@farmer_required
def upload(request, crop):
    if crop not in DiagnosisCase.Crop.values:
        raise PermissionDenied
    registered_crops = request.user.registered_crops.filter(species=crop)
    form = DiagnosisUploadForm(
        request.POST or None,
        request.FILES or None,
        farmer=request.user,
        crop_species=crop,
    )
    if request.method == "POST" and form.is_valid():
        case = form.save(commit=False)
        case.farmer = request.user
        case.crop = crop
        case.save()
        return redirect("case-analyzing", case_id=case.id)
    return render(
        request,
        "diagnoses/upload.html",
        {
            "form": form,
            "crop": crop,
            "crop_name": DiagnosisCase.Crop(crop).label,
            "crop_locations": {
                str(registered_crop.id): registered_crop.location
                for registered_crop in registered_crops
            },
            "farm_location": request.user.farm_location,
            "preserve_location_value": form.is_bound,
        },
    )


@farmer_required
def analyzing(request, case_id):
    case = owned_case(request, case_id)
    if case.status in {DiagnosisCase.Status.COMPLETED, DiagnosisCase.Status.FAILED}:
        return redirect("case-detail", case_id=case.id)
    recovery_available = (
        case.status == DiagnosisCase.Status.PROCESSING
        and case.updated_at <= stale_before()
    )
    return render(
        request,
        "diagnoses/analyzing.html",
        {
            "case": case,
            "should_submit": case.status == DiagnosisCase.Status.PENDING,
            "recovery_available": recovery_available,
            "stale_minutes": settings.PROCESSING_STALE_AFTER_SECONDS // 60,
        },
    )


@farmer_required
@require_POST
def process_case(request, case_id):
    case = owned_case(request, case_id)
    generated_job = None
    claimed_at = claim_case(case.id, request.user.id)
    if claimed_at is None:
        case.refresh_from_db(fields=["status"])
        if case.status in {DiagnosisCase.Status.COMPLETED, DiagnosisCase.Status.FAILED}:
            return redirect("case-detail", case_id=case.id)
        return redirect("case-analyzing", case_id=case.id)

    try:
        prediction = predict(case.crop, case.image.path)
    except (ModelUnavailableError, InferenceError) as exc:
        fail_case(case.id, request.user.id, claimed_at, str(exc))
    else:
        completed = complete_case(case.id, request.user.id, claimed_at, prediction)
        if completed:
            case.refresh_from_db()
            from weather.services import capture_automatic_case_weather
            from advisory.workflow import enqueue_for_case
            capture_automatic_case_weather(case)
            generated_job = enqueue_for_case(case, request.user)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        case.refresh_from_db()
        latest_job = generated_job or case.advisory_jobs.order_by("-created_at").first()
        return JsonResponse({
            "case_status": case.status,
            "advisory_status": latest_job.status if latest_job else None,
            "detail_url": request.build_absolute_uri(
                reverse("case-detail", args=[case.id])
            ),
        })
    return redirect("case-detail", case_id=case.id)


@farmer_required
@require_POST
def recover_case(request, case_id):
    case = owned_case(request, case_id)
    recover_stale_case(case.id, request.user.id)
    return redirect("case-analyzing", case_id=case.id)


@farmer_required
def case_detail(request, case_id):
    return render_case_detail(request, owned_case(request, case_id))


def render_case_detail(request, case, follow_up_form=None, status=200):
    advisory_language = normalize_language(get_language())
    advisory_version = case.advisory_versions.filter(
        language=advisory_language, status="succeeded"
    ).order_by("-version").first()
    latest_advisory_job = case.advisory_jobs.filter(
        language=advisory_language
    ).order_by("-created_at").first()
    if (
        case.status == DiagnosisCase.Status.COMPLETED
        and case.predicted_class
        and advisory_version is None
        and latest_advisory_job is None
    ):
        from advisory.workflow import enqueue_for_case
        latest_advisory_job = enqueue_for_case(case, request.user, advisory_language)
    auto_capture_window = timedelta(
        minutes=settings.CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES
    )
    case_age = timezone.now() - case.completed_at if case.completed_at else None
    weather_auto_capture_eligible = (
        case.status == DiagnosisCase.Status.COMPLETED
        and case_age is not None
        and timedelta(0) <= case_age <= auto_capture_window
    )
    weather_context = getattr(case, "weather_context", None)
    weather_is_late_lookup = bool(
        weather_context
        and (
            case.completed_at is None
            or abs(weather_context.observed_at - case.completed_at) > auto_capture_window
        )
    )
    from advisory.actions import ensure_actions
    from advisory.forms import RecommendationActionForm
    from advisory.models import RecommendationAction
    from advisory.services import SECTION_LABELS

    root_case = case.parent_case or case
    family_ids = [root_case.id, *root_case.reassessments.values_list("id", flat=True)]
    tracked_actions = []
    if advisory_version:
        ensure_actions(advisory_version, request.user)
        tracked_actions = list(advisory_version.tracked_actions.filter(farmer=request.user))
        labels = SECTION_LABELS[advisory_version.language]
        for action in tracked_actions:
            action.form = RecommendationActionForm(instance=action)
            action.section_label = labels.get(action.section_key, action.section_key)
    action_history = RecommendationAction.objects.filter(
        farmer=request.user, advisory_version__diagnosis_id__in=family_ids
    ).exclude(advisory_version=advisory_version).select_related(
        "advisory_version", "advisory_version__diagnosis"
    ).order_by("-created_at")
    follow_ups = list(root_case.follow_ups.select_related("reassessment"))
    assessments = [root_case, *root_case.reassessments.order_by("created_at")]
    advisory_updating = bool(
        advisory_version and latest_advisory_job
        and latest_advisory_job.status in {"pending", "running"}
    )

    active_referral = case.expert_referrals.exclude(state='revoked').first()
    revoked_referrals = case.expert_referrals.filter(state='revoked', review__isnull=False).order_by('-revoked_at')
    if active_referral:
        active_referral.messages.exclude(sender=request.user).filter(read_at__isnull=True).update(read_at=timezone.now())

    return render(
        request,
        "diagnoses/case_detail.html",
        {
            "case": case,
            "active_referral": active_referral,
            "revoked_referrals": revoked_referrals,
            "request_review_form": RequestReviewForm(),
            "expert_messages": active_referral.messages.select_related("sender") if active_referral else (),
            "revoked_messages": ExpertMessage.objects.filter(referral__diagnosis=case, referral__state=ExpertReferral.State.REVOKED).select_related("sender"),
            "message_form": ExpertMessageForm(),
            "weather_auto_capture_eligible": weather_auto_capture_eligible,
            "weather_is_late_lookup": weather_is_late_lookup,
            "confidence_threshold_percent": round(
                settings.DIAGNOSIS_CONFIDENCE_THRESHOLD * 100, 1
            ),
            "advisory_version": advisory_version,
            "advisory_versions": case.advisory_versions.filter(status="succeeded"),
            "latest_advisory_job": latest_advisory_job,
            "root_case": root_case,
            "tracked_actions": tracked_actions,
            "action_history": action_history,
            "follow_ups": follow_ups,
            "follow_up_form": follow_up_form or (CaseFollowUpForm() if not case.parent_case_id else None),
            "assessments": assessments,
            "advisory_updating": advisory_updating,
        },
        status=status,
    )


@farmer_required
@require_POST
def add_follow_up(request, case_id):
    case = owned_case(request, case_id)
    if case.parent_case_id or case.status != DiagnosisCase.Status.COMPLETED:
        raise PermissionDenied
    try:
        repeated = case.follow_ups.filter(request_token=request.POST.get("request_token")).exists()
    except (ValueError, ValidationError):
        repeated = False
    if repeated:
        return redirect(reverse("case-detail", args=[case.id]) + "#follow-ups")
    form = CaseFollowUpForm(request.POST, request.FILES)
    if not form.is_valid():
        return render_case_detail(request, case, follow_up_form=form, status=400)
    follow_up = form.save(commit=False)
    follow_up.diagnosis = case
    follow_up.save()
    messages.success(request, _("Follow-up observation saved."))
    return redirect(reverse("case-detail", args=[case.id]) + "#follow-ups")


@farmer_required
@require_POST
def reassess_case(request, case_id, follow_up_id):
    case = owned_case(request, case_id)
    get_object_or_404(CaseFollowUp, id=follow_up_id, diagnosis=case)
    try:
        follow_up, new_case, _job = request_reassessment(follow_up_id, request.user)
    except ReassessmentUnavailable:
        raise PermissionDenied
    if new_case is not None and new_case.status == DiagnosisCase.Status.PENDING:
        return redirect("case-analyzing", case_id=new_case.id)
    messages.info(request, _("Reassessment requested. Updated guidance will appear on this page when ready."))
    return redirect(reverse("case-detail", args=[case.id]) + "#follow-ups")


@farmer_required
def follow_up_image(request, case_id, follow_up_id):
    case = owned_case(request, case_id)
    follow_up = get_object_or_404(CaseFollowUp, id=follow_up_id, diagnosis=case)
    if not follow_up.image:
        raise Http404
    return image_response(follow_up.image)


@farmer_required
def history(request):
    return render(
        request,
        "diagnoses/history.html",
        {"cases": request.user.diagnosis_cases.filter(parent_case__isnull=True)},
    )


def image_response(field):
    from PIL import Image

    field.open("rb")
    try:
        with Image.open(field) as image:
            image_format = image.format
    finally:
        field.close()
    content_type = IMAGE_MIME_TYPES.get(image_format)
    if content_type is None:
        raise PermissionDenied
    response = FileResponse(field.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@farmer_required
def case_image(request, case_id):
    return image_response(owned_case(request, case_id).image)


@farmer_required
def case_guidance(request, case_id):
    case = owned_case(request, case_id)
    advisory = case.advisory_versions.filter(
        language=normalize_language(get_language()), status="succeeded"
    ).order_by("-version").first()
    return render(
        request,
        "diagnoses/guidance.html",
        {
            "case": case,
            "advisory": advisory,
        },
    )



@farmer_required
def agricultural_experts(request):
    active_referrals = (
        ExpertReferral.objects.filter(diagnosis__farmer=request.user)
        .exclude(state=ExpertReferral.State.REVOKED)
        .select_related("diagnosis", "assigned_expert")
        .order_by("-created_at")
    )
    return render(
        request,
        "diagnoses/experts.html",
        {
            "active_referrals": active_referrals,
            "expert_users": User.objects.filter(role=User.Role.EXPERT, is_active=True).select_related("expert_profile").order_by("first_name", "last_name", "email"),
        },
    )
