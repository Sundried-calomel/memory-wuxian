import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from scripts import install_macos_transaction as transaction


class BoundedReadinessTest(unittest.TestCase):
    def test_exact_probe_can_pass_with_history_debt_but_not_a_wrong_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sessions = root / "sessions"
            sessions.mkdir()
            probe = transaction.create_installed_effect_probe(sessions)
            imports = root / "imports" / "codex"
            imports.mkdir(parents=True)
            telemetry = {
                "format_version": 2, "ready": True, "pid": os.getpid(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source_watermark": probe["watermark"], "archive_watermark": None,
            }
            (imports / "collector-telemetry.json").write_text(json.dumps(telemetry))
            payload = Path(probe["path"]).read_bytes()
            cursor = {
                "session_id": probe["probe_id"], "source_path": probe["path"],
                "source_byte_sha256": probe["payload_sha256"], "complete": True,
                "committed_byte_offset": len(payload), "observed_source_size": len(payload),
                "updated_at": probe["watermark"],
            }
            path = imports / f"{probe['probe_id']}.json"
            for field, bad in (("source_byte_sha256", "bad"), ("complete", False),
                               ("committed_byte_offset", 0), ("source_path", "other")):
                path.write_text(json.dumps({**cursor, field: bad}))
                with self.assertRaises(RuntimeError):
                    transaction.verify_effect_probe_cursor(root, probe)
            path.write_text(json.dumps(cursor))
            # Process liveness is an OS boundary, independent of cursor validation.
            with patch.object(transaction.os, "kill") as check_alive:
                result = transaction.wait_for_collector(
                    root, previous_pid=None, effect_probe=probe, timeout_seconds=1,
                )
            check_alive.assert_called_once_with(os.getpid(), 0)
            self.assertIsNone(result["archive_watermark"])
            self.assertTrue(result["ready"])
