import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from .chat import MAX_QUESTION_CHARS


class ChatQuestionForm(forms.Form):
    question = forms.CharField(
        max_length=MAX_QUESTION_CHARS,
        strip=True,
        label=_("Your question"),
        widget=forms.Textarea(attrs={
            "rows": 3,
            "maxlength": MAX_QUESTION_CHARS,
            "class": "form-control",
            "required": True,
        }),
    )
    token = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
