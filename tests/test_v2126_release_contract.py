from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2126ReleaseContractTest(unittest.TestCase):
    def test_shared_round_requires_all_conversations_to_finish(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.14.4")
        contract = json.loads((ROOT / "docs/work-contracts/v2.12.6.json").read_text(encoding="utf-8"))
        self.assertIn("v2126-shared-round-completion-contract", contract["required_rehearsal_scenarios"])
        source = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        self.assertIn("user_conversations.issubset(final_conversations)", source)


if __name__ == "__main__":
    unittest.main()
