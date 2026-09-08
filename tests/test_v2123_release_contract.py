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

    def test_v2123_historical_contract_remains_recorded(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.12.3.json").read_text(encoding="utf-8")
        )
        self.assertIn("Raw-integrity failures fail closed", contract["primary_semantics"])
        self.assertIn(
            "Repairable projection drift cannot be reported as healthy",
            "\n".join(contract["invariants"]),
        )


if __name__ == "__main__":
    unittest.main()
