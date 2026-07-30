import json
import subprocess
import sys
import tempfile
import types
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
                {
                    "creationflags": getattr(
                        subprocess, "CREATE_NO_WINDOW", 0x08000000
                    )
                },
            )

    def test_non_windows_background_calls_keep_default_process_flags(self):
        with patch.object(memory_dashboard.sys, "platform", "darwin"):
            self.assertEqual(memory_dashboard.background_subprocess_kwargs(), {})

    def test_windows_scheduler_status_does_not_spawn_a_process(self):
        fake_key = unittest.mock.MagicMock()
        fake_winreg = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=object(),
            OpenKey=unittest.mock.MagicMock(return_value=fake_key),
        )
        with (
            patch.object(memory_dashboard.sys, "platform", "win32"),
            patch.dict(sys.modules, {"winreg": fake_winreg}),
            patch.object(memory_dashboard.subprocess, "run") as run,
        ):
            status = memory_dashboard.cloud_scheduler_status()
        self.assertTrue(status["installed"])
        run.assert_not_called()

    def test_windows_process_probe_reads_exit_status_without_a_signal(self):
        kernel32 = types.SimpleNamespace(
            OpenProcess=unittest.mock.MagicMock(return_value=123),
            GetExitCodeProcess=unittest.mock.MagicMock(
                side_effect=lambda _handle, pointer: (
                    setattr(pointer._obj, "value", 259) or True
                )
            ),
            CloseHandle=unittest.mock.MagicMock(return_value=True),
        )
        self.assertTrue(memory_dashboard.windows_process_running(42, kernel32))
        kernel32.OpenProcess.assert_called_once_with(0x1000, False, 42)
        kernel32.CloseHandle.assert_called_once_with(123)

    def test_windows_collector_probe_never_calls_os_kill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            telemetry = root / "imports/codex/collector-telemetry.json"
            telemetry.parent.mkdir(parents=True)
            telemetry.write_text(
                json.dumps({"pid": 42, "fallback_interval_seconds": 5}),
                encoding="utf-8",
            )
            with (
                patch.object(memory_dashboard.sys, "platform", "win32"),
                patch.dict(sys.modules, {"psutil": None}),
                patch.object(
                    memory_dashboard, "windows_process_running", return_value=True
                ) as probe,
                patch.object(memory_dashboard.os, "kill") as kill,
            ):
                result = memory_dashboard.collector_telemetry(root)
        self.assertTrue(result["process_running"])
        probe.assert_called_once_with(42)
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
