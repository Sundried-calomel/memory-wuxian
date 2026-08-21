from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.support.release_contracts import assert_minimum_project_version


ROOT = Path(__file__).resolve().parents[1]


class V2127ReleaseContractTest(unittest.TestCase):
    def test_live_paths_wait_for_last_shared_conversation(self) -> None:
        assert_minimum_project_version(self, ROOT, (2, 14, 4))
        contract = json.loads((ROOT / "docs/work-contracts/v2.12.7.json").read_text(encoding="utf-8"))
        self.assertIn("v2127-live-shared-round-contract", contract["required_rehearsal_scenarios"])
        python_source = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        rust_source = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        self.assertIn("shared_pending = any(", python_source)
        self.assertIn("let shared_pending = pending_rounds.iter().any", rust_source)


if __name__ == "__main__":
    unittest.main()
