import urllib.request
import urllib.parse
import json
import logging
import math
from datetime import timedelta, timezone as datetime_timezone
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

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

def get_weather_by_coords(lat, lon, name):
    cache_key = f'weather_{lat}_{lon}'
    cached = cache.get(cache_key)
    if cached:
        return cached

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
            return None

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
        }
        cache.set(cache_key, data, 1800)
        return data

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return None


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
