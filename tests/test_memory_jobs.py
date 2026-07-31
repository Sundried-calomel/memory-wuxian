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

from memory_jobs import MaintenanceQueue, run_model_free_tick
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


if __name__ == "__main__":
    unittest.main()
