import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2141ReleaseContractTest(unittest.TestCase):
    def test_offline_macos_runtime_contract(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(version, "2.14.3")
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.14.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["owner_id"], "release-installation-plane")
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        postinstall = (ROOT / "packaging/macos/scripts/postinstall").read_text(
            encoding="utf-8"
        )
        builder = (ROOT / "packaging/macos/build_pkg.sh").read_text(
            encoding="utf-8"
        )
        workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("vendor/yaml", builder)
        self.assertIn("--without-pip", postinstall)
        self.assertIn("Prepare offline PKG dependency source", workflow)
        self.assertNotIn("pip install", postinstall)
        self.assertNotIn("--break-system-packages", postinstall)


if __name__ == "__main__":
    unittest.main()
