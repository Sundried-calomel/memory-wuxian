from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_jobs import MaintenanceQueue
from semantic_dispatch import dispatch_job


class SemanticDispatchTests(unittest.TestCase):
    def test_mw27_dispatch_001_persists_eligibility_before_one_shot_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "archive"
            pending = archive / "pending"
            pending.mkdir(parents=True)
            job_path = pending / "job-000001.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_id": "job-000001",
                        "summary_level": 1,
                        "source_signature": "conversation:test:rounds:1-2",
                        "conversation_id": "codex:test",
                        "source_round_end": 2,
                    }
                ),
                encoding="utf-8",
            )
            config = base / "config.yaml"
            config.write_text(
                f'ai_summary:\n  worker_path: "{(ROOT / "scripts/semantic_worker.py").as_posix()}"\n',
                encoding="utf-8",
            )
            with patch(
                "semantic_dispatch.run_job",
                return_value={"status": "ingested", "job_id": "job-000001"},
            ) as worker:
                result = dispatch_job(archive, config, job_path)
            worker.assert_called_once()
            self.assertEqual(result["ai_invocations"], 1)
            queue = MaintenanceQueue(archive)
            jobs = queue.jobs()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["state"], "completed")
            self.assertEqual(jobs[0]["payload"]["completed_round"], 2)

    def test_mw27_dispatch_002_incomplete_boundary_fails_before_queue_or_ai(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "archive"
            pending = archive / "pending"
            pending.mkdir(parents=True)
            job_path = pending / "job-000001.json"
            job_path.write_text(
                json.dumps(
                    {
                        "job_id": "job-000001",
                        "summary_level": 1,
                        "source_signature": "conversation:test:incomplete",
                        "conversation_id": "codex:test",
                        "source_round_end": 0,
                    }
                ),
                encoding="utf-8",
            )
            config = base / "config.yaml"
            config.write_text("ai_summary:\n  worker_path: scripts/semantic_worker.py\n", encoding="utf-8")
            with patch("semantic_dispatch.run_job") as worker:
                with self.assertRaises(ValueError):
                    dispatch_job(archive, config, job_path)
            worker.assert_not_called()
            self.assertEqual(MaintenanceQueue(archive).jobs(), [])


if __name__ == "__main__":
    unittest.main()
