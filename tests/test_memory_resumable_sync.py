from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_content_store import ContentStore
from memory_resumable_sync import ResumableTransfer
from platform_transaction import atomic_write_canonical_json


class ResumableSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source_files = self.base / "source-files"
        self.source_files.mkdir()
        for index in range(4):
            (self.source_files / f"{index}.bin").write_bytes(bytes([index]) * (index + 1))
        self.source = ContentStore(self.base / "source-archive")
        self.target = ContentStore(self.base / "target-archive")
        self.manifest = self.source.build_manifest(
            self.source_files, "node-a:archive-snapshot", [f"{index}.bin" for index in range(4)]
        )
        self.transfer = ResumableTransfer(self.source, self.target, "archive", "node-b")

    def test_mw28_resume_001_interrupt_restart_and_complete_exactly(self):
        first = self.transfer.transfer(self.manifest["manifest_id"], start=0, count=2)
        self.assertEqual(first["status"], "receiving")
        restarted = ResumableTransfer(self.source, self.target, "archive", "node-b")
        checkpoint = restarted.checkpoint(self.manifest["manifest_id"])
        self.assertEqual(checkpoint["next_index"], 2)
        final = restarted.transfer(self.manifest["manifest_id"], start=2, count=2)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(self.target.verify(self.manifest["manifest_id"])["status"], "verified")
        restored = self.base / "restored"
        self.target.reconstruct(self.manifest["manifest_id"], restored, apply=True)
        for index in range(4):
            self.assertEqual(
                (restored / f"{index}.bin").read_bytes(),
                (self.source_files / f"{index}.bin").read_bytes(),
            )

    def test_mw28_replay_001_duplicate_is_idempotent_overlap_and_gap_fail(self):
        self.transfer.transfer(self.manifest["manifest_id"], start=0, count=2)
        replay = self.transfer.transfer(self.manifest["manifest_id"], start=0, count=2)
        self.assertEqual(replay["status"], "duplicate-replay")
        self.assertEqual(replay["transferred"], 0)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.transfer.transfer(self.manifest["manifest_id"], start=1, count=2)
        with self.assertRaisesRegex(ValueError, "missing segment"):
            self.transfer.transfer(self.manifest["manifest_id"], start=3, count=1)

    def test_mw28_corrupt_001_rejects_segment_without_advancing_checkpoint(self):
        entry = self.manifest["entries"][0]
        self.source.object_path(entry["sha256"]).write_bytes(b"corrupt")
        before = self.transfer.checkpoint(self.manifest["manifest_id"])
        with self.assertRaisesRegex(ValueError, "corrupt segment"):
            self.transfer.transfer(self.manifest["manifest_id"], start=0, count=1)
        after = self.transfer.checkpoint(self.manifest["manifest_id"])
        self.assertEqual(before, after)

    def test_mw28_isolation_001_archive_failure_does_not_change_environment_stream(self):
        environment_target = ContentStore(self.base / "environment-target")
        environment = ResumableTransfer(self.source, environment_target, "environment", "node-b")
        environment.transfer(self.manifest["manifest_id"], start=0, count=1)
        environment_before = environment.checkpoint(self.manifest["manifest_id"])
        with self.assertRaises(ValueError):
            self.transfer.transfer(self.manifest["manifest_id"], start=2, count=1)
        self.assertEqual(
            environment.checkpoint(self.manifest["manifest_id"]), environment_before
        )

    def test_mw28_checkpoint_001_tampering_fails_closed(self):
        result = self.transfer.transfer(self.manifest["manifest_id"], start=0, count=1)
        checkpoint_path = self.transfer._checkpoint_path(result["checkpoint"]["stream_id"])
        tampered = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        tampered["next_index"] = 3
        atomic_write_canonical_json(checkpoint_path, tampered)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.transfer.checkpoint(self.manifest["manifest_id"])

        tampered["next_index"] = 1
        tampered["accepted_sha256"] = ["0" * 64]
        atomic_write_canonical_json(checkpoint_path, tampered)
        with self.assertRaisesRegex(ValueError, "manifest prefix"):
            self.transfer.checkpoint(self.manifest["manifest_id"])


if __name__ == "__main__":
    unittest.main()
