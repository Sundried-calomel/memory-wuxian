import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_documentation_version,
    assert_readme_tokens,
    project_version,
)


ROOT = Path(__file__).resolve().parents[1]


class V2192ReleaseContractTests(unittest.TestCase):
    def test_exact_patch_version_and_documentation_are_synchronized(self):
        version = project_version(ROOT)
        self.assertEqual(version, "2.19.2")
        self.assertEqual(
            (ROOT / "native-collector/Cargo.toml").read_text(encoding="utf-8").count(
                'version = "2.19.2"'
            ),
            1,
        )
        assert_documentation_version(self, ROOT, version)
        assert_readme_tokens(self, ROOT, ("2.19.2",))

    def test_patch_contract_is_narrow_and_preserves_raw_authority(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.19.2.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn(
            "v2192-integrity-isolation-contract",
            contract["required_rehearsal_scenarios"],
        )
        joined = "\n".join(contract["invariants"] + contract["prohibited_changes"])
        for token in (
            "Raw history",
            "new summary jobs",
            "source SHA-256",
            "quarantined",
        ):
            self.assertIn(token, joined)


if __name__ == "__main__":
    unittest.main()
