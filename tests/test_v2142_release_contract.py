import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2142ReleaseContractTest(unittest.TestCase):
    def test_release_contract_matches_product_version(self):
        version = next(
            line.split('"')[1]
            for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version = ")
        )
        self.assertEqual(version, "2.14.3")
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.14.2.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["work_item_id"], "memory-wuxian-v2.14.2")


if __name__ == "__main__":
    unittest.main()
