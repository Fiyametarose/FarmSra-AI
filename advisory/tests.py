import os
import uuid
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from users.models import User
from crops.models import Crop
from diagnoses.models import DiagnosisCase
from advisory.models import AdvisoryResult
from advisory.services import retrieve_evidence, generate_advisory

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
