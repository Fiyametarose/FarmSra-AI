from django.core.exceptions import PermissionDenied

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
            "Observed weather: "
            f"{weather['condition']}, {weather['temperature']} C, "
            f"{weather['humidity']}% humidity, {weather['precipitation']} mm precipitation"
        )
    if case_context["symptoms"]:
        parts.append(f"Observed symptoms: {case_context['symptoms'][:60]}")
    return parts
