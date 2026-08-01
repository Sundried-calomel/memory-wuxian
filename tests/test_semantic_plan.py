from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, raw_source_sha256
from semantic_plan import (
    build_execution_plan,
    canonical_sha256,
    load_verified_result,
    materialize_unit_job,
    persist_result,
)
from semantic_worker import (
    build_prompt,
    execute_oversized_job,
    prompt_contract_sha256,
    run_job,
)


def record(sequence: int, round_number: int, text: str) -> dict:
    item = {
        "message_id": f"m-{sequence}",
        "sequence": sequence,
        "round_number": round_number,
        "speaker": "user" if sequence % 2 else "assistant",
        "timestamp": f"2026-08-01T00:00:{sequence % 60:02d}+00:00",
        "text": text,
        "source": {"kind": "codex", "line": sequence},
    }
    from memory_cli import raw_record_sha256
    item["content_sha256"] = raw_record_sha256(item)
    return item


def job(records: list[dict]) -> dict:
    return {
        "format_version": 1,
        "job_id": "job-000318",
        "target_summary_id": "L1-000318",
        "summary_level": 1,
        "conversation_id": "codex:test",
        "source_signature": "conversation:test:rounds:940-953",
        "source_round_start": 940,
        "source_round_end": 953,
        "source_round_numbers": sorted({item["round_number"] for item in records}),
        "source_round_count": len({item["round_number"] for item in records}),
        "source_start": records[0]["message_id"],
        "source_end": records[-1]["message_id"],
        "source_start_sequence": records[0]["sequence"],
        "source_end_sequence": records[-1]["sequence"],
        "source_files": ["raw/test.jsonl"],
        "source_message_ids": [item["message_id"] for item in records],
        "source_sha256": raw_source_sha256(records),
        "source_records": records,
        "required_result_keys": [
            "topics", "established_conclusions", "open_questions", "concepts", "policy_events"
        ],
    }


def empty_result() -> dict:
    return {
        "topics": [], "established_conclusions": [], "open_questions": [],
        "concepts": [], "policy_events": [],
    }


class SemanticPlanTests(unittest.TestCase):
    def test_small_job_keeps_single_call_path_and_creates_no_plan(self):
        parent = job([record(1, 1, "small")])
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "archive"
            store = MemoryStore(root, {})
            store.init()
            job_path = store.pending_dir / f"{parent['job_id']}.json"
            job_path.write_text(json.dumps(parent), encoding="utf-8")
            config = base / "config.yaml"
            config.write_text("", encoding="utf-8")
            calls = []

            def invoke(_command, _timeout, prompt):
                calls.append(prompt)
                return empty_result()

            with patch.object(
                MemoryStore,
                "ingest_summary",
                return_value=root / "summaries/level-1/L1-000318.md",
            ):
                result = run_job(
                    root, config, job_path, False, create_backup=False, invoker=invoke
                )
            self.assertEqual(result["status"], "ingested")
            self.assertEqual(result["ai_invocations"], 1)
            self.assertEqual(calls, [build_prompt(parent)])
            self.assertFalse((store.pending_dir / "semantic-plans").exists())

    def test_realistic_1_4m_shape_is_bounded_and_preserves_parent_identity(self):
        records = []
        for index in range(2253):
            round_number = [940, 941, 942, 952, 953][min(4, index // 450)]
            records.append(
                record(index + 1, round_number, f"文献¥資料🙂-{index:04d}-" + ("x" * 580))
            )
        parent = job(records)
        self.assertGreater(len(build_prompt(parent)), 1_300_000)
        plan = build_execution_plan(
            parent, build_prompt, prompt_contract_sha256(), 900_000, 900_000
        )
        self.assertEqual(plan["job_id"], parent["job_id"])
        self.assertEqual(plan["target_summary_id"], parent["target_summary_id"])
        self.assertEqual(plan["source_sha256"], parent["source_sha256"])
        self.assertLessEqual(len(plan["units"]) + 1, 16)
        restored_ids = []
        for unit in plan["units"]:
            unit_job = materialize_unit_job(parent, unit)
            restored_ids.extend(unit_job["source_message_ids"])
            prompt = build_prompt(unit_job)
            self.assertLessEqual(len(prompt), 900_000)
            self.assertLessEqual(len(prompt.encode("utf-8")), 900_000)
        self.assertEqual(restored_ids, parent["source_message_ids"])

    def test_utf8_byte_budget_and_single_record_field_slicing_are_exact(self):
        text = "中文日本語¥🙂" * 30_000
        parent = job([record(1, 953, text)])
        plan = build_execution_plan(parent, build_prompt, prompt_contract_sha256(), 900_000, 180_000)
        fragments = [
            fragment
            for unit in plan["units"] if unit["kind"] == "field-fragments"
            for fragment in unit["fragments"]
            if fragment["field_path"] == ["text"]
        ]
        self.assertGreater(len(fragments), 1)
        self.assertEqual("".join(item["value"] for item in fragments), text)
        self.assertEqual(fragments[0]["start_character"], 0)
        self.assertEqual(fragments[-1]["end_character"], len(text))
        for left, right in zip(fragments, fragments[1:]):
            self.assertEqual(left["end_character"], right["start_character"])

    def test_tampered_completed_segment_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "map.json"
            persist_result(path, "a" * 64, empty_result())
            envelope = json.loads(path.read_text(encoding="utf-8"))
            envelope["result"]["topics"] = ["tampered"]
            path.write_text(json.dumps(envelope), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "result hash mismatch"):
                load_verified_result(path, "a" * 64)

    def test_tampered_parent_source_fails_before_model_call(self):
        parent = job([record(1, 1, "a" * 120_000), record(2, 2, "b" * 120_000)])
        parent["source_records"][0]["text"] = "changed" + parent["source_records"][0]["text"]
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "archive", {})
            store.init()
            invoked = Mock(side_effect=AssertionError("must not invoke model"))
            with self.assertRaisesRegex(ValueError, "source SHA-256 mismatch"):
                execute_oversized_job(
                    store, parent, ["codex"], 1, 180_000, 180_000, invoked
                )
            invoked.assert_not_called()

    def test_resume_reuses_verified_map_results_without_model_call(self):
        records = [record(1, 1, "a" * 120_000), record(2, 2, "b" * 120_000)]
        parent = job(records)
        with tempfile.TemporaryDirectory() as temporary:
            store = MemoryStore(Path(temporary) / "archive", {})
            store.init()
            calls = []

            def invoke(_command, _timeout, _prompt):
                calls.append(1)
                return empty_result()

            first, first_calls = execute_oversized_job(
                store, parent, ["codex"], 1, 180_000, 180_000, invoke
            )
            self.assertEqual(first, empty_result())
            self.assertGreater(first_calls, 1)
            prior = len(calls)
            second, second_calls = execute_oversized_job(
                store, parent, ["codex"], 1, 180_000, 180_000, invoke
            )
            self.assertEqual(second, first)
            self.assertEqual(second_calls, 0)
            self.assertEqual(len(calls), prior)

    def test_non_contiguous_round_metadata_uses_unique_count(self):
        records = [record(1, 940, "a"), record(2, 941, "b"), record(3, 953, "c")]
        parent = job(records)
        self.assertEqual(parent["source_round_numbers"], [940, 941, 953])
        self.assertEqual(parent["source_round_count"], 3)

    def test_ingest_old_job_derives_non_contiguous_unique_round_count(self):
        records = [record(1, 940, "a"), record(2, 941, "b"), record(3, 953, "c")]
        parent = job(records)
        parent.pop("source_round_numbers")
        parent.pop("source_round_count")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "archive"
            store = MemoryStore(root, {})
            store.init()
            job_path = store.pending_dir / f"{parent['job_id']}.json"
            job_path.write_text(json.dumps(parent), encoding="utf-8")
            result_path = Path(temporary) / "result.json"
            result_path.write_text(json.dumps(empty_result()), encoding="utf-8")
            with patch.object(
                store, "current_job_source_sha256", return_value=parent["source_sha256"]
            ):
                summary_path = store.ingest_summary(job_path, result_path)
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("source_rounds: 3", summary)
            self.assertIn("source_round_numbers:", summary)
            self.assertIn("  - 940", summary)
            self.assertIn("  - 941", summary)
            self.assertIn("  - 953", summary)
            index = json.loads((store.index_dir / "summaries.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(index["source_round_count"], 3)
            self.assertEqual(index["source_round_numbers"], [940, 941, 953])


if __name__ == "__main__":
    unittest.main()
