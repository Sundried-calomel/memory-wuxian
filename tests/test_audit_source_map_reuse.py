import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from memory_cli import MemoryStore


class AuditSourceMapReuseTest(unittest.TestCase):
    def test_explicit_source_ids_reuse_audit_snapshot_without_rescanning(self):
        class NoScan(list):
            def __iter__(self):
                raise AssertionError("audit rescanned the complete raw snapshot")

        store = object.__new__(MemoryStore)
        records = [{"sequence": 1, "message_id": "one", "text": "exact"},
                   {"sequence": 2, "message_id": "two", "text": "source"}]
        summary = {"level": 1, "source_message_ids": ["one", "two"]}
        expected = store.actual_summary_source_sha256(summary, records)
        observed = store.actual_summary_source_sha256(
            summary, NoScan(), raw_by_id={record["message_id"]: record for record in records},
        )
        self.assertEqual(expected, observed)
        self.assertIsNone(store.actual_summary_source_sha256(summary, NoScan(), raw_by_id={}))
