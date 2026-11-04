import hashlib
import logging
from pathlib import Path

from django.utils import timezone

from .management.commands.ingest_knowledge import chunk_text
from .models import KnowledgeDocumentVersion
from .services import get_chroma_collection

logger = logging.getLogger(__name__)


def extract_uploaded_document(version):
    if not version.uploaded_file:
        return version
    name = version.uploaded_file.name
    extension = Path(name).suffix.lower()
    version.original_filename = Path(name).name
    version.file_size = version.uploaded_file.size
    version.content_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(extension, "")
    try:
        version.uploaded_file.open("rb")
        raw = version.uploaded_file.read()
        version.content_hash = hashlib.sha256(raw).hexdigest()
        version.uploaded_file.seek(0)
        if extension == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(version.uploaded_file)
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        elif extension == ".docx":
            from docx import Document
            document = Document(version.uploaded_file)
            text = "\n".join(paragraph.text.strip() for paragraph in document.paragraphs)
        else:
            raise ValueError("Unsupported document type")
        version.extracted_text = text.strip()
        if version.extracted_text:
            version.extraction_status = version.Extraction.SUCCEEDED
            version.extraction_error = ""
        else:
            version.extraction_status = version.Extraction.UNAVAILABLE
            version.extraction_error = "No extractable text was found; the file may be image-only."
    except Exception as exc:
        logger.warning("Knowledge extraction failed for %s: %s", version.pk, type(exc).__name__)
        version.extracted_text = ""
        version.extraction_status = version.Extraction.FAILED
        version.extraction_error = "The document could not be parsed safely."
    finally:
        version.uploaded_file.close()
    version.save(update_fields=[
        "original_filename", "file_size", "content_type", "content_hash",
        "extracted_text", "extraction_status", "extraction_error", "updated_at",
    ])
    return version


def eligible_versions():
    return KnowledgeDocumentVersion.objects.filter(
        approval_scope=KnowledgeDocumentVersion.ApprovalScope.PRODUCTION,
        lifecycle_status=KnowledgeDocumentVersion.Lifecycle.APPROVED,
        is_active=True,
        verification_status=KnowledgeDocumentVersion.Verification.VERIFIED,
        expert_review_status=KnowledgeDocumentVersion.ExpertReview.REVIEWED,
        extraction_status=KnowledgeDocumentVersion.Extraction.SUCCEEDED,
    ).select_related("document")


def remove_version_chunks(version):
    collection = get_chroma_collection()
    existing = collection.get(where={"document_version_id": str(version.id)})
    if existing and existing.get("ids"):
        collection.delete(ids=existing["ids"])


def index_document_version(version):
    if version.approval_scope == version.ApprovalScope.EDUCATIONAL_PROTOTYPE:
        from .prototype import index_prototype_version
        return index_prototype_version(version)
    if not version.is_retrieval_eligible:
        try:
            remove_version_chunks(version)
        except Exception:
            logger.exception("Could not remove ineligible knowledge chunks")
        version.indexing_status = version.Indexing.NOT_ELIGIBLE
        version.indexing_error = "Version is not approved, active, verified, and expert reviewed."
        version.indexed_at = None
        version.save(update_fields=["indexing_status", "indexing_error", "indexed_at", "updated_at"])
        return False

    chunks = chunk_text(version.extracted_text)
    if not chunks:
        version.indexing_status = version.Indexing.FAILED
        version.indexing_error = "No extractable text is available for indexing."
        version.save(update_fields=["indexing_status", "indexing_error", "updated_at"])
        return False
    document = version.document
    try:
        collection = get_chroma_collection()
        existing = collection.get(where={"document_version_id": str(version.id)})
        existing_ids = existing.get("ids", []) if existing else []
        digest = version.content_hash or hashlib.sha256(version.extracted_text.encode()).hexdigest()
        entries = [
            (crop, condition, index, chunk)
            for crop in document.crops for condition in document.diseases
            for index, chunk in enumerate(chunks)
        ]
        ids = [
            f"knowledge:{version.id}:{digest[:16]}:{crop}:{hashlib.sha256(condition.encode()).hexdigest()[:10]}:{index}"
            for crop, condition, index, _chunk in entries
        ]
        documents = [chunk for _crop, _condition, _index, chunk in entries]
        metadatas = [{
            "document_id": str(document.id), "document_version_id": str(version.id),
            "document_version": version.version, "source_id": document.source_identity or str(document.id),
            "crop": crop, "condition": condition,
            "title": document.title, "publisher": document.publisher,
            "url": document.source_url, "date": str(document.publication_date or ""),
            "section": "", "chunk_index": index, "source_hash": digest,
        } for crop, condition, index, _chunk in entries]
        new_ids = [item for item in ids if item not in set(existing_ids)]
        if new_ids:
            positions = [ids.index(item) for item in new_ids]
            collection.add(
                ids=new_ids,
                documents=[documents[index] for index in positions],
                metadatas=[metadatas[index] for index in positions],
            )
        obsolete = [item for item in existing_ids if item not in set(ids)]
        if obsolete:
            collection.delete(ids=obsolete)
        version.indexing_status = version.Indexing.INDEXED
        version.indexing_error = ""
        version.indexed_at = timezone.now()
        version.save(update_fields=["indexing_status", "indexing_error", "indexed_at", "updated_at"])
        return True
    except Exception:
        logger.exception("Knowledge indexing failed for %s", version.pk)
        version.indexing_status = version.Indexing.FAILED
        version.indexing_error = "Indexing failed. Retry from the administrator workflow."
        version.save(update_fields=["indexing_status", "indexing_error", "updated_at"])
        return False
