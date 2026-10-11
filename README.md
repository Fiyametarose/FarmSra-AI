# FarmSra AI

FarmSra AI is a university crop-health decision-support prototype for farmers in East Africa. Farmers can upload a required crop-leaf image, record symptoms and the case location, run crop-specific image inference, save the result, reopen case history, request guidance, and consent to a case-specific review by an assigned agricultural expert.

## Scope and workflows

- Supports tomato, potato, and maize only.
- Runs three local MobileNetV2-based Keras models on 224 x 224 RGB images. Results are tentative class predictions with model confidence, not guaranteed diagnoses or severity estimates.
- Retrieves agricultural evidence from the source documents in `advisory/sources/` through a local Chroma collection. Optional Gemini configuration can produce source-linked guidance from that retrieved evidence.
- Public registration creates farmer accounts. Experts are created and managed by administrators.
- Farmers can access only their own cases. Experts can access a case only after farmer consent and assignment.
- The interface supports English, Amharic, and Swahili through Django gettext catalogs.

The application is a Django 5.2 modular monolith using Django templates, vanilla CSS and JavaScript, PostgreSQL, TensorFlow/Keras, and Chroma.

## Local setup

Requirements include Python 3.13, Docker with Compose, and enough memory and disk space for TensorFlow and the local model files.

1. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and replace the example values. Do not commit `.env` or `.django-secret`.

3. Start PostgreSQL:

   ```powershell
   docker compose up -d db
   ```

   The provided configuration exposes PostgreSQL on `127.0.0.1:5433`. The Django defaults match the database name and user in `compose.yaml`; the password comes from `.env`.

4. Apply migrations and run the development server:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

## Models and guidance data

Place the trusted `.keras` weights locally at the paths documented in `docs/reference/MODEL_INTEGRATION.md`:

```text
model_artifacts/tomato/tomato_mobilenetv2_final.keras
model_artifacts/potato/potato_mobilenetv2_final.keras
model_artifacts/maize/maize_mobilenetv2_final.keras
```

Each folder must also contain its matching class-label JSON and metadata. The label arrays are in exact model-output order and must not be sorted or renamed. Model weights are intentionally excluded from Git.

Build the local Chroma collection from the tracked agricultural source documents with:

```powershell
python manage.py ingest_knowledge
```

The generated `chroma_db/` directory is local runtime data and is not included in Git. Gemini-backed generation is optional and requires the environment variables described in `.env.example`; retrieved source material remains the evidence basis.

## Translations and tests

After editing the existing PO catalogs, compile them with the repository workflow:

```powershell
python compile_po.py
```

Run validation and the test suite with:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python -m pip check
```

## Prototype limitations

FarmSra AI is a decision-support prototype, not a substitute for field or laboratory diagnosis. Model confidence is not disease severity, treatment effectiveness is not independently verified, and guidance quality depends on the available evidence and optional external model service. Expert accounts are administrator-managed, but the application does not independently certify expert qualifications. Local media, secrets, model weights, database contents, and Chroma data are not included in the repository.
