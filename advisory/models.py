import uuid
from django.conf import settings
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


class AIChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(
        DiagnosisCase, on_delete=models.CASCADE, related_name="ai_chat_session"
    )
    farmer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_chat_sessions"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AI chat for {self.case}"


class AIChatMessage(models.Model):
    class Role(models.TextChoices):
        FARMER = "farmer", "Farmer"
        ASSISTANT = "assistant", "Assistant"

    class Kind(models.TextChoices):
        QUESTION = "question", "Question"
        ANSWER = "answer", "Generated answer"
        EXCERPTS = "excerpts", "Source excerpts"
        NOTICE = "notice", "Conservative notice"

    session = models.ForeignKey(
        AIChatSession, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=9, choices=Role)
    kind = models.CharField(max_length=8, choices=Kind)
    content = models.TextField()
    language = models.CharField(max_length=2)
    sources = models.JSONField(default=list, blank=True)
    client_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "client_token"],
                condition=models.Q(client_token__isnull=False),
                name="advisory_aichatmessage_unique_token",
            )
        ]

    def __str__(self):
        return f"{self.role} message in {self.session_id}"
