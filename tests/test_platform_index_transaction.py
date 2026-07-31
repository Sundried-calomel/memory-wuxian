import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from platform_transaction import (  # noqa: E402
    INDEX_GENERATION_FORMAT,
    PlatformTransactionError,
    canonical_json_bytes,
    index_generation_id,
    read_index_generation,
    source_manifest_sha256,
    validate_index_generation,
    write_index_generation,
)


class PlatformIndexTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest_path = self.root / "状态" / "索引 generations" / "manifest.json"

    @staticmethod
    def generation(*, created_at="2026-08-01T12:34:56Z", previous=None):
        document = {
            "generation_id": "idxgen-" + "0" * 64,
            "format": INDEX_GENERATION_FORMAT,
            "format_version": 1,
            "builder": "memory-indexing-v2.6",
            "source_manifest": {
                "entries": [{
                    "path": "归档/会话 01.jsonl",
                    "byte_length": 19,
                    "sha256": "1" * 64,
                }],
                "sha256": "",
            },
            "files": [
                {
                    "path": "indexes/日本語-index.jsonl",
                    "byte_length": 23,
                    "sha256": "2" * 64,
                }
            ],
            "created_at": created_at,
            "previous_generation_id": previous,
            "status": "complete",
        }
        document["source_manifest"]["sha256"] = source_manifest_sha256(
            document["source_manifest"]["entries"]
        )
        document["generation_id"] = index_generation_id(document)
        return document

    def test_success_writes_exact_canonical_bytes_and_reads_validated_pointer(self):
        document = self.generation()
        later_metadata = self.generation(created_at="2026-08-02T12:34:56Z")
        previous_metadata = self.generation(previous="idxgen-" + "f" * 64)

        written = write_index_generation(self.manifest_path, document)

        self.assertEqual(document["generation_id"], later_metadata["generation_id"])
        self.assertEqual(document["generation_id"], previous_metadata["generation_id"])
        self.assertEqual(canonical_json_bytes(document), written)
        self.assertEqual(written, self.manifest_path.read_bytes())
        self.assertEqual(document, read_index_generation(self.manifest_path))

    def test_interrupted_replacement_preserves_previous_pointer_bytes(self):
        previous = self.generation(created_at="2026-08-01T00:00:00Z")
        write_index_generation(self.manifest_path, previous)
        before = self.manifest_path.read_bytes()
        replacement = self.generation(
            created_at="2026-08-01T01:00:00Z",
            previous=previous["generation_id"],
        )

        def interrupt(_temporary, _target):
            raise RuntimeError("MW26-SWITCH-001 injected interruption")

        with self.assertRaisesRegex(RuntimeError, "MW26-SWITCH-001"):
            write_index_generation(
                self.manifest_path, replacement, before_replace=interrupt
            )

        self.assertEqual(before, self.manifest_path.read_bytes())
        self.assertEqual(previous, read_index_generation(self.manifest_path))
        self.assertEqual([], list(self.manifest_path.parent.glob(".*.tmp")))

    def test_malformed_and_tampered_pointers_fail_closed(self):
        original = self.generation()
        write_index_generation(self.manifest_path, original)
        original_bytes = self.manifest_path.read_bytes()
        tampered = self.generation()
        tampered["files"][0]["byte_length"] += 1
        with self.assertRaisesRegex(PlatformTransactionError, "identity does not match"):
            write_index_generation(self.manifest_path, tampered)
        self.assertEqual(original_bytes, self.manifest_path.read_bytes())

        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_bytes(b'{"generation_id":"one","generation_id":"two"}')
        with self.assertRaisesRegex(PlatformTransactionError, "duplicate key"):
            read_index_generation(self.manifest_path)

        self.manifest_path.write_bytes(canonical_json_bytes(tampered))
        with self.assertRaisesRegex(PlatformTransactionError, "identity does not match"):
            read_index_generation(self.manifest_path)

    def test_windows_safe_unicode_paths_and_closed_schema(self):
        document = self.generation()
        write_index_generation(self.manifest_path, document)
        self.assertEqual(document, read_index_generation(self.manifest_path))

        schema = json.loads(
            (ROOT / "schemas" / "index-generation.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema", schema["$schema"]
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            {
                "generation_id",
                "format",
                "format_version",
                "builder",
                "source_manifest",
                "files",
                "created_at",
                "previous_generation_id",
                "status",
            },
            set(schema["required"]),
        )
        self.assertFalse(
            schema["properties"]["source_manifest"]["additionalProperties"]
        )
        self.assertEqual(
            ["entries", "sha256"],
            schema["properties"]["source_manifest"]["required"],
        )
        self.assertFalse(schema["$defs"]["exactFile"]["additionalProperties"])
        self.assertEqual(
            "^idxgen-[0-9a-f]{64}$", schema["$defs"]["generationId"]["pattern"]
        )

    def test_manifest_and_source_contract_reject_extra_or_tampered_fields(self):
        document = self.generation()
        self.assertIs(document, validate_index_generation(document))

        extra = self.generation()
        extra["unexpected"] = True
        with self.assertRaisesRegex(PlatformTransactionError, "closed v1 field set"):
            validate_index_generation(extra)

        source_extra = self.generation()
        source_extra["source_manifest"]["entries"][0]["kind"] = "raw"
        with self.assertRaisesRegex(PlatformTransactionError, "must contain exactly"):
            validate_index_generation(source_extra)

        source_tampered = self.generation()
        source_tampered["source_manifest"]["entries"][0]["byte_length"] += 1
        with self.assertRaisesRegex(PlatformTransactionError, "digest does not match"):
            validate_index_generation(source_tampered)


if __name__ == "__main__":
    unittest.main()
