import uuid
from django.conf import settings
from django.db import models

class Crop(models.Model):
    class Species(models.TextChoices):
        TOMATO = "tomato", "Tomato"
        POTATO = "potato", "Potato"
        MAIZE = "maize", "Maize"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        HARVESTED = "harvested", "Harvested"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    farmer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="registered_crops"
    )
    name = models.CharField(max_length=200)
    species = models.CharField(max_length=10, choices=Species)
    planting_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_species_display()})"
