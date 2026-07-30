import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_guarded_features import GuardedFeatures, archive_manifest


class GuardedFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config_path = self.base / "config.yaml"
        config_path.write_text(
            "memory:\n  root_directory: ./memory\n"
            "summaries:\n  maximum_summary_depth: 4\n"
            "safety:\n  redact_secrets: true\n",
            encoding="utf-8",
        )
        self.store = MemoryStore(self.base / "memory", load_simple_yaml(config_path))
        self.store.init()
        self.store.append_message(
            "user", "迁移与语义索引红线", "2026-07-28T10:00:00+09:00",
            "project-a", "m1", None, False,
        )
        self.store.append_message(
            "assistant", "必须保留原始归档", "2026-07-28T10:01:00+09:00",
            "project-a", "m2", "m1", False,
        )
        self.features = GuardedFeatures(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def test_migration_is_verified_copy_and_preserves_source(self):
        before = archive_manifest(self.store.root)
        destination = self.base / "migrated"
        result = self.features.migration_apply(destination, False)
        self.assertTrue(result["source_preserved"])
        self.assertFalse(result["source_deleted"])
        self.assertEqual(before["manifest_sha256"], archive_manifest(self.store.root)["manifest_sha256"])
        self.assertEqual(before["manifest_sha256"], archive_manifest(destination)["manifest_sha256"])

    def test_project_package_import_never_enters_local_raw(self):
        before = archive_manifest(self.store.root)
        package = self.base / "project.mwx-project"
        self.features.project_export(package, ["project-a"])
        result = self.features.project_import(package)
        self.assertEqual("imported-read-only-replica", result["status"])
        self.assertEqual(before["manifest_sha256"], archive_manifest(self.store.root)["manifest_sha256"])

    def test_time_travel_is_read_only(self):
        before = archive_manifest(self.store.root)
        result = self.features.as_of("2026-07-28T10:00:30+09:00", "project-a")
        self.assertEqual(1, result["raw_record_count"])
        self.assertTrue(result["read_only"])
        self.assertEqual(before["manifest_sha256"], archive_manifest(self.store.root)["manifest_sha256"])

    def test_semantic_index_is_disposable_and_backlinked(self):
        raw_before = {
            item["path"]: item["sha256"]
            for item in archive_manifest(self.store.root)["files"]
            if item["authoritative"]
        }
        self.features.semantic_build("local-hash-v1")
        result = self.features.semantic_retrieve("语义 索引", 2)
        self.assertTrue(result["verified_against_raw"])
        self.assertTrue(all(item["raw_path"] and item["record_sha256"] for item in result["matches"]))
        self.assertTrue(all(item["raw_line_start"] and item["raw_line_end"] for item in result["matches"]))
        cleared = self.features.semantic_clear()
        self.assertTrue(cleared["keyword_retrieval_available"])
        _, keyword_metadata = self.store.retrieve("迁移语义索引红线")
        self.assertTrue(keyword_metadata["raw_matches"])
        raw_after = {
            item["path"]: item["sha256"]
            for item in archive_manifest(self.store.root)["files"]
            if item["authoritative"]
        }
        self.assertEqual(raw_before, raw_after)

    def test_semantic_pointer_index_reads_each_raw_file_once(self):
        records = self.store.read_all_raw()
        raw_path = self.store.root / records[0]["_path"]
        original_read_text = Path.read_text
        raw_reads = 0

        def counted_read_text(path, *args, **kwargs):
            nonlocal raw_reads
            if path == raw_path:
                raw_reads += 1
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", counted_read_text):
            pointers = self.features.raw_pointer_index(records)

        self.assertEqual(1, raw_reads)
        self.assertEqual({("m1", 10, 13), ("m2", 15, 18)}, {
            (
                message_id,
                pointer["raw_line_start"],
                pointer["raw_line_end"],
            )
            for (_, message_id), pointer in pointers.items()
        })

    def test_multilingual_e5_provider_keeps_vectors_out_of_readable_metadata(self):
        def fake_embed(texts, prefix, output, batch_size=8):
            self.assertEqual("passage", prefix)
            output.write_bytes(b"fake-numpy-matrix")

        with patch.object(self.features, "e5_embed", side_effect=fake_embed):
            result = self.features.semantic_build("multilingual-e5-small")
        self.assertEqual("multilingual-e5-small", result["provider"])
        metadata = [
            json.loads(line)
            for line in Path(result["path"]).read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(metadata)
        self.assertTrue(all("vector" not in item for item in metadata))
        self.assertTrue(all(item["raw_line_start"] for item in metadata))
        with patch.object(
            self.features,
            "e5_scores",
            return_value=[0.9, 0.1],
        ):
            retrieved = self.features.semantic_retrieve("保留原始归档", 2)
        self.assertEqual("multilingual-e5-small", retrieved["provider"])
        self.assertEqual("m1", retrieved["matches"][0]["message_id"])

    def test_retrieval_evaluation_is_human_readable(self):
        dataset = self.base / "evaluation.jsonl"
        dataset.write_text(
            json.dumps({
                "id": "case-1",
                "query": "迁移语义索引红线",
                "expected_message_ids": ["m1"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = self.features.retrieval_evaluate(dataset, 10)
        self.assertEqual(1, result["case_count"])
        self.assertIn("mean_recall_at_k", result)


if __name__ == "__main__":
    unittest.main()
