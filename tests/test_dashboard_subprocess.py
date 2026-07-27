import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import memory_dashboard


class DashboardSubprocessTest(unittest.TestCase):
    def test_windows_background_calls_never_create_a_console(self):
        with patch.object(memory_dashboard.sys, "platform", "win32"):
            self.assertEqual(
                memory_dashboard.background_subprocess_kwargs(),
                {"creationflags": subprocess.CREATE_NO_WINDOW},
            )

    def test_non_windows_background_calls_keep_default_process_flags(self):
        with patch.object(memory_dashboard.sys, "platform", "darwin"):
            self.assertEqual(memory_dashboard.background_subprocess_kwargs(), {})

    def test_windows_scheduler_status_does_not_spawn_a_process(self):
        fake_key = unittest.mock.MagicMock()
        with (
            patch.object(memory_dashboard.sys, "platform", "win32"),
            patch("winreg.OpenKey", return_value=fake_key),
            patch.object(memory_dashboard.subprocess, "run") as run,
        ):
            status = memory_dashboard.cloud_scheduler_status()
        self.assertTrue(status["installed"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
