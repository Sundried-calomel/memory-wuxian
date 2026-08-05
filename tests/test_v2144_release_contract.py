import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2144ReleaseContractTest(unittest.TestCase):
    def test_readonly_context_refresh_contract_matches_product_version(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        native_version = tomllib.loads(
            (ROOT / "native-collector/Cargo.toml").read_text(encoding="utf-8")
        )["package"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 14, 4))
        self.assertEqual(native_version, version)
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.14.4.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["work_item_id"], "memory-wuxian-v2.14.4")
        self.assertEqual(contract["rollback_version"], "2.14.3")
        self.assertIn("python-regressions", contract["required_rehearsal_scenarios"])

    def test_normal_skill_flow_requires_no_acknowledgement(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        implementation = (ROOT / "references/implementation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("require no acknowledgement", skill)
        self.assertIn("Never run `ack-context-refresh` as part of normal operation", skill)
        self.assertIn("deprecated no-op", implementation)


if __name__ == "__main__":
    unittest.main()
