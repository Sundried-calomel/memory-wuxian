import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import memory_indexing
from memory_cli import MemoryStore
from platform_transaction import read_index_generation, validate_index_generation


class IndexOwnershipExtractionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.fixture_root = self.base / "fixture"
        self.config = {
            "summaries": {
                "level_1_trigger_rounds": 2,
                "level_1_trigger_characters": 20_000,
                "automatic_semantic_jobs": True,
                "higher_level_trigger_count": 10,
                "maximum_summary_depth": 4,
            },
            "safety": {"redact_secrets": True},
        }
        self._build_existing_archive_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def _store(self, root: Path) -> MemoryStore:
        return MemoryStore(root, self.config)

    def _append(self, store: MemoryStore, speaker: str, text: str, suffix: str) -> None:
        store.append_message(
            speaker=speaker,
            text=text,
            timestamp=f"2026-07-31T12:00:{suffix}+09:00",
            conversation_id="codex:index-parity",
            message_id=f"index-parity-{suffix}-{speaker}",
            reply_to=None,
            allow_secrets=False,
        )

    def _build_existing_archive_fixture(self) -> None:
        store = self._store(self.fixture_root)
        store.init()
        self._append(store, "user", "first index question", "01")
        self._append(store, "assistant", "first index answer", "02")
        self._append(store, "user", "second index question", "03")
        self._append(store, "assistant", "second index answer", "04")
        job_path = store.make_summary_job()
        self.assertIsNotNone(job_path)
        summary_path = self.base / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "topics": ["index ownership"],
                    "established_conclusions": ["persisted bytes remain unchanged"],
                    "open_questions": [],
                    "concepts": ["Memory Plane"],
                    "policy_events": [],
                }
            ),
            encoding="utf-8",
        )
        store.ingest_summary(job_path, summary_path)

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def _derived_snapshot(root: Path) -> dict[str, bytes]:
        paths = list((root / "indexes").rglob("*"))
        paths.append(root / "summaries" / "registry.jsonl")
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(paths)
            if path.is_file()
        }

    @staticmethod
    def _authoritative_snapshot(root: Path) -> dict[str, bytes]:
        paths = list((root / "raw").rglob("*"))
        paths.extend((root / "conversations").rglob("*"))
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(paths)
            if path.is_file()
        }

    @staticmethod
    def _without_backup(result: dict) -> dict:
        return {key: value for key, value in result.items() if key != "backup"}

    def test_preview_matches_memory_plane_and_does_not_write(self):
        module_root = self.base / "module-preview"
        public_root = self.base / "public-preview"
        shutil.copytree(self.fixture_root, module_root)
        shutil.copytree(self.fixture_root, public_root)
        module_store = self._store(module_root)
        public_store = self._store(public_root)
        module_before = self._snapshot(module_root)
        public_before = self._snapshot(public_root)

        module_result = memory_indexing.rebuild_indexes(module_store, False)
        public_result = public_store.rebuild_indexes(False)

        self.assertEqual(public_result, module_result)
        self.assertEqual(self._snapshot(module_root), module_before)
        self.assertEqual(self._snapshot(public_root), public_before)
        self.assertEqual(public_result["mode"], "preview")
        self.assertFalse(public_result["changed"])

    def test_apply_matches_memory_plane_bytes_and_result_contract(self):
        module_root = self.base / "module-apply"
        public_root = self.base / "public-apply"
        shutil.copytree(self.fixture_root, module_root)
        shutil.copytree(self.fixture_root, public_root)
        module_store = self._store(module_root)
        public_store = self._store(public_root)
        expected_raw = self._authoritative_snapshot(self.fixture_root)

        module_result = memory_indexing.rebuild_indexes(module_store, True)
        public_result = public_store.rebuild_indexes(True)

        self.assertEqual(
            self._without_backup(public_result),
            self._without_backup(module_result),
        )
        self.assertTrue(Path(public_result["backup"]).is_dir())
        self.assertTrue(Path(module_result["backup"]).is_dir())
        self.assertEqual(
            self._derived_snapshot(public_root),
            self._derived_snapshot(module_root),
        )
        self.assertEqual(self._authoritative_snapshot(public_root), expected_raw)
        self.assertEqual(public_result["mode"], "apply")
        self.assertTrue(public_result["changed"])

    def test_public_memory_store_method_delegates_unchanged(self):
        store = self._store(self.fixture_root)
        expected = {"mode": "preview", "compatible": True}
        with patch("memory_cli.memory_indexing.rebuild_indexes", return_value=expected) as call:
            self.assertIs(store.rebuild_indexes(False), expected)
        call.assert_called_once_with(store, False)

    def test_mw26_gen_001_deterministic_immutable_shadow_generation(self):
        store = self._store(self.fixture_root)
        authoritative_before = self._authoritative_snapshot(self.fixture_root)
        active_indexes_before = self._derived_snapshot(self.fixture_root)

        first = memory_indexing.build_shadow_generation(store)
        generation_root = (
            store.index_dir / "generations" / first["generation_id"]
        )
        generation_before = self._snapshot(generation_root)
        manifest = read_index_generation(generation_root / "manifest.json")
        payload_root = generation_root / "payload"
        payload_bytes = self._snapshot(payload_root)
        legacy_root = self.base / "legacy-byte-parity"
        shutil.copytree(self.fixture_root, legacy_root)
        legacy_store = self._store(legacy_root)
        memory_indexing.rebuild_indexes(legacy_store, True)
        legacy_bytes = {
            relative: (legacy_root / relative).read_bytes()
            for relative in payload_bytes
        }
        second = memory_indexing.build_shadow_generation(store)

        self.assertIs(manifest, validate_index_generation(manifest))
        schema = json.loads(
            (SKILL_ROOT / "schemas" / "index-generation.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(schema["required"]), set(manifest))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["format"]["const"], manifest["format"])
        self.assertEqual(
            schema["properties"]["format_version"]["const"],
            manifest["format_version"],
        )
        self.assertEqual(
            set(schema["properties"]["source_manifest"]["required"]),
            set(manifest["source_manifest"]),
        )
        self.assertTrue(
            all(
                set(item) == set(schema["$defs"]["exactFile"]["required"])
                for item in manifest["source_manifest"]["entries"]
                + manifest["files"]
            )
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["generation_id"], second["generation_id"])
        self.assertEqual(payload_bytes, legacy_bytes)
        self.assertEqual(self._snapshot(generation_root), generation_before)
        self.assertEqual(
            {
                key: value
                for key, value in self._derived_snapshot(self.fixture_root).items()
                if not key.replace("\\", "/").startswith("indexes/generations/")
            },
            active_indexes_before,
        )
        self.assertEqual(
            self._authoritative_snapshot(self.fixture_root), authoritative_before
        )

    def test_mw26_gen_002_rejects_source_hash_drift(self):
        store = self._store(self.fixture_root)
        generation = memory_indexing.build_shadow_generation(store)
        raw_path = next(store.raw_dir.rglob("*.md"))
        raw_bytes = raw_path.read_bytes()
        self.assertEqual(raw_bytes[-1:], b"\n")
        raw_path.write_bytes(raw_bytes[:-1] + b" ")
        pointer = store.index_dir / "active-generation.json"

        with self.assertRaisesRegex(RuntimeError, "source SHA-256 mismatch"):
            memory_indexing.activate_generation(store, generation["generation_id"])

        self.assertFalse(pointer.exists())

    def test_mw26_switch_001_atomic_activation_rejects_tampering(self):
        store = self._store(self.fixture_root)
        first = memory_indexing.build_shadow_generation(store)
        memory_indexing.activate_generation(store, first["generation_id"])
        self._append(store, "user", "switch index question", "05")
        self._append(store, "assistant", "switch index answer", "06")
        second = memory_indexing.build_shadow_generation(store)
        authoritative_before = self._authoritative_snapshot(self.fixture_root)
        pointer_path = store.index_dir / "active-generation.json"
        pointer_before = pointer_path.read_bytes()

        original_atomic_write = memory_indexing.atomic_write_canonical_json

        def interrupted_write(path, value):
            def interrupt(_temporary, _target):
                raise RuntimeError("MW26-SWITCH-001 injected interruption")

            return original_atomic_write(path, value, before_replace=interrupt)

        with patch(
            "memory_indexing.atomic_write_canonical_json",
            side_effect=interrupted_write,
        ):
            with self.assertRaisesRegex(RuntimeError, "MW26-SWITCH-001"):
                memory_indexing.activate_generation(store, second["generation_id"])

        self.assertEqual(pointer_path.read_bytes(), pointer_before)
        activated = memory_indexing.activate_generation(store, second["generation_id"])
        self.assertEqual(activated["active_generation_id"], second["generation_id"])
        self.assertEqual(activated["previous_generation_id"], first["generation_id"])
        payload = next(
            path
            for path in (
                store.index_dir / "generations" / second["generation_id"] / "payload"
            ).rglob("*")
            if path.is_file()
        )
        payload.write_bytes(payload.read_bytes() + b"tampered")

        with self.assertRaisesRegex(RuntimeError, "payload"):
            memory_indexing.inspect_generation_status(store, second["generation_id"])

        self.assertEqual(
            self._authoritative_snapshot(self.fixture_root), authoritative_before
        )

    def test_mw26_rollback_001_pointer_only_preserves_generations_and_raw(self):
        store = self._store(self.fixture_root)
        first = memory_indexing.build_shadow_generation(store)
        memory_indexing.activate_generation(store, first["generation_id"])
        first_root = store.index_dir / "generations" / first["generation_id"]
        first_before = self._snapshot(first_root)

        self._append(store, "user", "third index question", "05")
        self._append(store, "assistant", "third index answer", "06")
        second = memory_indexing.build_shadow_generation(store)
        memory_indexing.activate_generation(store, second["generation_id"])
        second_root = store.index_dir / "generations" / second["generation_id"]
        second_before = self._snapshot(second_root)
        authoritative_before = self._authoritative_snapshot(self.fixture_root)

        with patch(
            "memory_indexing.compute_source_manifest",
            side_effect=AssertionError("rollback must not process sources"),
        ):
            result = memory_indexing.rollback_generation(store)

        self.assertEqual(result["active_generation_id"], first["generation_id"])
        self.assertEqual(result["previous_generation_id"], second["generation_id"])
        self.assertEqual(self._snapshot(first_root), first_before)
        self.assertEqual(self._snapshot(second_root), second_before)
        self.assertEqual(
            self._authoritative_snapshot(self.fixture_root), authoritative_before
        )


if __name__ == "__main__":
    unittest.main()
