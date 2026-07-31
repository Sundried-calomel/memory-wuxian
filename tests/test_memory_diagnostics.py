from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_diagnostics import create_diagnostic_bundle
from memory_jobs import MaintenanceQueue


class DiagnosticBundleTests(unittest.TestCase):
    def test_mw27_diag_redaction_001_excludes_dialogue_secrets_and_local_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "archive"
            raw = root / "raw/2026/08/immutable.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(b"authoritative raw bytes\n")
            before = raw.read_bytes()
            queue = MaintenanceQueue(root)
            job = queue.enqueue("archive-health", "diag:1", max_attempts=1)
            claimed = queue.claim("worker")
            queue.fail(
                job["job_id"],
                "worker",
                "token=abc123 C:\\Users\\Alice\\private.txt /Users/alice/private.txt",
            )
            result = create_diagnostic_bundle(
                root,
                {
                    "status": "attention",
                    "password": "do-not-copy",
                    "text": "raw conversation must not appear",
                },
                queue.jobs(),
            )
            data = Path(result["path"]).read_text(encoding="utf-8")
            self.assertNotIn("abc123", data)
            self.assertNotIn("do-not-copy", data)
            self.assertNotIn("raw conversation must not appear", data)
            self.assertNotIn("Alice", data)
            self.assertNotIn("/Users/alice", data)
            self.assertIn("[REDACTED]", data)
            self.assertIn("[LOCAL_PATH]", data)
            self.assertFalse(result["contains_raw_dialogue"])
            self.assertEqual(raw.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
