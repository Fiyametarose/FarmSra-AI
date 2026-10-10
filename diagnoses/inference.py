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
    if not model_path.is_file() or not labels_path.is_file():
        raise ModelUnavailableError(f"The {crop} model is unavailable.")
    try:
        import keras

        model = keras.models.load_model(model_path, compile=False)
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelUnavailableError(f"The {crop} model could not be loaded.") from exc
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
        probabilities = np.asarray(raw_output)[0]
    except Exception as exc:
        raise InferenceError("The model could not analyze this image.") from exc

    if probabilities.shape != (len(labels),) or not np.isfinite(probabilities).all():
        raise InferenceError("The model returned an invalid prediction.")
    class_index = int(np.argmax(probabilities))
    return Prediction(
        class_name=labels[class_index],
        class_index=class_index,
        confidence=float(probabilities[class_index]),
        model_identifier=identifier,
    )
