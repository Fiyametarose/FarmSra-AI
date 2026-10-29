import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from django.conf import settings
from PIL import Image


class ModelUnavailableError(RuntimeError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Prediction:
    class_name: str
    class_index: int
    confidence: float
    model_identifier: str


def artifact_paths(crop):
    folder = settings.MODEL_ARTIFACTS_DIR / crop
    return (
        folder / f"{crop}_mobilenetv2_final.keras",
        folder / f"{crop}_classes.json",
    )


@lru_cache(maxsize=3)
def load_artifacts(crop):
    model_path, labels_path = artifact_paths(crop)
    metadata_path = model_path.parent / f"{crop}_model_metadata.json"
    if not model_path.is_file() or not labels_path.is_file() or not metadata_path.is_file():
        raise ModelUnavailableError(f"The {crop} model is unavailable.")
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelUnavailableError(f"The {crop} model artifacts could not be read.") from exc

    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ModelUnavailableError(f"The {crop} class labels are invalid.")
    if (
        not isinstance(metadata, dict)
        or metadata.get("crop") != crop
        or metadata.get("image_size") != [224, 224]
        or metadata.get("number_of_classes") != len(labels)
        or metadata.get("class_names") != labels
    ):
        raise ModelUnavailableError(f"The {crop} model metadata does not match its artifacts.")

    try:
        import keras

        model = keras.models.load_model(model_path, compile=False)
    except Exception as exc:
        raise ModelUnavailableError(f"The {crop} model could not be loaded.") from exc

    input_shape = model.input_shape
    output_shape = model.output_shape
    if (
        not isinstance(input_shape, tuple)
        or len(input_shape) != 4
        or input_shape[1:] != (224, 224, 3)
    ):
        raise ModelUnavailableError(f"The {crop} model input dimensions are incompatible.")
    if (
        not isinstance(output_shape, tuple)
        or len(output_shape) != 2
        or output_shape[-1] != len(labels)
    ):
        raise ModelUnavailableError(f"The {crop} model output dimensions are incompatible.")
    return model, labels, model_path.name


def preprocess_image(image_source):
    try:
        with Image.open(image_source) as image:
            rgb = image.convert("RGB").resize((224, 224), Image.Resampling.BILINEAR)
            pixels = np.asarray(rgb, dtype=np.float32)
    except (OSError, ValueError) as exc:
        raise InferenceError("The saved image could not be decoded.") from exc
    return np.expand_dims(pixels, axis=0)


def predict(crop, image_source):
    model, labels, identifier = load_artifacts(crop)
    batch = preprocess_image(image_source)
    try:
        raw_output = model(batch, training=False)
        output = np.asarray(raw_output)
    except Exception as exc:
        raise InferenceError("The model could not analyze this image.") from exc

    if output.shape != (1, len(labels)):
        raise InferenceError("The model returned probabilities with an invalid shape.")
    probabilities = output[0]
    if not np.isfinite(probabilities).all():
        raise InferenceError("The model returned non-finite probabilities.")
    if np.any(probabilities < 0) or np.any(probabilities > 1):
        raise InferenceError("The model returned probabilities outside the 0 to 1 range.")
    if not np.isclose(probabilities.sum(), 1.0, rtol=1e-5, atol=1e-4):
        raise InferenceError("The model returned probabilities that do not sum to 1.")
    class_index = int(np.argmax(probabilities))
    return Prediction(
        class_name=labels[class_index],
        class_index=class_index,
        confidence=float(probabilities[class_index]),
        model_identifier=identifier,
    )
