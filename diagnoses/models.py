import uuid

from django.conf import settings
from django.db import models


def case_image_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"cases/{instance.farmer_id}/{instance.id}.{extension}"


def follow_up_image_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"cases/{instance.diagnosis.farmer_id}/follow-ups/{instance.id}.{extension}"


class DiagnosisCase(models.Model):
    class LocationSource(models.TextChoices):
        BROWSER_GEOLOCATION = "browser_geolocation", "Browser coordinates (unverified)"

    class Crop(models.TextChoices):
        TOMATO = "tomato", "Tomato"
        POTATO = "potato", "Potato"
        MAIZE = "maize", "Maize"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    farmer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="diagnosis_cases",
    )
    registered_crop = models.ForeignKey(
        "crops.Crop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="diagnoses",
    )
    parent_case = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT,
        related_name="reassessments",
    )
    crop = models.CharField(max_length=10, choices=Crop)
    image = models.ImageField(upload_to=case_image_path)
    symptoms = models.TextField(blank=True)
    location = models.CharField(max_length=500, blank=True)
    location_source = models.CharField(
        max_length=24,
        choices=LocationSource,
        blank=True,
    )
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    failure_message = models.TextField(blank=True)
    predicted_class = models.CharField(max_length=200, blank=True)
    predicted_class_index = models.PositiveSmallIntegerField(null=True, blank=True)
    model_confidence = models.FloatField(null=True, blank=True)
    model_identifier = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def reference(self):
        return f"FS-{self.created_at.year}-{str(self.id)[:6].upper()}"

    @property
    def confidence_percent(self):
        if self.model_confidence is None:
            return None
        return round(self.model_confidence * 100, 1)

    @property
    def display_prediction(self):
        label = self.predicted_class
        prefixes = {
            "tomato": "Tomato_",
            "potato": "Potato___",
            "maize": "Corn_(maize)___",
        }
        if label.startswith(prefixes.get(self.crop, "")):
            label = label[len(prefixes[self.crop]) :]
        return label.replace("__", " ").replace("_", " ").strip()

    @property
    def is_spider_mite(self):
        return self.predicted_class == "Tomato_Spider_mites_Two_spotted_spider_mite"

    @property
    def is_uncertain(self):
        return (
            self.model_confidence is None
            or self.model_confidence < settings.DIAGNOSIS_CONFIDENCE_THRESHOLD
        )

    @property
    def is_eligible_for_advisory(self):
        if self.status != self.Status.COMPLETED:
            return False
        if not self.predicted_class:
            return False
        if self.is_uncertain:
            return False
        if self.is_spider_mite:
            return False
        healthy_classes = ["Tomato_healthy", "Potato___healthy", "Corn_(maize)___healthy"]
        if self.predicted_class in healthy_classes:
            return False
        return True

    def __str__(self):
        return f"{self.get_crop_display()} {self.reference}"


class CaseFollowUp(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagnosis = models.ForeignKey(
        DiagnosisCase, on_delete=models.CASCADE, related_name="follow_ups"
    )
    symptoms = models.TextField(blank=True)
    image = models.ImageField(upload_to=follow_up_image_path, null=True, blank=True)
    observed_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    actions_taken = models.TextField(blank=True)
    request_token = models.UUIDField(unique=True)
    reassessment = models.OneToOneField(
        DiagnosisCase, null=True, blank=True, on_delete=models.PROTECT,
        related_name="source_follow_up",
    )
    reassessment_requested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["observed_at", "created_at"]

    def __str__(self):
        return f"Follow-up for {self.diagnosis} at {self.observed_at}"
