import uuid

from django.conf import settings
from django.db import models


def case_image_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"cases/{instance.farmer_id}/{instance.id}.{extension}"


class DiagnosisCase(models.Model):
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
    crop = models.CharField(max_length=10, choices=Crop)
    image = models.ImageField(upload_to=case_image_path)
    symptoms = models.TextField(blank=True)
    location = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    failure_message = models.TextField(blank=True)
    predicted_class = models.CharField(max_length=200, blank=True)
    predicted_class_index = models.PositiveSmallIntegerField(null=True, blank=True)
    model_confidence = models.FloatField(null=True, blank=True)
    model_identifier = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        return self.crop == self.Crop.TOMATO and self.predicted_class_index == 5

    @property
    def is_uncertain(self):
        return self.model_confidence is not None and self.model_confidence < 0.70

    def __str__(self):
        return f"{self.get_crop_display()} {self.reference}"
