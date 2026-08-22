import json
import unittest
from pathlib import Path

from tests.support.release_contracts import assert_minimum_project_version


ROOT = Path(__file__).resolve().parents[1]


class V2123ReleaseContractTest(unittest.TestCase):
    def test_semantic_drain_hotfix_is_narrow_and_version_bound(self):
        assert_minimum_project_version(self, ROOT, (2, 14, 4))
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.12.3.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn(
            "v2123-semantic-drain-contract",
            contract["required_rehearsal_scenarios"],
        )

    def test_repairable_drift_and_integrity_block_are_distinct(self):
        source = (ROOT / "scripts/semantic_backfill.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_blocked = bool(integrity_issues)", source)
        self.assertIn(
            "scheduling_blocked = bool(integrity_issues or repairable_issues)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
