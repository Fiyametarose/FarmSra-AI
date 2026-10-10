from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image

from diagnoses.forms import DiagnosisUploadForm


def image_upload(image_format="PNG", size=(20, 20), name="leaf.png"):
    content = BytesIO()
    Image.new("RGB", size, "green").save(content, image_format)
    return SimpleUploadedFile(name, content.getvalue(), content_type=f"image/{image_format.lower()}")


class ImageValidationTests(SimpleTestCase):
    def test_accepts_decoded_supported_image(self):
        form = DiagnosisUploadForm(
            data={"symptoms": "spots", "location": "Nakuru"},
            files={"image": image_upload()},
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_corrupted_image(self):
        upload = SimpleUploadedFile("leaf.png", b"not an image", content_type="image/png")
        form = DiagnosisUploadForm(data={"location": "Nakuru"}, files={"image": upload})
        self.assertFalse(form.is_valid())
        self.assertIn("valid image", form.errors["image"][0])

    def test_rejects_unsupported_decoded_format(self):
        form = DiagnosisUploadForm(
            data={"location": "Nakuru"},
            files={"image": image_upload("GIF", name="leaf.gif")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("JPEG, PNG, or WebP", form.errors["image"][0])

    @override_settings(MAX_UPLOAD_SIZE=200)
    def test_upload_file_size_boundary(self):
        image_bytes = image_upload().read()
        at_limit = SimpleUploadedFile(
            "leaf.png",
            image_bytes + b"\0" * (200 - len(image_bytes)),
            content_type="image/png",
        )
        accepted = DiagnosisUploadForm(
            data={"location": "Nakuru"},
            files={"image": at_limit},
        )
        self.assertTrue(accepted.is_valid(), accepted.errors)

        over_limit = SimpleUploadedFile(
            "leaf.png",
            image_bytes + b"\0" * (201 - len(image_bytes)),
            content_type="image/png",
        )
        rejected = DiagnosisUploadForm(
            data={"location": "Nakuru"},
            files={"image": over_limit},
        )
        self.assertFalse(rejected.is_valid())
        self.assertIn("200 bytes or smaller", rejected.errors["image"][0])

    @override_settings(MAX_DECODED_IMAGE_PIXELS=400)
    def test_accepts_image_at_decoded_pixel_limit(self):
        form = DiagnosisUploadForm(
            data={"location": "Nakuru"},
            files={"image": image_upload(size=(20, 20))},
        )
        self.assertTrue(form.is_valid(), form.errors)

    @override_settings(MAX_DECODED_IMAGE_PIXELS=399)
    def test_rejects_image_over_decoded_pixel_limit(self):
        form = DiagnosisUploadForm(
            data={"location": "Nakuru"},
            files={"image": image_upload(size=(20, 20))},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("dimensions are too large", form.errors["image"][0])

    def test_pillow_decompression_bomb_is_a_validation_error(self):
        original_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 100
        try:
            form = DiagnosisUploadForm(
                data={"location": "Nakuru"},
                files={"image": image_upload(size=(20, 20))},
            )
            self.assertFalse(form.is_valid())
            self.assertIn("valid image", form.errors["image"][0])
        finally:
            Image.MAX_IMAGE_PIXELS = original_limit
