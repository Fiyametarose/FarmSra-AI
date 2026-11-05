import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from advisory import chat, prototype
from advisory.knowledge import eligible_versions, index_document_version
from advisory.library import load_documents
from advisory.models import KnowledgeDocument, KnowledgeDocumentVersion
from advisory.prototype import (
    ArchiveError,
    import_archive,
    index_all_prototype_versions,
    model_classes,
    read_archive_files,
    validate_archive,
)
from advisory.services import retrieve_evidence
from advisory.workflow import execute_job
from diagnoses.models import DiagnosisCase
from users.models import User

ARCHIVE = Path(__file__).resolve().parents[1] / "knowledge_assets" / "farmsra_rag_v6"
if not ARCHIVE.is_dir():
    raise RuntimeError(f"Required tracked knowledge directory is missing: {ARCHIVE}")
Version = KnowledgeDocumentVersion


class FakeCollection:
    """Minimal in-memory stand-in for a Chroma collection (no embedding download)."""

    def __init__(self):
        self.items = {}

    @staticmethod
    def matches(meta, where):
        if not where:
            return True
        if "$and" in where:
            return all(FakeCollection.matches(meta, clause) for clause in where["$and"])
        (key, condition), = where.items()
        expected = condition["$eq"] if isinstance(condition, dict) else condition
        return meta.get(key) == expected

    def add(self, ids, documents, metadatas):
        for item_id, text, meta in zip(ids, documents, metadatas):
            self.items[item_id] = (text, meta)

    def get(self, where=None):
        found = [(i, t, m) for i, (t, m) in self.items.items() if self.matches(m, where)]
        return {"ids": [f[0] for f in found], "documents": [f[1] for f in found], "metadatas": [f[2] for f in found]}

    def delete(self, ids):
        for item_id in ids:
            self.items.pop(item_id, None)

    def query(self, query_texts, n_results, where=None):
        words = set(query_texts[0].lower().split())
        found = [(i, t, m) for i, (t, m) in self.items.items() if self.matches(m, where)]
        found.sort(key=lambda f: -len(words & set(f[1].lower().split())))
        found = found[:n_results]
        return {"ids": [[f[0] for f in found]], "documents": [[f[1] for f in found]], "metadatas": [[f[2] for f in found]]}


def empty_production():
    collection = MagicMock()
    collection.query.return_value = {"documents": [[]], "metadatas": [[]], "ids": [[]]}
    return collection


class ArchiveTests(TestCase):
    def test_archive_has_all_documents_and_exact_model_class_mappings(self):
        records, summary = validate_archive(read_archive_files(ARCHIVE))
        self.assertEqual(
            (summary["documents"], summary["topics"], summary["model_classes"], summary["general_topics"]),
            (69, 23, 17, 6),
        )
        trained = {name for names in model_classes().values() for name in names}
        disease = {(r["meta"]["crop"], r["meta"]["condition"], r["language"]) for r in records if r["meta"]["crop"] != "all"}
        self.assertEqual({c for _crop, c, _l in disease}, trained)
        self.assertEqual(len(disease), 17 * 3)
        self.assertIn("Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot", trained)
        self.assertEqual({r["language"] for r in records}, {"en", "sw", "am"})
        self.assertTrue(all(r["sections"] for r in records))

    def test_validation_rejects_bad_crop_condition_language_and_references(self):
        files = read_archive_files(ARCHIVE)
        text = files["tomato_early_blight.md"].decode("utf-8")
        for broken, expected in [
            (text.replace("condition: Tomato_Early_blight", "condition: Tomato_Early_Blight", 1), "exact trained class"),
            (text.replace("language: en", "language: sw", 1), "language"),
            (text.replace("crop: tomato", "crop: cassava", 1), "crop"),
            (text.replace("rag_approval_scope: educational_prototype_only", "rag_approval_scope: production", 1), "prototype"),
            (text.replace("production_approval: false", "production_approval: true", 1), "prototype"),
            (text.replace('"id": "SRC1"', '"id": "SRC9"', 1), "SRC"),
        ]:
            mutated = {**files, "tomato_early_blight.md": broken.encode("utf-8")}
            with self.assertRaises(ArchiveError) as caught:
                validate_archive(mutated)
            self.assertIn(expected, " ".join(caught.exception.errors), expected)
        missing = {k: v for k, v in files.items() if k != "tomato_early_blight_am.md"}
        with self.assertRaises(ArchiveError):
            validate_archive(missing)
        with self.assertRaises(ArchiveError):
            read_archive_files(ARCHIVE.with_name("missing.zip"))

    def test_import_is_idempotent_unverified_and_preserves_existing_records(self):
        existing = KnowledgeDocument.objects.create(
            title="Existing guide", publisher="Extension", crops=["tomato"],
            diseases=["Tomato_Early_blight"], source_identity="existing-guide",
        )
        approved = Version.objects.create(
            document=existing, version=1, extracted_text="Approved text.",
            extraction_status="succeeded", lifecycle_status="approved",
            verification_status="verified", expert_review_status="reviewed", is_active=True,
        )
        _summary, first = import_archive(ARCHIVE)
        _summary, second = import_archive(ARCHIVE)
        self.assertEqual((first["documents_created"], first["versions_created"]), (69, 69))
        self.assertEqual((second["documents_created"], second["versions_created"], second["unchanged"]), (0, 0, 69))
        prototypes = Version.objects.filter(approval_scope="educational_prototype")
        self.assertEqual(prototypes.count(), 69)
        self.assertFalse(prototypes.filter(is_active=True).exists())
        self.assertFalse(prototypes.exclude(verification_status="unverified").exists())
        self.assertFalse(prototypes.exclude(expert_review_status="pending").exists())
        self.assertFalse(prototypes.exclude(lifecycle_status="draft").exists())
        self.assertEqual(prototypes.filter(prototype_approved=True).count(), 69)
        self.assertFalse(prototypes.exclude(uploaded_file="").exists())
        self.assertFalse(Version.objects.get(pk=approved.pk).approval_scope != "production")
        self.assertEqual(list(eligible_versions()), [approved])
        self.assertFalse(any(version.is_retrieval_eligible for version in prototypes))
        sample = prototypes.get(metadata__topic_id="tomato_early_blight", metadata__language="sw")
        self.assertEqual(sample.metadata["condition"], "Tomato_Early_blight")
        self.assertFalse(sample.metadata["agronomist_signoff"])
        self.assertEqual(len(sample.metadata["references"]), 2)
        self.assertIn("what", sample.metadata["claim_ledger"])
        self.assertIn("what", sample.metadata["farmer_display"])

    def test_changed_content_creates_new_version_and_supersedes_old_one(self):
        files = read_archive_files(ARCHIVE)
        records, _ = validate_archive(files)
        prototype.import_records(records)
        changed = {**files, "tomato_early_blight.md": files["tomato_early_blight.md"] + b"\nExtra note.\n"}
        changed_records, _ = validate_archive(changed)
        counts = prototype.import_records(changed_records)
        self.assertEqual(counts["versions_created"], 1)
        document = KnowledgeDocument.objects.get(source_identity="farmsra-rag:tomato_early_blight:en")
        old, new = document.versions.order_by("version")
        self.assertEqual((old.version, old.prototype_approved, old.lifecycle_status), (1, False, "superseded"))
        self.assertEqual((new.version, new.prototype_approved), (2, True))

    def test_command_dry_run_writes_nothing_and_reports_errors(self):
        out = io.StringIO()
        call_command("import_knowledge_archive", archive=str(ARCHIVE), dry_run=True, stdout=out)
        self.assertIn("69 documents", out.getvalue())
        self.assertEqual(KnowledgeDocument.objects.count(), 0)
        with self.assertRaises(CommandError):
            call_command("import_knowledge_archive", archive=str(ARCHIVE.with_name("nope.zip")), stderr=io.StringIO())

    def test_governance_constraint_still_blocks_unapproved_active_versions(self):
        document = KnowledgeDocument.objects.create(title="x", crops=["tomato"], diseases=["Tomato_healthy"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Version.objects.create(
                document=document, version=1, approval_scope="educational_prototype",
                prototype_approved=True, is_active=True,
            )


@override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=False)
class PrototypeRetrievalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_archive(ARCHIVE)

    def setUp(self):
        self.collection = FakeCollection()
        patcher = patch("advisory.prototype.get_prototype_collection", return_value=self.collection)
        patcher.start()
        self.addCleanup(patcher.stop)
        production = patch("advisory.services.get_chroma_collection", return_value=empty_production())
        production.start()
        self.addCleanup(production.stop)
        self.indexed, self.failed = index_all_prototype_versions()

    def retrieve(self, *args, **kwargs):
        with override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True):
            return retrieve_evidence(*args, **kwargs)

    def test_indexing_all_documents_and_repeat_indexing_is_stable(self):
        self.assertEqual((self.indexed, self.failed), (69, 0))
        before = sorted(self.collection.items)
        self.assertEqual(len(before), 69 * 8)
        index_all_prototype_versions()
        self.assertEqual(sorted(self.collection.items), before)
        metas = [meta for _text, meta in self.collection.items.values()]
        self.assertTrue(all(m["evidence_tier"] == "educational_prototype" for m in metas))
        self.assertFalse(any(m["section"] == "references" for m in metas))
        self.assertEqual(Version.objects.filter(indexing_status="indexed").count(), 69)

    def test_production_collection_is_never_written_by_prototype_indexing(self):
        with patch("advisory.knowledge.get_chroma_collection") as production:
            for version in Version.objects.filter(approval_scope="educational_prototype")[:3]:
                self.assertTrue(index_document_version(version))
        production.assert_not_called()

    def test_mode_off_ignores_prototype_evidence(self):
        with override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=False):
            self.assertEqual(retrieve_evidence("tomato", "Tomato_Early_blight", ["early blight"]), [])
        self.assertEqual(retrieve_evidence("tomato", "Tomato_Early_blight", ["early blight"]), [])

    def test_exact_disease_retrieval_does_not_mix_other_classes(self):
        evidence = self.retrieve(
            "tomato", "Tomato_Early_blight", ["concentric rings older leaves"], n_results=4, language="en"
        )
        specific = [item for item in evidence if not item["topic_id"].startswith("general_")]
        general = [item for item in evidence if item["topic_id"].startswith("general_")]
        self.assertEqual(len(specific), 4)
        self.assertTrue(general)
        self.assertEqual({item["topic_id"] for item in specific}, {"tomato_early_blight"})
        self.assertTrue(all(item["evidence_tier"] == "educational_prototype" for item in evidence))
        maize = self.retrieve("maize", "Corn_(maize)___Cercospora_leaf_spot Gray_leaf_spot", ["gray leaf spot"], language="en")
        self.assertEqual({i["topic_id"] for i in maize if not i["topic_id"].startswith("general_")}, {"maize_gray_leaf_spot"})
        unknown = self.retrieve("tomato", "Tomato_Not_A_Class", ["blight"], language="en")
        self.assertTrue(all(item["topic_id"].startswith("general_") for item in unknown))

    def test_general_question_without_diagnosis_uses_general_and_crop_guidance(self):
        evidence = self.retrieve("maize", None, ["how do I take a good leaf photo"], n_results=3, language="en")
        topics = {item["topic_id"] for item in evidence}
        self.assertTrue(any(t.startswith("general_") for t in topics))
        self.assertTrue(all(t.startswith("general_") or t.startswith("maize_") for t in topics))

    def test_language_specific_results_keep_english_twin_for_grounding(self):
        english = self.retrieve("potato", "Potato___Late_blight", ["water-soaked lesions"], language="en")
        for language in ("sw", "am"):
            localized = self.retrieve("potato", "Potato___Late_blight", ["water-soaked lesions"], language=language)
            self.assertEqual(len(localized), len(english))
            self.assertTrue(all(item["language"] == language and not item["language_fallback"] for item in localized))
            self.assertEqual([i["section"] for i in localized], [i["section"] for i in english])
            self.assertEqual([i["text_en"] for i in localized], [i["text"] for i in english])
            self.assertTrue(all(i["text"] != i["text_en"] for i in localized))
        amharic = self.retrieve("potato", "Potato___Late_blight", ["late blight"], language="am")
        self.assertTrue(any("\u1200" <= ch <= "\u137f" for ch in amharic[0]["text"]))

    def test_missing_translation_falls_back_to_english_explicitly(self):
        for item_id, (_text, meta) in list(self.collection.items.items()):
            if meta["language"] == "sw" and meta["topic_id"] == "potato_late_blight":
                del self.collection.items[item_id]
        evidence = self.retrieve("potato", "Potato___Late_blight", ["late blight"], language="sw")
        specific = [i for i in evidence if i["topic_id"] == "potato_late_blight"]
        self.assertTrue(specific and all(i["language"] == "en" and i["language_fallback"] for i in specific))

    def test_citation_integrity_and_attribution(self):
        evidence = self.retrieve("tomato", "Tomato_Late_blight", ["late blight"], n_results=8, language="sw")
        self.assertEqual(len({item["chunk_id"] for item in evidence}), len(evidence))
        self.assertTrue(all(item["chunk_id"] in self.collection.items for item in evidence))
        for item in evidence:
            version = Version.objects.get(pk=item["document_version_id"])
            known = {ref["id"]: ref for ref in version.metadata["references"]}
            for ref in item["references"]:
                self.assertEqual(known[ref["id"]]["url"], ref["url"])
                self.assertTrue(ref["url"].startswith("https://"))
            if item["references"]:
                self.assertEqual(item["url"], item["references"][0]["url"])
                self.assertEqual(item["title"], item["references"][0]["title"])
                self.assertEqual(item["publisher"], item["references"][0]["publisher"])
            else:
                self.assertEqual(item["url"], "")
            record = chat.source_record(item)
            self.assertEqual(record["evidence_tier"], "educational_prototype")
            self.assertEqual(record["document_version_id"], item["document_version_id"])

    def test_version_no_longer_prototype_approved_is_not_retrieved(self):
        Version.objects.filter(metadata__topic_id="tomato_early_blight").update(prototype_approved=False)
        evidence = self.retrieve("tomato", "Tomato_Early_blight", ["early blight"], language="en")
        self.assertFalse([i for i in evidence if i["topic_id"] == "tomato_early_blight"])

    def test_prototype_chroma_failure_keeps_production_results_and_reports_unavailable(self):
        with patch("advisory.prototype.get_prototype_collection", side_effect=RuntimeError("chroma down")):
            self.assertEqual(self.retrieve("tomato", "Tomato_Early_blight", ["x"]), [])
            with patch("advisory.services.get_chroma_collection", side_effect=RuntimeError("down")):
                self.assertIsNone(self.retrieve("tomato", "Tomato_Early_blight", ["x"]))

    def test_indexing_failure_is_recorded_without_raising(self):
        version = Version.objects.filter(approval_scope="educational_prototype").first()
        with patch("advisory.prototype.get_prototype_collection", side_effect=RuntimeError("down")):
            self.assertFalse(index_document_version(version))
        version.refresh_from_db()
        self.assertEqual(version.indexing_status, "failed")

    def test_library_lists_prototype_documents_only_in_prototype_mode(self):
        self.assertEqual(load_documents("en"), [])
        with override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True):
            documents = load_documents("sw")
        self.assertEqual(len(documents), 23)
        self.assertTrue(all(d["evidence_tier"] == "educational_prototype" for d in documents))
        self.assertTrue(all("[" not in h for d in documents for h in d["headings"]))


class PrototypeFeatureIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import_archive(ARCHIVE)

    def setUp(self):
        self.collection = FakeCollection()
        for target in ("advisory.prototype.get_prototype_collection",):
            patcher = patch(target, return_value=self.collection)
            patcher.start()
            self.addCleanup(patcher.stop)
        production = patch("advisory.services.get_chroma_collection", return_value=empty_production())
        production.start()
        self.addCleanup(production.stop)
        index_all_prototype_versions()
        self.farmer = User.objects.create_user("proto@example.com", "pw", preferred_language="sw")
        self.other = User.objects.create_user("proto2@example.com", "pw")
        self.case = self.make_case("Tomato_Early_blight", 0.93)

    def make_case(self, predicted_class, confidence, crop="tomato"):
        return DiagnosisCase.objects.create(
            farmer=self.farmer, crop=crop, image="cases/x.jpg", symptoms="Brown rings on old leaves",
            status=DiagnosisCase.Status.COMPLETED, predicted_class=predicted_class,
            model_confidence=confidence, completed_at=timezone.now(),
        )

    def run_job(self, case, language, generate):
        from advisory.workflow import enqueue_for_case
        job = enqueue_for_case(case, self.farmer, language)
        job.status, job.claimed_at = "running", timezone.now()
        job.save()
        return execute_job(job, generate_func=generate)

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True)
    def test_case_advisory_uses_prototype_evidence_and_records_it(self):
        seen = {}

        def generate(crop, condition, evidence, context, language):
            seen.update(evidence=evidence, context=context, language=language)
            first = evidence[0]["chunk_id"]
            return "### advice", [first], {"diagnosis_summary": "x"}

        job = self.run_job(self.case, "sw", generate)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(seen["language"], "sw")
        self.assertEqual(seen["context"]["confidence"], 0.93)
        self.assertEqual(seen["context"]["symptoms"], "Brown rings on old leaves")
        self.assertTrue(all(i["language"] == "sw" for i in seen["evidence"]))
        version = job.advisory_version
        version.refresh_from_db()
        self.assertEqual(version.source_references[0]["evidence_tier"], "educational_prototype")
        self.assertEqual(version.source_references[0]["topic_id"], "tomato_early_blight")
        self.client.force_login(self.farmer)
        page = self.client.get(reverse("case-detail", args=[self.case.id]), headers={"Accept-Language": "sw"})
        self.assertContains(page, version.source_references[0]["title"])
        self.assertContains(page, version.source_references[0]["publisher"])

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True)
    def test_healthy_uncertain_and_spider_mite_results_do_not_retrieve_or_generate(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("must not be called")

        cases = [
            self.make_case("Tomato_healthy", 0.97),
            self.make_case("Tomato_Early_blight", 0.30),
            self.make_case("Tomato_Spider_mites_Two_spotted_spider_mite", 0.95),
        ]
        for case in cases:
            from advisory.workflow import enqueue_for_case
            job = enqueue_for_case(case, self.farmer, "en")
            job.status, job.claimed_at = "running", timezone.now()
            job.save()
            result = execute_job(job, retrieve_func=forbidden, generate_func=forbidden)
            self.assertEqual(result.status, "succeeded")
            self.assertFalse(result.advisory_version.source_references)

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True)
    def test_chat_uses_language_evidence_and_validates_english_answer_with_twin(self):
        answer_en = "Remove badly affected leaves and improve air circulation around the plants."
        with patch.dict("os.environ", {"GEMINI_API_KEY": "key"}), \
                patch("advisory.chat.generate_chat_answer") as generate:
            def build(case_context, evidence, question, history, language):
                self.assertTrue(all(item["language"] == "sw" for item in evidence))
                return {"answer": "Ondoa majani yaliyoathirika na boresha mzunguko wa hewa.",
                        "answer_en": answer_en, "insufficient_evidence": False,
                        "citations": [evidence[0]["chunk_id"]]}
            generate.side_effect = build
            kind, content, sources = chat.reply_to_question(
                self.farmer, self.case, "Nifanye nini?", "sw", []
            )
        self.assertIn(kind, {"answer", "excerpts"})
        self.assertTrue(sources)
        self.assertEqual(sources[0]["evidence_tier"], "educational_prototype")

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True)
    def test_general_chat_without_gemini_falls_back_to_cited_english_excerpts(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("GEMINI_API_KEY", None)
            kind, content, sources = chat.reply_to_question(
                self.farmer, None, "How do I take a good leaf photo?", "en", [], crop="maize"
            )
        self.assertEqual(kind, "excerpts")
        self.assertTrue(sources and all(s["evidence_tier"] == "educational_prototype" for s in sources))

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=True)
    def test_chat_ownership_is_unchanged(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("case-chat", args=[self.case.id])).status_code, 404)

    @override_settings(RAG_EDUCATIONAL_PROTOTYPE_MODE=False)
    def test_mode_off_chat_has_no_evidence_without_governed_documents(self):
        with self.assertRaises(chat.ChatUnavailable):
            chat.reply_to_question(self.farmer, None, "How do I prevent disease?", "en", [], crop="maize")
