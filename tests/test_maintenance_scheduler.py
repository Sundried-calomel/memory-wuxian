import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import install_maintenance_supervisor as scheduler
import maintenance_supervisor as supervisor


class MaintenanceSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.archive = self.base / "archive"
        self.skill = self.base / "skill"
        self.python = self.base / "python.exe"
        self.pythonw = self.base / "pythonw.exe"
        self.archive.mkdir()
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "config.yaml").write_text("backup:\n  enabled: false\n", encoding="utf-8")
        (self.skill / "scripts" / "maintenance_supervisor.py").write_text("", encoding="utf-8")
        self.python.write_bytes(b"")
        self.pythonw.write_bytes(b"")

    def tearDown(self):
        self.temp.cleanup()

    def test_command_is_bounded_one_shot(self):
        command = scheduler.maintenance_command(self.python, self.skill, self.archive)
        self.assertEqual(command[-1], "--once")
        batch_index = command.index("--max-semantic-jobs")
        self.assertEqual(command[batch_index + 1], "8")
        self.assertNotIn("powershell", " ".join(command).lower())

    def test_supervisor_hard_limits_batch_to_eight(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 8"):
            supervisor.run_supervisor_tick(
                self.archive,
                self.skill / "config.yaml",
                maximum_semantic_jobs=9,
            )

    def test_supervisor_lock_serializes_overlapping_ticks(self):
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()

        def backfill(*_args, **_kwargs):
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1
            return {
                "status": "completed",
                "remaining_pending_jobs": 0,
                "completed_jobs": 0,
                "scheduled_summary_jobs": [],
                "skipped": [],
            }

        with (
            patch("maintenance_supervisor.run_backfill", side_effect=backfill),
            patch("maintenance_supervisor.ProjectEvidenceStore.refresh_owners", return_value={}),
        ):
            threads = [
                threading.Thread(
                    target=supervisor.run_supervisor_tick,
                    args=(self.archive, self.skill / "config.yaml"),
                )
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(maximum_active, 1)

    def test_macos_job_is_background_and_low_frequency(self):
        payload = scheduler.macos_plist(self.python, self.skill, self.archive)
        self.assertEqual(payload["StartInterval"], 300)
        self.assertEqual(payload["ProcessType"], "Background")
        self.assertFalse(payload["KeepAlive"])
        self.assertEqual(payload["ProgramArguments"][-1], "--once")
        batch_index = payload["ProgramArguments"].index("--max-semantic-jobs")
        self.assertEqual(payload["ProgramArguments"][batch_index + 1], "8")

    def test_windows_job_requires_pythonw_and_is_hidden(self):
        payload = scheduler.windows_xml(self.python, self.skill, self.archive)
        root = ET.fromstring(payload)
        ns = {"t": scheduler.TASK_XML_NAMESPACE}
        self.assertEqual(root.findtext(".//t:Hidden", namespaces=ns), "true")
        self.assertEqual(root.findtext(".//t:Interval", namespaces=ns), "PT5M")
        self.assertEqual(root.findtext(".//t:Command", namespaces=ns), str(self.pythonw))
        self.assertIn("--once", root.findtext(".//t:Arguments", namespaces=ns))
        self.assertEqual(root.findtext(".//t:ExecutionTimeLimit", namespaces=ns), "PT130M")
        self.assertGreaterEqual(
            scheduler.WINDOWS_EXECUTION_LIMIT_SECONDS,
            scheduler.DEFAULT_MAXIMUM_SEMANTIC_JOBS * scheduler.SEMANTIC_JOB_TIMEOUT_SECONDS
            + scheduler.MAINTENANCE_CLOSEOUT_MARGIN_SECONDS,
        )

    def test_windows_job_fails_closed_without_pythonw(self):
        self.pythonw.unlink()
        with self.assertRaisesRegex(ValueError, "pythonw.exe is required"):
            scheduler.windows_xml(self.python, self.skill, self.archive)

    def test_legacy_macos_launcher_retirement_does_not_touch_archive(self):
        home = self.base / "home"
        plist = home / "Library" / "LaunchAgents" / (
            f"{scheduler.LEGACY_MACOS_LABEL}.plist"
        )
        plist.parent.mkdir(parents=True)
        plist.write_bytes(b"legacy")
        archive_marker = self.archive / "keep.txt"
        archive_marker.write_text("keep", encoding="utf-8")
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))

        with patch.object(scheduler, "launchctl_domain", return_value="gui/501"):
            result = scheduler.retire_legacy_macos_semantic_backfill(
                runner=runner,
                home=home,
            )

        self.assertEqual(result["status"], "retired")
        self.assertFalse(plist.exists())
        self.assertEqual(archive_marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(runner.call_args.args[0][:3], ["/bin/launchctl", "bootout", "gui/501"])


if __name__ == "__main__":
    unittest.main()
