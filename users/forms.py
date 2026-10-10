from django import forms
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm
from django.contrib.auth.forms import UserCreationForm as BaseUserCreationForm
from django.core.exceptions import ValidationError

from .models import User


class UserCreationForm(BaseUserCreationForm):
    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email", "role")


class UserChangeForm(BaseUserChangeForm):
    class Meta(BaseUserChangeForm.Meta):
        model = User
        fields = "__all__"


class FarmerRegistrationForm(BaseUserCreationForm):
    full_name = forms.CharField(max_length=300)
    terms_accepted = forms.BooleanField(
        label="I understand that AI results are estimates, not confirmed diagnoses."
    )

    class Meta:
        model = User
        fields = (
            "full_name",
            "email",
            "password1",
            "password2",
            "country",
            "region",
            "subcounty",
            "ward",
            "town",
            "locality",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("country", "region", "subcounty", "ward", "town"):
            self.fields[field_name].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email address already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        names = self.cleaned_data["full_name"].strip().split(maxsplit=1)
        user.first_name = names[0]
        user.last_name = names[1] if len(names) > 1 else ""
        user.role = User.Role.FARMER
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user_cache = None

    def clean(self):
        from django.contrib.auth import authenticate

        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        password = cleaned_data.get("password")
        if email and password:
            self.user_cache = authenticate(
                self.request, email=email.strip().lower(), password=password
            )
            if self.user_cache is None:
                raise ValidationError("Enter a valid email address and password.")
            if not self.user_cache.is_active:
                raise ValidationError("This account is inactive.")
        return cleaned_data

    def get_user(self):
        return self.user_cache
