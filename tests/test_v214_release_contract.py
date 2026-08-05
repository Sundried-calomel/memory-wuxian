import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V214ReleaseContractTest(unittest.TestCase):
    def test_version_owner_contract_and_surfaces(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 14, 4))
        contract = json.loads((ROOT / "docs/work-contracts/v2.14.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["owner_id"], "project-evidence-plane")
        self.assertEqual(contract["rollback_version"], "2.13.0")
        self.assertIn("no-change-zero-write", contract["required_rehearsal_scenarios"])
        cli = (ROOT / "scripts/memory_cli.py").read_text(encoding="utf-8")
        dashboard = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
        supervisor = (ROOT / "scripts/maintenance_supervisor.py").read_text(encoding="utf-8")
        for command in ("project-evidence-owner-register", "project-evidence-owner-refresh", "project-evidence-owner-status"):
            self.assertIn(command, cli)
        self.assertIn("refreshEvidenceOwners", dashboard)
        self.assertIn("refresh_owners", supervisor)

    def test_cross_platform_surfaces_remain_registered(self):
        contract = json.loads((ROOT / "docs/work-contracts/v2.14.0.json").read_text(encoding="utf-8"))
        self.assertIn("windows-installer-and-dashboard", contract["required_rehearsal_scenarios"])
        self.assertTrue((ROOT / "scripts/install_codex_autosync_windows.py").is_file())
        self.assertTrue((ROOT / "scripts/install_macos_transaction.py").is_file())
        dashboard = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
        self.assertIn("evidence-owner-list", dashboard)
        self.assertIn("refreshEvidenceOwners", dashboard)


if __name__ == "__main__":
    unittest.main()
