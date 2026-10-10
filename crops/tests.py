from django.test import TestCase
from django.urls import reverse
from users.models import User
from .models import Crop
from datetime import date

class CropTests(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(email="farmer@test.com", password="pwd", role=User.Role.FARMER)
        self.other_farmer = User.objects.create_user(email="other@test.com", password="pwd", role=User.Role.FARMER)
        self.client.login(email="farmer@test.com", password="pwd")

    def test_create_crop(self):
        url = reverse("crop-add")
        response = self.client.post(url, {
            "name": "My Tomato",
            "species": "tomato",
            "planting_date": "2023-01-01",
            "location": "Farm 1",
            "notes": "Test",
            "status": "active"
        })
        self.assertEqual(Crop.objects.count(), 1)
        self.assertEqual(Crop.objects.first().farmer, self.farmer)
        self.assertRedirects(response, reverse("crop-detail", args=[Crop.objects.first().id]))

    def test_edit_crop(self):
        crop = Crop.objects.create(farmer=self.farmer, name="Test", species="maize")
        url = reverse("crop-edit", args=[crop.id])
        self.client.post(url, {"name": "Updated", "species": "maize", "status": "harvested"})
        crop.refresh_from_db()
        self.assertEqual(crop.name, "Updated")
        self.assertEqual(crop.status, "harvested")

    def test_crop_access_restriction(self):
        crop = Crop.objects.create(farmer=self.other_farmer, name="Secret", species="potato")
        url = reverse("crop-detail", args=[crop.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

        edit_url = reverse("crop-edit", args=[crop.id])
        response = self.client.post(edit_url, {"name": "Hacked", "species": "potato"})
        self.assertEqual(response.status_code, 404)
        crop.refresh_from_db()
        self.assertEqual(crop.name, "Secret")

    def test_prevent_future_planting_date(self):
        url = reverse("crop-add")
        response = self.client.post(url, {
            "name": "My Tomato",
            "species": "tomato",
            "planting_date": "2099-01-01",
            "location": "Farm 1",
            "status": "active"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Planting date cannot be in the future.")
        self.assertEqual(Crop.objects.count(), 0)

    def test_prevent_species_change_with_diagnoses(self):
        from diagnoses.models import DiagnosisCase
        crop = Crop.objects.create(farmer=self.farmer, name="Test", species="tomato")
        DiagnosisCase.objects.create(farmer=self.farmer, registered_crop=crop, crop="tomato", image="test.jpg")

        url = reverse("crop-edit", args=[crop.id])
        response = self.client.post(url, {"name": "Updated", "species": "maize", "status": "active"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cannot change the species of a crop that has associated diagnosis cases.")
        crop.refresh_from_db()
        self.assertEqual(crop.species, "tomato")

    def test_inconsistent_cross_farmer_diagnosis_privacy(self):
        from diagnoses.models import DiagnosisCase
        crop = Crop.objects.create(farmer=self.farmer, name="My Crop", species="tomato")
        # Inconsistent association: other_farmer's case is linked to my crop.
        case = DiagnosisCase.objects.create(farmer=self.other_farmer, registered_crop=crop, crop="tomato", image="test.jpg")

        url = reverse("crop-detail", args=[crop.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, case.reference)
        self.assertContains(response, "No diagnoses associated with this crop.")
