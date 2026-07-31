from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_content_store import ContentStore
from platform_transaction import atomic_write_canonical_json


def tree_hash(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ContentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        (self.source / "nested").mkdir(parents=True)
        (self.source / "a.txt").write_bytes(b"alpha\r\n")
        (self.source / "nested/b.bin").write_bytes(bytes(range(256)))
        self.before = tree_hash(self.source)
        self.store = ContentStore(self.base / "archive")

    def test_mw28_exact_001_manifest_is_ordered_stable_and_reconstructs_bytes(self):
        first = self.store.build_manifest(
            self.source, "fixture:exact", ["nested/b.bin", "a.txt"]
        )
        second = self.store.build_manifest(
            self.source, "fixture:exact", ["a.txt", "nested/b.bin"]
        )
        self.assertEqual(first, second)
        self.assertEqual([item["path"] for item in first["entries"]], ["a.txt", "nested/b.bin"])
        self.assertEqual(self.store.verify(first["manifest_id"], self.source)["status"], "verified")
        destination = self.base / "restored"
        preview = self.store.reconstruct(first["manifest_id"], destination)
        self.assertFalse(preview["applied"])
        self.assertFalse(destination.exists())
        result = self.store.reconstruct(first["manifest_id"], destination, apply=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(tree_hash(destination), self.before)
        self.assertEqual(tree_hash(self.source), self.before)

    def test_mw28_drift_001_source_and_object_corruption_fail_closed(self):
        manifest = self.store.build_manifest(self.source, "fixture:drift", ["a.txt"])
        (self.source / "a.txt").write_bytes(b"changed")
        verification = self.store.verify(manifest["manifest_id"], self.source)
        self.assertIn("source-drift", {item["reason"] for item in verification["issues"]})
        entry = manifest["entries"][0]
        self.store.object_path(entry["sha256"]).write_bytes(b"corrupt")
        verification = self.store.verify(manifest["manifest_id"])
        self.assertIn("corrupt-object", {item["reason"] for item in verification["issues"]})
        with self.assertRaises(ValueError):
            self.store.reconstruct(manifest["manifest_id"], self.base / "blocked", apply=True)

    def test_mw28_conflict_001_reconstruction_explains_without_guessing(self):
        manifest = self.store.build_manifest(self.source, "fixture:conflict", ["a.txt"])
        destination = self.base / "destination"
        destination.mkdir()
        (destination / "a.txt").write_bytes(b"different")
        result = self.store.reconstruct(manifest["manifest_id"], destination, apply=True)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicts"][0]["source_id"], "fixture:conflict")
        self.assertEqual((destination / "a.txt").read_bytes(), b"different")

    def test_mw28_rollback_001_shadow_disable_and_removal_leave_source_intact(self):
        self.store.build_manifest(self.source, "fixture:rollback", ["a.txt", "nested/b.bin"])
        preview = self.store.disable()
        self.assertFalse(preview["applied"])
        self.assertTrue(self.store.status()["enabled"])
        self.store.disable(apply=True)
        self.assertFalse(self.store.status()["enabled"])
        shutil.rmtree(self.store.root)
        self.assertEqual(tree_hash(self.source), self.before)
        self.assertEqual((self.source / "a.txt").read_bytes(), b"alpha\r\n")

    def test_mw28_path_001_rejects_escape_symlink_and_unknown_manifest_fields(self):
        with self.assertRaises(ValueError):
            self.store.build_manifest(self.source, "fixture:path", ["../outside"])
        manifest = self.store.build_manifest(self.source, "fixture:path", ["a.txt"])
        path = self.store.manifest_path(manifest["manifest_id"])
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["unknown"] = True
        atomic_write_canonical_json(path, tampered)
        with self.assertRaises(ValueError):
            self.store.load_manifest(manifest["manifest_id"])

        tampered.pop("unknown")
        tampered["manifest_id"] = "manifest-" + "0" * 64
        atomic_write_canonical_json(path, tampered)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.store.load_manifest(manifest["manifest_id"])


if __name__ == "__main__":
    unittest.main()
