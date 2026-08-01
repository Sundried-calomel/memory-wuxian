import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V2122ReleaseContractTest(unittest.TestCase):
    def test_targeted_patch_contract_is_version_bound(self):
        version = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        self.assertEqual(version, "2.12.4")
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.12.2.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        self.assertIn(
            "v2122-dashboard-cursor-summary-job-contract",
            contract["required_rehearsal_scenarios"],
        )

    def test_dashboard_cursor_and_parent_job_guards_are_present(self):
        dashboard = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
        self.assertIn(".bar.local{left:0;right:0", dashboard)
        self.assertIn("font-weight:700", dashboard)
        self.assertIn("bottom:2px", dashboard)
        collector = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        self.assertIn("message_last_line", collector)
        cli = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        self.assertIn("pending_children", cli)
        self.assertIn("repair_overlapping_pending_parent_jobs", cli)
        self.assertIn('"raw_archive_modified": False', cli)


if __name__ == "__main__":
    unittest.main()
