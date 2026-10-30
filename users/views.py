from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import get_language
from django.views.decorators.http import require_POST
from django.views.i18n import set_language as django_set_language

from .models import User, normalize_language
from .forms import EmailAuthenticationForm, FarmerRegistrationForm, FarmerAccountForm, FarmerLocationForm
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

def farmer_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.FARMER:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped
def role_home_name(user):
    if user.role == user.Role.FARMER:
        return "dashboard"
    if user.role == user.Role.EXPERT:
        return "expert-dashboard"
    if user.role == user.Role.ADMINISTRATOR and user.is_staff:
        return "admin:index"
    return "workspace-unavailable"


def welcome(request):
    if request.user.is_authenticated:
        return redirect(role_home_name(request.user))
    return render(request, "users/welcome.html")


class FarmSraLoginView(LoginView):
    authentication_form = EmailAuthenticationForm
    template_name = "users/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse(role_home_name(self.request.user))

    def form_valid(self, form):
        response = super().form_valid(form)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, form.get_user().preferred_language)
        return response


@require_POST
def set_language(request):
    response = django_set_language(request)
    language = request.POST.get("language")
    if request.user.is_authenticated and language in User.Language.values:
        if request.user.preferred_language != language:
            request.user.preferred_language = language
            request.user.save(update_fields=["preferred_language"])
    return response


class FarmSraLogoutView(LogoutView):
    http_method_names = ["post", "options"]


def register(request):
    if request.user.is_authenticated:
        return redirect(role_home_name(request.user))
    form = FarmerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.preferred_language = normalize_language(get_language())
        user.save()
        login(request, user, backend="users.backends.EmailBackend")
        return redirect("dashboard")
    return render(request, "users/register.html", {"form": form})


def workspace_unavailable(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.role == request.user.Role.FARMER:
        return redirect("dashboard")
    if request.user.role == request.user.Role.EXPERT:
        return redirect("expert-dashboard")
    if request.user.role == request.user.Role.ADMINISTRATOR and request.user.is_staff:
        return redirect("admin:index")
    return render(request, "users/workspace_unavailable.html", status=503)

@farmer_required
def settings_account(request):
    form = FarmerAccountForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("settings-account")
    return render(request, "users/settings_account.html", {"form": form})

@farmer_required
def settings_location(request):
    form = FarmerLocationForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("settings-location")
    return render(request, "users/settings_location.html", {"form": form})


@farmer_required
def settings_preferences(request):
    if request.method == "POST":
        lang = request.POST.get("language")
        if lang in User.Language.values:
            request.user.preferred_language = lang
            request.user.save(update_fields=["preferred_language"])
            response = redirect("settings-preferences")
            response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang)
            return response
    return render(request, "users/settings_preferences.html")


@farmer_required
def settings_notifications(request):
    return render(request, "users/settings_notifications.html")
