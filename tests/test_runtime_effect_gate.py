import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml  # noqa: E402
from runtime_effect_gate import check_runtime_effects  # noqa: E402


class RuntimeEffectGateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.archive = self.base / "archive"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            "summaries:\n  higher_level_trigger_count: 2\n  maximum_summary_depth: 4\n"
            "backup:\n  enabled: false\n",
            encoding="utf-8",
        )
        self.store = MemoryStore(self.archive, load_simple_yaml(self.config))
        self.store.init()
        supervisor = self.archive / "maintenance/supervisor-state.json"
        supervisor.parent.mkdir(parents=True, exist_ok=True)
        supervisor.write_text(
            json.dumps({"status": "healthy"}),
            encoding="utf-8",
        )

    def test_clean_runtime_passes(self):
        result = check_runtime_effects(self.archive, self.config)
        self.assertEqual(result["status"], "pass")

    def test_hidden_effect_failures_are_independently_reported(self):
        index = self.archive / "indexes/conversations.jsonl"
        index.write_text('{"message_id":"missing"}\n', encoding="utf-8")
        backup = self.base / "backups"
        backup.mkdir()
        orphan = backup / ".2026-08-01_120000_000001.tmp-1234"
        orphan.mkdir()
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "backup:\n  enabled: false\n",
                f'backup:\n  enabled: true\n  directory: "{backup}"\n',
            ),
            encoding="utf-8",
        )
        job = self.archive / "maintenance/jobs/quarantined/job.json"
        job.parent.mkdir(parents=True)
        job.write_text(
            json.dumps({"job_id": "job-1", "state": "quarantined"}),
            encoding="utf-8",
        )
        supervisor = self.archive / "maintenance/supervisor-state.json"
        os.utime(supervisor, (time.time() - 3600, time.time() - 3600))

        result = check_runtime_effects(self.archive, self.config)
        codes = {item["code"] for item in result["failures"]}
        self.assertEqual(result["status"], "fail")
        self.assertIn("conversation-index-not-converged", codes)
        self.assertIn("incomplete-backup-residue", codes)
        self.assertIn("permanent-maintenance-debt", codes)
        self.assertIn("maintenance-supervisor-not-healthy", codes)

    def test_parent_backlog_is_catching_up_when_one_parent_job_is_pending(self):
        pending = self.archive / "pending/job-000001.json"
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(
            json.dumps({
                "job_id": "job-000001",
                "summary_level": 2,
                "source_signature": "conversation:codex:test:children:L1-1,L1-2",
            }),
            encoding="utf-8",
        )
        with patch(
            "runtime_effect_gate.semantic_parent_debt",
            return_value=[{"conversation_id": "codex:other", "source_level": 1}],
        ):
            result = check_runtime_effects(self.archive, self.config)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(len(result["observations"]["pending_parent_jobs"]), 1)

    def test_parent_backlog_fails_when_no_parent_job_is_pending(self):
        with patch(
            "runtime_effect_gate.semantic_parent_debt",
            return_value=[{"conversation_id": "codex:test", "source_level": 1}],
        ):
            result = check_runtime_effects(self.archive, self.config)

        self.assertIn(
            "semantic-parent-job-missing",
            {item["code"] for item in result["failures"]},
        )

    def test_windows_activation_fails_on_sandbox_profile_shortcut(self):
        skill = self.base / ".codex" / "skills" / "memory-wuxian"
        launcher = skill / "bin" / "memory-wuxian-dashboard-launcher.exe"
        icon = skill / "assets" / "memory-wuxian.ico"
        inspector = skill / "scripts" / "inspect_dashboard_shortcut_windows.ps1"
        for path in (launcher, icon, inspector):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test")
        python = self.base / "python.exe"
        python.write_bytes(b"test")
        launcher_config = self.base / "launcher.json"
        launcher_config.write_text(
            json.dumps({
                "python_executable": str(python),
                "archive_root": str(self.archive),
            }),
            encoding="utf-8",
        )
        shortcut_path = self.base / "Memory.lnk"
        shortcut_path.write_bytes(b"test")
        with patch(
            "runtime_effect_gate.inspect_windows_shortcut",
            return_value={
                "exists": True,
                "target_exists": True,
                "target": r"C:\Users\CodexSandboxOffline\.codex\skills\memory-wuxian\bin\memory-wuxian-dashboard-launcher.exe",
                "working_directory": r"C:\Users\CodexSandboxOffline\.codex\skills\memory-wuxian",
                "icon": r"C:\Users\CodexSandboxOffline\.codex\skills\memory-wuxian\assets\memory-wuxian.ico,0",
                "arguments": "",
            },
        ):
            result = check_runtime_effects(
                self.archive,
                self.config,
                skill_root=skill,
                launcher_config=launcher_config,
                windows_shortcut=shortcut_path,
            )

        self.assertIn(
            "windows-shortcut-activation-invalid",
            {item["code"] for item in result["failures"]},
        )

    def test_windows_activation_passes_only_for_exact_live_paths(self):
        skill = self.base / ".codex" / "skills" / "memory-wuxian"
        launcher = skill / "bin" / "memory-wuxian-dashboard-launcher.exe"
        icon = skill / "assets" / "memory-wuxian.ico"
        inspector = skill / "scripts" / "inspect_dashboard_shortcut_windows.ps1"
        for path in (launcher, icon, inspector):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test")
        python = self.base / "python.exe"
        python.write_bytes(b"test")
        launcher_config = self.base / "launcher.json"
        launcher_config.write_text(
            json.dumps({
                "python_executable": str(python),
                "archive_root": str(self.archive.resolve()),
            }),
            encoding="utf-8",
        )
        shortcut_path = self.base / "Memory.lnk"
        shortcut_path.write_bytes(b"test")
        with patch(
            "runtime_effect_gate.inspect_windows_shortcut",
            return_value={
                "exists": True,
                "target_exists": True,
                "target": str(launcher.resolve()),
                "working_directory": str(skill.resolve()),
                "icon": f"{icon.resolve()},0",
                "arguments": "",
            },
        ):
            result = check_runtime_effects(
                self.archive,
                self.config,
                skill_root=skill,
                launcher_config=launcher_config,
                windows_shortcut=shortcut_path,
            )

        self.assertEqual(result["status"], "pass")


if __name__ == "__main__":
    unittest.main()
