import uuid
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from diagnoses.models import DiagnosisCase


def knowledge_file_path(instance, filename):
    return f"knowledge/{instance.document_id}/{instance.id}/{Path(filename).name}"


def validate_knowledge_file(value):
    extension = Path(value.name).suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise ValidationError("Only PDF and DOCX agricultural documents are supported.")
    if value.size > 20 * 1024 * 1024:
        raise ValidationError("Documents must not exceed 20 MB.")

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


class AdvisoryVersion(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagnosis = models.ForeignKey(
        DiagnosisCase, on_delete=models.CASCADE, related_name="advisory_versions"
    )
    version = models.PositiveIntegerField()
    language = models.CharField(max_length=2, choices=settings.LANGUAGES)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    is_generated = models.BooleanField(default=False)
    sections = models.JSONField(default=dict, blank=True)
    content = models.TextField(blank=True)
    citations = models.JSONField(default=list, blank=True)
    context_snapshot = models.JSONField(default=dict, blank=True)
    source_references = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["diagnosis", "version"], name="advisory_version_unique_number"
            )
        ]


class AdvisoryGenerationJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagnosis = models.ForeignKey(
        DiagnosisCase, on_delete=models.CASCADE, related_name="advisory_jobs"
    )
    idempotency_key = models.CharField(max_length=64, unique=True)
    language = models.CharField(max_length=2, choices=settings.LANGUAGES)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    advisory_version = models.ForeignKey(
        AdvisoryVersion, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="generation_jobs",
    )
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["diagnosis", "status"])]


class KnowledgeDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    author = models.CharField(max_length=250, blank=True)
    publisher = models.CharField(max_length=250, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    source_url = models.URLField(blank=True)
    original_language = models.CharField(max_length=10, default="en")
    countries = models.JSONField(default=list, blank=True)
    regions = models.JSONField(default=list, blank=True)
    crops = models.JSONField(default=list)
    diseases = models.JSONField(default=list)
    license = models.CharField(max_length=200, blank=True)
    usage_permissions = models.TextField(blank=True)
    provenance = models.TextField(blank=True)
    source_identity = models.CharField(max_length=300, unique=True, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_knowledge_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        indexes = [models.Index(fields=["publisher", "title"])]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if not isinstance(self.crops, list) or not self.crops:
            raise ValidationError({"crops": "Select at least one supported crop."})
        if set(self.crops) - set(DiagnosisCase.Crop.values):
            raise ValidationError({"crops": "Coverage contains an unsupported crop."})
        if not isinstance(self.diseases, list) or not self.diseases or not all(
            isinstance(item, str) and item.strip() for item in self.diseases
        ):
            raise ValidationError({"diseases": "Enter at least one exact disease-class identifier."})


class KnowledgeDocumentVersion(models.Model):
    class Lifecycle(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DEACTIVATED = "deactivated", "Deactivated"
        SUPERSEDED = "superseded", "Superseded"
        OUTDATED = "outdated", "Outdated"

    class Verification(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PARTIAL = "partial", "Partially verified"
        VERIFIED = "verified", "Source verified"
        REJECTED = "rejected", "Verification rejected"

    class ExpertReview(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Expert reviewed"
        REJECTED = "rejected", "Expert rejected"

    class Extraction(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        UNAVAILABLE = "unavailable", "No extractable text"
        FAILED = "failed", "Failed"

    class Indexing(models.TextChoices):
        NOT_ELIGIBLE = "not_eligible", "Not eligible"
        PENDING = "pending", "Pending"
        INDEXED = "indexed", "Indexed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    uploaded_file = models.FileField(
        upload_to=knowledge_file_path, validators=[validate_knowledge_file],
        null=True, blank=True,
    )
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    extracted_text = models.TextField(blank=True)
    extraction_status = models.CharField(
        max_length=12, choices=Extraction, default=Extraction.PENDING
    )
    extraction_error = models.CharField(max_length=300, blank=True)
    lifecycle_status = models.CharField(
        max_length=12, choices=Lifecycle, default=Lifecycle.DRAFT
    )
    verification_status = models.CharField(
        max_length=12, choices=Verification, default=Verification.UNVERIFIED
    )
    expert_review_status = models.CharField(
        max_length=10, choices=ExpertReview, default=ExpertReview.PENDING
    )
    checked_claims = models.TextField(blank=True)
    checked_references = models.TextField(blank=True)
    review_notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_knowledge_versions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="approved_knowledge_versions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    indexing_status = models.CharField(
        max_length=12, choices=Indexing, default=Indexing.NOT_ELIGIBLE
    )
    indexing_error = models.CharField(max_length=300, blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["document", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "version"], name="knowledge_document_unique_version"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_active=False)
                    | (
                        models.Q(lifecycle_status="approved")
                        & models.Q(verification_status="verified")
                        & models.Q(expert_review_status="reviewed")
                    )
                ),
                name="knowledge_active_requires_approval",
            ),
        ]
        indexes = [
            models.Index(fields=["lifecycle_status", "is_active", "verification_status"]),
            models.Index(fields=["indexing_status"]),
        ]

    @property
    def is_retrieval_eligible(self):
        return (
            self.lifecycle_status == self.Lifecycle.APPROVED
            and self.is_active
            and self.verification_status == self.Verification.VERIFIED
            and self.expert_review_status == self.ExpertReview.REVIEWED
            and self.extraction_status == self.Extraction.SUCCEEDED
            and bool(self.extracted_text.strip())
        )

    def clean(self):
        super().clean()
        if self.is_active and not (
            self.lifecycle_status == self.Lifecycle.APPROVED
            and self.verification_status == self.Verification.VERIFIED
            and self.expert_review_status == self.ExpertReview.REVIEWED
        ):
            raise ValidationError("Active versions must be approved, source verified, and expert reviewed.")

    def __str__(self):
        return f"{self.document} v{self.version}"


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
