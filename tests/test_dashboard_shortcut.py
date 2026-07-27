import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent


class DashboardShortcutTest(unittest.TestCase):
    def test_shortcut_installer_is_atomic_and_uses_current_paths(self):
        script = (SKILL_ROOT / "scripts/install_dashboard_shortcut_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[char]0x65E0", script)
        self.assertIn("[char]0x72B6", script)
        self.assertIn("pythonw.exe", script)
        self.assertIn("memory_dashboard.py", script)
        self.assertIn("--port 8765 --window", script)
        self.assertIn("[IO.File]::Replace($temporaryPath, $shortcutPath, $backupPath)", script)
        self.assertIn("[IO.File]::Move($temporaryPath, $shortcutPath)", script)
        self.assertIn("[IO.File]::Delete($shortcutPath)", script)

    def test_installer_preserves_active_root_and_rebuilds_shortcut(self):
        install = (SKILL_ROOT / "packaging/windows/install.ps1").read_text(encoding="utf-8")
        self.assertIn("memory-wuxian-active-root.txt", install)
        self.assertIn("install_dashboard_shortcut_windows.ps1", install)
        self.assertIn("-ArchiveRoot $archiveRoot", install)


if __name__ == "__main__":
    unittest.main()
