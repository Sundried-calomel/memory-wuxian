import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_minimum_project_version,
    assert_source_tokens,
)


ROOT = Path(__file__).resolve().parents[1]


class V2141ReleaseContractTest(unittest.TestCase):
    def test_offline_macos_runtime_contract(self):
        assert_minimum_project_version(self, ROOT, (2, 14, 4))
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.14.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["owner_id"], "release-installation-plane")
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        assert_source_tokens(
            self,
            ROOT,
            "packaging/macos/build_pkg.sh",
            present=("vendor/yaml",),
        )
        assert_source_tokens(
            self,
            ROOT,
            "packaging/macos/scripts/postinstall",
            present=("--without-pip",),
            absent=("pip install", "--break-system-packages"),
        )
        assert_source_tokens(
            self,
            ROOT,
            ".github/workflows/release.yml",
            present=("Prepare offline PKG dependency source",),
        )


if __name__ == "__main__":
    unittest.main()
