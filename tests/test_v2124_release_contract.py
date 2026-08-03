import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2124ReleaseContractTest(unittest.TestCase):
    def test_sparse_payload_and_transaction_audit_repair_is_version_bound(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(version, "2.14.3")
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.12.4.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn("bundled-native-version", contract["required_rehearsal_scenarios"])
        self.assertEqual(
            contract["defect_workbook"]["applicable_families"],
            ["MW-R05", "MW-R07", "MW-R08", "MW-R10"],
        )
        self.assertTrue(contract["defect_workbook"]["project_workbook_updated"])

    def test_lossless_presence_and_heartbeat_lock_remain_hard_gates(self):
        worker = (ROOT / "scripts/semantic_worker.py").read_text(encoding="utf-8")
        cli = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        self.assertIn('"presence": [[column in item', worker)
        self.assertIn('with exclusive_lock(self.locks_dir / "archive.lock")', cli)


if __name__ == "__main__":
    unittest.main()
