"""Educational-prototype knowledge: archive import, separate Chroma indexing and retrieval.

Prototype versions are stored as KnowledgeDocumentVersion rows with
approval_scope="educational_prototype". They are never active, verified or expert reviewed,
so the production approval constraint and retrieval remain untouched. They are retrieved only
when settings.RAG_EDUCATIONAL_PROTOTYPE_MODE is enabled, from a separate Chroma collection.
"""
import csv
import hashlib
import io
import json
import logging
import re
import zipfile
from pathlib import Path

import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Max

from diagnoses.models import DiagnosisCase

from .models import KnowledgeDocument, KnowledgeDocumentVersion

logger = logging.getLogger(__name__)

COLLECTION_NAME = "agricultural_advisory_prototype"
TIER = "educational_prototype"
LANGUAGES = ("en", "sw", "am")
LANGUAGE_SUFFIXES = {"en": "", "sw": "_sw", "am": "_am"}
GENERAL_CROP = "all"
GENERAL_CONDITION = "general"
SCOPE_MARKER = "educational_prototype_only"
REQUIRED_KEYS = (
    "title", "crop", "condition", "language", "publisher", "url", "references_json",
    "evidence_map_json", "approved_for_rag", "rag_approval_scope", "production_approval",
    "agronomist_signoff", "content_version", "topic_id", "topic_type", "source_language",
)
SECTION_RE = re.compile(r"^##\s+(.*?)\s*\[([a-z_]+)\]\s*$")
SOURCE_ID_RE = re.compile(r"SRC\d+")
EVIDENCE_SECTIONS = ("what", "signs", "check", "action", "manage", "treatment", "weather", "monitor")
BIBLIOGRAPHY_SECTION = "references"
MODEL_ARTIFACTS = "model_artifacts"


class ArchiveError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors[:5]))


def prototype_mode_enabled():
    return bool(getattr(settings, "RAG_EDUCATIONAL_PROTOTYPE_MODE", False))


def prototype_versions():
    return KnowledgeDocumentVersion.objects.filter(
        approval_scope=KnowledgeDocumentVersion.ApprovalScope.EDUCATIONAL_PROTOTYPE,
        prototype_approved=True,
        extraction_status=KnowledgeDocumentVersion.Extraction.SUCCEEDED,
    ).select_related("document")


def parse_markdown_document(text):
    """Split YAML front matter (parsed with yaml.safe_load) from the Markdown body."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("missing YAML front matter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("unterminated YAML front matter")
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front matter: {exc}") from None
    if not isinstance(meta, dict):
        raise ValueError("front matter is not a mapping")
    return meta, text[end + 5:].strip()


def parse_sections(body):
    sections, current = [], None
    for line in body.splitlines():
        match = SECTION_RE.match(line.strip())
        if match:
            current = {"heading": match.group(1), "key": match.group(2), "lines": []}
            sections.append(current)
        elif current is not None and line.strip():
            current["lines"].append(line.strip())
    return [
        {"heading": s["heading"], "key": s["key"], "text": " ".join(s["lines"])}
        for s in sections if s["lines"]
    ]


def model_classes():
    """Exact trained class names per crop, read from the saved label files."""
    classes = {}
    for crop in DiagnosisCase.Crop.values:
        path = Path(settings.BASE_DIR) / MODEL_ARTIFACTS / crop / f"{crop}_classes.json"
        classes[crop] = json.loads(path.read_text(encoding="utf-8"))
    return classes


def read_archive_files(path):
    path = Path(path)
    if not path.exists():
        raise ArchiveError([f"Archive not found: {path}"])
    if path.is_dir():
        return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}
    try:
        with zipfile.ZipFile(path) as archive:
            return {
                Path(info.filename).name: archive.read(info)
                for info in archive.infolist() if not info.is_dir()
            }
    except zipfile.BadZipFile:
        raise ArchiveError([f"Not a valid ZIP archive: {path}"]) from None


def _csv_rows(files, name, errors):
    if name not in files:
        errors.append(f"Missing {name}")
        return []
    return list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig"))))


def _clean_row(row):
    return {(key or "").lstrip("\ufeff"): value for key, value in row.items()}


def validate_archive(files):
    """Return (records, summary). Raises ArchiveError listing every problem found."""
    errors = []
    for name in ("README_AND_INTEGRATION.txt", "MODEL_CLASS_AND_UI_COVERAGE.csv"):
        if name not in files:
            errors.append(f"Missing {name}")
    coverage = [_clean_row(row) for row in _csv_rows(files, "MODEL_CLASS_AND_UI_COVERAGE.csv", errors)]
    ledger = [_clean_row(row) for row in _csv_rows(files, "ENGLISH_SOURCE_AND_CLAIM_LEDGER.csv", errors)]
    evaluation = [_clean_row(row) for row in _csv_rows(files, "ACADEMIC_RAG_EVALUATION_TEST_CASES.csv", errors)]
    display = {}
    if "FARMER_DISPLAY_CONTENT_BY_MODEL_CLASS.json" in files:
        try:
            display = json.loads(files["FARMER_DISPLAY_CONTENT_BY_MODEL_CLASS.json"].decode("utf-8-sig"))
        except ValueError:
            errors.append("FARMER_DISPLAY_CONTENT_BY_MODEL_CLASS.json is not valid JSON")
    else:
        errors.append("Missing FARMER_DISPLAY_CONTENT_BY_MODEL_CLASS.json")

    classes = model_classes()
    all_classes = {name: crop for crop, names in classes.items() for name in names}
    covered = {row["model_class_exact"] for row in coverage if row["crop"] != GENERAL_CROP}
    if covered != set(all_classes):
        errors.append(
            "Coverage model classes differ from trained labels: "
            f"{sorted(covered ^ set(all_classes))}"
        )
    if set(display) != set(all_classes):
        errors.append("Farmer display JSON does not cover exactly the trained model classes")
    topic_ids = {row["topic_id"] for row in coverage}
    for row in ledger:
        if row["topic_id"] not in topic_ids:
            errors.append(f"Ledger references unknown topic {row['topic_id']}")
    for row in evaluation:
        if row["topic_id"] not in topic_ids:
            errors.append(f"Evaluation case references unknown topic {row['topic_id']}")
    ledger_by_topic = {}
    for row in ledger:
        ledger_by_topic.setdefault(row["topic_id"], {})[row["English section"]] = {
            "claim_summary": row["claim summary"],
            "source_ids": row["source_ids"],
            "verification_category": row["verification_category"],
            "known_limit": row["known_limit"],
        }

    records = []
    for row in coverage:
        topic = row["topic_id"]
        files_by_language = {"en": row["english"], "sw": row["swahili"], "am": row["amharic"]}
        for language, filename in files_by_language.items():
            where = f"{filename}"
            if filename not in files:
                errors.append(f"{where}: file listed in coverage is missing")
                continue
            if filename != f"{topic}{LANGUAGE_SUFFIXES[language]}.md":
                errors.append(f"{where}: unexpected file name for {topic}/{language}")
            raw = files[filename]
            try:
                meta, body = parse_markdown_document(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append(f"{where}: {exc}")
                continue
            problems = _validate_document(where, meta, body, row, language, classes, display)
            errors.extend(problems)
            if problems:
                continue
            references = json.loads(meta["references_json"])
            records.append({
                "filename": filename, "topic_id": topic, "language": language,
                "meta": meta, "body": body, "sections": parse_sections(body),
                "references": references, "evidence_map": json.loads(meta["evidence_map_json"]),
                "hash": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                "ledger": ledger_by_topic.get(topic, {}),
                "farmer_display": (
                    display.get(row["model_class_exact"], {}).get("languages", {}).get(language)
                    if row["crop"] != GENERAL_CROP else None
                ),
            })
    markdown_count = len([name for name in files if name.endswith(".md")])
    if markdown_count != len(coverage) * len(LANGUAGES):
        errors.append(f"Archive has {markdown_count} Markdown files; coverage expects {len(coverage) * 3}")
    if errors:
        raise ArchiveError(errors)
    summary = {
        "documents": len(records), "topics": len(coverage),
        "model_classes": len(covered), "general_topics": len(coverage) - len(covered),
        "ledger_rows": len(ledger), "evaluation_cases": len(evaluation),
    }
    return records, summary


def _validate_document(where, meta, body, row, language, classes, display):
    errors = []
    missing = [key for key in REQUIRED_KEYS if key not in meta]
    if missing:
        return [f"{where}: missing front matter keys {missing}"]
    if meta["language"] != language:
        errors.append(f"{where}: language {meta['language']!r} does not match file name")
    if meta["topic_id"] != row["topic_id"]:
        errors.append(f"{where}: topic_id does not match coverage")
    if meta["topic_type"] != row["topic_type"]:
        errors.append(f"{where}: topic_type does not match coverage")
    if meta["crop"] != row["crop"] or meta["crop"] not in {*DiagnosisCase.Crop.values, GENERAL_CROP}:
        errors.append(f"{where}: unsupported or mismatched crop {meta['crop']!r}")
    elif meta["crop"] == GENERAL_CROP:
        if meta["condition"] != GENERAL_CONDITION:
            errors.append(f"{where}: general documents must use condition 'general'")
    elif meta["condition"] != row["model_class_exact"] or meta["condition"] not in classes[meta["crop"]]:
        errors.append(f"{where}: condition {meta['condition']!r} is not an exact trained class for {meta['crop']}")
    if meta["rag_approval_scope"] != SCOPE_MARKER or meta["production_approval"] is not False:
        errors.append(f"{where}: document is not marked educational-prototype only")
    if meta["agronomist_signoff"] is not False:
        errors.append(f"{where}: unexpected agronomist sign-off flag")
    if not isinstance(meta["approved_for_rag"], bool):
        errors.append(f"{where}: approved_for_rag must be boolean")
    try:
        references = json.loads(meta["references_json"])
        evidence_map = json.loads(meta["evidence_map_json"])
    except (TypeError, ValueError):
        return errors + [f"{where}: references_json or evidence_map_json is not valid JSON"]
    if not isinstance(references, list) or not references or not isinstance(evidence_map, dict):
        return errors + [f"{where}: references or evidence map has the wrong shape"]
    ids = [ref.get("id") for ref in references if isinstance(ref, dict)]
    if len(ids) != len(references) or len(set(ids)) != len(ids):
        errors.append(f"{where}: reference ids are missing or duplicated")
    for ref in references:
        if not isinstance(ref, dict) or not all(ref.get(k) for k in ("id", "publisher", "title", "url")):
            errors.append(f"{where}: reference lacks id, publisher, title or url")
        elif not str(ref["url"]).startswith("https://"):
            errors.append(f"{where}: reference {ref['id']} must use an https URL")
    sections = parse_sections(body)
    keys = [section["key"] for section in sections]
    if not sections or len(set(keys)) != len(keys):
        errors.append(f"{where}: missing or duplicate [section] keys")
    for key in EVIDENCE_SECTIONS:
        if key not in keys:
            errors.append(f"{where}: missing section [{key}]")
    for key in keys:
        if key == BIBLIOGRAPHY_SECTION:
            continue
        if key not in EVIDENCE_SECTIONS:
            errors.append(f"{where}: unexpected section [{key}]")
        if key not in evidence_map:
            errors.append(f"{where}: section {key} has no evidence map entry")
        for source_id in SOURCE_ID_RE.findall(str(evidence_map.get(key, ""))):
            if source_id not in ids:
                errors.append(f"{where}: section {key} cites unknown {source_id}")
    return errors


def source_identity(topic_id, language):
    return f"farmsra-rag:{topic_id}:{language}"


def import_records(records):
    """Idempotently store prototype documents. Returns counts of created/updated/unchanged."""
    counts = {"documents_created": 0, "versions_created": 0, "unchanged": 0}
    for record in records:
        meta = record["meta"]
        crops = list(DiagnosisCase.Crop.values) if meta["crop"] == GENERAL_CROP else [meta["crop"]]
        with transaction.atomic():
            document, created = KnowledgeDocument.objects.get_or_create(
                source_identity=source_identity(record["topic_id"], record["language"]),
                defaults={
                    "title": meta["title"], "publisher": meta["publisher"],
                    "source_url": record["references"][0]["url"],
                    "original_language": str(meta["source_language"]),
                    "crops": crops, "diseases": [meta["condition"]],
                    "license": str(meta.get("licence", "")),
                    "usage_permissions": "Links only; external sources are not reproduced.",
                    "provenance": " ".join(
                        str(meta.get(key, "")) for key in ("provenance_note", "activation_basis", "review_method")
                    ).strip(),
                },
            )
            counts["documents_created"] += int(created)
            existing = document.versions.filter(content_hash=record["hash"]).first()
            if existing:
                counts["unchanged"] += 1
                continue
            number = (document.versions.aggregate(Max("version"))["version__max"] or 0) + 1
            Version = KnowledgeDocumentVersion
            document.versions.filter(
                approval_scope=Version.ApprovalScope.EDUCATIONAL_PROTOTYPE
            ).update(prototype_approved=False, lifecycle_status=Version.Lifecycle.SUPERSEDED)
            Version.objects.create(
                document=document, version=number,
                approval_scope=Version.ApprovalScope.EDUCATIONAL_PROTOTYPE,
                prototype_approved=meta["approved_for_rag"] is True,
                original_filename=record["filename"], file_size=record["size"],
                content_type="text/markdown", content_hash=record["hash"],
                extracted_text=record["body"],
                extraction_status=Version.Extraction.SUCCEEDED,
                lifecycle_status=Version.Lifecycle.DRAFT,
                verification_status=Version.Verification.UNVERIFIED,
                expert_review_status=Version.ExpertReview.PENDING,
                is_active=False,
                review_notes="Educational prototype import; not professionally verified or expert reviewed.",
                metadata={
                    "archive": "FarmSra_RAG_v6", "topic_id": record["topic_id"],
                    "topic_type": meta["topic_type"], "language": record["language"],
                    "crop": meta["crop"], "condition": meta["condition"],
                    "content_version": str(meta["content_version"]),
                    "last_reviewed": str(meta.get("last_reviewed", "")),
                    "source_language": str(meta["source_language"]),
                    "translation_review": str(meta.get("translation_review", "")),
                    "editorial_review": str(meta.get("editorial_review", "")),
                    "review_status": str(meta.get("review_status", "")),
                    "rag_approval_scope": meta["rag_approval_scope"],
                    "production_approval": False, "agronomist_signoff": False,
                    "references": record["references"],
                    "evidence_map": record["evidence_map"],
                    "claim_ledger": record["ledger"],
                    "farmer_display": record["farmer_display"],
                },
            )
            counts["versions_created"] += 1
    return counts


def import_archive(path, dry_run=False):
    records, summary = validate_archive(read_archive_files(path))
    if dry_run:
        return summary, None
    return summary, import_records(records)


def get_prototype_collection():
    from .services import get_chroma_collection
    return get_chroma_collection(COLLECTION_NAME)


def _chunk_entries(version):
    metadata = version.metadata
    document = version.document
    references = {ref["id"]: ref for ref in metadata.get("references", [])}
    sections = parse_sections(version.extracted_text)
    digest = version.content_hash[:12]
    entries = []
    for section in sections:
        if section["key"] == BIBLIOGRAPHY_SECTION:
            continue
        mapped = SOURCE_ID_RE.findall(str(metadata.get("evidence_map", {}).get(section["key"], "")))
        refs = [references[item] for item in dict.fromkeys(mapped) if item in references]
        primary = refs[0] if refs else None
        entries.append((
            f"proto:{version.id}:{section['key']}:{digest}",
            f"{section['heading']}: {section['text']}",
            {
                "evidence_tier": TIER, "approval_scope": version.approval_scope,
                "document_id": str(document.id), "document_version_id": str(version.id),
                "document_version": version.version, "source_id": document.source_identity,
                "topic_id": metadata["topic_id"], "topic_type": metadata["topic_type"],
                "language": metadata["language"], "crop": metadata["crop"],
                "condition": metadata["condition"], "section": section["key"],
                "title": primary["title"] if primary else document.title,
                "publisher": primary["publisher"] if primary else document.publisher,
                "url": primary["url"] if primary else "",
                "document_title": document.title,
                "date": "", "content_version": metadata.get("content_version", ""),
                "references_json": json.dumps(refs, ensure_ascii=False),
                "source_hash": version.content_hash,
            },
        ))
    return entries


def remove_prototype_chunks(version, collection=None):
    collection = collection or get_prototype_collection()
    existing = collection.get(where={"document_version_id": str(version.id)})
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])


def index_prototype_version(version):
    """Index (or re-index idempotently) one prototype version in the prototype collection."""
    eligible = (
        version.approval_scope == version.ApprovalScope.EDUCATIONAL_PROTOTYPE
        and version.prototype_approved
        and version.extraction_status == version.Extraction.SUCCEEDED
        and version.extracted_text.strip()
    )
    try:
        collection = get_prototype_collection()
        if not eligible:
            remove_prototype_chunks(version, collection)
            version.indexing_status = version.Indexing.NOT_ELIGIBLE
            version.indexing_error = "Educational-prototype approval flag is not set for this version."
            version.indexed_at = None
        else:
            entries = _chunk_entries(version)
            ids = [item[0] for item in entries]
            existing = collection.get(where={"document_version_id": str(version.id)})
            existing_ids = set(existing.get("ids", []) if existing else [])
            fresh = [item for item in entries if item[0] not in existing_ids]
            if fresh:
                collection.add(
                    ids=[item[0] for item in fresh],
                    documents=[item[1] for item in fresh],
                    metadatas=[item[2] for item in fresh],
                )
            obsolete = [item for item in existing_ids if item not in set(ids)]
            if obsolete:
                collection.delete(ids=obsolete)
            from django.utils import timezone
            version.indexing_status = version.Indexing.INDEXED
            version.indexing_error = ""
            version.indexed_at = timezone.now()
    except Exception:
        logger.exception("Prototype indexing failed for %s", version.pk)
        version.indexing_status = version.Indexing.FAILED
        version.indexing_error = "Prototype indexing failed. Check Chroma and retry."
        version.save(update_fields=["indexing_status", "indexing_error", "updated_at"])
        return False
    version.save(update_fields=["indexing_status", "indexing_error", "indexed_at", "updated_at"])
    return bool(eligible)


def index_all_prototype_versions():
    indexed = failed = 0
    for version in prototype_versions():
        if index_prototype_version(version):
            indexed += 1
        else:
            failed += 1
    return indexed, failed


def _evidence_item(chunk_id, document, meta, text_en=None, language_fallback=False):
    refs = json.loads(meta.get("references_json") or "[]")
    return {
        "chunk_id": chunk_id,
        "source_id": meta.get("source_id", ""),
        "text": document,
        "text_en": text_en,
        "url": meta.get("url", ""),
        "title": meta.get("title", ""),
        "publisher": meta.get("publisher", ""),
        "date": meta.get("date", ""),
        "section": meta.get("section", ""),
        "document_id": meta.get("document_id", ""),
        "document_version_id": meta.get("document_version_id", ""),
        "document_version": meta.get("document_version", ""),
        "evidence_tier": TIER,
        "topic_id": meta.get("topic_id", ""),
        "language": meta.get("language", ""),
        "language_fallback": language_fallback,
        "document_title": meta.get("document_title", ""),
        "references": refs,
    }


def _ranked_english_chunks(collection, where, query, limit):
    try:
        results = collection.query(query_texts=[query], n_results=limit, where=where)
    except Exception:
        logger.exception("Prototype Chroma query failed")
        return []
    if not results or not results.get("documents") or not results["documents"][0]:
        return []
    return list(zip(results["ids"][0], results["documents"][0], results["metadatas"][0]))


def _localized(collection, chunk, language):
    """Return the chunk in the farmer's language, keeping the English twin for grounding."""
    chunk_id, text_en, meta = chunk
    if language == "en":
        return chunk_id, text_en, meta, None, False
    try:
        twin = collection.get(where={"$and": [
            {"topic_id": {"$eq": meta["topic_id"]}},
            {"section": {"$eq": meta["section"]}},
            {"language": {"$eq": language}},
        ]})
    except Exception:
        logger.exception("Prototype translation lookup failed")
        twin = None
    if twin and twin.get("ids"):
        return twin["ids"][0], twin["documents"][0], twin["metadatas"][0], text_en, False
    return chunk_id, text_en, meta, None, True


def retrieve_prototype_evidence(crop, condition, query, language="en", n_results=3, general_results=2):
    """Exact crop/class guidance plus general guidance, ranked on English text.

    Ranking always uses the English twin; the returned text is in `language` when available.
    Only versions flagged prototype_approved in the database are returned.
    """
    language = language if language in LANGUAGES else "en"
    try:
        collection = get_prototype_collection()
    except Exception:
        logger.exception("Prototype Chroma collection unavailable")
        return []
    english = {"language": {"$eq": "en"}}
    if condition:
        specific = {"$and": [{"crop": {"$eq": crop}}, {"condition": {"$eq": condition}}, english]}
    else:
        specific = {"$and": [{"crop": {"$eq": crop}}, english]}
    general = {"$and": [{"crop": {"$eq": GENERAL_CROP}}, {"condition": {"$eq": GENERAL_CONDITION}}, english]}
    chunks = _ranked_english_chunks(collection, specific, query, n_results)
    chunks += _ranked_english_chunks(collection, general, query, general_results)
    if not chunks:
        return []
    localized = [_localized(collection, chunk, language) for chunk in chunks]
    version_ids = {meta.get("document_version_id") for _i, _t, meta, _e, _f in localized}
    version_ids |= {chunk[2].get("document_version_id") for chunk in chunks}
    approved = {str(item) for item in prototype_versions().filter(id__in=version_ids).values_list("id", flat=True)}
    evidence = []
    for (chunk_id, text, meta, text_en, fallback), original in zip(localized, chunks):
        if meta.get("document_version_id") not in approved or original[2].get("document_version_id") not in approved:
            continue
        evidence.append(_evidence_item(chunk_id, text, meta, text_en, fallback))
    return evidence
