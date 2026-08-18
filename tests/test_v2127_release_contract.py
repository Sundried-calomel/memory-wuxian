from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2127ReleaseContractTest(unittest.TestCase):
    def test_live_paths_wait_for_last_shared_conversation(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 14, 4))
        contract = json.loads((ROOT / "docs/work-contracts/v2.12.7.json").read_text(encoding="utf-8"))
        self.assertIn("v2127-live-shared-round-contract", contract["required_rehearsal_scenarios"])
        python_source = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        rust_source = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        self.assertIn("shared_pending = any(", python_source)
        self.assertIn("let shared_pending = pending_rounds.iter().any", rust_source)


if __name__ == "__main__":
    unittest.main()
