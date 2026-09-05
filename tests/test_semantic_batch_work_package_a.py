from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, raw_record_sha256, raw_source_sha256
from semantic_backfill import run_backfill
from semantic_dispatch import dispatch_job
from semantic_worker import run_job


def source_record(sequence: int, round_number: int, text: str) -> dict:
    record = {
        "sequence": sequence,
        "message_id": f"message-{sequence}",
        "conversation_id": "codex:test",
        "round_number": round_number,
        "speaker": "user" if sequence % 2 else "assistant",
        "text": text,
        "timestamp": f"2026-08-01T00:00:{sequence:02d}+00:00",
        "source": {"kind": "codex", "line": sequence},
    }
    record["content_sha256"] = raw_record_sha256(record)
    return record


def summary_job(records: list[dict]) -> dict:
    source_sha256 = raw_source_sha256(records)
    return {
        "job_id": "job-000001",
        "target_summary_id": "L1-000001",
        "summary_level": 1,
        "conversation_id": "codex:test",
        "source_signature": "conversation:codex:test:rounds:1-1",
        "source_start": records[0]["message_id"],
        "source_end": records[-1]["message_id"],
        "source_start_sequence": records[0]["sequence"],
        "source_end_sequence": records[-1]["sequence"],
        "source_round_start": 1,
        "source_round_end": 1,
        "source_round_numbers": [1],
        "source_round_count": 1,
        "source_message_ids": [item["message_id"] for item in records],
        "source_records": records,
        "source_files": [],
        "source_sha256": source_sha256,
        "start_time": records[0]["timestamp"],
        "end_time": records[-1]["timestamp"],
        "required_result_keys": [
            "topics",
            "established_conclusions",
            "open_questions",
            "concepts",
            "policy_events",
        ],
    }


def empty_summary() -> dict:
    return {
        "topics": [],
        "established_conclusions": [],
        "open_questions": [],
        "concepts": [],
        "policy_events": [],
    }


class SemanticBatchWorkPackageATests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "archive"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            "summaries:\n"
            "  level_1_trigger_rounds: 2\n"
            "  level_1_trigger_characters: 20000\n"
            "  higher_level_trigger_count: 10\n"
            "ai_summary:\n"
            "  maximum_parallel_model_calls: 3\n"
            "backup:\n"
            "  enabled: false\n",
            encoding="utf-8",
        )

    def test_worker_forwards_snapshot_and_defers_derived_updates(self) -> None:
        store = MemoryStore(self.root, {})
        store.init()
        records = [source_record(1, 1, "question"), source_record(2, 1, "answer")]
        job = summary_job(records)
        job_path = store.pending_dir / "job-000001.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        snapshot = {"raw_by_id": {item["message_id"]: item for item in records}}
        captured = {}

        def ingest(_store, _job_path, _result_path, **kwargs):
            captured.update(kwargs)
            return self.root / "summaries/level-1/L1-000001.md"

        with patch.object(MemoryStore, "ingest_summary", autospec=True, side_effect=ingest):
            result = run_job(
                self.root,
                self.config,
                job_path,
                False,
                create_backup=False,
                invoker=lambda *_args: empty_summary(),
                source_snapshot=snapshot,
                defer_derived_updates=True,
            )

        self.assertIs(captured["source_snapshot"], snapshot)
        self.assertIs(captured["defer_derived_updates"], True)
        self.assertIs(result["derived_updates_deferred"], True)

    def test_dispatch_forwards_batch_controls_to_bundled_worker(self) -> None:
        pending = self.root / "pending"
        pending.mkdir(parents=True)
        job_path = pending / "job-000001.json"
        job_path.write_text(
            json.dumps(
                {
                    "job_id": "job-000001",
                    "summary_level": 1,
                    "source_signature": "conversation:codex:test:rounds:1-1",
                    "conversation_id": "codex:test",
                    "source_round_end": 1,
                }
            ),
            encoding="utf-8",
        )
        self.config.write_text(
            f'ai_summary:\n  worker_path: "{(ROOT / "scripts/semantic_worker.py").as_posix()}"\n',
            encoding="utf-8",
        )
        snapshot = {"snapshot": "batch"}
        with patch(
            "semantic_dispatch.run_job",
            return_value={"status": "ingested", "job_id": "job-000001"},
        ) as worker:
            dispatch_job(
                self.root,
                self.config,
                job_path,
                source_snapshot=snapshot,
                defer_derived_updates=True,
            )

        self.assertIs(worker.call_args.kwargs["source_snapshot"], snapshot)
        self.assertIs(worker.call_args.kwargs["defer_derived_updates"], True)

    def test_backfill_builds_one_snapshot_finalizes_once_and_keeps_siblings(self) -> None:
        store = MemoryStore(self.root, {})
        store.init()
        jobs = []
        for number in range(1, 4):
            path = store.pending_dir / f"job-{number:06d}.json"
            path.write_text(
                json.dumps(
                    {
                        "job_id": path.stem,
                        "summary_level": 1,
                        "source_signature": f"conversation:codex:test:rounds:{number}-{number}",
                        "conversation_id": "codex:test",
                        "created_at": f"2026-08-01T00:00:0{number}Z",
                        "source_round_end": number,
                    }
                ),
                encoding="utf-8",
            )
            jobs.append(path)

        snapshot = {"snapshot": "shared"}
        dispatched_snapshots = []

        def dispatch(_root, _config, job_path, **kwargs):
            dispatched_snapshots.append(kwargs["source_snapshot"])
            if job_path == jobs[1]:
                raise RuntimeError("isolated failure")
            return {
                "status": "ingested",
                "job_id": job_path.stem,
                "summary": str(self.root / f"summaries/{job_path.stem}.md"),
                "derived_updates_deferred": True,
            }

        recovery = {
            "status": "ok",
            "integrity_issues": [],
            "repairable_issues": [],
            "repairs": [],
        }
        with (
            patch.object(MemoryStore, "heartbeat", return_value=recovery),
            patch.object(
                MemoryStore,
                "build_summary_source_snapshot",
                return_value=snapshot,
            ) as build_snapshot,
            patch.object(
                MemoryStore,
                "finalize_summary_batch",
                return_value={"status": "completed", "summaries": 2},
            ) as finalize,
            patch.object(MemoryStore, "make_summary_job", return_value=None),
            patch("semantic_backfill.dispatch_job", side_effect=dispatch),
        ):
            result = run_backfill(self.root, self.config, max_jobs=3, dry_run=False)

        build_snapshot.assert_called_once_with()
        self.assertEqual(dispatched_snapshots, [snapshot, snapshot, snapshot])
        finalized = finalize.call_args.args[0]
        self.assertEqual([item["job_id"] for item in finalized], [jobs[0].stem, jobs[2].stem])
        self.assertEqual(result["completed_jobs"], 2)
        self.assertEqual(
            [item["reason"] for item in result["skipped"]].count("dispatch-failed"),
            1,
        )
        self.assertTrue(result["source_snapshot_built"])
        self.assertIn("snapshot_seconds", result["timing"])
        self.assertIn("finalize_seconds", result["timing"])
        state = json.loads(
            (self.root / "maintenance/semantic-batch-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["stage"], "completed")
        self.assertEqual(state["finished_jobs"], 2)
        self.assertEqual(state["failed_jobs"], 1)


if __name__ == "__main__":
    unittest.main()
