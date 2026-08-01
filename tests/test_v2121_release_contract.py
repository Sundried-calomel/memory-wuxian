import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2121ReleaseContractTest(unittest.TestCase):
    def test_patch_contract_is_targeted_and_version_bound(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(version, "2.12.3")
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.12.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn(
            "v2121-token-ledger-v1-upgrade-contract",
            contract["required_rehearsal_scenarios"],
        )

    def test_native_collector_rebuilds_instead_of_rejecting_v1_ledger(self):
        source = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        self.assertNotIn("unsupported token usage ledger format", source)
        self.assertIn("new_ledger()", source)
        self.assertIn("remove_file_if_present(&recovery_debt)?", source)
        regression = (ROOT / "tests/test_memory_cli.py").read_text(encoding="utf-8")
        self.assertIn('legacy_native_usage["format_version"] = 1', regression)
        self.assertIn('migrated_usage["daily_usage"]', regression)


if __name__ == "__main__":
    unittest.main()
