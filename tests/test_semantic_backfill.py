from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_jobs import MaintenanceQueue, reconcile_pending_debt
from memory_cli import MemoryStore
from semantic_backfill import run_backfill
from semantic_dispatch import dispatch_job
from maintenance_supervisor import run_supervisor


class SemanticBackfillV211Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "archive"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            "memory:\n  root_directory: ./archive\n"
            "summaries:\n  level_1_trigger_rounds: 2\n"
            "  level_1_trigger_characters: 20000\n"
            "  higher_level_trigger_count: 2\n"
            "  maximum_summary_depth: 4\n",
            encoding="utf-8",
        )
        (self.root / "pending").mkdir(parents=True)

    def job(self, name: str, signature: str, *, level: int = 1, boundary: int = 2) -> Path:
        path = self.root / "pending" / name
        payload = {
            "job_id": name.removesuffix(".json"),
            "summary_level": level,
            "source_signature": signature,
            "conversation_id": "codex:test",
            "created_at": f"2026-08-01T00:00:0{boundary}Z",
        }
        if level == 1:
            payload["source_round_end"] = boundary
        else:
            payload["source_end_sequence"] = boundary
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_mw211_dispatch_001_offline_probe_does_not_lease_or_consume_retry(self):
        job = self.job("job-000001.json", "conversation:test:rounds:1-2")
        result = dispatch_job(
            self.root,
            self.config,
            job,
            check_availability=True,
            availability_probe=lambda _config: (False, "offline"),
        )
        queued = MaintenanceQueue(self.root).jobs()[0]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["ai_invocations"], 0)
        self.assertEqual(queued["state"], "semantic-ready")
        self.assertEqual(queued["attempts"], 0)

    def test_mw211_backfill_002_quarantined_first_job_does_not_block_next(self):
        first = self.job("job-000001.json", "conversation:test:rounds:1-4", level=2, boundary=4)
        second = self.job("job-000002.json", "conversation:test:rounds:5-6", level=1, boundary=6)
        queue = MaintenanceQueue(self.root)
        reconcile_pending_debt(self.root, queue)
        queue.mark_semantic_ready_bulk()
        first_maintenance = next(
            item for item in queue.jobs()
            if Path(item["payload"]["summary_job"]) == first.resolve()
        )
        for attempt in range(4):
            owner = f"failing-{attempt}"
            queue.claim_semantic(first_maintenance["job_id"], owner)
            queue.fail_semantic(first_maintenance["job_id"], owner, "permanent", retry_delay_seconds=0)

        with patch(
            "semantic_backfill.dispatch_job",
            return_value={"status": "ingested", "job_id": second.stem},
        ) as dispatch:
            result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        dispatch.assert_called_once()
        self.assertTrue(os.path.samefile(dispatch.call_args.args[2], second))
        self.assertEqual(result["completed_jobs"], 1)
        self.assertTrue(any(item["reason"] == "quarantined" for item in result["skipped"]))

    def test_mw211_supervisor_003_repeats_bounded_ticks_without_console_dependency(self):
        results = [
            {"completed_jobs": 0, "skipped": [{"reason": "runtime-unavailable"}]},
            {"completed_jobs": 2, "skipped": []},
        ]
        with patch("maintenance_supervisor.run_backfill", side_effect=results) as backfill:
            run_supervisor(
                self.root,
                self.config,
                interval_seconds=30,
                maximum_semantic_jobs=2,
                maximum_cycles=2,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(backfill.call_count, 2)
        self.assertTrue(all(call.kwargs["max_jobs"] == 2 for call in backfill.call_args_list))
        state = json.loads((self.root / "maintenance/supervisor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["cycle"], 2)

    def test_mw211_dispatch_004_runtime_drop_after_probe_restores_attempt(self):
        job = self.job("job-000004.json", "conversation:test:rounds:7-8", boundary=8)
        probes = iter([(True, "available"), (False, "network unavailable")])
        with patch("semantic_dispatch.run_job", side_effect=RuntimeError("connection failed")):
            result = dispatch_job(
                self.root,
                self.config,
                job,
                check_availability=True,
                availability_probe=lambda _config: next(probes),
            )
        queued = MaintenanceQueue(self.root).jobs()[0]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(queued["state"], "semantic-ready")
        self.assertEqual(queued["attempts"], 0)

    def test_mw211_recovery_005_clears_marker_only_after_repair_is_ok(self):
        marker = self.root / "pending/native-recovery-debt.json"
        marker.write_text('{"format_version": 1}', encoding="utf-8")

        with patch.object(MemoryStore, "heartbeat", return_value={"status": "attention"}):
            first = run_backfill(self.root, self.config, max_jobs=0, dry_run=False)
        self.assertTrue(marker.exists())
        self.assertEqual(first["native_recovery"]["status"], "attention")

        with patch.object(MemoryStore, "heartbeat", return_value={"status": "ok"}) as heartbeat:
            second = run_backfill(self.root, self.config, max_jobs=0, dry_run=False)
        heartbeat.assert_called_once_with(create_jobs=False, repair=True)
        self.assertFalse(marker.exists())
        self.assertEqual(second["native_recovery"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
