from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2125ReleaseContractTest(unittest.TestCase):
    def test_duplicate_pending_round_recovery_is_version_bound(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.14.0")
        contract = json.loads((ROOT / "docs/work-contracts/v2.12.5.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn("v2125-duplicate-pending-round-contract", contract["required_rehearsal_scenarios"])
        self.assertTrue(contract["defect_workbook"]["project_workbook_updated"])
        self.assertIn("MW-R05", contract["defect_workbook"]["applicable_families"])
        python_source = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        rust_source = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("if number <= 0:", python_source)
        self.assertIn("if number == 0 {", rust_source)


if __name__ == "__main__":
    unittest.main()
