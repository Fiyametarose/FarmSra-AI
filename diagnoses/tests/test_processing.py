from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from diagnoses.inference import Prediction
from diagnoses.models import DiagnosisCase
from diagnoses.processing import claim_case, complete_case, recover_stale_case


class ProcessingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        farmer = get_user_model().objects.create_user(
            "concurrent@example.com", "test-password"
        )
        self.farmer_id = farmer.id
        self.case = DiagnosisCase.objects.create(
            farmer=farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image="cases/concurrency-test.png",
        )

    def test_only_one_concurrent_request_claims_pending_case(self):
        barrier = Barrier(2)

        def attempt_claim():
            close_old_connections()
            barrier.wait()
            try:
                return claim_case(self.case.id, self.farmer_id)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt_claim(), range(2)))

        self.assertEqual(sum(result is not None for result in results), 1)
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, DiagnosisCase.Status.PROCESSING)

    def test_recovered_claim_cannot_be_overwritten_by_old_worker(self):
        old_claim = claim_case(self.case.id, self.farmer_id)
        DiagnosisCase.objects.filter(id=self.case.id).update(
            updated_at=timezone.now() - timedelta(minutes=16)
        )
        self.assertEqual(recover_stale_case(self.case.id, self.farmer_id), 1)
        new_claim = claim_case(self.case.id, self.farmer_id)
        old_prediction = Prediction("old", 0, 0.9, "old.keras")
        new_prediction = Prediction("new", 1, 0.8, "new.keras")

        self.assertEqual(
            complete_case(self.case.id, self.farmer_id, old_claim, old_prediction),
            0,
        )
        self.assertEqual(
            complete_case(self.case.id, self.farmer_id, new_claim, new_prediction),
            1,
        )
        self.case.refresh_from_db()
        self.assertEqual(self.case.predicted_class, "new")
        self.assertIsNotNone(self.case.completed_at)
        self.assertEqual(self.case.completed_at, self.case.updated_at)

        completion_time = self.case.completed_at
        self.case.location = "Later correction"
        self.case.save(update_fields=["location"])
        self.case.refresh_from_db()
        self.assertEqual(self.case.completed_at, completion_time)
