import os
import uuid
import json
from unittest.mock import patch, MagicMock
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User
from crops.models import Crop
from diagnoses.models import DiagnosisCase
from advisory.models import AdvisoryResult
from advisory.services import retrieve_evidence, generate_advisory
from advisory.context import build_case_context, retrieval_query_texts
from advisory.management.commands.ingest_knowledge import parse_frontmatter

class AdvisoryTests(TestCase):
    def setUp(self):
        self.farmer1 = User.objects.create_user(email="f1@example.com", password="pwd", role="farmer")
        self.expert = User.objects.create_user(email="ex@example.com", password="pwd", role="expert")
        self.crop1 = Crop.objects.create(farmer=self.farmer1, name="My Tomato", species="tomato")

        self.case_normal = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1,
            status="completed", predicted_class="Tomato_Early_blight", model_confidence=0.85
        )
        self.case_healthy_tom = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1,
            status="completed", predicted_class="Tomato_healthy", model_confidence=0.99
        )
        self.case_low_conf = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1,
            status="completed", predicted_class="Tomato_Late_blight", model_confidence=0.65
        )
        self.case_spider_mite = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1,
            status="completed", predicted_class="Tomato_Spider_mites_Two_spotted_spider_mite", model_confidence=0.90
        )
        self.case_unfinished = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1, status="analyzing"
        )
        self.case_failed = DiagnosisCase.objects.create(
            farmer=self.farmer1, crop="tomato", registered_crop=self.crop1, status="failed"
        )

    def test_unauthorized_and_roles(self):
        self.client.login(email="ex@example.com", password="pwd")
        url = reverse('generate-advisory', args=[self.case_normal.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

    def test_unfinished_and_failed_cases(self):
        self.client.login(email="f1@example.com", password="pwd")
        for case in [self.case_unfinished, self.case_failed]:
            resp = self.client.post(reverse('generate-advisory', args=[case.id]))
            self.assertRedirects(resp, reverse('case-detail', args=[case.id]))
            self.assertFalse(AdvisoryResult.objects.filter(diagnosis=case).exists())

    def test_healthy_cases(self):
        self.client.login(email="f1@example.com", password="pwd")
        for case in [self.case_healthy_tom]:
            resp = self.client.post(reverse('generate-advisory', args=[case.id]))
            self.assertRedirects(resp, reverse('case-detail', args=[case.id]))
            self.assertFalse(AdvisoryResult.objects.filter(diagnosis=case).exists())

    def test_low_confidence_and_spider_mites(self):
        self.client.login(email="f1@example.com", password="pwd")
        for case in [self.case_low_conf, self.case_spider_mite]:
            resp = self.client.post(reverse('generate-advisory', args=[case.id]))
            self.assertRedirects(resp, reverse('case-detail', args=[case.id]))
            self.assertFalse(AdvisoryResult.objects.filter(diagnosis=case).exists())

    @patch('advisory.views.retrieve_evidence')
    def test_fallback_without_gemini(self, mock_retrieve):
        mock_retrieve.return_value = [
            {"title": "Test Title", "publisher": "Test Pub", "url": "https://test", "text": "Test passage.", "chunk_id": "1"}
        ]
        self.client.login(email="f1@example.com", password="pwd")

        with patch.dict(os.environ, clear=True):
            resp = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))

        self.assertRedirects(resp, reverse('case-detail', args=[self.case_normal.id]))
        advisory = AdvisoryResult.objects.get(diagnosis=self.case_normal)
        self.assertFalse(advisory.is_generated)
        self.assertIn("Test passage.", advisory.content)

    @patch('advisory.views.retrieve_evidence', return_value=None)
    def test_chroma_failure_does_not_save_advisory(self, mock_retrieve):
        self.client.login(email="f1@example.com", password="pwd")
        response = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))
        self.assertRedirects(response, reverse('case-detail', args=[self.case_normal.id]))
        self.assertFalse(AdvisoryResult.objects.filter(diagnosis=self.case_normal).exists())

    @patch('advisory.views.AdvisoryResult.objects.get_or_create')
    @patch('advisory.views.retrieve_evidence')
    def test_concurrent_save_loser_reuses_existing_advisory(self, mock_retrieve, mock_get_or_create):
        mock_retrieve.return_value = [
            {"title": "Test", "publisher": "Extension", "url": "https://test",
             "text": "Evidence", "chunk_id": "1"}
        ]
        existing = AdvisoryResult(diagnosis=self.case_normal, content="Already saved")
        mock_get_or_create.return_value = (existing, False)
        self.client.login(email="f1@example.com", password="pwd")
        with patch.dict(os.environ, clear=True):
            response = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))
        self.assertRedirects(response, reverse('case-detail', args=[self.case_normal.id]))
        mock_get_or_create.assert_called_once()

    @patch('advisory.views.retrieve_evidence')
    @patch('advisory.services.genai.Client')
    def test_successful_gemini_generation(self, mock_genai, mock_retrieve):
        mock_retrieve.return_value = [
            {"title": "Test Title", "publisher": "Test Pub", "url": "https://test", "text": "Test passage.", "chunk_id": "1"}
        ]
        mock_client = MagicMock()
        mock_genai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Gen exp", "citations": ["1"]
        })
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            self.client.login(email="f1@example.com", password="pwd")
            resp = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))

        advisory = AdvisoryResult.objects.get(diagnosis=self.case_normal)
        self.assertTrue(advisory.is_generated)
        self.assertIn("Gen exp", advisory.content)

    @patch('advisory.views.retrieve_evidence')
    @patch('advisory.services.genai.Client')
    def test_fabricated_citations_fallback(self, mock_genai, mock_retrieve):
        mock_retrieve.return_value = [
            {"title": "Test Title", "publisher": "Test Pub", "url": "https://test", "text": "Test passage.", "chunk_id": "1"}
        ]
        mock_client = MagicMock()
        mock_genai.return_value = mock_client
        mock_response = MagicMock()
        # Invalid citation ID '99'
        mock_response.text = json.dumps({
            "explanation": "Gen exp", "citations": ["99"]
        })
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            self.client.login(email="f1@example.com", password="pwd")
            resp = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))

        advisory = AdvisoryResult.objects.get(diagnosis=self.case_normal)
        self.assertFalse(advisory.is_generated)
        self.assertIn("Test passage.", advisory.content)

    def test_duplicate_requests_redirect(self):
        AdvisoryResult.objects.create(diagnosis=self.case_normal, is_generated=False, content="Existing")
        self.client.login(email="f1@example.com", password="pwd")
        resp = self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))
        self.assertRedirects(resp, reverse('case-detail', args=[self.case_normal.id]))

    def test_eligible_case_page_posts_automatic_advisory_once(self):
        self.client.login(email="f1@example.com", password="pwd")
        response = self.client.get(reverse("case-detail", args=[self.case_normal.id]))
        self.assertContains(response, 'id="case-advisory-form"')
        self.assertContains(response, f'advisory-auto-{self.case_normal.id}')
        self.assertContains(response, 'document.getElementById("case-advisory-form").requestSubmit()')

    def test_excluded_case_page_has_no_automatic_advisory_post(self):
        self.client.login(email="f1@example.com", password="pwd")
        response = self.client.get(reverse("case-detail", args=[self.case_healthy_tom.id]))
        self.assertNotContains(response, f'advisory-auto-{self.case_healthy_tom.id}')

    def test_recent_healthy_and_uncertain_cases_still_capture_weather_first(self):
        from django.utils import timezone
        for case in (self.case_healthy_tom, self.case_low_conf):
            case.completed_at = timezone.now()
            case.location = "Nakuru"
            case.save(update_fields=["completed_at", "location"])
        self.client.login(email="f1@example.com", password="pwd")
        for case in (self.case_healthy_tom, self.case_low_conf):
            response = self.client.get(reverse("case-detail", args=[case.id]))
            self.assertContains(response, f'weather-capture-{case.id}')
            self.assertContains(response, 'document.getElementById("case-weather-form").requestSubmit()')
            self.assertNotContains(response, f'advisory-auto-{case.id}')

    def test_context_history_is_farmer_scoped_and_weather_is_optional(self):
        other = User.objects.create_user(email="other@example.com", password="pwd", role="farmer")
        DiagnosisCase.objects.create(
            farmer=other, crop="tomato", image="cases/other.jpg", status="completed",
            predicted_class="Tomato_Other_farmer_only", model_confidence=0.9,
        )
        context = build_case_context(self.farmer1, self.case_normal)
        self.assertTrue(context["missing"]["weather"])
        self.assertNotIn("Tomato_Other_farmer_only", [item["predicted_class"] for item in context["history"]])

    def test_late_weather_is_not_historical_diagnosis_weather(self):
        from datetime import timedelta
        from django.utils import timezone
        from weather.models import WeatherContext
        self.case_normal.completed_at = timezone.now() - timedelta(days=2)
        self.case_normal.save(update_fields=["completed_at"])
        WeatherContext.objects.create(
            diagnosis=self.case_normal, location_name="Plot", location_source="explicit",
            latitude=1, longitude=2, provider="Open-Meteo", observed_at=timezone.now(),
            temperature=20, humidity=80, precipitation=3, wind_speed=5, condition="Rain",
        )
        context = build_case_context(self.farmer1, self.case_normal)
        self.assertEqual(context["weather"]["timing"], "later_observation")
        self.assertFalse(context["weather"]["is_historical_context"])
        query_parts = retrieval_query_texts(context)
        self.assertIn("not diagnosis-time", " ".join(query_parts))
        with patch("advisory.services.get_chroma_collection") as collection:
            collection.return_value.query.return_value = {
                "documents": [[]], "metadatas": [[]], "ids": [[]],
            }
            retrieve_evidence("tomato", self.case_normal.predicted_class, query_parts)
        query = collection.return_value.query.call_args.kwargs["query_texts"][0]
        self.assertLessEqual(len(query), 200)
        for value in ("Tomato_Early_blight", "Rain", "20C", "RH 80%", "rain 3mm"):
            self.assertIn(value, query)

    @patch('advisory.services.genai.Client')
    def test_unsupported_chemical_claim_is_rejected(self, mock_genai):
        response = MagicMock()
        response.text = json.dumps({
            "explanation": "Condition summary", "management": "Apply a fungicide spray.",
            "citations": ["chunk-1"],
        })
        mock_genai.return_value.models.generate_content.return_value = response
        evidence = [{"chunk_id": "chunk-1", "title": "Source", "publisher": "Extension",
                     "url": "https://example.test", "text": "Use clean seed and rotate crops."}]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            content, citations = generate_advisory("tomato", "Tomato_Bacterial_spot", evidence)
        self.assertIsNone(content)
        self.assertIsNone(citations)

    @patch('advisory.services.genai.Client')
    def test_evidence_grounded_fungicide_term_is_allowed(self, mock_genai):
        response = MagicMock()
        response.text = json.dumps({
            "management": "A fungicide may protect healthy tissue; verify the local label.",
            "citations": ["chunk-1"],
        })
        mock_genai.return_value.models.generate_content.return_value = response
        evidence = [{"chunk_id": "chunk-1", "title": "Source", "publisher": "Extension",
                     "url": "https://example.test", "text": "A fungicide may protect healthy tissue."}]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            content, citations = generate_advisory("maize", "Gray leaf spot", evidence)
        self.assertIn("fungicide", content)
        self.assertEqual(citations, ["chunk-1"])

    @patch('advisory.services.genai.Client')
    def test_generic_fungicide_evidence_cannot_bypass_operational_safety(self, mock_genai):
        evidence = [{
            "chunk_id": "chunk-1", "title": "Source", "publisher": "Extension",
            "url": "https://example.test", "text": "A fungicide may protect healthy tissue.",
        }]
        unsafe_claims = [
            "Apply BrandShield fungicide every 7 days.",
            "Apply BrandShield every 7 days.",
            "Apply azoxystrobin every 7 days.",
            "Use a fungicide containing copper hydroxide.",
            "Mix fungicide at 20 ml per litre.",
            "Use a 2% fungicide concentration weekly.",
        ]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            for claim in unsafe_claims:
                response = MagicMock()
                response.text = json.dumps({"management": claim, "citations": ["chunk-1"]})
                mock_genai.return_value.models.generate_content.return_value = response
                content, citations = generate_advisory("tomato", "Tomato_Bacterial_spot", evidence)
                self.assertIsNone(content, claim)
                self.assertIsNone(citations, claim)

    def test_new_interface_translation_is_compiled(self):
        from django.utils.translation import activate, deactivate, gettext
        message = "Grounded guidance retrieval starts automatically. Use the button below to retry if retrieval does not complete."
        try:
            activate("sw")
            self.assertIn("kiotomatiki", gettext(message))
            activate("am")
            self.assertIn("በራስ-ሰር", gettext(message))
        finally:
            deactivate()

    def test_context_rejects_another_farmer(self):
        other = User.objects.create_user(email="context-other@example.com", password="pwd", role="farmer")
        with self.assertRaises(PermissionDenied):
            build_case_context(other, self.case_normal)

    def test_retrieval_query_includes_symptoms_and_weather_when_available(self):
        self.case_normal.symptoms = "Dark concentric spots"
        self.case_normal.save(update_fields=["symptoms"])
        from weather.models import WeatherContext
        from django.utils import timezone
        WeatherContext.objects.create(
            diagnosis=self.case_normal, location_name="Plot", location_source="explicit",
            latitude=1, longitude=2, provider="Open-Meteo", observed_at=timezone.now(),
            temperature=20, humidity=80, precipitation=3, wind_speed=5, condition="Rain",
        )
        query = " ".join(retrieval_query_texts(build_case_context(self.farmer1, self.case_normal)))
        self.assertIn("Dark concentric spots", query)
        self.assertIn("Rain", query)

        context = build_case_context(self.farmer1, self.case_normal)
        self.assertNotIn("farmer_id", context)
        self.assertNotIn("case_id", context)
        self.assertEqual(context["weather"]["provider"], "Open-Meteo")
        self.assertEqual(context["weather"]["location_source"], "explicit")
        self.assertIn("observed_at", context["weather"])

    @patch("advisory.services.get_chroma_collection")
    def test_retrieval_keeps_crop_and_condition_filters(self, mock_collection):
        mock_collection.return_value.query.return_value = {
            "documents": [[]], "metadatas": [[]], "ids": [[]],
        }
        retrieve_evidence(
            "tomato",
            "Tomato_Early_blight",
            query_texts=["Dark spots", "Rain"],
        )
        where = mock_collection.return_value.query.call_args.kwargs["where"]
        self.assertEqual(where, {
            "$and": [
                {"crop": {"$eq": "tomato"}},
                {"condition": {"$eq": "Tomato_Early_blight"}},
            ]
        })

    @patch("advisory.services.get_chroma_collection")
    def test_long_symptoms_cannot_remove_weather_from_chroma_query(self, mock_collection):
        mock_collection.return_value.query.return_value = {
            "documents": [[]], "metadatas": [[]], "ids": [[]],
        }
        context = {
            "crop": "tomato",
            "predicted_class": "Tomato_Early_blight",
            "symptoms": "x" * 1000,
            "weather": {
                "condition": "Heavy rain",
                "temperature": 19,
                "humidity": 95,
                "precipitation": 12,
            },
        }
        retrieve_evidence(
            "tomato",
            "Tomato_Early_blight",
            query_texts=retrieval_query_texts(context),
        )
        query = mock_collection.return_value.query.call_args.kwargs["query_texts"][0]
        self.assertLessEqual(len(query), 200)
        self.assertIn("Weather:", query)
        self.assertIn("Heavy rain", query)
        self.assertIn("19C", query)
        self.assertIn("RH 95%", query)
        self.assertIn("rain 12mm", query)
        self.assertLessEqual(len(query), 200)
        self.assertIn("Heavy rain", query)

    @patch('advisory.views.retrieve_evidence')
    @patch('advisory.services.genai.Client')
    def test_generated_citations_saved_evidence(self, mock_genai, mock_retrieve):
        mock_retrieve.return_value = [
            {"title": "Test Title", "publisher": "Test Pub", "url": "https://test", "text": "Test passage.", "chunk_id": "1", "source_id": "src1"}
        ]
        mock_client = MagicMock()
        mock_genai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "explanation": "Gen exp",
            "prevention": "prev",
            "management": "mgmt",
            "next_steps": "next",
            "citations": ["1"]
        })
        mock_client.models.generate_content.return_value = mock_response

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            self.client.login(email="f1@example.com", password="pwd")
            self.client.post(reverse('generate-advisory', args=[self.case_normal.id]))

        advisory = AdvisoryResult.objects.get(diagnosis=self.case_normal)
        self.assertTrue(advisory.is_generated)
        # Check that retrieved_sources contains the exact cited chunk
        self.assertEqual(len(advisory.retrieved_sources), 1)
        self.assertEqual(advisory.retrieved_sources[0]['chunk_id'], "1")

import tempfile
import shutil
from pathlib import Path
from django.core.management import call_command
from advisory.management.commands.ingest_knowledge import Command

class IngestionTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_supported_disease_documents_match_model_metadata(self):
        from django.conf import settings
        expected = {
            "tomato": {
                "Tomato_Bacterial_spot", "Tomato_Early_blight", "Tomato_Late_blight",
                "Tomato_Leaf_Mold", "Tomato_Septoria_leaf_spot", "Tomato__Target_Spot",
                "Tomato__Tomato_YellowLeaf__Curl_Virus", "Tomato__Tomato_mosaic_virus",
            },
            "potato": {"Potato___Early_blight", "Potato___Late_blight"},
            "maize": {
                "Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot",
                "Corn_(maize)___Common_rust_", "Corn_(maize)___Northern_Leaf_Blight",
            },
        }
        found = {crop: set() for crop in expected}
        for path in (Path(settings.BASE_DIR) / "advisory" / "sources").glob("*.md"):
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertTrue(body, path.name)
            self.assertTrue(metadata["url"].startswith("https://"), path.name)
            if metadata["crop"] in found:
                found[metadata["crop"]].add(metadata["condition"])
        self.assertEqual(found, expected)

    @patch('advisory.management.commands.ingest_knowledge.settings')
    def test_ingestion_update_guarantees(self, mock_settings):
        from django.conf import settings
        mock_settings.BASE_DIR = settings.BASE_DIR
        mock_settings.CHROMA_DB_DIR = Path(self.temp_dir) / 'chroma'

        # We need to mock Path to return the real dir for valid_classes,
        # but a temp dir for sources.
        real_sources_dir = Path(settings.BASE_DIR) / 'advisory' / 'sources'
        temp_sources_dir = Path(self.temp_dir) / 'sources'
        temp_sources_dir.mkdir()

        test_md = temp_sources_dir / 'test_src.md'
        test_md.write_text(
            "---\n"
            "title: T1\n"
            "publisher: P1\n"
            "url: https://url\n"
            "crop: tomato\n"
            "condition: Tomato_Early_blight\n"
            "---\n"
            "Body content 1"
        )

        cmd = Command()

        # We mock Path.glob to only return our test file
        with patch('advisory.management.commands.ingest_knowledge.Path.glob', return_value=[test_md]):
            # 1. Initial ingestion
            with patch('sys.stdout', new=MagicMock()):
                cmd.handle()

        import chromadb
        client = chromadb.PersistentClient(path=str(mock_settings.CHROMA_DB_DIR))
        collection = client.get_collection("agricultural_advisory")
        results1 = collection.get(where={"source_id": "test_src"})
        self.assertEqual(len(results1['ids']), 1)
        old_id = results1['ids'][0]

        # 2. Update ingestion (successful replacement)
        test_md.write_text(
            "---\n"
            "title: T1\n"
            "publisher: P1\n"
            "url: https://url\n"
            "crop: tomato\n"
            "condition: Tomato_Early_blight\n"
            "---\n"
            "Body content 2"
        )
        with patch('advisory.management.commands.ingest_knowledge.Path.glob', return_value=[test_md]):
            with patch('sys.stdout', new=MagicMock()):
                cmd.handle()

        results2 = collection.get(where={"source_id": "test_src"})
        self.assertEqual(len(results2['ids']), 1)
        new_id = results2['ids'][0]
        self.assertNotEqual(old_id, new_id) # Hash changed

        # 3. Failed update should not delete the new chunks (mock collection.add to fail)
        test_md.write_text(
            "---\n"
            "title: T1\n"
            "publisher: P1\n"
            "url: https://url\n"
            "crop: tomato\n"
            "condition: Tomato_Early_blight\n"
            "---\n"
            "Body content 3"
        )
        with patch.object(collection, 'add', side_effect=Exception("DB Failure")):
            with patch('advisory.management.commands.ingest_knowledge.chromadb.PersistentClient') as mock_client:
                mock_client.return_value.get_or_create_collection.return_value = collection
                with patch('advisory.management.commands.ingest_knowledge.Path.glob', return_value=[test_md]):
                    with patch('sys.stdout', new=MagicMock()):
                        cmd.handle()

        # Verify the version 2 is still there because version 3 failed
        results3 = collection.get(where={"source_id": "test_src"})
        self.assertEqual(len(results3['ids']), 1)
        self.assertEqual(results3['ids'][0], new_id)
