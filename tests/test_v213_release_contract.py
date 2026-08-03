from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V213ReleaseContractTest(unittest.TestCase):
    def test_project_evidence_candidate_contract(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        native_version = tomllib.loads((ROOT / "native-collector/Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
        self.assertEqual(version, "2.14.4")
        self.assertEqual(native_version, version)
        contract = json.loads((ROOT / "docs/work-contracts/v2.13.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["owner_id"], "project-evidence-plane")
        self.assertEqual(contract["rollback_version"], "2.12.7")
        self.assertIn("old-stream-compatibility", contract["required_rehearsal_scenarios"])
        source = (ROOT / "scripts/memory_project_evidence.py").read_text(encoding="utf-8")
        self.assertIn('"project-evidence-v1"', source)
        self.assertIn("automatic_activation", source)


if __name__ == "__main__":
    unittest.main()
