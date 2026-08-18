from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_jobs import (
    MaintenanceQueue,
    commit_backup_debt_generation,
    maintenance_projection,
    reconcile_pending_debt,
    run_model_free_tick,
    stable_path_identity,
)
from memory_service_state import service_state


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class MaintenanceQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "archive"
        self.clock = Clock()
        self.queue = MaintenanceQueue(self.root, clock=self.clock)

    def test_mw27_duplicate_001_enqueue_and_completion_are_idempotent(self):
        first = self.queue.enqueue("archive-health", "health:1", {"scope": "bounded"})
        second = self.queue.enqueue("archive-health", "health:1", {"scope": "bounded"})
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        claimed = self.queue.claim("worker-a")
        completed = self.queue.complete(claimed["job_id"], "worker-a", {"ok": True})
        repeated = self.queue.complete(claimed["job_id"], "worker-a", {"ok": True})
        self.assertEqual(completed, repeated)
        self.assertEqual(self.queue.status()["counts"]["completed"], 1)

    def test_mw27_crash_restart_and_stalelock_001_recovers_expired_lease(self):
        job = self.queue.enqueue("archive-health", "health:crash", max_attempts=2)
        claimed = self.queue.claim("crashed-worker", lease_seconds=5)
        self.assertEqual(claimed["attempts"], 1)
        self.clock.advance(6)
        restarted = MaintenanceQueue(self.root, clock=self.clock)
        self.assertEqual(restarted.recover_expired(), [job["job_id"]])
        reclaimed = restarted.claim("replacement", lease_seconds=5)
        self.assertEqual(reclaimed["job_id"], job["job_id"])
        self.assertEqual(reclaimed["attempts"], 2)
        self.clock.advance(6)
        restarted.recover_expired()
        self.assertEqual(restarted.status()["counts"]["quarantined"], 1)

    def test_mw27_permission_001_failed_persistence_leaves_prior_job_readable(self):
        job = self.queue.enqueue("archive-health", "health:permission")
        path = self.queue._path(job["job_id"])
        before = path.read_bytes()
        with patch("memory_jobs.atomic_write_canonical_json", side_effect=PermissionError(13)):
            with self.assertRaises(PermissionError):
                self.queue.claim("worker")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.queue.jobs()[0]["state"], "queued")

    def test_mw27_round_boundary_and_no_side_effect_001_only_marks_semantic_ready(self):
        self.queue.enqueue(
            "semantic-summary-eligibility",
            "conversation:complete-round:2",
            {
                "conversation_id": "codex:test",
                "completed_round": 2,
                "round_complete": True,
            },
        )
        forbidden = unittest.mock.Mock(side_effect=AssertionError("AI must not run"))
        result = run_model_free_tick(
            self.queue,
            {"semantic-summary-eligibility": forbidden},
            owner="mechanical",
        )
        self.assertEqual(result["ai_invocations"], 0)
        self.assertEqual(result["processed"][0]["state"], "semantic-ready")
        forbidden.assert_not_called()

    def test_mw27_capture_001_service_telemetry_advances_while_job_quarantined(self):
        job = self.queue.enqueue("archive-health", "health:quarantine", max_attempts=1)
        claimed = self.queue.claim("worker")
        self.queue.fail(job["job_id"], "worker", "permanent")
        telemetry = self.root / "imports/codex/collector-telemetry.json"
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        telemetry.write_text(json.dumps({"last_heartbeat_epoch": 1785513600}), encoding="utf-8")
        with patch("memory_service_state.time.time", return_value=1785513610):
            state = service_state(self.root, {"integration": {"codex": {"enabled": True}}})
        self.assertEqual(state["actual"]["collector"], "running")
        self.assertEqual(state["queue"]["quarantined"], 1)

    def test_mw27_semantic_retry_001_is_bounded_and_quarantined(self):
        job = self.queue.enqueue(
            "semantic-summary-eligibility",
            "conversation:complete-round:3",
            {"completed_round": 3, "round_complete": True},
            max_attempts=3,
        )
        run_model_free_tick(self.queue, {}, owner="mechanical", maximum_jobs=1)
        first = self.queue.claim_semantic(job["job_id"], "semantic-a")
        self.assertIsNotNone(first)
        retriable = self.queue.fail_semantic(
            job["job_id"], "semantic-a", "token=private", retry_delay_seconds=0
        )
        self.assertEqual(retriable["state"], "semantic-ready")
        self.assertNotIn("private", retriable["last_error"])
        second = self.queue.claim_semantic(job["job_id"], "semantic-b")
        self.assertIsNotNone(second)
        final = self.queue.fail_semantic(job["job_id"], "semantic-b", "permanent")
        self.assertEqual(final["state"], "quarantined")
        self.assertIsNone(self.queue.claim_semantic(job["job_id"], "semantic-c"))

    def test_semantic_lease_renewal_preserves_owner_and_attempt(self):
        job = self.queue.enqueue(
            "semantic-summary-eligibility",
            "conversation:complete-round:renew",
            {"completed_round": 4, "round_complete": True},
            max_attempts=3,
        )
        self.queue.mark_semantic_ready_bulk()
        claimed = self.queue.claim_semantic(job["job_id"], "semantic-owner", lease_seconds=10)
        self.clock.advance(6)
        renewed = self.queue.renew_semantic(
            job["job_id"], "semantic-owner", lease_seconds=10
        )
        self.clock.advance(6)

        self.assertEqual(renewed["attempts"], claimed["attempts"])
        self.assertEqual(self.queue.recover_expired(), [])
        self.assertEqual(self.queue.jobs()[0]["lease_owner"], "semantic-owner")

    def test_quarantined_job_requeue_preserves_failure_receipt(self):
        job = self.queue.enqueue(
            "semantic-summary-eligibility",
            "conversation:complete-round:requeue",
            {"completed_round": 3, "round_complete": True},
            max_attempts=1,
        )
        self.queue.mark_semantic_ready_bulk()
        claimed = self.queue.claim_semantic(job["job_id"], "semantic-a")
        self.queue.fail_semantic(job["job_id"], "semantic-a", "invalid policy output")

        requeued = self.queue.requeue_quarantined(job["job_id"], "worker contract upgraded")

        self.assertEqual(requeued["state"], "semantic-ready")
        self.assertEqual(requeued["attempts"], 0)
        receipt = json.loads(Path(requeued["requeue_receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["previous_attempts"], 1)
        self.assertEqual(receipt["previous_last_error"], "invalid policy output")
        self.assertEqual(receipt["reason"], "worker contract upgraded")

    def test_non_quarantined_job_cannot_be_requeued(self):
        job = self.queue.enqueue("archive-health", "health:not-quarantined")
        with self.assertRaisesRegex(ValueError, "Only a quarantined"):
            self.queue.requeue_quarantined(job["job_id"], "not allowed")

    def test_mw27_service_state_001_reads_real_collector_telemetry_shape(self):
        telemetry = self.root / "imports/codex/collector-telemetry.json"
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        telemetry.write_text(
            json.dumps({"updated_at": "2026-08-01T00:00:10Z", "ready": True}),
            encoding="utf-8",
        )
        with patch("memory_service_state.time.time", return_value=1785542420):
            state = service_state(self.root, {"integration": {"codex": {"enabled": True}}})
        self.assertEqual(state["actual"]["collector"], "running")

    def test_service_state_is_pure_read_and_does_not_recover_leases(self):
        job = self.queue.enqueue("archive-health", "health:pure-read")
        path = self.queue._path(job["job_id"])
        before = path.read_bytes()
        with patch("memory_service_state.MaintenanceQueue.recover_expired") as recover:
            service_state(self.root, {"integration": {"codex": {"enabled": False}}})
        recover.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def _pending_summary(self, name: str, signature: str, round_end: int = 2):
        pending = self.root / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        path = pending / name
        path.write_text(
            json.dumps({
                "job_id": name.removesuffix(".json"),
                "summary_level": 1,
                "source_signature": signature,
                "conversation_id": "codex:test",
                "source_round_end": round_end,
            }),
            encoding="utf-8",
        )
        return path

    def test_mw211_reconcile_001_scans_all_debt_once_and_is_idempotent(self):
        self._pending_summary("job-000001.json", "conversation:a:rounds:1-2")
        self._pending_summary("job-000002.json", "conversation:b:rounds:1-2")
        debt = self.root / "pending" / "backup-debt.json"
        debt.write_text(json.dumps({"format_version": 1, "mutation_count": 7}), encoding="utf-8")

        first = reconcile_pending_debt(self.root, self.queue)
        second = reconcile_pending_debt(self.root, self.queue)

        self.assertEqual(first["summary_jobs_scanned"], 2)
        self.assertEqual(first["created"], 3)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["existing"], 3)
        self.assertEqual(len(self.queue.jobs()), 3)

    def test_semantic_enqueue_reuses_exact_legacy_payload(self):
        pending = self._pending_summary(
            "job-000010.json", "conversation:a:rounds:1-2"
        )
        payload = {
            "summary_job": str(pending.resolve()),
            "summary_job_id": "job-000010",
            "source_signature": "conversation:a:rounds:1-2",
            "conversation_id": "codex:test",
            "completed_round": 2,
            "round_complete": True,
        }
        legacy = self.queue.enqueue(
            "semantic-summary-eligibility",
            "summary:conversation:a:rounds:1-2",
            payload,
            max_attempts=4,
        )

        reconciled = reconcile_pending_debt(self.root, self.queue)

        self.assertEqual(reconciled["created"], 0)
        self.assertEqual(reconciled["existing"], 1)
        self.assertEqual(self.queue.jobs()[0]["job_id"], legacy["job_id"])

    def test_semantic_enqueue_allows_new_persisted_replay_for_same_source(self):
        first = self._pending_summary(
            "job-000010.json", "conversation:a:rounds:1-2"
        )
        first_payload = {
            "summary_job": str(first.resolve()),
            "summary_job_id": "job-000010",
            "source_signature": "conversation:a:rounds:1-2",
            "conversation_id": "codex:test",
            "completed_round": 2,
            "round_complete": True,
        }
        legacy = self.queue.enqueue(
            "semantic-summary-eligibility",
            "summary:conversation:a:rounds:1-2",
            first_payload,
            max_attempts=4,
        )
        claimed = self.queue.claim( "legacy-worker")
        self.queue.complete(claimed["job_id"], "legacy-worker", {"status": "ingested"})
        first.unlink()
        second = self._pending_summary(
            "job-000011.json", "conversation:a:rounds:1-2"
        )

        reconciled = reconcile_pending_debt(self.root, self.queue)

        self.assertEqual(reconciled["created"], 1)
        self.assertEqual(reconciled["invalid"], [])
        jobs = self.queue.jobs()
        self.assertEqual(len(jobs), 2)
        replay = next(item for item in jobs if item["job_id"] != legacy["job_id"])
        self.assertEqual(replay["payload"]["summary_job_id"], "job-000011")
        self.assertEqual(replay["payload"]["summary_job"], str(second.resolve()))
        self.assertEqual(
            replay["idempotency_key"],
            "summary:conversation:a:rounds:1-2:job:job-000011",
        )

    def test_mw211_queue_002_quarantined_and_deferred_work_do_not_block_ready_jobs(self):
        blocked = self.queue.enqueue("archive-health", "health:a", max_attempts=1)
        claimed = self.queue.claim("worker")
        self.assertEqual(claimed["job_id"], blocked["job_id"])
        self.queue.fail(blocked["job_id"], "worker", "permanent")
        semantic = self.queue.enqueue(
            "semantic-summary-eligibility",
            "summary:next",
            {"completed_round": 2, "round_complete": True},
        )
        promoted = self.queue.mark_semantic_ready_bulk()
        self.assertEqual(promoted, [semantic["job_id"]])
        self.assertEqual(self.queue.claim_semantic(semantic["job_id"], "semantic")["job_id"], semantic["job_id"])

    def test_mw211_semantic_003_unavailable_runtime_does_not_consume_attempt(self):
        job = self.queue.enqueue(
            "semantic-summary-eligibility",
            "summary:offline",
            {"completed_round": 1, "round_complete": True},
        )
        self.queue.mark_semantic_ready_bulk()
        claimed = self.queue.claim_semantic(job["job_id"], "semantic")
        self.assertEqual(claimed["attempts"], 1)
        deferred = self.queue.defer_semantic(job["job_id"], "semantic", "Codex unavailable")
        self.assertEqual(deferred["state"], "semantic-ready")
        self.assertEqual(deferred["attempts"], 0)

    def test_mw211_backup_004_generation_clear_is_compare_and_delete(self):
        debt = self.root / "pending" / "backup-debt.json"
        debt.parent.mkdir(parents=True, exist_ok=True)
        debt.write_text(json.dumps({"mutation_count": 1}), encoding="utf-8")
        reconcile_pending_debt(self.root, self.queue)
        generation = maintenance_projection(self.root, self.queue)["backup_debt"]["generation_sha256"]
        debt.write_text(json.dumps({"mutation_count": 2}), encoding="utf-8")
        self.assertFalse(commit_backup_debt_generation(self.root, generation))
        self.assertTrue(debt.exists())
        current = maintenance_projection(self.root, self.queue)["backup_debt"]["generation_sha256"]
        self.assertTrue(commit_backup_debt_generation(self.root, current))
        self.assertFalse(debt.exists())

    def test_mw211_projection_005_separates_semantic_and_backup_debt(self):
        self._pending_summary("job-000003.json", "conversation:c:rounds:1-2")
        reconcile_pending_debt(self.root, self.queue)
        projection = maintenance_projection(self.root, self.queue)
        self.assertEqual(projection["format"], "memory-wuxian-maintenance-projection-v1")
        self.assertEqual(projection["semantic_debt"]["pending_summary_jobs"], 1)
        self.assertFalse(projection["backup_debt"]["present"])

    def test_mw2115_path_001_extended_windows_path_reconciles_with_normal_path(self):
        extended = {
            "summary_job": r"\\?\C:\Archive\pending\job-000318.json",
            "summary_job_id": "job-000318",
            "source_signature": "conversation:test:rounds:1-2",
            "conversation_id": "codex:test",
            "completed_round": 2,
            "round_complete": True,
        }
        normal = {**extended, "summary_job": r"c:\archive\pending\job-000318.json"}
        first = self.queue.enqueue(
            "semantic-summary-eligibility", "summary:stable-path", extended, max_attempts=4
        )
        second = self.queue.enqueue(
            "semantic-summary-eligibility", "summary:stable-path", normal, max_attempts=4
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(stable_path_identity(extended["summary_job"]), stable_path_identity(normal["summary_job"]))
        self.assertFalse(self.queue.jobs()[0]["payload"]["summary_job"].startswith("\\\\?\\"))

    def test_mw2115_projection_002_quarantine_is_explicit_permanent_failure(self):
        job = self.queue.enqueue("archive-health", "health:terminal", max_attempts=1)
        self.queue.claim("worker")
        self.queue.fail(job["job_id"], "worker", "permanent diagnostic")
        projection = maintenance_projection(self.root, self.queue)
        category = projection["mechanical_debt"]["archive_health"]
        self.assertEqual(category["permanent_failures"], 1)
        self.assertEqual(category["permanent_failure_details"][0]["last_error"], "permanent diagnostic")


if __name__ == "__main__":
    unittest.main()
