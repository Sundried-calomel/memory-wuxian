from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_jobs import MaintenanceQueue
from semantic_dispatch import codex_runtime_available, dispatch_job


class SemanticDispatchTests(unittest.TestCase):
    def test_mw211_runtime_probe_executes_expanded_home_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            executable = home / ".codex" / ".sandbox-bin" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"candidate")
            config = home / "config.yaml"
            config.write_text(
                'ai_summary:\n  codex_cli_path_windows: "~/.codex/.sandbox-bin/codex.exe"\n'
                '  codex_cli_path: "~/.codex/.sandbox-bin/codex.exe"\n',
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="Logged in")
            with patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                with patch("semantic_dispatch.shutil.which", return_value=None):
                    with patch("semantic_dispatch.subprocess.run", return_value=completed) as run:
                        available, reason = codex_runtime_available(config)
            self.assertTrue(available)
            self.assertEqual(reason, "available")
            self.assertEqual(Path(run.call_args.args[0][0]), executable)

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

    def test_mw2115_dispatch_003_terminal_failure_returns_quarantine_without_rethrow(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "archive"
            pending = archive / "pending"
            pending.mkdir(parents=True)
            job_path = pending / "job-000003.json"
            job_path.write_text(
                json.dumps({
                    "job_id": "job-000003",
                    "summary_level": 1,
                    "source_signature": "conversation:test:rounds:3-4",
                    "conversation_id": "codex:test",
                    "source_round_end": 4,
                }),
                encoding="utf-8",
            )
            config = base / "config.yaml"
            config.write_text(
                f'ai_summary:\n  worker_path: "{(ROOT / "scripts/semantic_worker.py").as_posix()}"\n',
                encoding="utf-8",
            )
            with patch("semantic_dispatch.run_job", side_effect=RuntimeError("permanent failure")):
                for _ in range(3):
                    with self.assertRaises(RuntimeError):
                        dispatch_job(archive, config, job_path, retry_delay_seconds=0)
                terminal = dispatch_job(archive, config, job_path, retry_delay_seconds=0)
            self.assertEqual(terminal["status"], "quarantined")
            self.assertIn("permanent failure", terminal["error"])
            self.assertEqual(MaintenanceQueue(archive).status()["quarantined"], 1)


if __name__ == "__main__":
    unittest.main()
