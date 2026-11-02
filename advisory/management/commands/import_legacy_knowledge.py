from pathlib import Path
import hashlib

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from advisory.management.commands.ingest_knowledge import parse_frontmatter
from advisory.models import KnowledgeDocument, KnowledgeDocumentVersion


class Command(BaseCommand):
    help = "Register tracked Markdown sources as inactive, unverified knowledge drafts."

    def handle(self, *args, **options):
        created = updated = 0
        for path in sorted((Path(settings.BASE_DIR) / "advisory" / "sources").glob("*.md")):
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not body or not all(metadata.get(key) for key in ("crop", "condition", "title")):
                self.stderr.write(f"Skipped malformed source: {path.name}")
                continue
            identity = path.stem
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            with transaction.atomic():
                document, was_created = KnowledgeDocument.objects.update_or_create(
                    source_identity=identity,
                    defaults={
                        "title": metadata["title"], "publisher": metadata.get("publisher", ""),
                        "source_url": metadata.get("url", ""), "original_language": "en",
                        "crops": [metadata["crop"]], "diseases": [metadata["condition"]],
                        "provenance": f"Tracked repository source: advisory/sources/{path.name}",
                    },
                )
                latest = document.versions.order_by("-version").first()
                if latest and latest.content_hash == content_hash:
                    version_created = False
                else:
                    KnowledgeDocumentVersion.objects.filter(
                        document=document, is_active=True
                    ).update(
                        lifecycle_status=KnowledgeDocumentVersion.Lifecycle.SUPERSEDED,
                        is_active=False,
                        indexing_status=KnowledgeDocumentVersion.Indexing.NOT_ELIGIBLE,
                    )
                    KnowledgeDocumentVersion.objects.create(
                        document=document, version=(latest.version + 1 if latest else 1),
                        original_filename=path.name, content_type="text/markdown",
                        file_size=path.stat().st_size, content_hash=content_hash,
                        extracted_text=body,
                        extraction_status=KnowledgeDocumentVersion.Extraction.SUCCEEDED,
                        lifecycle_status=KnowledgeDocumentVersion.Lifecycle.DRAFT,
                        verification_status=KnowledgeDocumentVersion.Verification.UNVERIFIED,
                        expert_review_status=KnowledgeDocumentVersion.ExpertReview.PENDING,
                        is_active=False,
                        indexing_status=KnowledgeDocumentVersion.Indexing.NOT_ELIGIBLE,
                    )
                    version_created = True
            created += int(was_created and version_created)
            updated += int(not was_created or not version_created)
        self.stdout.write(self.style.SUCCESS(f"Legacy registration complete: {created} created, {updated} unchanged/updated."))
