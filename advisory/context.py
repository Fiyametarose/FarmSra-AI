from django.core.exceptions import PermissionDenied
from django.conf import settings
from datetime import timedelta

from diagnoses.models import DiagnosisCase
from users.models import User


def _bounded(value, length=1000):
    return (value or "").strip()[:length]


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
    history = (
        DiagnosisCase.objects.filter(
            farmer=user,
            crop=case.crop,
            status=DiagnosisCase.Status.COMPLETED,
        )
        .exclude(id=case.id)
        .order_by("-created_at")[:5]
    )
    return {
        "crop": case.crop,
        "registered_crop": case.registered_crop.name if case.registered_crop else None,
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
