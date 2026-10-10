from django.contrib import admin
from django.urls import include, path

from users import views as user_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", user_views.welcome, name="welcome"),
    path("login/", user_views.FarmSraLoginView.as_view(), name="login"),
    path("logout/", user_views.FarmSraLogoutView.as_view(), name="logout"),
    path("register/", user_views.register, name="register"),
    path(
        "workspace-unavailable/",
        user_views.workspace_unavailable,
        name="workspace-unavailable",
    ),
    path("", include("diagnoses.urls")),
]
