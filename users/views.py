from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import User
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


class FarmSraLogoutView(LogoutView):
    http_method_names = ["post", "options"]


def register(request):
    if request.user.is_authenticated:
        return redirect(role_home_name(request.user))
    form = FarmerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
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
