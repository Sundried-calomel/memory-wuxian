import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class V211ReleaseContractTest(unittest.TestCase):
    def test_mw211_01_continuous_catchup_contract_is_versioned(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.11.1")
        contract = json.loads((ROOT / "docs/work-contracts/v2.11.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["work_item_id"], "memory-wuxian-v2.11.0")
        review = json.loads((ROOT / "docs/promotion-reviews/v2.11.0.json").read_text(encoding="utf-8"))
        self.assertEqual(review["disposition"], "no-candidate")
        self.assertIn("An upgrade may preserve or move the activation boundary earlier but can never move it later.", contract["invariants"])
        for relative in (
            "scripts/collector_activation.py",
            "scripts/maintenance_supervisor.py",
            "scripts/install_maintenance_supervisor.py",
            "scripts/semantic_plan.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        for readme in ("README.md", "README.zh-CN.md", "README.ja.md"):
            text = (ROOT / readme).read_text(encoding="utf-8")
            self.assertIn("collector-activation.json", text)
            self.assertIn("semantic_plan.py", text)
            self.assertIn("900,000", text)

    def test_mw211_02_incremental_sync_cannot_overwrite_global_coverage(self):
        source = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        batch = source.split("fn sync_batch_with_semantic_worker", 1)[1].split(
            "fn sync_batch(", 1
        )[0]
        self.assertNotIn("write_coverage_status", batch)
        self.assertEqual(source.count("store.write_coverage_status(&current_paths)?;"), 2)
        self.assertEqual(source.count("store.write_coverage_status(&scoped_paths)?;"), 2)


if __name__ == "__main__":
    unittest.main()
