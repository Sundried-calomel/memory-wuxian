import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_documentation_version,
    assert_minimum_project_version,
    assert_readme_tokens,
)


ROOT = Path(__file__).resolve().parents[1]


class V219ReleaseContractTests(unittest.TestCase):
    def test_release_version_and_documentation_remain_compatible(self):
        version = assert_minimum_project_version(self, ROOT, (2, 19, 1))
        assert_documentation_version(self, ROOT, version)
        assert_readme_tokens(self, ROOT, ("2.19.1", "2.19.0", "R1-R8"))

    def test_release_contract_preserves_data_and_owner_boundaries(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.19.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "full")
        joined = "\n".join(contract["invariants"] + contract["prohibited_changes"])
        for token in (
            "Raw messages",
            "device identities",
            "summary Markdown",
            "failed artifact",
            "Federation event sequences",
            "Malformed rollout quarantine",
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
