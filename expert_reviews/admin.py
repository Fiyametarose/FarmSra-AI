from django.contrib import admin
from django import forms
from django.utils import timezone
from .models import ExpertMessage, ExpertProfile, ExpertReferral, ExpertReview
from .services import notify_assignment
from users.models import User

class ExpertReferralAdminForm(forms.ModelForm):
    class Meta:
        model = ExpertReferral
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'assigned_expert' in self.fields:
            self.fields['assigned_expert'].queryset = User.objects.filter(role=User.Role.EXPERT, is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        state = cleaned_data.get('state')
        assigned_expert = cleaned_data.get('assigned_expert')

        if self.instance.pk:
            # Lock the row to prevent concurrent farmer revocation from being overwritten by a stale admin form
            locked_instance = ExpertReferral.objects.select_for_update().get(pk=self.instance.pk)

            if locked_instance.state == ExpertReferral.State.REVOKED and state != ExpertReferral.State.REVOKED:
                raise forms.ValidationError("A revoked request cannot be reopened.")
            if locked_instance.state == ExpertReferral.State.REVIEWED and state != ExpertReferral.State.REVIEWED:
                raise forms.ValidationError("A reviewed request cannot be reverted.")
            if state == ExpertReferral.State.REVIEWED and not hasattr(self.instance, 'review'):
                raise forms.ValidationError("Cannot mark as reviewed without an actual submitted review.")
            if state == ExpertReferral.State.REVOKED and locked_instance.state != ExpertReferral.State.REVOKED:
                raise forms.ValidationError("Cannot revoke consent from admin; only farmers can revoke.")

        if assigned_expert:
            if assigned_expert.role != User.Role.EXPERT or not assigned_expert.is_active:
                raise forms.ValidationError("Assignee must be an active expert.")
            if state == ExpertReferral.State.REQUESTED:
                cleaned_data['state'] = ExpertReferral.State.ASSIGNED
                if not self.instance.assigned_at:
                    cleaned_data['assigned_at'] = timezone.now()
            if (self.instance.recipient_choice == ExpertReferral.RecipientChoice.SPECIFIC
                    and self.instance.selected_expert_id
                    and assigned_expert.pk != self.instance.selected_expert_id):
                raise forms.ValidationError("A specifically selected expert cannot be reassigned without renewed farmer authorization.")
        elif state == ExpertReferral.State.ASSIGNED:
            raise forms.ValidationError("An expert must be assigned to set state to ASSIGNED.")

        return cleaned_data

@admin.register(ExpertReferral)
class ExpertReferralAdmin(admin.ModelAdmin):
    form = ExpertReferralAdminForm
    list_display = ('diagnosis', 'state', 'assigned_expert', 'consent_granted_at', 'assigned_at')
    list_filter = ('state', 'consent_granted_at', 'assigned_at')
    search_fields = ('diagnosis__id', 'diagnosis__farmer__email', 'assigned_expert__email')
    readonly_fields = ('diagnosis', 'recipient_choice', 'selected_expert', 'consent_granted_at', 'revoked_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        old_expert_id = None
        old_state = None
        if obj.pk:
            try:
                old_instance = ExpertReferral.objects.get(pk=obj.pk)
                old_expert_id = old_instance.assigned_expert_id
                old_state = old_instance.state
            except ExpertReferral.DoesNotExist:
                pass
        super().save_model(request, obj, form, change)
        new_expert = obj.assigned_expert
        expert_changed = (new_expert is not None and new_expert.id != old_expert_id)
        became_assigned = (old_state != ExpertReferral.State.ASSIGNED and obj.state == ExpertReferral.State.ASSIGNED and new_expert is not None)

        if expert_changed or became_assigned:
            notify_assignment(obj, f"referral_{obj.id}_assignment_{new_expert.id}")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.state in [ExpertReferral.State.REVOKED, ExpertReferral.State.REVIEWED]:
            return self.readonly_fields + ('state', 'assigned_expert')
        return self.readonly_fields

@admin.register(ExpertReview)
class ExpertReviewAdmin(admin.ModelAdmin):
    list_display = ('referral', 'expert', 'outcome', 'submitted_at')
    list_filter = ('outcome', 'submitted_at')
    search_fields = ('referral__diagnosis__id', 'expert__email')
    readonly_fields = ('referral', 'expert', 'outcome', 'findings', 'next_steps', 'submitted_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExpertProfile)
class ExpertProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "specialization", "availability", "verification_status")
    list_filter = ("availability", "verification_status")
    search_fields = ("user__email", "user__first_name", "user__last_name", "specialization")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = User.objects.filter(role=User.Role.EXPERT)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ExpertMessage)
class ExpertMessageAdmin(admin.ModelAdmin):
    list_display = ("referral", "sender", "created_at", "read_at")
    readonly_fields = ("referral", "sender", "body", "created_at", "read_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
