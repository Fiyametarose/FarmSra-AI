import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase, override_settings

from diagnoses.inference import ModelUnavailableError, load_artifacts, predict, preprocess_image
from diagnoses.tests.test_forms import image_upload


class InferenceAdapterTests(SimpleTestCase):
    def tearDown(self):
        load_artifacts.cache_clear()

    def test_preprocessing_is_rgb_224_and_not_normalized(self):
        upload = image_upload(size=(10, 30))
        batch = preprocess_image(upload)
        self.assertEqual(batch.shape, (1, 224, 224, 3))
        self.assertEqual(batch.dtype, np.float32)
        self.assertGreater(batch.max(), 1.0)
        self.assertLessEqual(batch.max(), 255.0)

    @patch("diagnoses.inference.load_artifacts")
    def test_prediction_preserves_output_index_mapping(self, mocked_load):
        model = Mock(return_value=np.array([[0.1, 0.8, 0.1]], dtype=np.float32))
        mocked_load.return_value = (model, ["class-z", "class-a", "class-m"], "model.keras")
        result = predict("potato", image_upload())
        self.assertEqual(result.class_index, 1)
        self.assertEqual(result.class_name, "class-a")
        self.assertAlmostEqual(result.confidence, 0.8, places=5)
        model.assert_called_once()
        self.assertFalse(model.call_args.kwargs["training"])

    def test_missing_model_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(MODEL_ARTIFACTS_DIR=Path(directory)):
                with self.assertRaises(ModelUnavailableError):
                    load_artifacts("maize")
