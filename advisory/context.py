from django.core.exceptions import PermissionDenied
from django.conf import settings
from datetime import timedelta

from diagnoses.models import DiagnosisCase
from users.models import User

from .models import AdvisoryVersion, RecommendationAction


def _bounded(value, length=1000):
    return (value or "").strip()[:length]


def _follow_up_context(root, case):
    follow_ups = list(root.follow_ups.order_by("-observed_at", "-created_at")[:10])
    reassessment = None
    if case.parent_case_id:
        reassessment = {"kind": "photo", "new_image_classified": True}
    else:
        pending = next(
            (item for item in follow_ups
             if item.reassessment_requested_at and item.reassessment_id is None),
            None,
        )
        if pending:
            reassessment = {"kind": "symptoms_only", "new_image_classified": False}
    return {
        "follow_ups": [
            {
                "observed_at": item.observed_at.isoformat(),
                "symptoms": _bounded(item.symptoms, 500),
                "notes": _bounded(item.notes, 500),
                "actions_taken": _bounded(item.actions_taken, 500),
                "has_photo": bool(item.image),
                "reassessment_requested": item.reassessment_requested_at is not None,
            }
            for item in reversed(follow_ups)
        ],
        "reassessment": reassessment,
    }


def _tracking_context(user, case, root, family_ids):
    actions = RecommendationAction.objects.filter(
        farmer=user, advisory_version__diagnosis_id__in=family_ids
    ).select_related("advisory_version").order_by("-created_at")[:20]
    versions = AdvisoryVersion.objects.filter(
        diagnosis_id__in=family_ids, status=AdvisoryVersion.Status.SUCCEEDED
    ).select_related("diagnosis").order_by("-completed_at", "-version")[:3]
    assessments = DiagnosisCase.objects.filter(
        id__in=family_ids, status=DiagnosisCase.Status.COMPLETED
    ).exclude(id=case.id).order_by("completed_at")
    return {
        "recommended_actions": [
            {
                "advisory_version": action.advisory_version.version,
                "section": action.section_key,
                "recommendation": _bounded(action.recommendation, 300),
                "status": action.status,
                "farmer_notes": _bounded(action.farmer_notes, 300),
                "status_changed_at": action.status_changed_at.isoformat(),
            }
            for action in actions
        ],
        "previous_advisories": [
            {
                "case_reference": str(version.diagnosis.reference),
                "version": version.version,
                "language": version.language,
                "created_at": version.created_at.isoformat(),
                "diagnosis_summary": _bounded(version.sections.get("diagnosis_summary"), 400),
                "immediate_actions": _bounded(version.sections.get("immediate_actions"), 400),
                "monitoring": _bounded(version.sections.get("monitoring"), 300),
            }
            for version in versions
        ],
        "previous_assessments": [
            {
                "case_reference": str(item.reference),
                "type": "original" if item.id == root.id else "photo_reassessment",
                "predicted_class": item.predicted_class,
                "confidence": item.model_confidence,
                "assessed_at": item.completed_at.isoformat() if item.completed_at else None,
            }
            for item in assessments
        ],
    }


def build_case_context(user, case):
    if (
        not user.is_authenticated
        or user.role != User.Role.FARMER
        or case.farmer_id != user.id
    ):
        raise PermissionDenied

    weather = getattr(case, "weather_context", None)
    weather_timing = None
    if weather is not None:
        capture_window = timedelta(minutes=settings.CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES)
        if case.completed_at is None:
            weather_timing = "completion_time_unknown"
        elif abs(weather.observed_at - case.completed_at) <= capture_window:
            weather_timing = "near_completion"
        else:
            weather_timing = "later_observation"
    root = case.parent_case or case
    family_ids = [root.id, *root.reassessments.values_list("id", flat=True)]
    history = (
        DiagnosisCase.objects.filter(
            farmer=user,
            crop=case.crop,
            status=DiagnosisCase.Status.COMPLETED,
            parent_case__isnull=True,
        )
        .exclude(id__in=family_ids)
        .order_by("-created_at")[:5]
    )
    return {
        **_follow_up_context(root, case),
        **_tracking_context(user, case, root, family_ids),
        "crop": case.crop,
        "image_reference": case.image.name if case.image else None,
        "case_location": _bounded(case.location, 500) or None,
        "farm_location": _bounded(user.farm_location, 500) or None,
        "registered_crop": None if case.registered_crop is None else {
            "name": case.registered_crop.name,
            "location": _bounded(case.registered_crop.location, 500) or None,
            "planting_date": (
                case.registered_crop.planting_date.isoformat()
                if case.registered_crop.planting_date else None
            ),
            "notes": _bounded(case.registered_crop.notes, 500) or None,
        },
        "symptoms": _bounded(case.symptoms),
        "predicted_class": case.predicted_class,
        "confidence": case.model_confidence,
        "is_uncertain": case.is_uncertain,
        "weather": None if weather is None else {
            "location": weather.location_name,
            "location_source": weather.location_source,
            "location_source_label": str(weather.get_location_source_display()),
            "provider": weather.provider,
            "observed_at": weather.observed_at.isoformat(),
            "temperature": weather.temperature,
            "humidity": weather.humidity,
            "precipitation": weather.precipitation,
            "wind_speed": weather.wind_speed,
            "condition": weather.condition,
            "forecast": weather.forecast,
            "timing": weather_timing,
            "is_historical_context": weather_timing == "near_completion",
        },
        "history": [
            {
                "crop": previous.crop,
                "predicted_class": previous.predicted_class,
                "confidence": previous.model_confidence,
                "symptoms": _bounded(previous.symptoms, 300),
                "created_at": previous.created_at.isoformat(),
            }
            for previous in history
        ],
        "missing": {
            "registered_crop": case.registered_crop_id is None,
            "symptoms": not bool(case.symptoms.strip()),
            "weather": weather is None,
            "history": not history.exists(),
        },
    }


def retrieval_query_texts(case_context):
    parts = [case_context["crop"], case_context["predicted_class"]]
    weather = case_context["weather"]
    if weather:
        parts.append(
            f"Weather: {str(weather['condition'])[:12]}; {weather['temperature']}C; "
            f"RH {weather['humidity']}%; rain {weather['precipitation']}mm"
        )
        parts.append({
            "near_completion": "Weather timing: near diagnosis completion",
            "later_observation": "Weather timing: later observation, not diagnosis-time",
            "completion_time_unknown": "Weather timing: diagnosis completion unknown",
        }.get(weather.get("timing"), "Weather timing: unknown"))
    if case_context["symptoms"]:
        parts.append(f"Observed symptoms: {case_context['symptoms'][:60]}")
    return parts
