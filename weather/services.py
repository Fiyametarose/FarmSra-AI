import urllib.request
import urllib.parse
import json
import logging
import math
from datetime import timedelta, timezone as datetime_timezone
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

REFRESH_MIN_INTERVAL = timedelta(seconds=60)
LAST_GOOD_TTL = 7 * 24 * 3600

COUNTRY_CODES = {
    "ethiopia": "ET",
    "et": "ET",
    "kenya": "KE",
    "ke": "KE",
    "rwanda": "RW",
    "rw": "RW",
    "somalia": "SO",
    "so": "SO",
    "south sudan": "SS",
    "ss": "SS",
    "tanzania": "TZ",
    "tz": "TZ",
    "uganda": "UG",
    "ug": "UG",
}


def normalize_region(value):
    words = (value or "").casefold().replace("-", " ").split()
    return " ".join(word for word in words if word not in {"county", "region", "state"})

def geocode_town(town, country_code=None, region=None):
    if not town:
        return []

    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(town)}&count=10&format=json"
        req = urllib.request.Request(geo_url, headers={'User-Agent': 'FarmSra AI'})
        with urllib.request.urlopen(req, timeout=5) as response:
            geo_data = json.loads(response.read().decode())

        results = geo_data.get('results', [])
        options = []
        for r in results:
            if country_code and r.get('country_code', '').upper() != country_code.upper():
                continue
            options.append({
                'id': r.get('id'),
                'name': r.get('name'),
                'admin1': r.get('admin1', ''),
                'country': r.get('country', ''),
                'lat': r.get('latitude'),
                'lon': r.get('longitude'),
            })

        if region:
            expected_region = normalize_region(region)
            region_matches = [
                option for option in options
                if normalize_region(option['admin1']) == expected_region
            ]
            return region_matches
        return options
    except Exception as e:
        logger.error(f"Geocoding fetch failed: {e}")
        return []

def weather_fetched_at(data):
    return parse_datetime((data or {}).get("fetched_at") or "")


def weather_is_stale(data):
    """True when the data is a retained earlier observation or older than the stale limit."""
    if not data:
        return False
    if data.get("stale"):
        return True
    fetched_at = weather_fetched_at(data)
    if fetched_at is None:
        return False
    limit = timedelta(minutes=getattr(settings, "WEATHER_STALE_AFTER_MINUTES", 60))
    return timezone.now() - fetched_at > limit


def get_weather_by_coords(lat, lon, name, force_refresh=False, allow_stale=False):
    """Fetch current weather, cached for 30 minutes.

    force_refresh bypasses the cache at most once per minute. allow_stale returns the last
    successful observation, flagged stale, when the provider fails; case capture never uses it.
    """
    cache_key = f'weather_{lat}_{lon}'
    last_key = f'weather_last_{lat}_{lon}'
    cached = cache.get(cache_key)
    if cached:
        fetched_at = weather_fetched_at(cached)
        recently_fetched = fetched_at is not None and timezone.now() - fetched_at < REFRESH_MIN_INTERVAL
        if not force_refresh or recently_fetched:
            return cached

    def failed():
        last = cache.get(last_key) if allow_stale else None
        return {**last, "stale": True} if last else None

    try:
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&"
            f"current=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code&"
            f"daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum&"
            f"timezone=auto&forecast_days=4"
        )
        req = urllib.request.Request(weather_url, headers={'User-Agent': 'FarmSra AI'})
        with urllib.request.urlopen(req, timeout=5) as response:
            weather_data = json.loads(response.read().decode())

        current = weather_data.get('current', {})
        daily = weather_data.get('daily', {})
        if not current:
            return failed()

        wmo_codes = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            56: "Light freezing drizzle", 57: "Dense freezing drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            77: "Snow grains",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
        }

        forecast = []
        if 'time' in daily:
            for i in range(1, min(4, len(daily['time']))):
                forecast.append({
                    'date': daily['time'][i],
                    'temp_max': daily['temperature_2m_max'][i],
                    'temp_min': daily['temperature_2m_min'][i],
                    'precip': daily['precipitation_sum'][i],
                    'condition': wmo_codes.get(daily['weather_code'][i], "Unknown")
                })

        data = {
            'temperature': current.get('temperature_2m'),
            'humidity': current.get('relative_humidity_2m'),
            'precipitation': current.get('precipitation'),
            'wind_speed': current.get('wind_speed_10m'),
            'condition': wmo_codes.get(current.get('weather_code'), "Unknown"),
            'weather_code': current.get('weather_code'),
            'town': name,
            'forecast': forecast,
            'provider': 'Open-Meteo',
            'timestamp': current.get('time', weather_data.get('current_weather', {}).get('time', '')),
            'utc_offset_seconds': weather_data.get('utc_offset_seconds'),
            'timezone': weather_data.get('timezone', ''),
            'timezone_abbreviation': weather_data.get('timezone_abbreviation', ''),
            'fetched_at': timezone.now().isoformat(),
        }
        cache.set(cache_key, data, 1800)
        cache.set(last_key, data, LAST_GOOD_TTL)
        return data

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return failed()


def valid_coordinates(latitude, longitude):
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def resolve_location(location, country_code=None, region=None, search_name=None):
    location = (location or "").strip()
    if not location:
        return None, _("Location is unavailable. Enter the case location and try again.")

    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) == 2:
        numeric_values = []
        for part in parts:
            try:
                numeric_values.append(float(part))
            except ValueError:
                numeric_values.append(None)
        if all(value is not None for value in numeric_values):
            latitude, longitude = numeric_values
            if valid_coordinates(latitude, longitude):
                return {
                    "name": location,
                    "lat": latitude,
                    "lon": longitude,
                }, None
            return None, _("The supplied coordinates are invalid.")
        if any(value is not None for value in numeric_values):
            return None, _("The coordinate pair is malformed. Enter latitude and longitude as numbers.")

    searchable_place = search_name or (parts[0] if parts else location)
    place_parts = [] if search_name else parts[1:]
    parsed_country_code = country_code
    if place_parts:
        country_hint = COUNTRY_CODES.get(place_parts[-1].casefold())
        if not country_hint and len(place_parts[-1]) == 2 and place_parts[-1].isalpha():
            country_hint = place_parts[-1].upper()
        if country_hint:
            parsed_country_code = country_hint
            place_parts = place_parts[:-1]
        elif len(place_parts) >= 2:
            return None, _("The country in this location was not recognized. Enter a country name or two-letter country code.")
    parsed_region = place_parts[0] if place_parts else region

    options = geocode_town(searchable_place, parsed_country_code, parsed_region)
    if not options:
        return None, _("Location could not be found. Check the case location and try again.")
    if len(options) != 1:
        return None, _("Location is ambiguous. Enter a more specific town, region, and country.")
    option = options[0]
    try:
        latitude = float(option["lat"])
        longitude = float(option["lon"])
    except (KeyError, TypeError, ValueError):
        return None, _("The supplied coordinates are invalid.")
    if not valid_coordinates(latitude, longitude):
        return None, _("The supplied coordinates are invalid.")
    option["lat"] = latitude
    option["lon"] = longitude
    return option, None


def weather_observed_at(weather_data):
    observed_at = parse_datetime(weather_data.get("timestamp", ""))
    if observed_at is None:
        return None
    if timezone.is_naive(observed_at):
        try:
            utc_offset_seconds = int(weather_data["utc_offset_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if not -18 * 3600 <= utc_offset_seconds <= 18 * 3600:
            return None
        observed_at = observed_at.replace(
            tzinfo=datetime_timezone(timedelta(seconds=utc_offset_seconds))
        )
    return observed_at


def case_weather_values(data, resolved, source):
    return {
        "location_name": resolved["name"],
        "location_source": source,
        "latitude": resolved["lat"],
        "longitude": resolved["lon"],
        "provider": data["provider"],
        "observed_at": weather_observed_at(data),
        "temperature": data.get("temperature"),
        "humidity": data.get("humidity"),
        "precipitation": data.get("precipitation"),
        "wind_speed": data.get("wind_speed"),
        "condition": data.get("condition", ""),
        "weather_code": data.get("weather_code"),
        "forecast": data.get("forecast", []),
    }


def persist_weather_snapshot(case, values, replace=False):
    """Save the latest observation in WeatherContext and append it to the case history."""
    from .models import WeatherContext, WeatherSnapshot

    created = False
    with transaction.atomic():
        context = None
        if replace:
            context = WeatherContext.objects.select_for_update().filter(diagnosis=case).first()
            if context:
                for field, value in values.items():
                    setattr(context, field, value)
                context.save(update_fields=list(values))
        if context is None:
            context, created = WeatherContext.objects.get_or_create(diagnosis=case, defaults=values)
        if replace or created:
            history = {k: v for k, v in values.items() if k not in {"observed_at", "latitude", "longitude"}}
            WeatherSnapshot.objects.get_or_create(
                diagnosis=case, observed_at=values["observed_at"],
                latitude=values["latitude"], longitude=values["longitude"],
                defaults=history,
            )
    return context, created


def fetch_case_weather(case, force_refresh=False):
    """Observation values for the case location (case, registered crop, then farm profile)."""
    from .models import WeatherContext

    candidates = [
        (case.location, WeatherContext.LocationSource.EXPLICIT),
        (case.registered_crop.location if case.registered_crop else "", WeatherContext.LocationSource.REGISTERED_CROP),
        (case.farmer.farm_location, WeatherContext.LocationSource.FARM_PROFILE),
    ]
    location, source = next(((value.strip(), source) for value, source in candidates if value and value.strip()), ("", None))
    if not location:
        return None
    resolved, error = resolve_location(
        location, case.farmer.country or None,
        case.farmer.region if source == WeatherContext.LocationSource.FARM_PROFILE else None,
        search_name=case.farmer.town if source == WeatherContext.LocationSource.FARM_PROFILE else None,
    )
    if error or not resolved:
        return None
    if (
        source == WeatherContext.LocationSource.EXPLICIT
        and case.location_source == "browser_geolocation"
    ):
        source = WeatherContext.LocationSource.DEVICE_GPS
    data = get_weather_by_coords(resolved["lat"], resolved["lon"], resolved["name"], force_refresh=force_refresh)
    if not data or weather_observed_at(data) is None:
        return None
    return case_weather_values(data, resolved, source)


def capture_automatic_case_weather(case):
    """Best-effort first snapshot; never raises and never replaces an existing one."""
    from .models import WeatherContext

    if WeatherContext.objects.filter(diagnosis=case).exists():
        return
    try:
        values = fetch_case_weather(case)
        if values:
            persist_weather_snapshot(case, values)
    except Exception:
        logger.exception("Automatic case weather capture failed")


def refresh_case_weather(case):
    """Best-effort new observation for a reassessment.

    On failure the previous observation is retained unchanged. Returns True when a new
    observation time was recorded.
    """
    from .models import WeatherSnapshot

    try:
        values = fetch_case_weather(case, force_refresh=True)
        if not values:
            return False
        known = WeatherSnapshot.objects.filter(
            diagnosis=case, observed_at=values["observed_at"],
            latitude=values["latitude"], longitude=values["longitude"],
        ).exists()
        persist_weather_snapshot(case, values, replace=True)
        return not known
    except Exception:
        logger.exception("Reassessment weather refresh failed")
        return False
