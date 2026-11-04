import warnings
import math
import uuid
from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone
from PIL import Image, UnidentifiedImageError
from django.utils.translation import gettext_lazy as _

from crops.models import Crop

from .models import CaseFollowUp, DiagnosisCase


def validate_crop_image(upload):
    if upload.size > settings.MAX_UPLOAD_SIZE:
        limit = "10 MB" if settings.MAX_UPLOAD_SIZE == 10 * 1024 * 1024 else f"{settings.MAX_UPLOAD_SIZE} bytes"
        raise forms.ValidationError(_("The image must be %(limit)s or smaller.") % {"limit": limit})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            width, height = image.size
            image_format = image.format
            if width * height > settings.MAX_DECODED_IMAGE_PIXELS:
                raise forms.ValidationError(_("The decoded image dimensions are too large."))
            image.verify()
    except forms.ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, SyntaxError):
        raise forms.ValidationError(_("Upload a valid, uncorrupted image.")) from None
    finally:
        upload.seek(0)
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        raise forms.ValidationError(_("Use a JPEG, PNG, or WebP image."))
    return upload


class DiagnosisUploadForm(forms.ModelForm):
    class Meta:
        model = DiagnosisCase
        fields = ("registered_crop", "image", "symptoms", "location", "location_source")
        widgets = {
            "symptoms": forms.Textarea(
                attrs={"rows": 5, "placeholder": _("Describe visible changes (optional)")}
            ),
            "location": forms.TextInput(attrs={"placeholder": _("Case location")}),
            "location_source": forms.HiddenInput(),
        }

    def __init__(self, *args, farmer=None, crop_species=None, **kwargs):
        self.farmer = farmer
        self.crop_species = crop_species
        super().__init__(*args, **kwargs)
        if farmer and crop_species:
            self.fields["registered_crop"].queryset = farmer.registered_crops.filter(species=crop_species)
        else:
            self.fields["registered_crop"].queryset = Crop.objects.none()
        self.fields["registered_crop"].empty_label = _("Select a registered crop (optional)")

    def clean(self):
        cleaned_data = super().clean()
        registered_crop = cleaned_data.get("registered_crop")
        location = (cleaned_data.get("location") or "").strip()
        if registered_crop:
            if registered_crop.farmer_id != getattr(self.farmer, "id", None):
                self.add_error("registered_crop", _("Select one of your registered crops."))
            elif registered_crop.species != self.crop_species:
                self.add_error("registered_crop", _("The registered crop must match the selected species."))
            elif not location:
                location = registered_crop.location.strip()
        if not location and self.farmer:
            location = self.farmer.farm_location
        cleaned_data["location"] = location
        if cleaned_data.get("location_source") == DiagnosisCase.LocationSource.BROWSER_GEOLOCATION:
            try:
                latitude, longitude = map(
                    float,
                    [part.strip() for part in location.split(",")],
                )
            except (TypeError, ValueError):
                cleaned_data["location_source"] = ""
            else:
                if not (
                    math.isfinite(latitude)
                    and math.isfinite(longitude)
                    and -90 <= latitude <= 90
                    and -180 <= longitude <= 180
                ):
                    cleaned_data["location_source"] = ""
        return cleaned_data

    def clean_image(self):
        return validate_crop_image(self.cleaned_data["image"])


class CaseFollowUpForm(forms.ModelForm):
    request_token = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    class Meta:
        model = CaseFollowUp
        fields = ("symptoms", "image", "observed_at", "notes", "actions_taken", "request_token")
        widgets = {
            "observed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "symptoms": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "actions_taken": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["observed_at"].widget.format = "%Y-%m-%dT%H:%M"
        self.fields["observed_at"].initial = lambda: timezone.localtime().replace(second=0, microsecond=0)

    def clean_image(self):
        upload = self.cleaned_data.get("image")
        return validate_crop_image(upload) if upload else None

    def clean_observed_at(self):
        observed_at = self.cleaned_data["observed_at"]
        if observed_at > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError(_("The observation time cannot be in the future."))
        return observed_at

    def clean(self):
        cleaned = super().clean()
        if not any(cleaned.get(field) for field in ("symptoms", "image", "notes", "actions_taken")):
            raise forms.ValidationError(_("Add symptoms, a photograph, notes, or actions taken."))
        return cleaned
