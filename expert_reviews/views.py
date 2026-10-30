from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.http import FileResponse

from django import forms
from django.db import transaction, IntegrityError
from diagnoses.models import DiagnosisCase
from users.models import User
from users.forms import FarmerAccountForm
from .models import ExpertMessage, ExpertProfile, ExpertReferral, ExpertReview
from .forms import ExpertMessageForm, RequestReviewForm
from .services import notify, notify_assignment, notify_coordinators
from notifications.models import Notification


from django.utils.translation import gettext_lazy as _

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
    case = get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)
    if case.status != DiagnosisCase.Status.COMPLETED:
        raise PermissionDenied("Case is not completed.")
    form = RequestReviewForm(request.POST)
    if not form.is_valid():
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(form.errors.as_text())

    try:
        with transaction.atomic():
            # Check if there is an active referral
            active_referral = case.expert_referrals.exclude(state=ExpertReferral.State.REVOKED).select_for_update().first()
            if active_referral:
                return redirect('case-detail', case_id=case.id)

            selected_expert = form.cleaned_data["recipient"]
            referral = ExpertReferral.objects.create(
                diagnosis=case,
                state=ExpertReferral.State.ASSIGNED if selected_expert else ExpertReferral.State.REQUESTED,
                recipient_choice=(ExpertReferral.RecipientChoice.SPECIFIC if selected_expert else ExpertReferral.RecipientChoice.ANY),
                selected_expert=selected_expert,
                assigned_expert=selected_expert,
                assigned_at=timezone.now() if selected_expert else None,
            )
            if selected_expert:
                notify_assignment(referral, f"referral_{referral.id}_direct_assignment")
            else:
                notify_coordinators(referral)
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
    active_referrals = request.user.assigned_referrals.exclude(
        state=ExpertReferral.State.REVOKED
    ).select_related('diagnosis').order_by('-assigned_at')
    pending_referrals = active_referrals.filter(state=ExpertReferral.State.ASSIGNED)
    completed_reviews_count = request.user.submitted_reviews.count()

    return render(
        request,
        "expert_reviews/dashboard.html",
        {
            "referrals": pending_referrals,
            "active_referrals": active_referrals,
            "pending_count": pending_referrals.count(),
            "completed_count": completed_reviews_count,
            "total_assigned_count": active_referrals.count(),
        },
    )


@expert_required
def assigned_cases(request):
    referrals = request.user.assigned_referrals.exclude(
        state=ExpertReferral.State.REVOKED
    ).select_related('diagnosis').order_by('-assigned_at')
    filter_state = request.GET.get('state', 'all')
    if filter_state == 'pending':
        filtered = referrals.filter(state=ExpertReferral.State.ASSIGNED)
    elif filter_state == 'reviewed':
        filtered = referrals.filter(state=ExpertReferral.State.REVIEWED)
    else:
        filtered = referrals

    return render(
        request,
        "expert_reviews/assigned_cases.html",
        {
            "referrals": filtered,
            "filter_state": filter_state,
            "total_count": referrals.count(),
            "pending_count": referrals.filter(state=ExpertReferral.State.ASSIGNED).count(),
            "reviewed_count": referrals.filter(state=ExpertReferral.State.REVIEWED).count(),
        },
    )


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

    if referral.state not in (ExpertReferral.State.ASSIGNED, ExpertReferral.State.REVIEWED):
        raise PermissionDenied
    case = referral.diagnosis
    referral.messages.exclude(sender=request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
    return render(request, "expert_reviews/case_detail.html", {
        "referral": referral,
        "case": case,
        "expert_messages": referral.messages.select_related("sender"),
        "message_form": ExpertMessageForm(),
    })


def _message_referral_for(request, referral_id):
    referral = get_object_or_404(ExpertReferral.objects.select_related("diagnosis__farmer"), id=referral_id)
    is_farmer = request.user.id == referral.diagnosis.farmer_id
    is_expert = request.user.id == referral.assigned_expert_id
    if not (is_farmer or is_expert) or referral.state not in (ExpertReferral.State.ASSIGNED, ExpertReferral.State.REVIEWED):
        raise PermissionDenied
    return referral, is_farmer


@login_required
@require_POST
def send_message(request, referral_id):
    referral, is_farmer = _message_referral_for(request, referral_id)
    form = ExpertMessageForm(request.POST)
    if not form.is_valid():
        if is_farmer:
            return redirect("case-detail", case_id=referral.diagnosis_id)
        return render(request, "expert_reviews/case_detail.html", {
            "referral": referral, "case": referral.diagnosis,
            "expert_messages": referral.messages.select_related("sender"), "message_form": form,
        }, status=400)
    with transaction.atomic():
        locked = ExpertReferral.objects.select_for_update().get(pk=referral.pk)
        if locked.state not in (ExpertReferral.State.ASSIGNED, ExpertReferral.State.REVIEWED):
            raise PermissionDenied
        if request.user.id not in (locked.diagnosis.farmer_id, locked.assigned_expert_id):
            raise PermissionDenied
        message = form.save(commit=False)
        message.referral = locked
        message.sender = request.user
        message.save()
        recipient = locked.assigned_expert if is_farmer else locked.diagnosis.farmer
        path = reverse("expert-case-detail", args=[locked.id]) if is_farmer else reverse("case-detail", args=[locked.diagnosis_id])
        notify(recipient, Notification.EventType.NEW_MESSAGE, f"message_{message.id}",
               {"referral_id": str(locked.id), "diagnosis_id": str(locked.diagnosis_id), "message_id": str(message.id)},
               "New FarmSra AI message", "You have a new message about a crop-health case.", path)
    return redirect("case-detail", case_id=referral.diagnosis_id) if is_farmer else redirect("expert-case-detail", referral_id=referral.id)


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

            notify(
                referral.diagnosis.farmer, Notification.EventType.EXPERT_REVIEWED,
                f"review_{review.id}_submitted",
                {'referral_id': str(referral.id), 'diagnosis_id': str(referral.diagnosis.id)},
                "FarmSra AI expert review submitted",
                "An expert has submitted a review for your crop-health case.",
                reverse("case-detail", args=[referral.diagnosis_id]),
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
