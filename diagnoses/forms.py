import warnings

from django import forms
from django.conf import settings
from PIL import Image, UnidentifiedImageError

from .models import DiagnosisCase


class DiagnosisUploadForm(forms.ModelForm):
    class Meta:
        model = DiagnosisCase
        fields = ("image", "symptoms", "location")
        widgets = {
            "symptoms": forms.Textarea(
                attrs={"rows": 5, "placeholder": "Describe visible changes (optional)"}
            ),
            "location": forms.TextInput(attrs={"placeholder": "Case location"}),
        }

    def clean_image(self):
        upload = self.cleaned_data["image"]
        if upload.size > settings.MAX_UPLOAD_SIZE:
            max_size = settings.MAX_UPLOAD_SIZE
            if max_size == 10 * 1024 * 1024:
                limit = "10 MB"
            else:
                limit = f"{max_size} bytes"
            raise forms.ValidationError(f"The image must be {limit} or smaller.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(upload)
                width, height = image.size
                image_format = image.format
                if width * height > settings.MAX_DECODED_IMAGE_PIXELS:
                    raise forms.ValidationError(
                        "The decoded image dimensions are too large."
                    )
                image.verify()
        except forms.ValidationError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
        ):
            raise forms.ValidationError("Upload a valid, uncorrupted image.") from None
        finally:
            upload.seek(0)

        if image_format not in {"JPEG", "PNG", "WEBP"}:
            raise forms.ValidationError("Use a JPEG, PNG, or WebP image.")
        return upload
