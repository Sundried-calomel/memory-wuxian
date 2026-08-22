import hashlib
import json
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_federation import FederationManager
from memory_project_attachments import (
    CHUNK_BYTES,
    MAX_FILE_BYTES,
    ProjectAttachmentExchangeManager,
    ProjectAttachmentStore,
)
from tests.support.federation import authenticated_import


class ProjectAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config = load_simple_yaml(ROOT / "config.yaml")
        self.store_a = MemoryStore(self.base / "a", config)
        self.store_b = MemoryStore(self.base / "b", config)
        self.store_a.init(); self.store_b.init()
        FederationManager(self.store_a).init_node("A", requested_node_id="node-a")
        FederationManager(self.store_b).init_node("B", requested_node_id="node-b")
        FederationManager(self.store_a).add_peer("node-b")
        FederationManager(self.store_b).add_peer("node-a")
        self.source = self.base / "文献 ¥ project"
        self.source.mkdir()
        self.pptx = self.source / "汇报 原件.pptx"
        self.pdf = self.source / "論文.pdf"
        self.pptx.write_bytes((b"PPTX-v1\0" * 600_000)[: CHUNK_BYTES + 700_123])
        self.pdf.write_bytes((b"PDF-v1\n" * 700_000)[: 4_700_321])

    def tearDown(self):
        self.temporary.cleanup()

    def spec(self):
        return {
            "schema_version": 1,
            "project_id": "literature-alpha",
            "title": "Literature alpha",
            "source_root": str(self.source),
            "conversation_ids": ["codex:019fb8f2-9a67-7b03-9474-6f92cd6b21a7"],
            "files": [
                {"path": self.pptx.name, "role": "presentation"},
                {"path": self.pdf.name, "role": "source-paper"},
            ],
        }

    def test_large_unicode_files_build_and_reconstruct_exactly(self):
        store = ProjectAttachmentStore(self.store_a)
        preview = store.build(self.spec())
        self.assertEqual(preview["status"], "preview")
        self.assertGreater(preview["bytes"], 4 * 1024 * 1024)
        recorded = store.build(self.spec(), apply=True)
        self.assertEqual(recorded["status"], "recorded")
        destination = self.base / "restored"
        result = store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(store.status()["verified_reconstructions"], 1)
        for source in (self.pptx, self.pdf):
            restored = destination / source.name
            self.assertEqual(hashlib.sha256(restored.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest())

    def test_source_is_unchanged_and_owner_refresh_is_idempotent(self):
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.pptx, self.pdf)}
        store = ProjectAttachmentStore(self.store_a)
        self.assertEqual(store.register_owner(self.spec(), apply=True)["status"], "recorded")
        first = store.refresh_owner("literature-alpha", apply=True)
        second = store.refresh_owner("literature-alpha", apply=True)
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(second["status"], "no-change")
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.pptx, self.pdf)}
        self.assertEqual(before, after)

    def test_owner_status_uses_explicit_current_generation_not_manifest_order(self):
        store = ProjectAttachmentStore(self.store_a)
        store.register_owner(self.spec(), apply=True)
        current = store.refresh_owner("literature-alpha", apply=True)["generation_id"]
        owner = json.loads(store._owner_path("literature-alpha").read_text(encoding="utf-8"))
        self.assertEqual(owner["current_generation_id"], current)

        later = None
        for marker in range(32):
            self.pdf.write_bytes(f"direct-generation-{marker}".encode("ascii"))
            candidate = store.build(self.spec(), apply=True)["generation_id"]
            if candidate > current:
                later = candidate
                break
        self.assertIsNotNone(later)
        self.assertEqual(store.owner_status()["owners"][0]["current_generation_id"], current)

    def test_build_memory_is_bounded_by_chunk_size_not_generation_size(self):
        tracemalloc.start()
        try:
            result = ProjectAttachmentStore(self.store_a).build(self.spec(), apply=True)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result["status"], "recorded")
        self.assertLess(peak, CHUNK_BYTES * 4)

    def test_disallowed_oversized_and_symlink_sources_fail_closed(self):
        script = self.source / "run.py"
        script.write_text("print('no')", encoding="utf-8")
        spec = self.spec(); spec["files"] = [{"path": script.name, "role": "other-deliverable"}]
        with self.assertRaisesRegex(ValueError, "unsupported"):
            ProjectAttachmentStore(self.store_a).build(spec)
        with self.pptx.open("r+b") as handle:
            handle.truncate(MAX_FILE_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ProjectAttachmentStore(self.store_a).build(self.spec())

    def test_authenticated_stream_is_bounded_resumable_and_exact(self):
        recorded = ProjectAttachmentStore(self.store_a).build(self.spec(), apply=True)
        sender = ProjectAttachmentExchangeManager(self.store_a)
        receiver = ProjectAttachmentExchangeManager(self.store_b)
        cursor = 0
        predecessor = None
        bundle_count = 0
        while True:
            bundle = self.base / f"bundle-{bundle_count}.mwxb"
            exported = sender.export_delta(bundle, cursor, "node-b", predecessor)
            if exported["status"] == "no-change":
                break
            self.assertLessEqual(exported["payload_bytes"], 32 * 1024 * 1024)
            imported = authenticated_import(receiver, bundle)
            cursor = imported["last_event_sequence"]
            predecessor = imported["last_bundle_sha256"]
            bundle_count += 1
        self.assertGreater(bundle_count, 1)
        destination = self.base / "peer-restored"
        result = ProjectAttachmentStore(self.store_b).reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.pptx.read_bytes(), (destination / self.pptx.name).read_bytes())
        self.assertEqual(self.pdf.read_bytes(), (destination / self.pdf.name).read_bytes())

    def test_missing_and_corrupt_chunks_never_materialize_a_file(self):
        recorded = ProjectAttachmentStore(self.store_a).build(self.spec(), apply=True)
        store = ProjectAttachmentStore(self.store_a)
        manifest = store.manifests()[0]
        chunk = manifest["files"][0]["chunks"][0]
        object_path = store._object_path(chunk["sha256"])
        original = object_path.read_bytes()
        object_path.unlink()
        destination = self.base / "missing"
        result = store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(store.status()["verified_reconstructions"], 0)
        self.assertFalse(destination.exists())
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(original + b"corrupt")
        with self.assertRaisesRegex(ValueError, "integrity"):
            store.reconstruct(recorded["generation_id"], self.base / "corrupt", apply=True)
        self.assertFalse((self.base / "corrupt").exists())

    def test_interrupted_reconstruction_has_no_receipt_and_retries_exactly(self):
        recorded = ProjectAttachmentStore(self.store_a).build(self.spec(), apply=True)
        store = ProjectAttachmentStore(self.store_a)
        destination = self.base / "interrupted"
        from memory_project_attachments import atomic_write_bytes as real_atomic_write_bytes

        writes = 0

        def interrupt_after_first(path, content):
            nonlocal writes
            writes += 1
            real_atomic_write_bytes(path, content)
            if writes == 1:
                raise RuntimeError("simulated reconstruction interruption")

        with patch("memory_project_attachments.atomic_write_bytes", side_effect=interrupt_after_first):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(store.status()["verified_reconstructions"], 0)

        retried = store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(store.status()["verified_reconstructions"], 1)
        self.assertEqual(self.pptx.read_bytes(), (destination / self.pptx.name).read_bytes())
        self.assertEqual(self.pdf.read_bytes(), (destination / self.pdf.name).read_bytes())

    def test_reconstruction_receipt_is_immutable_and_conflicts_fail_before_writes(self):
        recorded = ProjectAttachmentStore(self.store_a).build(self.spec(), apply=True)
        store = ProjectAttachmentStore(self.store_a)
        destination = self.base / "first-restored"
        store.reconstruct(recorded["generation_id"], destination, apply=True)
        digest = recorded["generation_id"].split(":", 1)[1]
        receipt_path = store.reconstruction_root / f"{digest}.json"
        before = receipt_path.read_bytes()
        before_mtime = receipt_path.stat().st_mtime_ns
        time.sleep(0.01)

        store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(receipt_path.read_bytes(), before)
        self.assertEqual(receipt_path.stat().st_mtime_ns, before_mtime)

        corrupt = json.loads(before)
        corrupt["files"][0]["sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(corrupt), encoding="utf-8")
        rejected_destination = self.base / "receipt-conflict"
        with self.assertRaisesRegex(ValueError, "receipt conflicts"):
            store.reconstruct(recorded["generation_id"], rejected_destination, apply=True)
        self.assertFalse(rejected_destination.exists())


if __name__ == "__main__":
    unittest.main()
