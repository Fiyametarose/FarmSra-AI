import json
from datetime import timedelta
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language, gettext as _
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from diagnoses.models import DiagnosisCase
from users.models import normalize_language
from users.views import farmer_required
from . import chat, library
from .forms import ChatQuestionForm, GlobalChatForm, RecommendationActionForm
from .models import AIChatMessage, AIChatSession, RecommendationAction

@login_required
def generate_case_advisory(request, case_id):
    if request.method != "POST":
        raise PermissionDenied

    case = get_object_or_404(DiagnosisCase, id=case_id)
    if case.farmer != request.user:
        raise PermissionDenied

    if request.user.role != 'farmer':
        raise PermissionDenied

    if hasattr(case, "advisory") and not case.advisory_versions.exists():
        return redirect('case-detail', case_id=case.id)

    if case.status != DiagnosisCase.Status.COMPLETED or not case.predicted_class:
        messages.error(request, "Advisory generation is only available for completed cases.")
        return redirect('case-detail', case_id=case.id)

    from .workflow import enqueue_for_case
    job = enqueue_for_case(case, request.user, normalize_language(get_language()))
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "status": job.status,
            "error": job.error_message,
            "detail_url": request.build_absolute_uri(
                redirect('case-detail', case_id=case.id).url
            ),
        })
    messages.info(request, "Agricultural advisory generation has been queued.")
    return redirect('case-detail', case_id=case.id)


@farmer_required
@require_POST
def update_action(request, action_id):
    action = get_object_or_404(RecommendationAction, id=action_id, farmer=request.user)
    previous_status = action.status
    form = RecommendationActionForm(request.POST, instance=action)
    if form.is_valid():
        action = form.save(commit=False)
        if action.status != previous_status:
            action.status_changed_at = timezone.now()
        action.save()
        messages.success(request, _("Action updated."))
    else:
        messages.error(request, _("The action could not be updated."))
    return redirect(
        reverse("case-detail", args=[action.advisory_version.diagnosis_id]) + "#action-tracking"
    )


@farmer_required
@require_GET
def advisory_status(request, case_id):
    case = get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)
    language = normalize_language(get_language())
    version = case.advisory_versions.filter(language=language).order_by("-version").first()
    job = case.advisory_jobs.filter(language=language).order_by("-created_at").first()
    status = version.status if version else (job.status if job else "missing")
    stage = job.stage if job else ("ready" if status == "succeeded" else "preparing")
    return JsonResponse({
        "status": status,
        "stage": stage,
        "error": job.error_message if job and job.status == job.Status.FAILED else "",
        "retry_available": bool(job and job.status == job.Status.FAILED),
        "detail_url": redirect("case-detail", case_id=case.id).url,
    })


def _chat_page(request, case, form, error=None, status=200):
    session = AIChatSession.objects.filter(case=case).first()
    return render(
        request,
        "advisory/chat.html",
        {
            "case": case,
            "form": form,
            "chat_messages": list(session.messages.all()) if session else [],
            "error": error,
            "active_referral": case.expert_referrals.exclude(state="revoked").first(),
            "max_question_chars": chat.MAX_QUESTION_CHARS,
            "confidence_threshold_percent": round(settings.DIAGNOSIS_CONFIDENCE_THRESHOLD * 100, 1),
        },
        status=status,
    )


def _send_question(request, case, question, token, language):
    """Save the question and reply; return an error message or None."""
    with transaction.atomic():
        session, _created = AIChatSession.objects.get_or_create(
            case=case, defaults={"farmer": request.user}
        )
        session = AIChatSession.objects.select_for_update().get(pk=session.pk)
        if session.messages.filter(client_token=token).exists():
            return None
        questions = session.messages.filter(role=AIChatMessage.Role.FARMER)
        if questions.count() >= chat.MAX_QUESTIONS_PER_CASE:
            return _("This conversation has reached its question limit.")
        recent = AIChatMessage.objects.filter(
            session__farmer=request.user,
            role=AIChatMessage.Role.FARMER,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= chat.MAX_QUESTIONS_PER_HOUR:
            return _("Too many questions in the last hour. Please try again later.")
        history = list(session.messages.order_by("-created_at", "-id")[:20])[::-1]
        # Saving the question claims its token, so the lock is not needed during the external calls.
        AIChatMessage.objects.create(
            session=session, role=AIChatMessage.Role.FARMER, kind=AIChatMessage.Kind.QUESTION,
            content=question, language=language, client_token=token,
        )

    def release_question():
        AIChatMessage.objects.filter(session=session, client_token=token).delete()

    try:
        kind, content, sources = chat.reply_to_question(
            request.user, case, question, language, history
        )
    except chat.ChatUnavailable as exc:
        release_question()
        return str(exc)
    except Exception:
        release_question()
        raise
    with transaction.atomic():
        AIChatMessage.objects.create(
            session=session, role=AIChatMessage.Role.ASSISTANT, kind=kind,
            content=content, language=language, sources=sources,
        )
        session.save(update_fields=["updated_at"])
    return None


def _message_data(message):
    return {
        "id": message.id, "role": message.role, "kind": message.kind,
        "content": message.content, "language": message.language,
        "sources": message.sources, "created_at": message.created_at.isoformat(),
    }


def _session_data(session):
    return {
        "id": str(session.id),
        "case_id": str(session.case_id) if session.case_id else None,
        "messages": [_message_data(item) for item in session.messages.all()],
    }


@farmer_required
@require_GET
def global_chat_state(request):
    if request.user.is_staff or request.user.is_superuser:
        raise PermissionDenied
    general, _created = AIChatSession.objects.get_or_create(
        farmer=request.user, case=None
    )
    sessions = AIChatSession.objects.filter(farmer=request.user).select_related("case")
    return JsonResponse({
        "general_session": str(general.id),
        "sessions": [_session_data(item) for item in sessions],
        "cases": [
            {
                "id": str(case.id), "reference": case.reference,
                "crop": case.crop, "crop_label": case.get_crop_display(),
                "prediction": case.display_prediction,
            }
            for case in request.user.diagnosis_cases.filter(
                status=DiagnosisCase.Status.COMPLETED
            ).order_by("-created_at")[:30]
        ],
    })


@farmer_required
@require_POST
def global_chat_send(request):
    if request.user.is_staff or request.user.is_superuser:
        raise PermissionDenied
    form = GlobalChatForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": _("Check the question and selected crop or case.")}, status=400)
    case_id = form.cleaned_data.get("case")
    case = None
    crop = form.cleaned_data.get("crop")
    if case_id:
        case = get_object_or_404(
            DiagnosisCase, id=case_id, farmer=request.user,
            status=DiagnosisCase.Status.COMPLETED,
        )
        crop = case.crop
    elif not crop:
        return JsonResponse({"error": _("Select tomato, potato or maize for a general question.")}, status=400)

    with transaction.atomic():
        session, _created = AIChatSession.objects.get_or_create(
            farmer=request.user, case=case
        )
        session = AIChatSession.objects.select_for_update().get(
            pk=session.pk, farmer=request.user
        )
        token = form.cleaned_data["token"]
        existing = session.messages.filter(client_token=token).first()
        if existing:
            return JsonResponse({"duplicate": True, "session": _session_data(session)})
        if session.messages.filter(role=AIChatMessage.Role.FARMER).count() >= chat.MAX_QUESTIONS_PER_CASE:
            return JsonResponse({"error": _("This conversation has reached its question limit.")}, status=429)
        recent = AIChatMessage.objects.filter(
            session__farmer=request.user, role=AIChatMessage.Role.FARMER,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= chat.MAX_QUESTIONS_PER_HOUR:
            return JsonResponse({"error": _("Too many questions in the last hour. Please try again later.")}, status=429)
        history = list(session.messages.order_by("-created_at", "-id")[:20])[::-1]
        question = form.cleaned_data["question"]
        language = normalize_language(get_language())
        AIChatMessage.objects.create(
            session=session, role=AIChatMessage.Role.FARMER,
            kind=AIChatMessage.Kind.QUESTION, content=question,
            language=language, client_token=token,
        )

    try:
        kind, content, sources = chat.reply_to_question(
            request.user, case, question, language, history, crop=crop
        )
    except chat.ChatUnavailable as exc:
        session.messages.filter(client_token=token).delete()
        return JsonResponse({"error": str(exc), "retry": True}, status=503)
    except Exception:
        session.messages.filter(client_token=token).delete()
        raise
    AIChatMessage.objects.create(
        session=session, role=AIChatMessage.Role.ASSISTANT, kind=kind,
        content=content, language=language, sources=sources,
    )
    session.save(update_fields=["updated_at"])
    return JsonResponse({"session": _session_data(session)})


@farmer_required
@require_http_methods(["GET", "POST"])
def case_chat(request, case_id):
    case = get_object_or_404(DiagnosisCase, id=case_id, farmer=request.user)
    if case.status != DiagnosisCase.Status.COMPLETED or not case.predicted_class:
        messages.warning(request, _("The AI assistant is available once the analysis has completed."))
        return redirect("case-detail", case_id=case.id)
    if request.method == "GET":
        return _chat_page(request, case, ChatQuestionForm())

    form = ChatQuestionForm(request.POST)
    if not form.is_valid():
        return _chat_page(request, case, form, status=400)
    language = normalize_language(get_language())
    error = _send_question(
        request, case, form.cleaned_data["question"], form.cleaned_data["token"], language
    )
    if error:
        return _chat_page(request, case, form, error=error)
    return redirect("case-chat", case_id=case.id)


@farmer_required
def guidance_library(request):
    documents = library.load_documents()
    crop = request.GET.get("crop", "")
    condition = request.GET.get("condition", "")
    query = request.GET.get("q", "")[:100]
    if crop not in library.CROP_PREFIXES:
        crop = ""
    if condition not in {d["condition"] for d in documents}:
        condition = ""
    return render(
        request,
        "advisory/library.html",
        {
            "documents": library.filter_documents(documents, crop, condition, query),
            "total_documents": len(documents),
            "all_documents": documents,
            "crops": DiagnosisCase.Crop,
            "selected_crop": crop,
            "selected_condition": condition,
            "query": query,
        },
    )
