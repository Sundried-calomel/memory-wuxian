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


if __name__ == "__main__":
    unittest.main()
