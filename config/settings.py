import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DEBUG is false.")
    development_secret_file = BASE_DIR / ".django-secret"
    if development_secret_file.exists():
        SECRET_KEY = development_secret_file.read_text(encoding="utf-8").strip()
    else:
        SECRET_KEY = secrets.token_urlsafe(50)
        development_secret_file.write_text(SECRET_KEY, encoding="utf-8")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "users",
    "diagnoses",
    "crops",
    "advisory",
    "expert_reviews",
    "notifications",
    "weather",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "notifications.context_processors.unread_notifications",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "farmsra"),
        "USER": os.getenv("POSTGRES_USER", "farmsra"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5433"),
        "OPTIONS": {"connect_timeout": 5},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

from django.utils.translation import gettext_lazy as _

LANGUAGES = [
    ("en", _("English")),
    ("am", _("Amharic")),
    ("sw", _("Swahili")),
]

EXTRA_LANG_INFO = {
    'am': {
        'bidi': False,
        'code': 'am',
        'name': 'Amharic',
        'name_local': 'አማርኛ',
    },
    'sw': {
        'bidi': False,
        'code': 'sw',
        'name': 'Swahili',
        'name_local': 'Kiswahili',
    }
}

import django.conf.locale
from django.conf.locale import LANG_INFO
LANG_INFO.update(EXTRA_LANG_INFO)

LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = ["users.backends.EmailBackend"]
SILENCED_SYSTEM_CHECKS = ["auth.W004"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "welcome"

MODEL_ARTIFACTS_DIR = BASE_DIR / "model_artifacts"
try:
    DIAGNOSIS_CONFIDENCE_THRESHOLD = float(
        os.getenv("DIAGNOSIS_CONFIDENCE_THRESHOLD", "0.70")
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "DIAGNOSIS_CONFIDENCE_THRESHOLD must be a number between 0 and 1."
    ) from exc
if not 0 <= DIAGNOSIS_CONFIDENCE_THRESHOLD <= 1:
    raise ImproperlyConfigured(
        "DIAGNOSIS_CONFIDENCE_THRESHOLD must be between 0 and 1."
    )
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_DECODED_IMAGE_PIXELS = 25_000_000
PROCESSING_STALE_AFTER_SECONDS = 15 * 60
try:
    CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES = int(
        os.getenv("CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES", "30")
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES must be a non-negative integer."
    ) from exc
if CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES < 0:
    raise ImproperlyConfigured(
        "CASE_WEATHER_AUTO_CAPTURE_MAX_AGE_MINUTES must be a non-negative integer."
    )
CHROMA_DB_DIR = BASE_DIR / "chroma_db"

# External delivery is opt-in. In-app notifications remain available without SMTP.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if os.getenv("EMAIL_HOST") else "django.core.mail.backends.dummy.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "FarmSra AI <no-reply@localhost>")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
