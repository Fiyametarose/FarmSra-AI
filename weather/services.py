import urllib.request
import urllib.parse
import json
import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)

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
            if country_code and r.get('country_code') != country_code:
                continue
            options.append({
                'id': r.get('id'),
                'name': r.get('name'),
                'admin1': r.get('admin1', ''),
                'country': r.get('country', ''),
                'lat': r.get('latitude'),
                'lon': r.get('longitude'),
            })

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
            'town': name,
            'forecast': forecast,
            'provider': 'Open-Meteo',
            'timestamp': current.get('time', weather_data.get('current_weather', {}).get('time', ''))
        }
        cache.set(cache_key, data, 1800)
        return data

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return None
