from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from users.models import User

from .forms import DiagnosisUploadForm
from .inference import InferenceError, ModelUnavailableError, predict
from .models import DiagnosisCase
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
        status=DiagnosisCase.Status.COMPLETED
    )[:3]
    recent_crops = request.user.registered_crops.all()[:3]
    crop_count = request.user.registered_crops.count()
    return render(request, "diagnoses/dashboard.html", {
        "recent_cases": recent_cases,
        "recent_crops": recent_crops,
        "crop_count": crop_count,
    })


@farmer_required
def select_crop(request):
    return render(request, "diagnoses/select_crop.html", {"crops": DiagnosisCase.Crop})


@farmer_required
def upload(request, crop):
    if crop not in DiagnosisCase.Crop.values:
        raise PermissionDenied
    initial = {"location": request.user.farm_location}
    form = DiagnosisUploadForm(
        request.POST or None,
        request.FILES or None,
        initial=initial,
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
        {"form": form, "crop": crop, "crop_name": DiagnosisCase.Crop(crop).label},
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
        complete_case(case.id, request.user.id, claimed_at, prediction)
    return redirect("case-detail", case_id=case.id)


@farmer_required
@require_POST
def recover_case(request, case_id):
    case = owned_case(request, case_id)
    recover_stale_case(case.id, request.user.id)
    return redirect("case-analyzing", case_id=case.id)


@farmer_required
def case_detail(request, case_id):
    return render(
        request,
        "diagnoses/case_detail.html",
        {"case": owned_case(request, case_id)},
    )


@farmer_required
def history(request):
    return render(
        request,
        "diagnoses/history.html",
        {"cases": request.user.diagnosis_cases.all()},
    )


@farmer_required
def case_image(request, case_id):
    case = owned_case(request, case_id)
    from PIL import Image

    case.image.open("rb")
    try:
        with Image.open(case.image) as image:
            image_format = image.format
    finally:
        case.image.close()
    content_type = IMAGE_MIME_TYPES.get(image_format)
    if content_type is None:
        raise PermissionDenied
    response = FileResponse(case.image.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    return response
