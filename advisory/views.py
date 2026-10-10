import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from diagnoses.models import DiagnosisCase
from .models import AdvisoryResult
from .services import retrieve_evidence, generate_advisory

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

    evidence = retrieve_evidence(
        crop=case.crop,
        condition=case.predicted_class,
        query_texts=None
    )

    if not evidence:
        messages.error(request, "No verified agricultural sources found for this condition.")
        return redirect('case-detail', case_id=case.id)

    generated_text, citations = generate_advisory(case.crop, case.predicted_class, evidence)

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

    advisory = AdvisoryResult.objects.create(
        diagnosis=case,
        is_generated=is_generated,
        content=content.strip(),
        retrieved_sources=saved_sources
    )

    messages.success(request, "Agricultural advisory retrieved successfully.")
    return redirect('case-detail', case_id=case.id)
