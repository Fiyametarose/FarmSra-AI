import time

from django.core.management.base import BaseCommand

from advisory.workflow import claim_next_job, execute_job


class Command(BaseCommand):
    help = "Run the durable agricultural advisory generation worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        while True:
            job = claim_next_job()
            if job:
                self.stdout.write(f"Processing advisory job {job.pk}")
                try:
                    execute_job(job)
                except Exception as exc:
                    self.stderr.write(f"Advisory job {job.pk} failed: {type(exc).__name__}")
                    from advisory.models import AdvisoryGenerationJob, AdvisoryVersion
                    from django.utils import timezone
                    now = timezone.now()
                    AdvisoryGenerationJob.objects.filter(pk=job.pk, status="running").update(
                        status="failed", stage="unavailable", error_code="worker_error",
                        error_message="The advisory worker could not complete this request. Please retry.",
                        completed_at=now,
                    )
                    AdvisoryVersion.objects.filter(pk=job.advisory_version_id, status="running").update(
                        status="failed", completed_at=now
                    )
            if options["once"]:
                return
            if not job:
                time.sleep(max(0.2, options["poll_seconds"]))
