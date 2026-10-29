from django.contrib import admin

from .models import WeatherContext


@admin.register(WeatherContext)
class WeatherContextAdmin(admin.ModelAdmin):
    list_display = ("diagnosis", "location_name", "provider", "observed_at", "created_at")
    readonly_fields = ("created_at",)
