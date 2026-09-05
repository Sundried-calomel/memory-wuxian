import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_readme_tokens,
)


ROOT = Path(__file__).resolve().parents[1]


class V219ReleaseContractTests(unittest.TestCase):
    def test_v219_release_remains_documented_after_patch_versions(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.19.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "full")
        assert_readme_tokens(self, ROOT, ("2.19.1", "R1-R8"))

    def test_release_contract_preserves_data_and_owner_boundaries(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.19.0.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "full")
        joined = "\n".join(contract["invariants"] + contract["prohibited_changes"])
        for token in (
            "Raw archives",
            "archive-root pointer",
            "Summary V2 rescue",
            "canonical owner",
            "additive bypass",
            "regress an earlier passing case",
        ):
            self.assertIn(token, joined)

    def test_release_pipeline_binds_candidate_ci_and_cross_platform_installers(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for token in (
            "Require successful candidate CI for this commit",
            "macos-installer",
            "windows-installer",
            "Build and sign governed update metadata",
            "target_commitish: ${{ github.sha }}",
        ):
            self.assertIn(token, release)


if __name__ == "__main__":
    unittest.main()
