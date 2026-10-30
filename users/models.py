from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from .managers import UserManager


def normalize_language(code):
    base = (code or "").split("-")[0].lower()
    return base if base in User.Language.values else User.Language.ENGLISH.value


class User(AbstractUser):
    class Role(models.TextChoices):
        FARMER = "farmer", "Farmer"
        EXPERT = "expert", "Expert"
        ADMINISTRATOR = "administrator", "Administrator"

    class Country(models.TextChoices):
        KENYA = "KE", "Kenya"
        ETHIOPIA = "ET", "Ethiopia"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        SWAHILI = "sw", "Swahili"
        AMHARIC = "am", "Amharic"

    username = None
    email = models.EmailField("email address")
    role = models.CharField(max_length=13, choices=Role, default=Role.FARMER)
    preferred_language = models.CharField(
        max_length=2, choices=Language, default=Language.ENGLISH
    )
    country = models.CharField(max_length=2, choices=Country, blank=True)
    region = models.CharField("region or county", max_length=100, blank=True)
    subcounty = models.CharField("zone or sub-county", max_length=100, blank=True)
    ward = models.CharField("woreda or ward", max_length=100, blank=True)
    town = models.CharField("city or town", max_length=100, blank=True)
    locality = models.CharField("kebele or locality", max_length=100, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="users_user_email_ci_unique"),
            models.CheckConstraint(
                condition=models.Q(role__in=["farmer", "expert", "administrator"]),
                name="users_user_valid_role",
            )
        ]

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()

    @property
    def farm_location(self):
        parts = [self.locality, self.town, self.ward, self.subcounty, self.region]
        if self.country:
            parts.append(self.get_country_display())
        return ", ".join(part for part in parts if part)

    def __str__(self):
        return self.email
