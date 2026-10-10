from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from diagnoses.forms import DiagnosisUploadForm
from crops.models import Crop
from users.models import User
from io import BytesIO
from PIL import Image

def image_upload(image_format="PNG", size=(20, 20), name="leaf.png"):
    content = BytesIO()
    Image.new("RGB", size, "green").save(content, image_format)
    return SimpleUploadedFile(name, content.getvalue(), content_type=f"image/{image_format.lower()}")

class DiagnosisCropIntegrationTests(TestCase):
    def setUp(self):
        self.farmer1 = User.objects.create_user("f1@test.com", "pwd", role=User.Role.FARMER)
        self.farmer2 = User.objects.create_user("f2@test.com", "pwd", role=User.Role.FARMER)

        self.tomato1 = Crop.objects.create(farmer=self.farmer1, name="T1", species="tomato")
        self.maize1 = Crop.objects.create(farmer=self.farmer1, name="M1", species="maize")
        self.tomato2 = Crop.objects.create(farmer=self.farmer2, name="T2", species="tomato")

    def test_form_initializes_with_correct_crop_queryset(self):
        form = DiagnosisUploadForm(farmer=self.farmer1, crop_species="tomato")
        self.assertIn(self.tomato1, form.fields["registered_crop"].queryset)
        self.assertNotIn(self.maize1, form.fields["registered_crop"].queryset)
        self.assertNotIn(self.tomato2, form.fields["registered_crop"].queryset)

    def test_form_accepts_valid_registered_crop(self):
        form = DiagnosisUploadForm(
            data={"location": "Farm", "registered_crop": self.tomato1.id},
            files={"image": image_upload()},
            farmer=self.farmer1,
            crop_species="tomato"
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["registered_crop"], self.tomato1)

    def test_form_rejects_other_farmers_crop(self):
        form = DiagnosisUploadForm(
            data={"location": "Farm", "registered_crop": self.tomato2.id},
            files={"image": image_upload()},
            farmer=self.farmer1,
            crop_species="tomato"
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Select a valid choice", str(form.errors["registered_crop"]))

    def test_form_rejects_wrong_species_crop(self):
        form = DiagnosisUploadForm(
            data={"location": "Farm", "registered_crop": self.maize1.id},
            files={"image": image_upload()},
            farmer=self.farmer1,
            crop_species="tomato"
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Select a valid choice", str(form.errors["registered_crop"]))

    def test_form_accepts_no_registered_crop(self):
        form = DiagnosisUploadForm(
            data={"location": "Farm", "registered_crop": ""},
            files={"image": image_upload()},
            farmer=self.farmer1,
            crop_species="tomato"
        )
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["registered_crop"])
