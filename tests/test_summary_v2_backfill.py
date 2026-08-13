import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_atoms import _source_sha256  # noqa: E402
from memory_cli import MemoryStore  # noqa: E402
from memory_summary_v2 import build_level_1_source, project  # noqa: E402
from platform_transaction import atomic_write_canonical_json  # noqa: E402
from summary_v2_backfill import SummaryV2Error, build_plan  # noqa: E402


def summary_markdown(summary_id, level, conversation_id, source_sha, message_ids, children=()):
    source_messages = "\n".join(f'  - "{value}"' for value in message_ids)
    source_summaries = "\n".join(f'  - "{value}"' for value in children)
    child_block = f"source_summaries: \n{source_summaries}\n" if children else ""
    message_block = f"source_message_ids: \n{source_messages}\n" if message_ids else ""
    return (
        "---\n"
        f"summary_id: {summary_id}\n"
        f"summary_level: {level}\n"
        f"conversation_id: \"{conversation_id}\"\n"
        "created_at: \"2026-08-13T00:00:00+00:00\"\n"
        f"source_sha256: \"{source_sha}\"\n"
        f"{message_block}{child_block}"
        "format_version: 1\n"
        "---\n\n"
        f"# Level-{level} Summary {summary_id}\n\n"
        "## Topics\n\n- topic\n\n"
        "## Established Conclusions\n\n- conclusion\n\n"
        "## Open Questions\n\n- None recorded.\n\n"
        "## Concepts\n\n- concept\n\n"
        "## Policy Events\n\n- None recorded.\n\n"
        "## Source References\n"
    )


class SummaryV2BackfillTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "archive"
        self.output = self.base / "summary-v2"
        self.store = MemoryStore(self.archive, {})
        self.store.init()
        self.conversation = "codex:fixture-conversation"
        for index in range(1, 5):
            self.store.append_message(
                speaker="user" if index % 2 else "assistant",
                text=f"消息 {index} / 日本語 / ￥ / emoji 😀",
                timestamp=f"2026-08-13T00:00:0{index}+00:00",
                conversation_id=self.conversation,
                message_id=f"fixture-{index}",
                reply_to=None,
                allow_secrets=False,
                complete_round=index % 2 == 0,
            )
        raw = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in self.store.read_all_raw()
        ]
        by_id = {item["message_id"]: item for item in raw}
        self.first_ids = ["fixture-1", "fixture-2"]
        self.second_ids = ["fixture-3", "fixture-4"]
        level1 = self.archive / "summaries" / "level-1"
        level1.mkdir(parents=True, exist_ok=True)
        (level1 / "L1-000001.md").write_text(
            summary_markdown(
                "L1-000001",
                1,
                self.conversation,
                _source_sha256([by_id[value] for value in self.first_ids]),
                self.first_ids,
            ),
            encoding="utf-8",
        )
        (level1 / "L1-000002.md").write_text(
            summary_markdown(
                "L1-000002",
                1,
                self.conversation,
                _source_sha256([by_id[value] for value in self.second_ids]),
                self.second_ids,
            ),
            encoding="utf-8",
        )
        level2 = self.archive / "summaries" / "level-2"
        level2.mkdir(parents=True, exist_ok=True)
        (level2 / "L2-000001.md").write_text(
            summary_markdown(
                "L2-000001",
                2,
                self.conversation,
                "0" * 64,
                [],
                ["L1-000001", "L1-000002"],
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_reconstructs_verified_l1_and_waits_for_parent(self):
        before = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        calls = 0
        original = MemoryStore.read_all_raw

        def counted(store):
            nonlocal calls
            calls += 1
            return original(store)

        with patch.object(MemoryStore, "read_all_raw", counted):
            plan = build_plan(self.archive, self.output)
        self.assertEqual(1, calls)
        self.assertEqual(4, plan["raw_message_count"])
        self.assertEqual(3, plan["summary_v1_count"])
        self.assertEqual(2, plan["counts"]["level_1_ready"])
        self.assertEqual(1, plan["counts"]["level_2_waiting_for_children"])
        self.assertFalse(plan["quarantine"])
        self.assertTrue((self.output / "backfill" / "jobs" / "level-1" / "L1-000001.json").is_file())
        after = {
            path.relative_to(self.archive): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.archive.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        repeated = build_plan(self.archive, self.output)
        self.assertEqual(plan["counts"], repeated["counts"])

    def test_conflicting_existing_sidecars_quarantine_only_their_summary(self):
        plan = build_plan(self.archive, self.output)
        task = next(item for item in plan["tasks"] if item["summary_id"] == "L1-000001")
        job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
        source = build_level_1_source(job)

        def candidate(text):
            refs = list(source["source_refs"])
            return {
                "format_version": 2,
                "job_id": source["job_id"],
                "summary_level": 1,
                "source_sha256": source["source_sha256"],
                "overview": [{"local_id": "o1", "text": text, "source_refs": refs}],
                "scenes": [{"local_id": "s1", "title": "多语言记录", "summary": text, "source_refs": refs}],
                "atoms": [
                    {
                        "local_id": "a1",
                        "atom_type": "work_fact",
                        "statement": text,
                        "epistemic_status": "explicit_fact",
                        "scope": "fixture",
                        "source_refs": refs,
                    }
                ],
                "relations": [],
                "retrieval_anchors": [],
                "omissions": [],
            }

        roots = [self.base / "candidate-a", self.base / "candidate-b"]
        for root, text in zip(roots, ["候选甲", "候选乙"]):
            sidecar = project(source, candidate(text))
            target = root / sidecar["summary_v2_id"] / "summary.json"
            target.parent.mkdir(parents=True)
            atomic_write_canonical_json(target, sidecar)
        conflicted = build_plan(self.archive, self.base / "fresh-output", roots)
        by_id = {task["summary_id"]: task for task in conflicted["tasks"]}
        self.assertEqual("quarantined", by_id["L1-000001"]["status"])
        self.assertEqual("ready", by_id["L1-000002"]["status"])
        reasons = {item["summary_id"]: item["reason"] for item in conflicted["quarantine"]}
        self.assertEqual("conflicting-existing-sidecars", reasons["L1-000001"])

    def test_source_hash_drift_is_quarantined(self):
        path = self.archive / "summaries" / "level-1" / "L1-000001.md"
        text = path.read_text(encoding="utf-8").replace(
            "source_sha256: \"", "source_sha256: \"f", 1
        )
        path.write_text(text, encoding="utf-8")
        plan = build_plan(self.archive, self.output)
        quarantined = {item["summary_id"]: item["reason"] for item in plan["quarantine"]}
        self.assertIn("L1-000001", quarantined)
        self.assertIn("source-validation", quarantined["L1-000001"])

    def test_output_inside_archive_is_rejected(self):
        with self.assertRaisesRegex(SummaryV2Error, "outside the archive"):
            build_plan(self.archive, self.archive / "derived")


if __name__ == "__main__":
    unittest.main()
