import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V216ReleaseContractTests(unittest.TestCase):
    def test_version_documentation_and_contract_are_synchronized(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 16, 0))
        documentation = json.loads((ROOT / "docs/documentation-contract.json").read_text(encoding="utf-8"))
        self.assertEqual(documentation["reviewed_version"], version)
        contract = json.loads((ROOT / "docs/work-contracts/v2.16.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["validation_profile"], "full")
        for readme in ("README.md", "README.zh-CN.md", "README.ja.md"):
            text = (ROOT / readme).read_text(encoding="utf-8")
            self.assertIn("2.16.0", text)

    def test_lifecycle_gate_is_bounded_and_startup_authority_is_unique(self):
        lifecycle = (ROOT / "scripts/collector_lifecycle.py").read_text(encoding="utf-8")
        windows = (ROOT / "scripts/install_codex_autosync_windows.py").read_text(encoding="utf-8")
        runtime_gate = (ROOT / "scripts/runtime_effect_gate.py").read_text(encoding="utf-8")
        self.assertIn("collector-startup-owner-count-invalid", lifecycle)
        self.assertIn("collector-watermark-not-converged", lifecycle)
        self.assertNotIn("HKCU_RUN_KEY", windows)
        self.assertNotIn("rglob(", runtime_gate)


if __name__ == "__main__":
    unittest.main()
