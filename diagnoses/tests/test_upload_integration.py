from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from diagnoses.models import DiagnosisCase
from crops.models import Crop
from users.models import User
from io import BytesIO
from PIL import Image

def image_upload(image_format="PNG", size=(20, 20), name="leaf.png"):
    content = BytesIO()
    Image.new("RGB", size, "green").save(content, image_format)
    return SimpleUploadedFile(name, content.getvalue(), content_type=f"image/{image_format.lower()}")

class DiagnosisUploadIntegrationTests(TestCase):
    def setUp(self):
        self.farmer1 = User.objects.create_user(
            "f1@test.com", "pwd", role=User.Role.FARMER, town="Profile Town", country="KE"
        )
        self.farmer2 = User.objects.create_user("f2@test.com", "pwd", role=User.Role.FARMER)

        self.tomato1 = Crop.objects.create(
            farmer=self.farmer1, name="T1", species="tomato", location="Crop Plot"
        )
        self.maize1 = Crop.objects.create(farmer=self.farmer1, name="M1", species="maize")
        self.tomato2 = Crop.objects.create(farmer=self.farmer2, name="T2", species="tomato")
        self.client.login(email="f1@test.com", password="pwd")
        self.url = reverse("case-upload", kwargs={"crop": "tomato"})

    @override_settings(MEDIA_ROOT="/tmp")
    def test_upload_with_own_matching_crop(self):
        response = self.client.post(self.url, {
            "image": image_upload(),
            "location": "Farm",
            "registered_crop": self.tomato1.id
        })
        self.assertEqual(response.status_code, 302)
        case = DiagnosisCase.objects.get()
        self.assertEqual(case.registered_crop, self.tomato1)

    @override_settings(MEDIA_ROOT="/tmp")
    def test_upload_with_other_farmer_crop_rejected(self):
        response = self.client.post(self.url, {
            "image": image_upload(),
            "location": "Farm",
            "registered_crop": self.tomato2.id
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice. That choice is not one of the available choices.")
        self.assertEqual(DiagnosisCase.objects.count(), 0)

    @override_settings(MEDIA_ROOT="/tmp")
    def test_upload_with_different_species_rejected(self):
        response = self.client.post(self.url, {
            "image": image_upload(),
            "location": "Farm",
            "registered_crop": self.maize1.id
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice. That choice is not one of the available choices.")
        self.assertEqual(DiagnosisCase.objects.count(), 0)

    @override_settings(MEDIA_ROOT="/tmp")
    def test_upload_without_registered_crop_supported(self):
        response = self.client.post(self.url, {
            "image": image_upload(),
            "location": "Farm",
            "registered_crop": ""
        })
        self.assertEqual(response.status_code, 302)
        case = DiagnosisCase.objects.get()
        self.assertIsNone(case.registered_crop)

    @override_settings(MEDIA_ROOT="/tmp")
    def test_explicit_case_location_overrides_crop_and_profile(self):
        self.client.post(self.url, {
            "image": image_upload(),
            "location": "Field Override",
            "registered_crop": self.tomato1.id,
        })
        self.assertEqual(DiagnosisCase.objects.get().location, "Field Override")

    @override_settings(MEDIA_ROOT="/tmp")
    def test_crop_location_precedes_profile_fallback(self):
        self.client.post(self.url, {
            "image": image_upload(),
            "location": "",
            "registered_crop": self.tomato1.id,
        })
        self.assertEqual(DiagnosisCase.objects.get().location, "Crop Plot")

    @override_settings(MEDIA_ROOT="/tmp")
    def test_profile_location_is_last_fallback(self):
        self.client.post(self.url, {
            "image": image_upload(),
            "location": "",
            "registered_crop": "",
        })
        self.assertIn("Profile Town", DiagnosisCase.objects.get().location)

    def test_gps_is_only_offered_as_an_explicit_action(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'id="use-device-location"')
        self.assertNotContains(response, "getCurrentPosition(")

    @override_settings(MEDIA_ROOT="/tmp")
    def test_browser_geolocation_provenance_is_saved_as_unverified(self):
        self.client.post(self.url, {
            "image": image_upload(),
            "location": "0.5, 35.2",
            "location_source": "browser_geolocation",
            "registered_crop": "",
        })
        case = DiagnosisCase.objects.get()
        self.assertEqual(
            case.location_source,
            DiagnosisCase.LocationSource.BROWSER_GEOLOCATION,
        )
