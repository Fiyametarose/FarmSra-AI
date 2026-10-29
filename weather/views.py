from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _
from django.utils import timezone
from datetime import timedelta

from diagnoses.models import DiagnosisCase

from .models import WeatherContext
from .services import (
    geocode_town,
    get_weather_by_coords,
    resolve_location,
    valid_coordinates,
    weather_observed_at,
)
from users.models import User

@login_required
def weather_widget(request):
    if request.user.role != User.Role.FARMER:
        return HttpResponseForbidden("Only farmers can access weather.")

    lat = request.GET.get('lat')
    lon = request.GET.get('lon')
    name = request.GET.get('name')

    if lat is not None and lon is not None and name:
        try:
            flat = float(lat)
            flon = float(lon)
            import math
            if math.isfinite(flat) and math.isfinite(flon) and -90 <= flat <= 90 and -180 <= flon <= 180:
                data = get_weather_by_coords(flat, flon, name)
                return render(request, 'weather/widget.html', {'weather': data, 'town': name})
        except ValueError:
            pass

    if 'search' in request.GET:
        search = request.GET.get('search', '').strip()
        if not search:
            return render(request, 'weather/search.html')
        options = geocode_town(search, None, None)
        return render(request, 'weather/options.html', {'options': options})

    town = request.user.town
    country = request.user.country
    region = request.user.region

    if not town:
        return render(request, 'weather/search.html')

    options = geocode_town(town, country, region)
    if not options:
        return render(request, 'weather/search.html', {'error': 'Location not found'})

    if len(options) == 1:
        data = get_weather_by_coords(options[0]['lat'], options[0]['lon'], options[0]['name'])
        return render(request, 'weather/widget.html', {'weather': data, 'town': options[0]['name']})
    if region:
        exact = [o for o in options if o['admin1'].lower() == region.lower()]
        if len(exact) == 1:
            data = get_weather_by_coords(exact[0]['lat'], exact[0]['lon'], exact[0]['name'])
            return render(request, 'weather/widget.html', {'weather': data, 'town': exact[0]['name']})

    return render(request, 'weather/options.html', {'options': options})


def automatic_capture_allowed(case):
    case_age = timezone.now() - case.completed_at if case.completed_at else None
    return (
        case.status == DiagnosisCase.Status.COMPLETED
        and case_age is not None
        and timedelta(0) <= case_age
        <= timedelta(minutes=settings.CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES)
    )


def location_source(case, location, requested_source, coordinates_supplied, existing=None):
    if (
        requested_source == WeatherContext.LocationSource.DEVICE_GPS
        and coordinates_supplied
    ):
        return WeatherContext.LocationSource.DEVICE_GPS
    if (
        case.location_source == DiagnosisCase.LocationSource.BROWSER_GEOLOCATION
        and location == case.location
    ):
        return WeatherContext.LocationSource.DEVICE_GPS
    if case.registered_crop and location == case.registered_crop.location.strip():
        return WeatherContext.LocationSource.REGISTERED_CROP
    if location == case.farmer.farm_location:
        return WeatherContext.LocationSource.FARM_PROFILE
    if location != case.location:
        return WeatherContext.LocationSource.CORRECTION
    if existing:
        return existing.location_source
    return WeatherContext.LocationSource.EXPLICIT


def persist_weather_snapshot(case, values, replace=False):
    with transaction.atomic():
        if replace:
            snapshot = WeatherContext.objects.select_for_update().filter(
                diagnosis=case
            ).first()
            if snapshot:
                for field, value in values.items():
                    setattr(snapshot, field, value)
                snapshot.save(update_fields=list(values))
                return snapshot, False
        return WeatherContext.objects.get_or_create(diagnosis=case, defaults=values)


@login_required
@require_POST
def capture_case_weather(request, case_id):
    case = get_object_or_404(
        DiagnosisCase.objects.select_related("farmer", "registered_crop"),
        id=case_id,
        farmer=request.user,
    )
    if request.user.role != User.Role.FARMER:
        return HttpResponseForbidden("Only the case owner can capture weather.")

    existing = getattr(case, "weather_context", None)
    replace = request.POST.get("replace") == "1"
    if existing and not replace:
        return redirect("case-detail", case_id=case.id)
    if request.POST.get("capture_mode") == "automatic" and not automatic_capture_allowed(case):
        messages.warning(request, _("Automatic weather capture is only available shortly after diagnosis completion."))
        return redirect("case-detail", case_id=case.id)

    location = (request.POST.get("location") or case.location).strip()
    requested_source = request.POST.get("location_source", "")
    latitude = request.POST.get("latitude")
    longitude = request.POST.get("longitude")
    coordinates_supplied = bool(latitude and longitude)
    source = location_source(
        case,
        location,
        requested_source,
        coordinates_supplied,
        existing,
    )
    if location and location != case.location:
        case.location = location
        case.location_source = ""
        case.save(update_fields=["location", "location_source"])
    resolved = None
    error = None
    if latitude or longitude:
        if not latitude or not longitude:
            error = _("The coordinate pair is malformed. Enter latitude and longitude as numbers.")
        else:
            try:
                latitude = float(latitude)
                longitude = float(longitude)
            except ValueError:
                error = _("The supplied coordinates are invalid.")
            else:
                if valid_coordinates(latitude, longitude):
                    resolved = {"name": location or f"{latitude}, {longitude}", "lat": latitude, "lon": longitude}
                else:
                    error = _("The supplied coordinates are invalid.")
    else:
        resolved, error = resolve_location(
            location,
            request.user.country or None,
            request.user.region if source == WeatherContext.LocationSource.FARM_PROFILE else None,
            search_name=request.user.town if source == WeatherContext.LocationSource.FARM_PROFILE else None,
        )

    if error:
        messages.warning(request, error)
        return redirect("case-detail", case_id=case.id)

    weather_data = get_weather_by_coords(resolved["lat"], resolved["lon"], resolved["name"])
    observed_at = weather_observed_at(weather_data or {})
    if not weather_data or observed_at is None:
        messages.warning(request, _("Weather is currently unavailable for this case location."))
        return redirect("case-detail", case_id=case.id)

    values = {
        "location_name": resolved["name"],
        "location_source": source,
        "latitude": resolved["lat"],
        "longitude": resolved["lon"],
        "provider": weather_data["provider"],
        "observed_at": observed_at,
        "temperature": weather_data.get("temperature"),
        "humidity": weather_data.get("humidity"),
        "precipitation": weather_data.get("precipitation"),
        "wind_speed": weather_data.get("wind_speed"),
        "condition": weather_data.get("condition", ""),
        "weather_code": weather_data.get("weather_code"),
    }
    persist_weather_snapshot(case, values, replace=replace)

    messages.success(request, _("Case weather snapshot saved."))
    return redirect("case-detail", case_id=case.id)
