import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2143ReleaseContractTest(unittest.TestCase):
    def test_semantic_maintenance_contract_matches_product_version(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        native_version = tomllib.loads(
            (ROOT / "native-collector/Cargo.toml").read_text(encoding="utf-8")
        )["package"]["version"]
        self.assertEqual(version, "2.14.3")
        self.assertEqual(native_version, version)
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.14.3.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["work_item_id"], "memory-wuxian-v2.14.3")
        self.assertEqual(contract["rollback_version"], "2.14.2")
        self.assertIn("python-regressions", contract["required_rehearsal_scenarios"])


if __name__ == "__main__":
    unittest.main()
