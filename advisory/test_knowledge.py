import io
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings

from docx import Document

from advisory.admin import KnowledgeDocumentAdmin, KnowledgeDocumentVersionAdmin
from advisory.chat import source_record
from advisory.knowledge import extract_uploaded_document, index_document_version
from advisory.library import load_documents
from advisory.models import KnowledgeDocument, KnowledgeDocumentVersion
from advisory.services import retrieve_evidence


def docx_file(text="Rotate crops and inspect lower leaves for spots."):
    stream = io.BytesIO()
    document = Document()
    document.add_heading("Crop guidance", level=1)
    document.add_paragraph(text)
    document.save(stream)
    return SimpleUploadedFile(
        "guide.docx", stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class KnowledgeGovernanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser("admin@example.com", "password")
        self.expert = User.objects.create_user(
            "expert@example.com", "password", role=User.Role.EXPERT, is_staff=True
        )
        self.document = KnowledgeDocument.objects.create(
            title="Tomato early blight guide", author="Agronomist", publisher="Extension",
            source_url="https://example.test/guide", original_language="en",
            countries=["KE"], regions=["Nakuru"], crops=["tomato"],
            diseases=["Tomato_Early_blight"], license="CC BY",
            usage_permissions="Advisory use permitted", provenance="Publisher repository",
            created_by=self.admin_user,
        )

    def make_version(self, **overrides):
        values = {
            "document": self.document, "version": 1,
            "extracted_text": "Rotate crops. Inspect leaves for concentric spots.",
            "extraction_status": KnowledgeDocumentVersion.Extraction.SUCCEEDED,
        }
        values.update(overrides)
        return KnowledgeDocumentVersion.objects.create(**values)

    def test_admin_is_restricted_to_staff_administrators(self):
        model_admin = KnowledgeDocumentAdmin(KnowledgeDocument, admin.site)
        factory = RequestFactory()
        request = factory.get("/admin/")
        request.user = self.admin_user
        self.assertTrue(model_admin.has_module_permission(request))
        request.user = self.expert
        self.assertFalse(model_admin.has_module_permission(request))

    def test_docx_upload_extracts_preview_metadata(self):
        version = self.make_version(
            uploaded_file=docx_file(), extracted_text="",
            extraction_status=KnowledgeDocumentVersion.Extraction.PENDING,
        )
        extract_uploaded_document(version)
        version.refresh_from_db()
        self.assertEqual(version.extraction_status, KnowledgeDocumentVersion.Extraction.SUCCEEDED)
        self.assertIn("Rotate crops", version.extracted_text)
        self.assertEqual(version.content_type, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertTrue(version.content_hash)

    def test_invalid_extension_and_oversized_file_are_rejected(self):
        invalid = self.make_version(version=2, uploaded_file=SimpleUploadedFile("notes.txt", b"text"))
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        large = self.make_version(
            version=3,
            uploaded_file=SimpleUploadedFile("large.pdf", b"x" * (20 * 1024 * 1024 + 1)),
        )
        with self.assertRaises(ValidationError):
            large.full_clean()

    def test_malformed_or_image_only_file_never_invents_text(self):
        version = self.make_version(
            uploaded_file=SimpleUploadedFile("scan.pdf", b"not a pdf"),
            extracted_text="", extraction_status=KnowledgeDocumentVersion.Extraction.PENDING,
        )
        extract_uploaded_document(version)
        version.refresh_from_db()
        self.assertEqual(version.extraction_status, KnowledgeDocumentVersion.Extraction.FAILED)
        self.assertEqual(version.extracted_text, "")

    @patch("advisory.admin.index_document_version", return_value=True)
    @patch("advisory.admin.remove_version_chunks")
    def test_approval_supersedes_prior_version_and_preserves_history(self, remove, index):
        old = self.make_version(
            lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
            verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
            expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
            is_active=True,
        )
        new = self.make_version(
            version=2, verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
            expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
            checked_claims="Disease and management claims checked.",
            checked_references="References checked against publisher.",
        )
        request = RequestFactory().post("/admin/")
        request.user = self.admin_user
        model_admin = KnowledgeDocumentVersionAdmin(KnowledgeDocumentVersion, admin.site)
        with patch.object(model_admin, "message_user"):
            model_admin.approve_versions(request, KnowledgeDocumentVersion.objects.filter(pk=new.pk))
        old.refresh_from_db(); new.refresh_from_db()
        self.assertEqual(old.lifecycle_status, KnowledgeDocumentVersion.Lifecycle.SUPERSEDED)
        self.assertFalse(old.is_active)
        self.assertTrue(new.is_active)
        self.assertEqual(new.approved_by, self.admin_user)
        index.assert_called_once_with(new)
        remove.assert_called_once_with(old)

    def test_unverified_version_cannot_be_active(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_version(
                lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
                is_active=True,
            )

    def test_legacy_import_is_idempotent_and_unverified(self):
        call_command("import_legacy_knowledge", verbosity=0)
        first_count = KnowledgeDocument.objects.filter(provenance__startswith="Tracked repository source:").count()
        call_command("import_legacy_knowledge", verbosity=0)
        self.assertEqual(first_count, 13)
        self.assertEqual(
            KnowledgeDocument.objects.filter(provenance__startswith="Tracked repository source:").count(), 13
        )
        version = KnowledgeDocumentVersion.objects.filter(document__provenance__startswith="Tracked repository source:").first()
        self.assertEqual(version.verification_status, KnowledgeDocumentVersion.Verification.UNVERIFIED)
        self.assertFalse(version.is_active)
        self.assertEqual(version.indexing_status, KnowledgeDocumentVersion.Indexing.NOT_ELIGIBLE)

    @patch("advisory.knowledge.get_chroma_collection")
    def test_indexing_is_stable_idempotent_and_records_provenance(self, get_collection):
        version = self.make_version(
            lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
            verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
            expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
            is_active=True, content_hash="a" * 64,
        )
        collection = get_collection.return_value
        collection.get.return_value = {"ids": []}
        self.assertTrue(index_document_version(version))
        first_ids = collection.add.call_args.kwargs["ids"]
        first_meta = collection.add.call_args.kwargs["metadatas"][0]
        self.assertEqual(first_meta["document_version_id"], str(version.id))
        self.assertEqual(first_meta["document_version"], 1)
        collection.add.reset_mock()
        collection.get.return_value = {"ids": first_ids}
        self.assertTrue(index_document_version(version))
        collection.add.assert_not_called()

    @patch("advisory.services.get_chroma_collection")
    def test_retrieval_excludes_legacy_and_unapproved_chroma_chunks(self, get_collection):
        approved = self.make_version(
            lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
            verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
            expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
            is_active=True, indexing_status=KnowledgeDocumentVersion.Indexing.INDEXED,
        )
        unapproved = self.make_version(version=2)
        get_collection.return_value.query.return_value = {
            "documents": [["approved", "draft", "legacy"]],
            "ids": [["approved-id", "draft-id", "legacy-id"]],
            "metadatas": [[
                {"document_version_id": str(approved.id), "document_version": 1, "document_id": str(self.document.id), "crop": "tomato", "condition": "Tomato_Early_blight", "title": "Approved", "publisher": "Extension", "url": "https://example.test", "source_id": "approved"},
                {"document_version_id": str(unapproved.id), "crop": "tomato", "condition": "Tomato_Early_blight", "title": "Draft"},
                {"crop": "tomato", "condition": "Tomato_Early_blight", "title": "Old legacy chunk"},
            ]],
        }
        evidence = retrieve_evidence("tomato", "Tomato_Early_blight")
        self.assertEqual([item["chunk_id"] for item in evidence], ["approved-id"])
        self.assertEqual(evidence[0]["document_version_id"], str(approved.id))
        citation = source_record(evidence[0])
        self.assertEqual(citation["document_version_id"], str(approved.id))
        self.assertEqual(citation["document_version"], 1)

    @patch("advisory.knowledge.get_chroma_collection")
    def test_deactivation_removes_chunks_and_library_visibility(self, get_collection):
        version = self.make_version(
            lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
            verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
            expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
            is_active=True, indexing_status=KnowledgeDocumentVersion.Indexing.INDEXED,
        )
        self.assertEqual(len(load_documents()), 1)
        get_collection.return_value.get.return_value = {"ids": ["chunk"]}
        request = RequestFactory().post("/admin/")
        request.user = self.admin_user
        model_admin = KnowledgeDocumentVersionAdmin(KnowledgeDocumentVersion, admin.site)
        model_admin.deactivate_versions(request, KnowledgeDocumentVersion.objects.filter(pk=version.pk))
        version.refresh_from_db()
        self.assertFalse(version.is_active)
        self.assertEqual(load_documents(), [])
        get_collection.return_value.delete.assert_called_once_with(ids=["chunk"])
