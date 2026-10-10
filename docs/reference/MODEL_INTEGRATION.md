# Inference integration contract (verified from final export archives)

| Crop | File | Classes | Metadata-reported held-out accuracy |
| --- | --- | ---: | ---: |
| Tomato | `model_artifacts/tomato/tomato_mobilenetv2_final.keras` | 10 | 93.5866% |
| Potato | `model_artifacts/potato/potato_mobilenetv2_final.keras` | 3 | 98.1481% |
| Maize | `model_artifacts/maize/maize_mobilenetv2_final.keras` | 4 | 96.3476% |

Each crop folder also contains `<crop>_classes.json` and `<crop>_model_metadata.json`. The arrays in the classes files are in **exact output-index order** and match corresponding metadata. Do not sort/rename labels before output mapping. Maize dataset labels begin with `Corn_(maize)`.

Static Keras structure inspection confirms input `(None, 224, 224, 3)`, internal augmentation and `Rescaling(scale=1/127.5, offset=-1)`, MobileNetV2 and a crop-specific Softmax head. **Supply pixels in [0, 255] (not /255 and not [-1,1])** after RGB conversion and resizing. Recommended loading API: `keras.models.load_model(path, compile=False)` or a verified TensorFlow-Keras equivalent. Call inference with `training=False`; preprocessing augmentation must be inactive at inference. Validate actual runtime execution locally before declaring models integrated.

Metadata reports Keras saved version 3.13.2. Final `.keras` archive integrity and checksums are verified. The reported test accuracies have **not** been independently reproduced. Probabilities should be labeled tentative model confidence, not severity. Threshold is `null` in all metadata, so select and document a provisional threshold or postpone treating it as calibrated; do not claim a validated cutoff.

Tomato class index 5 is `Tomato_Spider_mites_Two_spotted_spider_mite`, a pest not covered by the approved disease-focused proposal. Keep output mapping untouched. If top prediction is this class, do not present it as an in-scope disease diagnosis or generate confident disease-specific treatment. Surface scope limitation and offer review/follow-up.

Do not copy all training checkpoints/data into the Django repository. The three final binary weights belong locally and must be excluded from Git. The adjacent JSONs are small and can be tracked. Do not try to deserialize untrusted Keras models; these files came from the user's trusted local artifacts.
