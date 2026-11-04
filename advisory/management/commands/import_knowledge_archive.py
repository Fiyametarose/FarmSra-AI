from django.core.management.base import BaseCommand, CommandError

from advisory.prototype import (
    ArchiveError,
    import_archive,
    index_all_prototype_versions,
)


class Command(BaseCommand):
    help = (
        "Validate and import the FarmSra RAG v6 educational-prototype archive (ZIP or folder). "
        "Imported documents stay unverified and are retrieved only when "
        "RAG_EDUCATIONAL_PROTOTYPE_MODE=true."
    )

    def add_arguments(self, parser):
        parser.add_argument("--archive", required=True, help="Path to FarmSra_RAG_v6.zip or its extracted folder.")
        parser.add_argument("--dry-run", action="store_true", help="Validate only; write nothing.")
        parser.add_argument(
            "--index", action="store_true",
            help="Also index approved prototype versions into the separate prototype Chroma collection.",
        )

    def handle(self, *args, **options):
        try:
            summary, counts = import_archive(options["archive"], dry_run=options["dry_run"])
        except ArchiveError as exc:
            for error in exc.errors:
                self.stderr.write(error)
            raise CommandError(f"Archive validation failed with {len(exc.errors)} problem(s).") from None
        self.stdout.write(
            f"Validated {summary['documents']} documents / {summary['topics']} topics "
            f"({summary['model_classes']} model classes, {summary['general_topics']} general); "
            f"{summary['ledger_rows']} ledger rows, {summary['evaluation_cases']} evaluation cases."
        )
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: no records were written."))
            return
        self.stdout.write(self.style.SUCCESS(
            f"Imported: {counts['documents_created']} documents created, "
            f"{counts['versions_created']} versions created, {counts['unchanged']} unchanged."
        ))
        if options["index"]:
            indexed, failed = index_all_prototype_versions()
            self.stdout.write(f"Prototype indexing: {indexed} indexed, {failed} failed.")
            if failed:
                raise CommandError("Some prototype versions could not be indexed.")
