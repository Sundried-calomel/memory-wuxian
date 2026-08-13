import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_atoms import (  # noqa: E402
    MemoryAtomsError,
    _source_sha256,
    compare,
    persist_sidecar,
    project,
    read_json,
)
from platform_transaction import canonical_json_bytes  # noqa: E402


class MemoryAtomsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive_root = self.root / "archive"
        self.archive_root.mkdir()
        self.records = [
            {
                "sequence": 7,
                "message_id": "消息-日圓-¥-1",
                "role": "user",
                "timestamp": "2026-08-10T09:00:00+09:00",
                "conversation_id": "对话-日本語-😀",
                "content": "保留 summary-v1；任务路径可包含 ¥、空格和😀。",
            },
            {
                "sequence": 8,
                "message_id": "message-2",
                "role": "assistant",
                "timestamp": "2026-08-10T09:01:00+09:00",
                "conversation_id": "对话-日本語-😀",
                "content": "采用并行侧车；不得改写原始归档。",
            },
        ]
        self.job = {
            "format_version": 1,
            "job_id": "job-000123",
            "summary_level": 1,
            "conversation_id": "对话-日本語-😀",
            "source_message_ids": ["消息-日圓-¥-1", "message-2"],
            "source_records": self.records,
            "source_sha256": _source_sha256(self.records),
        }
        self.candidate = {
            "format_version": 1,
            "job_id": self.job["job_id"],
            "source_sha256": self.job["source_sha256"],
            "atoms": [
                {
                    "local_id": "a1",
                    "atom_type": "work_method",
                    "statement": "保留 summary-v1，并采用并行侧车。",
                    "epistemic_status": "accepted_decision",
                    "scope": "Memory无限 / 日本語 / ¥ / 😀",
                    "source_message_ids": ["消息-日圓-¥-1", "message-2"],
                },
                {
                    "local_id": "a2",
                    "atom_type": "work_fact",
                    "statement": "原始归档不得改写。",
                    "epistemic_status": "explicit_fact",
                    "scope": "Memory无限 / 日本語 / ¥ / 😀",
                    "source_message_ids": ["message-2"],
                },
            ],
            "relations": [
                {
                    "from_local_id": "a2",
                    "to_local_id": "a1",
                    "relation_type": "supports",
                    "source_message_ids": ["message-2"],
                }
            ],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_projection_is_deterministic_and_preserves_utf8(self):
        first = project(self.job, self.candidate)
        second = project(self.job, self.candidate)
        self.assertEqual(first, second)
        encoded = canonical_json_bytes(first)
        self.assertIn("日本語".encode("utf-8"), encoded)
        self.assertIn("¥".encode("utf-8"), encoded)
        self.assertIn("😀".encode("utf-8"), encoded)
        self.assertEqual(2, first["metrics"]["atom_count"])
        self.assertEqual(1, first["metrics"]["relation_count"])
        self.assertEqual(1, first["metrics"]["scene_count"])
        self.assertRegex(first["projection_sha256"], r"^[0-9a-f]{64}$")

    def test_unknown_source_id_fails_closed(self):
        candidate = json.loads(json.dumps(self.candidate, ensure_ascii=False))
        candidate["atoms"][0]["source_message_ids"] = ["invented-id"]
        with self.assertRaisesRegex(MemoryAtomsError, "outside the job"):
            project(self.job, candidate)

    def test_candidate_source_ids_must_follow_source_order(self):
        candidate = json.loads(json.dumps(self.candidate, ensure_ascii=False))
        candidate["atoms"][0]["source_message_ids"] = [
            "message-2",
            "消息-日圓-¥-1",
        ]
        with self.assertRaisesRegex(MemoryAtomsError, "not source ordered"):
            project(self.job, candidate)

    def test_source_hash_mismatch_fails_closed(self):
        job = dict(self.job)
        job["source_sha256"] = "0" * 64
        candidate = dict(self.candidate)
        candidate["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(MemoryAtomsError, "does not match embedded"):
            project(job, candidate)

    def test_duplicate_source_sequence_fails_closed(self):
        job = json.loads(json.dumps(self.job, ensure_ascii=False))
        job["source_records"][1]["sequence"] = job["source_records"][0]["sequence"]
        job["source_sha256"] = _source_sha256(job["source_records"])
        candidate = dict(self.candidate)
        candidate["source_sha256"] = job["source_sha256"]
        with self.assertRaisesRegex(MemoryAtomsError, "duplicate sequences"):
            project(job, candidate)

    def test_status_type_mismatch_fails_closed(self):
        candidate = json.loads(json.dumps(self.candidate, ensure_ascii=False))
        candidate["atoms"][1]["epistemic_status"] = "accepted_decision"
        with self.assertRaisesRegex(MemoryAtomsError, "incompatible"):
            project(self.job, candidate)

    def test_relation_evidence_must_belong_to_related_atoms(self):
        candidate = json.loads(json.dumps(self.candidate, ensure_ascii=False))
        candidate["relations"][0]["source_message_ids"] = ["消息-日圓-¥-1"]
        candidate["atoms"][0]["source_message_ids"] = ["message-2"]
        with self.assertRaisesRegex(MemoryAtomsError, "outside the job"):
            project(self.job, candidate)

    def test_sidecar_write_is_external_idempotent_and_append_only(self):
        summary_path = self.archive_root / "summaries" / "L1-000001.md"
        summary_path.parent.mkdir()
        summary_path.write_text("immutable summary-v1\n", encoding="utf-8")
        before = hashlib.sha256(summary_path.read_bytes()).hexdigest()
        value = project(self.job, self.candidate)
        destination, status = persist_sidecar(
            value, self.root / "sidecar-output", self.archive_root
        )
        self.assertEqual("created", status)
        self.assertTrue(destination.is_file())
        same_destination, same_status = persist_sidecar(
            value, self.root / "sidecar-output", self.archive_root
        )
        self.assertEqual(destination, same_destination)
        self.assertEqual("existing-identical", same_status)
        self.assertEqual(before, hashlib.sha256(summary_path.read_bytes()).hexdigest())

        different = json.loads(json.dumps(self.candidate, ensure_ascii=False))
        different["atoms"][0]["statement"] += " 不覆盖旧文件。"
        with self.assertRaisesRegex(MemoryAtomsError, "different bytes"):
            persist_sidecar(
                project(self.job, different),
                self.root / "sidecar-output",
                self.archive_root,
            )

    def test_sidecar_refuses_archive_and_archive_named_paths(self):
        value = project(self.job, self.candidate)
        with self.assertRaisesRegex(MemoryAtomsError, "outside the archive"):
            persist_sidecar(value, self.archive_root / "derived", self.archive_root)
        with self.assertRaisesRegex(MemoryAtomsError, "resembles a live archive"):
            persist_sidecar(value, self.root / "pending" / "sidecar", self.archive_root)

    def test_reader_rejects_duplicate_keys_and_invalid_utf8(self):
        duplicate = self.root / "duplicate.json"
        duplicate.write_bytes(b'{"a":1,"a":2}')
        with self.assertRaisesRegex(MemoryAtomsError, "duplicate key"):
            read_json(duplicate, 1024)
        invalid = self.root / "invalid.json"
        invalid.write_bytes(b'{"text":"\xff"}')
        with self.assertRaisesRegex(MemoryAtomsError, "malformed UTF-8"):
            read_json(invalid, 1024)

    def test_ab_report_labels_interpretation_limit(self):
        sidecar = project(self.job, self.candidate)
        summary = {
            "topics": ["Memory无限"],
            "established_conclusions": ["采用侧车"],
            "open_questions": [],
            "concepts": ["summary-v1"],
            "policy_events": [],
        }
        report = compare(summary, sidecar, 100, len(canonical_json_bytes(sidecar)))
        self.assertEqual(0, report["summary_v1"]["source_traceable_item_count"])
        self.assertEqual(2, report["memory_atoms_v1"]["source_traceable_atom_count"])
        self.assertIn("do not prove semantic quality", report["interpretation_limits"][0])

    def test_sidecar_nested_tampering_fails_even_with_recomputed_top_hash(self):
        sidecar = project(self.job, self.candidate)
        sidecar["metrics"]["atom_count"] = 999
        unsigned = {key: value for key, value in sidecar.items() if key != "projection_sha256"}
        sidecar["projection_sha256"] = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        summary = {
            "topics": [],
            "established_conclusions": [],
            "open_questions": [],
            "concepts": [],
            "policy_events": [],
        }
        with self.assertRaisesRegex(MemoryAtomsError, "metrics does not match"):
            compare(summary, sidecar, 1, 1)

    def test_atom_identity_tampering_fails_even_with_recomputed_top_hash(self):
        sidecar = project(self.job, self.candidate)
        sidecar["atoms"][0]["statement"] += " 篡改"
        unsigned = {key: value for key, value in sidecar.items() if key != "projection_sha256"}
        sidecar["projection_sha256"] = hashlib.sha256(
            canonical_json_bytes(unsigned)
        ).hexdigest()
        summary = {
            "topics": [],
            "established_conclusions": [],
            "open_questions": [],
            "concepts": [],
            "policy_events": [],
        }
        with self.assertRaisesRegex(MemoryAtomsError, "atom_id does not match"):
            compare(summary, sidecar, 1, 1)

    def test_real_cli_validate_and_project(self):
        job_path = self.root / "工作 job.json"
        candidate_path = self.root / "候选 ¥ 😀.json"
        job_path.write_text(
            json.dumps(self.job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        candidate_path.write_text(
            json.dumps(self.candidate, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        validate = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "memory_atoms.py"),
                "validate",
                "--job",
                str(job_path),
                "--candidate",
                str(candidate_path),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, validate.returncode, validate.stderr)
        self.assertEqual("valid", json.loads(validate.stdout)["status"])
        projected = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "memory_atoms.py"),
                "project",
                "--job",
                str(job_path),
                "--candidate",
                str(candidate_path),
                "--output-dir",
                str(self.root / "可读 sidecar"),
                "--archive-root",
                str(self.archive_root),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, projected.returncode, projected.stderr)
        result = json.loads(projected.stdout)
        self.assertEqual("created", result["status"])
        self.assertTrue(Path(result["path"]).is_file())

    def test_candidate_schema_is_strict_and_versioned(self):
        schema = json.loads(
            (ROOT / "schemas" / "memory-atoms-result.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(1, schema["properties"]["format_version"]["const"])
        self.assertFalse(
            schema["properties"]["atoms"]["items"]["additionalProperties"]
        )


if __name__ == "__main__":
    unittest.main()
