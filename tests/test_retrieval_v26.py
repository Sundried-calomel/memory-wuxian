import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_guarded_features import GuardedFeatures


FIXTURE = ROOT / "tests" / "fixtures" / "retrieval-v2.6" / "benchmark.jsonl"


class FakeRetrievalStore:
    def __init__(self, root: Path):
        self.root = root
        self.calls = []

    def retrieve(self, query, mode="historical"):
        self.calls.append((query, mode))
        policy_events = [
            {"policy_event_id": "P-policy-E001", "validity": "superseded"},
            {"policy_event_id": "P-policy-E002", "validity": "active"},
        ]
        if query == "codex:019f-exact-thread":
            return "", {
                "raw_matches": [{"message_id": "m-exact-thread"}],
                "verification": "verified",
                "policy_events": [],
                "disambiguation": {
                    "kind": "conversation-id",
                    "requested": query,
                    "resolved_conversation_id": query,
                    "resolved_title": "Exact benchmark thread",
                },
            }
        return "", {
            "raw_matches": [{"message_id": "m-policy-current"}],
            "verification": "verified",
            "policy_events": policy_events,
        }


class RetrievalV26Test(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = FakeRetrievalStore(self.root)
        self.features = GuardedFeatures(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def write_corpus(self, records):
        path = self.root / "corpus.jsonl"
        path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_fixed_benchmark_covers_policy_disambiguation_and_intended_delta(self):
        result = self.features.retrieval_evaluate(FIXTURE, 10)

        self.assertEqual("memory-wuxian-retrieval-evaluation-v2", result["format"])
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), result["corpus_sha256"])
        self.assertEqual(3, result["case_count"])
        self.assertTrue(result["all_cases_passed"])
        self.assertEqual(0, result["unexplained_delta_count"])
        cases = {case["id"]: case for case in result["cases"]}
        self.assertTrue(cases["MW26-RET-001"]["policy_matches"])
        self.assertTrue(cases["MW26-RET-002"]["disambiguation_matches"])
        self.assertEqual(
            {"added_message_ids": ["m-policy-current"], "removed_message_ids": ["m-policy-previous"]},
            cases["MW26-DELTA-001"]["intended_delta"],
        )
        self.assertEqual(cases["MW26-DELTA-001"]["intended_delta"], cases["MW26-DELTA-001"]["actual_delta"])
        self.assertEqual(
            [
                ("current summary trigger policy", "current-policy"),
                ("codex:019f-exact-thread", "historical"),
                ("current summary trigger policy", "current-policy"),
            ],
            self.store.calls,
        )

    def test_unexplained_delta_fails_the_case(self):
        corpus = self.write_corpus([
            {"format": "memory-wuxian-retrieval-corpus-v2", "version": "2.6"},
            {
                "id": "MW26-DELTA-001",
                "query": "current summary trigger policy",
                "mode": "current-policy",
                "expected_message_ids": ["m-policy-current"],
                "expected_confidence": "verified",
                "expected_policy_event_ids": ["P-policy-E001", "P-policy-E002"],
                "expected_policy_validity": {
                    "P-policy-E001": "superseded",
                    "P-policy-E002": "active",
                },
                "comparison": {
                    "baseline_message_ids": ["m-policy-previous"],
                    "intended_added_message_ids": [],
                    "intended_removed_message_ids": ["m-policy-previous"],
                },
            },
        ])

        result = self.features.retrieval_evaluate(corpus, 10)

        self.assertFalse(result["all_cases_passed"])
        self.assertEqual(1, result["unexplained_delta_count"])
        self.assertEqual(
            ["m-policy-current"],
            result["cases"][0]["unexplained_delta"]["unexpected_added_message_ids"],
        )

    def test_fixed_corpus_rejects_empty_duplicate_and_malformed_case_ids(self):
        invalid_corpora = [
            [{"format": "memory-wuxian-retrieval-corpus-v2", "version": "2.6"}],
            [
                {"format": "memory-wuxian-retrieval-corpus-v2", "version": "2.6"},
                {"id": "MW26-RET-001", "query": "one"},
                {"id": "MW26-RET-001", "query": "two"},
            ],
            [
                {"format": "memory-wuxian-retrieval-corpus-v2", "version": "2.6"},
                {"id": "case-1", "query": "one"},
            ],
        ]
        for records in invalid_corpora:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    self.features.retrieval_evaluate(self.write_corpus(records), 10)

    def test_legacy_dataset_keeps_v1_format_and_call_contract(self):
        corpus = self.write_corpus([{
            "id": "case-1",
            "query": "current summary trigger policy",
            "expected_message_ids": ["m-policy-current"],
        }])

        result = self.features.retrieval_evaluate(corpus, 10)

        self.assertEqual("memory-wuxian-retrieval-evaluation-v1", result["format"])
        self.assertEqual(
            [("current summary trigger policy", "historical")], self.store.calls
        )


if __name__ == "__main__":
    unittest.main()
