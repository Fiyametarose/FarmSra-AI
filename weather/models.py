from django.db import models
from django.utils.translation import gettext_lazy as _


class WeatherContext(models.Model):
    class LocationSource(models.TextChoices):
        EXPLICIT = "explicit", _("Case location")
        REGISTERED_CROP = "registered_crop", _("Registered crop")
        FARM_PROFILE = "farm_profile", _("Farm profile")
        DEVICE_GPS = "device_gps", _("Browser coordinates (unverified)")
        CORRECTION = "correction", _("Manual correction")

    diagnosis = models.OneToOneField(
        "diagnoses.DiagnosisCase",
        on_delete=models.CASCADE,
        related_name="weather_context",
    )
    location_name = models.CharField(max_length=500)
    location_source = models.CharField(max_length=20, choices=LocationSource)
    latitude = models.FloatField()
    longitude = models.FloatField()
    provider = models.CharField(max_length=100)
    observed_at = models.DateTimeField()
    temperature = models.FloatField(null=True, blank=True)
    humidity = models.FloatField(null=True, blank=True)
    precipitation = models.FloatField(null=True, blank=True)
    wind_speed = models.FloatField(null=True, blank=True)
    condition = models.CharField(max_length=100, blank=True)
    weather_code = models.IntegerField(null=True, blank=True)
    forecast = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Weather for {self.diagnosis} at {self.location_name}"


class WeatherSnapshot(models.Model):
    """Append-only weather observation linked to a case; WeatherContext keeps the latest one."""

    diagnosis = models.ForeignKey(
        "diagnoses.DiagnosisCase",
        on_delete=models.CASCADE,
        related_name="weather_snapshots",
    )
    location_name = models.CharField(max_length=500)
    location_source = models.CharField(max_length=20, choices=WeatherContext.LocationSource)
    latitude = models.FloatField()
    longitude = models.FloatField()
    provider = models.CharField(max_length=100)
    observed_at = models.DateTimeField()
    temperature = models.FloatField(null=True, blank=True)
    humidity = models.FloatField(null=True, blank=True)
    precipitation = models.FloatField(null=True, blank=True)
    wind_speed = models.FloatField(null=True, blank=True)
    condition = models.CharField(max_length=100, blank=True)
    weather_code = models.IntegerField(null=True, blank=True)
    forecast = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["observed_at", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["diagnosis", "observed_at", "latitude", "longitude"],
                name="weather_unique_case_observation",
            )
        ]

    def __str__(self):
        return f"Weather for {self.diagnosis} at {self.observed_at}"
