import hashlib
import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from users.models import normalize_language

from .context import build_case_context, retrieval_query_texts
from .models import AdvisoryGenerationJob, AdvisoryResult, AdvisoryVersion
from .services import (
    SECTION_LABELS,
    format_advisory,
    generate_advisory_payload,
    retrieve_evidence,
)


SAFE_SECTIONS = {
    "en": {
        "healthy": {
            "diagnosis_summary": "The model found a healthy-crop visual match. This is a tentative image assessment, not a field diagnosis.",
            "monitoring": "Continue routine monitoring and record any new symptoms with clear photographs.",
            "expert_consultation": "Consult an agricultural expert if symptoms develop or the crop declines.",
        },
        "uncertain": {
            "diagnosis_summary": "The model prediction is uncertain because its confidence is below the advisory threshold.",
            "symptoms_to_inspect": "Inspect the whole plant and record where symptoms began and how they change.",
            "immediate_actions": "Do not begin disease-specific treatment from this prediction alone.",
            "expert_consultation": "Ask an agricultural expert to verify the crop condition.",
        },
        "spider_mite": {
            "diagnosis_summary": "The model returned the two-spotted spider-mite class, which is outside this disease advisory scope.",
            "symptoms_to_inspect": "Check leaf undersides for mites, fine webbing and stippled discoloration.",
            "immediate_actions": "Do not use disease-treatment advice for this result.",
            "expert_consultation": "Seek pest-specific advice from an agricultural expert.",
        },
    },
    "sw": {
        "healthy": {
            "diagnosis_summary": "Mfumo ulipata mfanano wa picha wa zao lenye afya. Hii ni tathmini ya awali ya picha, si utambuzi wa shambani.",
            "monitoring": "Endelea kufuatilia zao mara kwa mara na urekodi dalili mpya kwa picha zilizo wazi.",
            "expert_consultation": "Wasiliana na mtaalamu wa kilimo ikiwa dalili zitatokea au hali ya zao itazorota.",
        },
        "uncertain": {
            "diagnosis_summary": "Utabiri wa mfumo hauna uhakika kwa sababu kiwango cha kujiamini kiko chini ya kizingiti cha ushauri.",
            "symptoms_to_inspect": "Chunguza mmea mzima na urekodi dalili zilipoanza na jinsi zinavyobadilika.",
            "immediate_actions": "Usianze matibabu mahususi ya ugonjwa kwa kutegemea utabiri huu pekee.",
            "expert_consultation": "Mwombe mtaalamu wa kilimo athibitishe hali ya zao.",
        },
        "spider_mite": {
            "diagnosis_summary": "Mfumo ulirudisha kundi la utitiri mwenye madoa mawili, ambalo liko nje ya wigo wa ushauri huu wa magonjwa.",
            "symptoms_to_inspect": "Chunguza chini ya majani kuona utitiri, utando mwembamba na madoadoa ya rangi.",
            "immediate_actions": "Usitumie ushauri wa matibabu ya magonjwa kwa matokeo haya.",
            "expert_consultation": "Tafuta ushauri mahususi wa wadudu kutoka kwa mtaalamu wa kilimo.",
        },
    },
    "am": {
        "healthy": {
            "diagnosis_summary": "ሞዴሉ ጤናማ ሰብልን የሚመስል ምስል አግኝቷል። ይህ ጊዜያዊ የምስል ግምገማ እንጂ የመስክ ምርመራ አይደለም።",
            "monitoring": "መደበኛ ክትትልን ይቀጥሉ እና አዲስ ምልክቶችን ግልጽ በሆኑ ፎቶዎች ይመዝግቡ።",
            "expert_consultation": "ምልክቶች ከታዩ ወይም ሰብሉ ከተዳከመ የግብርና ባለሙያን ያማክሩ።",
        },
        "uncertain": {
            "diagnosis_summary": "የሞዴሉ እርግጠኝነት ከምክር መስጫ ገደብ በታች ስለሆነ ትንበያው እርግጠኛ አይደለም።",
            "symptoms_to_inspect": "ሙሉውን ተክል ይመርምሩ፤ ምልክቶቹ የጀመሩበትንና ለውጣቸውን ይመዝግቡ።",
            "immediate_actions": "በዚህ ትንበያ ብቻ የተወሰነ የበሽታ ሕክምና አይጀምሩ።",
            "expert_consultation": "የሰብሉን ሁኔታ እንዲያረጋግጥ የግብርና ባለሙያን ያማክሩ።",
        },
        "spider_mite": {
            "diagnosis_summary": "ሞዴሉ ከዚህ የበሽታ ምክር ወሰን ውጭ የሆነውን ባለሁለት ነጥብ ሸረሪት ሚጥ ክፍል መልሷል።",
            "symptoms_to_inspect": "ከቅጠሎች በታች ሚጦች፣ ቀጭን ድር እና ነጠብጣብ ያለው የቀለም ለውጥ ይፈልጉ።",
            "immediate_actions": "ለዚህ ውጤት የበሽታ ሕክምና ምክርን አይጠቀሙ።",
            "expert_consultation": "ከግብርና ባለሙያ ተባይ-ተኮር ምክር ይጠይቁ።",
        },
    },
}


def _context_key(case, language, context):
    payload = json.dumps(
        {"case": str(case.id), "language": language, "context": context},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_kind(case):
    if case.is_spider_mite:
        return "spider_mite"
    if case.is_uncertain:
        return "uncertain"
    if case.predicted_class.endswith("healthy"):
        return "healthy"
    return None


def generate_for_case(case, farmer, language=None, retrieve_func=None, generate_func=None):
    retrieve_func = retrieve_func or retrieve_evidence
    generate_func = generate_func or generate_advisory_payload
    language = normalize_language(language or farmer.preferred_language)
    context = build_case_context(farmer, case)
    key = _context_key(case, language, context)
    now = timezone.now()

    with transaction.atomic():
        type(case).objects.select_for_update().get(pk=case.pk)
        job = AdvisoryGenerationJob.objects.select_for_update().filter(idempotency_key=key).first()
        if job and job.status == AdvisoryGenerationJob.Status.SUCCEEDED:
            return job
        if job and job.status == AdvisoryGenerationJob.Status.RUNNING:
            return job
        if job is None:
            number = (AdvisoryVersion.objects.filter(diagnosis=case).aggregate(Max("version"))["version__max"] or 0) + 1
            version = AdvisoryVersion.objects.create(
                diagnosis=case, version=number, language=language,
                status=AdvisoryVersion.Status.RUNNING, context_snapshot=context, started_at=now,
            )
            job = AdvisoryGenerationJob.objects.create(
                diagnosis=case, idempotency_key=key, language=language,
                status=AdvisoryGenerationJob.Status.RUNNING, advisory_version=version,
                started_at=now,
            )
        else:
            version = job.advisory_version
            job.status = AdvisoryGenerationJob.Status.RUNNING
            job.error_code = job.error_message = ""
            job.started_at = now
            job.completed_at = None
            job.save(update_fields=["status", "error_code", "error_message", "started_at", "completed_at"])
            version.status = AdvisoryVersion.Status.RUNNING
            version.started_at = now
            version.completed_at = None
            version.save(update_fields=["status", "started_at", "completed_at"])

    kind = _safe_kind(case)
    sources = []
    citations = []
    sections = {}
    content = None
    is_generated = False
    error_code = ""
    error_message = ""
    if kind:
        unavailable = {
            "en": "Not applicable to this result.",
            "sw": "Haitumiki kwa matokeo haya.",
            "am": "ለዚህ ውጤት አይመለከትም።",
        }[language]
        sections = {
            key: SAFE_SECTIONS[language][kind].get(key, unavailable)
            for key in SECTION_LABELS[language] if key != "sources"
        }
        content = format_advisory(sections, [], language)
    else:
        evidence = retrieve_func(
            case.crop, case.predicted_class, query_texts=retrieval_query_texts(context)
        )
        if evidence:
            content, citations, sections = generate_func(
                case.crop, case.predicted_class, evidence, context, language
            )
            is_generated = bool(content)
            if content:
                cited = {str(item) for item in citations}
                sources = [item for item in evidence if str(item.get("chunk_id")) in cited]
            else:
                error_code, error_message = "generation_unavailable", "The advisory could not be generated in the selected language. Please retry."
        else:
            error_code, error_message = "evidence_unavailable", "Approved agricultural evidence is currently unavailable. Please retry later."

    finished = timezone.now()
    with transaction.atomic():
        version = AdvisoryVersion.objects.select_for_update().get(pk=version.pk)
        job = AdvisoryGenerationJob.objects.select_for_update().get(pk=job.pk)
        if content:
            version.status = AdvisoryVersion.Status.SUCCEEDED
            version.is_generated = is_generated
            version.sections = sections
            version.content = content
            version.citations = citations
            version.source_references = sources
            version.completed_at = finished
            version.save()
            AdvisoryResult.objects.update_or_create(
                diagnosis=case,
                defaults={"is_generated": is_generated, "content": content, "retrieved_sources": sources},
            )
            job.status = AdvisoryGenerationJob.Status.SUCCEEDED
        else:
            version.status = AdvisoryVersion.Status.FAILED
            version.completed_at = finished
            version.save(update_fields=["status", "completed_at"])
            job.status = AdvisoryGenerationJob.Status.FAILED
            job.error_code = error_code
            job.error_message = error_message[:300]
        job.completed_at = finished
        job.save()
    return job
