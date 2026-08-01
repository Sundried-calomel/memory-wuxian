import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import install_maintenance_supervisor as scheduler


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
        self.assertNotIn("powershell", " ".join(command).lower())

    def test_macos_job_is_background_and_low_frequency(self):
        payload = scheduler.macos_plist(self.python, self.skill, self.archive)
        self.assertEqual(payload["StartInterval"], 300)
        self.assertEqual(payload["ProcessType"], "Background")
        self.assertFalse(payload["KeepAlive"])
        self.assertEqual(payload["ProgramArguments"][-1], "--once")

    def test_windows_job_requires_pythonw_and_is_hidden(self):
        payload = scheduler.windows_xml(self.python, self.skill, self.archive)
        root = ET.fromstring(payload)
        ns = {"t": scheduler.TASK_XML_NAMESPACE}
        self.assertEqual(root.findtext(".//t:Hidden", namespaces=ns), "true")
        self.assertEqual(root.findtext(".//t:Interval", namespaces=ns), "PT5M")
        self.assertEqual(root.findtext(".//t:Command", namespaces=ns), str(self.pythonw))
        self.assertIn("--once", root.findtext(".//t:Arguments", namespaces=ns))
        self.assertEqual(root.findtext(".//t:ExecutionTimeLimit", namespaces=ns), "PT70M")
        self.assertGreaterEqual(
            scheduler.WINDOWS_EXECUTION_LIMIT_SECONDS,
            scheduler.DEFAULT_MAXIMUM_SEMANTIC_JOBS * scheduler.SEMANTIC_JOB_TIMEOUT_SECONDS
            + scheduler.MAINTENANCE_CLOSEOUT_MARGIN_SECONDS,
        )

    def test_windows_job_fails_closed_without_pythonw(self):
        self.pythonw.unlink()
        with self.assertRaisesRegex(ValueError, "pythonw.exe is required"):
            scheduler.windows_xml(self.python, self.skill, self.archive)


if __name__ == "__main__":
    unittest.main()
