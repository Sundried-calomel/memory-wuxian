import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_cli
import memory_retrieval_algorithms as algorithms


class MemoryRetrievalAlgorithmTests(unittest.TestCase):
    def test_stop_terms_and_facade_share_the_canonical_contract(self):
        self.assertIs(memory_cli.SEARCH_STOP_TERMS, algorithms.SEARCH_STOP_TERMS)
        self.assertEqual(["memory"], algorithms.search_terms("这个 Memory about"))

    def test_excerpt_compacts_whitespace_and_preserves_slice_behavior(self):
        cases = [
            ("  中文\n 日本語\t¥ ￥ 🙂  ", 240, "中文 日本語 ¥ ￥ 🙂"),
            ("abcdef", 3, "abc"),
            ("abcdef", 0, ""),
            ("abcdef", -1, "abcde"),
        ]
        for text, limit, expected in cases:
            with self.subTest(text=text, limit=limit):
                self.assertEqual(expected, algorithms.deterministic_excerpt(text, limit))
                self.assertEqual(expected, memory_cli.MemoryStore.deterministic_excerpt(text, limit))

    def test_normalization_preserves_nfkc_casefold_currency_and_emoji_behavior(self):
        text = "  ＡＢＣ\tStraße\n中文 日本語  ￥ ¥ 🙂  "
        expected = "abc strasse 中文 日本語 ¥ ¥ 🙂"
        self.assertEqual(expected, algorithms.normalize_search_text(text))
        self.assertEqual(expected, memory_cli.MemoryStore.normalize_search_text(text))
        with self.assertRaises(TypeError):
            algorithms.normalize_search_text(None)
        with self.assertRaises(TypeError):
            memory_cli.MemoryStore.normalize_search_text(None)

    def test_search_terms_freezes_ascii_order_cjk_ngrams_limits_and_empty_input(self):
        expected = ["abcdef", "中文测试", "中文测", "文测试", "中文", "文测", "测试"]
        self.assertEqual(expected, algorithms.search_terms("ABCDEF 中文测试"))
        self.assertEqual(expected, memory_cli.MemoryStore.search_terms("ABCDEF 中文测试"))
        self.assertEqual(expected[:3], algorithms.search_terms("ABCDEF 中文测试", 3))
        self.assertEqual([], algorithms.search_terms("", 128))
        self.assertEqual([], algorithms.search_terms("ABCDEF 中文测试", 0))
        self.assertEqual(expected[:-1], algorithms.search_terms("ABCDEF 中文测试", -1))

    def test_classmethod_facades_preserve_normalizer_overrides(self):
        class CustomStore(memory_cli.MemoryStore):
            @staticmethod
            def normalize_search_text(text):
                return "override"

        self.assertEqual(["override"], CustomStore.search_terms("ignored"))
        ranked = CustomStore.ranked_search(
            [{"sequence": 1, "text": "ignored"}],
            "override",
            ["override"],
            lambda record: record["text"],
        )
        self.assertTrue(ranked[0]["exact_match"])
        self.assertEqual(["override"], ranked[0]["matched_terms"])

    def test_ranked_search_freezes_score_exact_match_and_sequence_sorting(self):
        records = [
            {"sequence": 9, "text": "alpha beta"},
            {"sequence": 3, "text": "alpha beta"},
            {"source_start_sequence": 1, "text": "alpha"},
            {"sequence": 2, "text": "unrelated"},
        ]
        ranked = algorithms.ranked_search(
            records,
            "alpha beta",
            ["alpha", "beta"],
            lambda record: record["text"],
        )
        self.assertEqual([3, 9, 1], [
            item["record"].get("sequence", item["record"].get("source_start_sequence"))
            for item in ranked
        ])
        self.assertEqual([True, True, False], [item["exact_match"] for item in ranked])
        self.assertEqual([["alpha", "beta"], ["alpha", "beta"], ["alpha"]], [
            item["matched_terms"] for item in ranked
        ])
        expected_exact_score = (
            (1.0 + 5 / 4.0) * (1.0 + math.log(5 / 4))
            + (1.0 + 4 / 4.0) * (1.0 + math.log(5 / 3))
            + 1000.0
        )
        self.assertAlmostEqual(expected_exact_score, ranked[0]["score"])
        self.assertEqual(ranked, memory_cli.MemoryStore.ranked_search(
            records,
            "alpha beta",
            ["alpha", "beta"],
            lambda record: record["text"],
        ))

    def test_ranked_search_preserves_empty_query_input_and_exception_behavior(self):
        records = [{"sequence": 2, "text": "中文 ￥ 🙂"}]
        ranked = algorithms.ranked_search(records, "", [], lambda record: record["text"])
        self.assertEqual(1, len(ranked))
        self.assertTrue(ranked[0]["exact_match"])
        self.assertEqual(1000.0, ranked[0]["score"])
        self.assertEqual([], algorithms.ranked_search([], "x", ["x"], lambda record: "x"))
        with self.assertRaises(ValueError):
            algorithms.ranked_search(
                [{"sequence": "bad", "text": "x"}],
                "x",
                ["x"],
                lambda record: record["text"],
            )

    def test_strongest_matches_freezes_term_count_threshold_exact_and_limit(self):
        ranked = [
            {"score": 100.0, "matched_terms": ["a", "b"], "exact_match": False},
            {"score": 55.0, "matched_terms": ["a", "b"], "exact_match": False},
            {"score": 54.999, "matched_terms": ["a", "b", "c"], "exact_match": False},
            {"score": 70.0, "matched_terms": ["a"], "exact_match": False},
            {"score": 60.0, "matched_terms": [], "exact_match": True},
        ]
        expected = [ranked[0], ranked[4]]
        self.assertEqual(expected, algorithms.strongest_matches(ranked, 3, 10))
        self.assertEqual(expected[:1], algorithms.strongest_matches(ranked, 3, 1))
        self.assertEqual(ranked[:1], algorithms.strongest_matches(ranked[:2], 2, 10))
        self.assertEqual([], algorithms.strongest_matches([], 3, 10))
        self.assertEqual(expected, memory_cli.MemoryStore.strongest_matches(ranked, 3, 10))

    def test_unique_values_preserves_order_empty_values_limits_and_hash_errors(self):
        values = ["", "中文", "中文", "日本語", "¥", "￥", "🙂"]
        expected = ["中文", "日本語", "¥", "￥"]
        self.assertEqual(expected, algorithms.unique_values(values, 4))
        self.assertEqual(expected, memory_cli.MemoryStore.unique_values(values, 4))
        self.assertEqual(["中文"], algorithms.unique_values(values, 0))
        with self.assertRaises(TypeError):
            algorithms.unique_values([["unhashable"]], 1)


if __name__ == "__main__":
    unittest.main()
