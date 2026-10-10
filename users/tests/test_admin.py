from django.contrib import admin
from django.test import SimpleTestCase

from users.admin import UserAdmin
from users.models import User


class UserAdminTests(SimpleTestCase):
    def test_custom_user_is_registered(self):
        self.assertIsInstance(admin.site._registry[User], UserAdmin)
