import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_documentation_version,
    assert_minimum_project_version,
    assert_readme_tokens,
    assert_source_tokens,
)


ROOT = Path(__file__).resolve().parents[1]


class V216ReleaseContractTests(unittest.TestCase):
    def test_version_documentation_and_contract_are_synchronized(self):
        version = assert_minimum_project_version(self, ROOT, (2, 16, 0))
        assert_documentation_version(self, ROOT, version)
        contract = json.loads((ROOT / "docs/work-contracts/v2.16.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["validation_profile"], "full")
        assert_readme_tokens(self, ROOT, ("2.16.0",))

    def test_lifecycle_gate_is_bounded_and_startup_authority_is_unique(self):
        assert_source_tokens(
            self,
            ROOT,
            "scripts/collector_lifecycle.py",
            present=("collector-startup-owner-count-invalid", "collector-watermark-not-converged"),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/install_codex_autosync_windows.py",
            absent=("HKCU_RUN_KEY",),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/collector_runtime_effect_gate.py",
            absent=("rglob(",),
        )


if __name__ == "__main__":
    unittest.main()
