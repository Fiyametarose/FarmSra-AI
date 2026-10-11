from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.http import FileResponse

from django import forms
from django.db import transaction, IntegrityError
from diagnoses.models import DiagnosisCase
from users.models import User
from users.forms import FarmerAccountForm
from .models import ExpertReferral, ExpertReview


from django.utils.translation import gettext_lazy as _

class RequestReviewForm(forms.Form):
    consent = forms.BooleanField(
        required=True,
        error_messages={'required': _('Explicit consent is required to request an expert review.')}
    )

def farmer_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.FARMER:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


def expert_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.EXPERT:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


# --- Farmer Endpoints ---

@farmer_required
@require_POST
def request_review(request, case_id):
    form = RequestReviewForm(request.POST)
    if not form.is_valid():
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(form.errors['consent'].as_text())

    case = get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)
    if case.status != DiagnosisCase.Status.COMPLETED:
        raise PermissionDenied("Case is not completed.")

    try:
        with transaction.atomic():
            # Check if there is an active referral
            active_referral = case.expert_referrals.exclude(state=ExpertReferral.State.REVOKED).select_for_update().first()
            if active_referral:
                return redirect('case-detail', case_id=case.id)

            ExpertReferral.objects.create(
                diagnosis=case,
                state=ExpertReferral.State.REQUESTED
            )
    except IntegrityError:
        pass

    return redirect('case-detail', case_id=case.id)


@farmer_required
@require_POST
def revoke_review(request, case_id):
    case = get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)

    with transaction.atomic():
        active_referral = case.expert_referrals.exclude(state=ExpertReferral.State.REVOKED).select_for_update().first()

        if active_referral:
            active_referral.state = ExpertReferral.State.REVOKED
            active_referral.revoked_at = timezone.now()
            active_referral.save(update_fields=['state', 'revoked_at'])

            if active_referral.assigned_expert:
                from notifications.models import Notification
                Notification.objects.get_or_create(
                    recipient=active_referral.assigned_expert,
                    event_type=Notification.EventType.CONSENT_REVOKED,
                    event_key=f"referral_{active_referral.id}_revoked",
                    defaults={'metadata': {'diagnosis_id': str(case.id)}}
                )

    return redirect('case-detail', case_id=case.id)


# --- Expert Endpoints ---

@expert_required
def dashboard(request):
    referrals = request.user.assigned_referrals.exclude(state=ExpertReferral.State.REVOKED).exclude(state=ExpertReferral.State.REVIEWED).order_by('-assigned_at')
    return render(request, "expert_reviews/dashboard.html", {"referrals": referrals})


@expert_required
def history(request):
    reviews = request.user.submitted_reviews.select_related('referral__diagnosis').order_by('-submitted_at')
    return render(request, "expert_reviews/history.html", {"reviews": reviews})


@expert_required
def case_detail(request, referral_id):
    referral = get_object_or_404(
        ExpertReferral,
        id=referral_id,
        assigned_expert=request.user
    )
    if referral.state == ExpertReferral.State.REVOKED:
        raise PermissionDenied("Farmer has revoked consent for this case.")

    case = referral.diagnosis
    return render(request, "expert_reviews/case_detail.html", {
        "referral": referral,
        "case": case
    })


class SubmitReviewForm(forms.ModelForm):
    class Meta:
        model = ExpertReview
        fields = ['outcome', 'findings', 'next_steps']

@expert_required
@require_POST
def submit_review(request, referral_id):
    with transaction.atomic():
        referral = get_object_or_404(
            ExpertReferral.objects.select_for_update(),
            id=referral_id,
            assigned_expert=request.user
        )

        if referral.state != ExpertReferral.State.ASSIGNED:
            raise PermissionDenied("Referral is not assigned or is already reviewed/revoked.")

        form = SubmitReviewForm(request.POST)
        if not form.is_valid():
            return render(request, "expert_reviews/case_detail.html", {
                "referral": referral,
                "case": referral.diagnosis,
                "error": _("Outcome and findings are required and must be valid.")
            })

        if not hasattr(referral, 'review'):
            review = form.save(commit=False)
            review.referral = referral
            review.expert = request.user
            review.save()

            from notifications.models import Notification
            Notification.objects.get_or_create(
                recipient=referral.diagnosis.farmer,
                event_type=Notification.EventType.EXPERT_REVIEWED,
                event_key=f"review_{review.id}_submitted",
                defaults={'metadata': {'referral_id': str(referral.id), 'diagnosis_id': str(referral.diagnosis.id)}}
            )

        referral.state = ExpertReferral.State.REVIEWED
        referral.save(update_fields=['state'])

    return redirect('expert-dashboard')


@expert_required
def case_image(request, referral_id):
    referral = get_object_or_404(
        ExpertReferral,
        id=referral_id,
        assigned_expert=request.user
    )
    if referral.state == ExpertReferral.State.REVOKED:
        raise PermissionDenied("Farmer has revoked consent for this case.")

    case = referral.diagnosis
    from PIL import Image

    IMAGE_MIME_TYPES = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }

    try:
        case.image.open("rb")
        try:
            with Image.open(case.image) as image:
                image_format = image.format
        finally:
            case.image.close()
    except (ValueError, FileNotFoundError, OSError):
        from django.http import Http404
        raise Http404("Image not found or unreadable.")

    content_type = IMAGE_MIME_TYPES.get(image_format)
    if content_type is None:
        raise PermissionDenied
    response = FileResponse(case.image.open("rb"), content_type=content_type)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response

@expert_required
def settings_account(request):
    form = FarmerAccountForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("expert-settings")
    return render(request, "expert_reviews/settings_account.html", {"form": form})
