import uuid
from django.db import models
from diagnoses.models import DiagnosisCase

class AdvisoryResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagnosis = models.OneToOneField(
        DiagnosisCase,
        on_delete=models.CASCADE,
        related_name='advisory'
    )
    is_generated = models.BooleanField(default=False)
    content = models.TextField(help_text="Generated advice or combined retrieved excerpts.")
    retrieved_sources = models.JSONField(default=list, help_text="List of source dictionaries with url, title, and publisher.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Advisory for {self.diagnosis}"
