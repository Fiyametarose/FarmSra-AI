import uuid
from django.db import models
from users.models import User
from django.utils.translation import gettext_lazy as _

class Notification(models.Model):
    class EventType(models.TextChoices):
        EXPERT_ASSIGNED = 'expert_assigned', _('Expert Assigned')
        FARMER_ASSIGNED = 'farmer_assigned', _('Request Assigned')
        EXPERT_REVIEWED = 'expert_reviewed', _('Review Submitted')
        CONSENT_REVOKED = 'consent_revoked', _('Consent Revoked')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    event_key = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['recipient', 'event_type', 'event_key'], name='unique_notification_event')
        ]

    def __str__(self):
        return f"{self.recipient.email} - {self.event_type}"

    def is_read(self):
        return self.read_at is not None

