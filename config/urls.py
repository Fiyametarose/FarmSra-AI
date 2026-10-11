from django.contrib import admin
from django.urls import include, path

from users import views as user_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", user_views.welcome, name="welcome"),
    path("login/", user_views.FarmSraLoginView.as_view(), name="login"),
    path("logout/", user_views.FarmSraLogoutView.as_view(), name="logout"),
    path("register/", user_views.register, name="register"),
    path("settings/account/", user_views.settings_account, name="settings-account"),
    path("settings/location/", user_views.settings_location, name="settings-location"),
    path(
        "workspace-unavailable/",
        user_views.workspace_unavailable,
        name="workspace-unavailable",
    ),
    path("", include("diagnoses.urls")),
    path("crops/", include("crops.urls")),
    path("advisory/", include("advisory.urls")),
    path("", include("expert_reviews.urls")),
    path("notifications/", include("notifications.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("weather/", include("weather.urls")),
]
