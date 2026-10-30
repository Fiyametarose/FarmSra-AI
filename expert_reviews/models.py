import uuid

from django.conf import settings
from django.db import models
from django.core.validators import MaxLengthValidator


class ExpertProfile(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = "available", "Available"
        UNAVAILABLE = "unavailable", "Unavailable"

    class Verification(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        VERIFIED = "verified", "Verified"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expert_profile"
    )
    specialization = models.CharField(max_length=150, blank=True)
    speaks_english = models.BooleanField(default=False)
    speaks_swahili = models.BooleanField(default=False)
    speaks_amharic = models.BooleanField(default=False)
    availability = models.CharField(
        max_length=20, choices=Availability.choices, default=Availability.UNAVAILABLE
    )
    professional_description = models.TextField(blank=True, validators=[MaxLengthValidator(1000)])
    verification_status = models.CharField(
        max_length=20, choices=Verification.choices, default=Verification.UNVERIFIED
    )

    @property
    def supported_languages(self):
        return [name for enabled, name in (
            (self.speaks_english, "English"),
            (self.speaks_swahili, "Swahili"),
            (self.speaks_amharic, "Amharic"),
        ) if enabled]

    @property
    def is_complete(self):
        return bool(self.specialization and self.supported_languages)

    def __str__(self):
        return f"Profile for {self.user}"


class ExpertReferral(models.Model):
    class RecipientChoice(models.TextChoices):
        ANY = "any", "Any available expert"
        SPECIFIC = "specific", "Specific expert"
    class State(models.TextChoices):
        REQUESTED = "requested", "Awaiting Assignment"
        ASSIGNED = "assigned", "Assigned"
        REVIEWED = "reviewed", "Review Completed"
        REVOKED = "revoked", "Consent Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diagnosis = models.ForeignKey(
        "diagnoses.DiagnosisCase",
        on_delete=models.CASCADE,
        related_name="expert_referrals"
    )
    state = models.CharField(max_length=20, choices=State, default=State.REQUESTED)
    assigned_expert = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_referrals"
    )
    recipient_choice = models.CharField(
        max_length=10, choices=RecipientChoice.choices, default=RecipientChoice.ANY
    )
    selected_expert = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="selected_referrals"
    )
    consent_granted_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["diagnosis"],
                condition=~models.Q(state="revoked"),
                name="unique_active_referral_per_diagnosis"
            )
        ]

    def __str__(self):
        return f"Referral for {self.diagnosis}"


class ExpertMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referral = models.ForeignKey(ExpertReferral, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expert_messages"
    )
    body = models.TextField(validators=[MaxLengthValidator(2000)])
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Message on {self.referral_id} by {self.sender_id}"


class ExpertReview(models.Model):
    class Outcome(models.TextChoices):
        AGREES = "agrees", "Agrees with AI Prediction"
        ALTERNATIVE = "alternative", "Alternative Assessment"
        INCONCLUSIVE = "inconclusive", "Inconclusive"
        MORE_INFO_NEEDED = "more_info_needed", "More Information Needed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referral = models.OneToOneField(
        ExpertReferral,
        on_delete=models.CASCADE,
        related_name="review"
    )
    expert = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="submitted_reviews"
    )
    outcome = models.CharField(max_length=30, choices=Outcome)
    findings = models.TextField()
    next_steps = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Review by {self.expert} on {self.referral.diagnosis}"
