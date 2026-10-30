from django import forms
from django.utils.translation import gettext_lazy as _

from users.models import User
from .models import ExpertMessage, ExpertProfile, ExpertReferral


class RequestReviewForm(forms.Form):
    recipient = forms.ChoiceField(required=True)
    consent = forms.BooleanField(
        required=True,
        error_messages={"required": _("Explicit consent is required to request an expert review.")},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        experts = User.objects.filter(
            role=User.Role.EXPERT, is_active=True,
            expert_profile__availability=ExpertProfile.Availability.AVAILABLE,
        ).order_by("first_name", "last_name", "email")
        self.fields["recipient"].choices = [("", _("Choose a recipient")), ("any", _("Any available expert"))] + [
            (str(expert.pk), expert.get_full_name() or _("Unnamed expert")) for expert in experts
        ]

    def clean_recipient(self):
        value = self.cleaned_data["recipient"]
        if value == "any":
            return None
        try:
            return User.objects.get(
                pk=value, role=User.Role.EXPERT, is_active=True,
                expert_profile__availability=ExpertProfile.Availability.AVAILABLE,
            )
        except (User.DoesNotExist, ValueError):
            raise forms.ValidationError(_("Select an active, available expert."))


class ExpertMessageForm(forms.ModelForm):
    class Meta:
        model = ExpertMessage
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "maxlength": 2000})}

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError(_("Enter a message."))
        return body
