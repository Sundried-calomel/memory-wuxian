from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from memory_cli import MemoryStore, load_simple_yaml
from memory_summary_budget import evaluate_summary_budget


class SummaryBudgetTests(unittest.TestCase):
    policy = {"minimum_completed_rounds": 2, "character_threshold": 1000, "token_threshold": 300}

    @staticmethod
    def metrics(**changes):
        value = {"conversation_id": "codex:test", "completed_rounds": 2, "summarized_rounds": 0, "unsummarized_characters": 999, "estimated_unsummarized_tokens": 299, "round_complete": True}
        value.update(changes)
        return value

    def test_mw29_budget_001_before_at_after_threshold_is_deterministic(self):
        self.assertFalse(evaluate_summary_budget(self.metrics(completed_rounds=1), self.policy)["due"])
        self.assertTrue(evaluate_summary_budget(self.metrics(), self.policy)["due"])
        self.assertTrue(evaluate_summary_budget(self.metrics(completed_rounds=3), self.policy)["due"])

    def test_mw29_boundary_001_incomplete_round_blocks_queue_and_ai(self):
        decision = evaluate_summary_budget(self.metrics(round_complete=False, unsummarized_characters=1000), self.policy)
        self.assertFalse(decision["due"])
        self.assertTrue(decision["blocked_by_incomplete_round"])
        self.assertEqual(0, decision["ai_invocations"])

    def test_mw29_queue_001_real_scheduler_uses_token_budget_at_round_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.yaml"
            config.write_text(
                "memory:\n  root_directory: ./memory\n"
                "summaries:\n  level_1_trigger_rounds: 99\n"
                "  level_1_trigger_characters: 99999\n"
                "  level_1_trigger_tokens: 10\n"
                "  maximum_summary_depth: 4\n",
                encoding="utf-8",
            )
            store = MemoryStore(root / "memory", load_simple_yaml(config))
            store.init()
            store.append_message("user", "x" * 40, "2026-08-01T00:00:00+09:00", "codex:test", "u1", None, False)
            store.append_message("assistant", "y" * 40, "2026-08-01T00:00:01+09:00", "codex:test", "a1", "u1", False)
            job = store.make_summary_job()
            self.assertIsNotNone(job)
            self.assertEqual(1, len(store.pending_jobs()))
            self.assertIsNone(store.make_summary_job())


if __name__ == "__main__":
    unittest.main()
