import json
from datetime import timedelta
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.utils.translation import get_language, gettext as _
from django.views.decorators.http import require_http_methods
from diagnoses.models import DiagnosisCase
from users.models import normalize_language
from users.views import farmer_required
from . import chat, library
from .forms import ChatQuestionForm
from .models import AdvisoryResult, AIChatMessage, AIChatSession
from .services import retrieve_evidence, generate_advisory
from .context import build_case_context, retrieval_query_texts

@login_required
def generate_case_advisory(request, case_id):
    if request.method != "POST":
        raise PermissionDenied

    case = get_object_or_404(DiagnosisCase, id=case_id)
    if case.farmer != request.user:
        raise PermissionDenied

    if request.user.role != 'farmer':
        raise PermissionDenied

    if hasattr(case, 'advisory'):
        return redirect('case-detail', case_id=case.id)

    if not case.is_eligible_for_advisory:
        if case.status != 'completed':
            messages.error(request, "Advisory generation is only available for completed cases.")
        elif not case.predicted_class:
            messages.error(request, "Advisory generation requires a predicted class.")
        elif case.model_confidence is None:
            messages.error(request, "Advisory generation requires model confidence.")
        elif case.predicted_class in ['Tomato_healthy', 'Potato___healthy', 'Corn_(maize)___healthy']:
            messages.warning(request, "Advisory generation is not needed for healthy crops.")
        else:
            messages.warning(request, "Advisory generation is not available for this condition or confidence level.")
        return redirect('case-detail', case_id=case.id)

    case_context = build_case_context(request.user, case)
    evidence = retrieve_evidence(
        crop=case.crop,
        condition=case.predicted_class,
        query_texts=retrieval_query_texts(case_context),
    )

    if not evidence:
        messages.error(request, "No verified agricultural sources found for this condition.")
        return redirect('case-detail', case_id=case.id)

    generated_text, citations = generate_advisory(
        case.crop,
        case.predicted_class,
        evidence,
        case_context=case_context,
    )

    is_generated = True
    cited_chunks = []
    if generated_text:
        content = generated_text
        cited_chunks = citations
    else:
        is_generated = False
        content = "The following evidence was retrieved from trusted agricultural sources:\n\n"
        for ev in evidence:
            content += f"**{ev['title']}** ({ev['publisher']})\n{ev['text']}\n\n"
        cited_chunks = [ev['chunk_id'] for ev in evidence]

    saved_sources = []
    for ev in evidence:
        if ev['chunk_id'] in cited_chunks:
            saved_sources.append({
                'chunk_id': ev['chunk_id'],
                'source_id': ev.get('source_id', ''),
                'title': ev.get('title', ''),
                'publisher': ev.get('publisher', ''),
                'url': ev.get('url', ''),
                'text': ev.get('text', ''),
                'date': ev.get('date', ''),
                'section': ev.get('section', '')
            })

    with transaction.atomic():
        advisory, created = AdvisoryResult.objects.get_or_create(
            diagnosis=case,
            defaults={
                'is_generated': is_generated,
                'content': content.strip(),
                'retrieved_sources': saved_sources,
            },
        )

    if created:
        messages.success(request, "Agricultural advisory retrieved successfully.")
    return redirect('case-detail', case_id=case.id)


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
