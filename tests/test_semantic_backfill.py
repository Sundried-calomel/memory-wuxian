from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_jobs import MaintenanceQueue, reconcile_pending_debt
from memory_cli import MemoryStore
from memory_cli import load_simple_yaml
from semantic_backfill import recent_recovery_audit, run_backfill
from semantic_dispatch import dispatch_job
from maintenance_supervisor import run_supervisor, run_supervisor_tick


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
        self.assertEqual(state["status"], "healthy")
        self.assertEqual(state["cycle"], 2)

    def test_mw211_supervisor_records_runtime_failure_detail(self):
        job = self.job("job-000006.json", "conversation:test:rounds:11-12", boundary=12)
        with patch(
            "semantic_backfill.dispatch_job",
            return_value={
                "status": "unavailable",
                "reason": "Codex CLI executable is unavailable",
            },
        ):
            result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)
        self.assertEqual(result["completed_jobs"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "runtime-unavailable")
        self.assertIn("Codex CLI executable", result["skipped"][0]["error"])
        self.assertTrue(job.exists())

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

    def test_mw2115_parent_001_ingest_schedules_due_parent_with_store_owner(self):
        store = MemoryStore(self.root, load_simple_yaml(self.config))
        store.init()

        def add_summary(number: int) -> None:
            summary_id = f"L1-{number:06d}"
            relative = f"summaries/level-1/{summary_id}.md"
            summary_path = self.root / relative
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(f"# {summary_id}\n", encoding="utf-8")
            record = {
                "event": "created",
                "summary_id": summary_id,
                "level": 1,
                "conversation_id": "codex:test",
                "path": relative,
                "source_start": f"msg-{number}-u",
                "source_end": f"msg-{number}-a",
                "source_start_sequence": number * 2 - 1,
                "source_end_sequence": number * 2,
                "start_time": f"2026-08-01T00:00:0{number}Z",
                "end_time": f"2026-08-01T00:00:0{number}Z",
                "source_files": [],
            }
            with (self.root / "indexes/summaries.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            with (self.root / "summaries/registry.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "created", "summary_id": summary_id}) + "\n")

        add_summary(1)
        level_one_job = self.job("job-000010.json", "conversation:test:rounds:3-4", boundary=4)

        def ingest_second(_root, _config, job_path, **_kwargs):
            add_summary(2)
            job_path.unlink()
            return {"status": "ingested", "job_id": "job-000010"}

        heartbeat = {
            "status": "ok",
            "integrity_issues": [],
            "repairable_issues": [],
            "repairs": [],
        }
        with patch.object(MemoryStore, "heartbeat", return_value=heartbeat):
            with patch("semantic_backfill.dispatch_job", side_effect=ingest_second):
                result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        parents = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.root / "pending").glob("job-*.json")
        ]
        self.assertTrue(any(item.get("summary_level") == 2 for item in parents))
        self.assertTrue(result["scheduled_summary_jobs"])
        self.assertFalse(level_one_job.exists())

    def test_mw2115_recovery_002_repairs_internal_conversation_index_hole_without_marker(self):
        store = MemoryStore(self.root, load_simple_yaml(self.config))
        store.init()
        for speaker, text in (("user", "question"), ("assistant", "answer")):
            store.append_message(
                speaker, text, None, "codex:repair", None, None, False
            )
        index_path = self.root / "indexes/conversations.jsonl"
        lines = index_path.read_text(encoding="utf-8").splitlines()
        index_path.write_text(lines[0] + "\n", encoding="utf-8")

        result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        repaired = index_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(repaired), 2)
        self.assertEqual(result["native_recovery"]["status"], "ok")
        self.assertTrue(result["native_recovery"]["repairs"])
        self.assertFalse((self.root / "pending/native-recovery-debt.json").exists())

    def test_mw2115_recovery_003_integrity_mismatch_is_not_auto_repaired(self):
        store = MemoryStore(self.root, load_simple_yaml(self.config))
        store.init()
        store.append_message("user", "original", None, "codex:integrity", None, None, False)
        raw_path = next((self.root / "raw").rglob("*.md"))
        corrupted = raw_path.read_text(encoding="utf-8").replace("original", "tampered", 1)
        raw_path.write_text(corrupted, encoding="utf-8")
        before = raw_path.read_bytes()

        result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        self.assertEqual(result["status"], "attention")
        self.assertTrue(result["integrity_issues"])
        self.assertEqual(result["native_recovery"]["repairs"], [])
        self.assertEqual(raw_path.read_bytes(), before)
        self.assertIn("integrity-failure", [item["reason"] for item in result["skipped"]])

    def test_mw2123_repairable_projection_drift_does_not_block_frozen_jobs(self):
        job = self.job("job-000011.json", "conversation:test:rounds:5-6", boundary=6)
        recovery = {
            "status": "attention",
            "integrity_issues": [],
            "repairable_issues": ["conversation transcripts differ from raw records"],
            "repairs": [],
        }

        def ingest(_root, _config, job_path, **_kwargs):
            job_path.unlink()
            return {"status": "ingested", "job_id": job_path.stem}

        with patch.object(MemoryStore, "heartbeat", return_value=recovery):
            with patch("semantic_backfill.dispatch_job", side_effect=ingest) as dispatch:
                result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        dispatch.assert_called_once()
        self.assertFalse(job.exists())
        self.assertEqual(result["completed_jobs"], 1)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["scheduled_summary_jobs"], [])
        self.assertFalse(any(item["reason"] == "repair-incomplete" for item in result["skipped"]))

    def test_mw2123_recent_recovery_audit_avoids_repeating_full_rebuild(self):
        timestamp = datetime.now(timezone.utc) - timedelta(minutes=10)
        state = {
            "result": {
                "native_recovery": {
                    "status": "attention",
                    "timestamp": timestamp.isoformat(),
                    "integrity_issues": [],
                    "repairable_issues": ["conversation transcripts differ from raw records"],
                    "repairs": [{"conversations": {"changed": True}}],
                }
            }
        }
        state_path = self.root / "maintenance/supervisor-state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        cached = recent_recovery_audit(self.root)

        self.assertEqual(cached["mode"], "cached-recovery-audit")
        self.assertEqual(cached["repairs"], [])
        self.assertEqual(cached["repairable_issues"], state["result"]["native_recovery"]["repairable_issues"])

    def test_mw2123_recovery_debt_marker_forces_fresh_audit(self):
        job = self.job("job-000012.json", "conversation:test:rounds:7-8", boundary=8)
        marker = self.root / "pending/native-recovery-debt.json"
        marker.write_text('{"format_version": 1}', encoding="utf-8")
        recovery = {
            "status": "attention",
            "integrity_issues": ["raw content SHA-256 mismatch: msg-1"],
            "repairable_issues": [],
            "repairs": [],
        }

        with patch.object(MemoryStore, "heartbeat", return_value=recovery) as heartbeat:
            with patch("semantic_backfill.dispatch_job") as dispatch:
                result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        heartbeat.assert_called_once_with(create_jobs=False, repair=True)
        dispatch.assert_not_called()
        self.assertTrue(job.exists())
        self.assertTrue(marker.exists())
        self.assertEqual(result["status"], "attention")

    def test_mw2115_status_004_partial_or_permanent_debt_never_reports_completed(self):
        with patch(
            "maintenance_supervisor.run_backfill",
            return_value={
                "status": "completed",
                "completed_jobs": 1,
                "remaining_pending_jobs": 2,
                "skipped": [{"reason": "runtime-unavailable"}],
                "permanent_failures": 0,
                "reconciliation": {"invalid": []},
            },
        ):
            catching_up = run_supervisor_tick(self.root, self.config)
        self.assertEqual(catching_up["status"], "catching-up")

        with patch(
            "maintenance_supervisor.run_backfill",
            return_value={
                "status": "completed",
                "completed_jobs": 1,
                "remaining_pending_jobs": 1,
                "skipped": [{"reason": "quarantined"}],
                "permanent_failures": 1,
                "reconciliation": {"invalid": [{"path": "job.json"}]},
            },
        ):
            attention = run_supervisor_tick(self.root, self.config)
        self.assertEqual(attention["status"], "attention")


if __name__ == "__main__":
    unittest.main()
