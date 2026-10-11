from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from .services import geocode_town, get_weather_by_coords
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
    else:
        if region:
            exact = [o for o in options if o['admin1'].lower() == region.lower()]
            if len(exact) == 1:
                data = get_weather_by_coords(exact[0]['lat'], exact[0]['lon'], exact[0]['name'])
                return render(request, 'weather/widget.html', {'weather': data, 'town': exact[0]['name']})

        return render(request, 'weather/options.html', {'options': options})
