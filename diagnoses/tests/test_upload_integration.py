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
        self.farmer1 = User.objects.create_user("f1@test.com", "pwd", role=User.Role.FARMER)
        self.farmer2 = User.objects.create_user("f2@test.com", "pwd", role=User.Role.FARMER)

        self.tomato1 = Crop.objects.create(farmer=self.farmer1, name="T1", species="tomato")
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
