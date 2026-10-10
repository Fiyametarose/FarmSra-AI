import uuid

from django.conf import settings
from django.db import models


class ExpertReferral(models.Model):
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
