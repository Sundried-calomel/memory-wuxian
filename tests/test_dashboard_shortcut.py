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
        self.assertIn("memory-wuxian-dashboard-launcher.exe", script)
        self.assertIn('$shortcut.Arguments = ""', script)
        self.assertIn("memory-wuxian-dashboard-launcher.json", script)
        self.assertIn("[IO.File]::Replace($launcherConfigTemporary", script)
        self.assertNotIn("$shortcut.TargetPath = $pythonw", script)
        self.assertNotIn('" --root "', script)
        self.assertIn("[IO.File]::Replace($temporaryPath, $shortcutPath, $backupPath)", script)
        self.assertIn("[IO.File]::Move($temporaryPath, $shortcutPath)", script)
        self.assertIn("[IO.File]::Delete($shortcutPath)", script)

    def test_installer_preserves_active_root_and_rebuilds_shortcut(self):
        install = (SKILL_ROOT / "packaging/windows/install.ps1").read_text(encoding="utf-8")
        self.assertIn("memory-wuxian-active-root.txt", install)
        self.assertIn("install_dashboard_shortcut_windows.ps1", install)
        self.assertIn("-ArchiveRoot $archiveRoot", install)
        self.assertIn("$installedUserProfile = Split-Path -Parent $codexHome", install)
        self.assertIn("$sessionsRoot = Join-Path $codexHome", install)
        self.assertNotIn('Join-Path $env:USERPROFILE ".codex\\memory-wuxian-active-root.txt"', install)

    def test_native_launcher_is_packaged_for_windows(self):
        workflow = (SKILL_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        build = (SKILL_ROOT / "scripts/build_native_collector.ps1").read_text(encoding="utf-8")
        source = (
            SKILL_ROOT
            / "native-collector/src/bin/memory-wuxian-dashboard-launcher.rs"
        ).read_text(encoding="utf-8")
        self.assertIn("memory-wuxian-dashboard-launcher.exe", workflow)
        self.assertIn("memory-wuxian-dashboard-launcher.exe", build)
        self.assertIn('windows_subsystem = "windows"', source)
        self.assertIn("Command::new(python)", source)


if __name__ == "__main__":
    unittest.main()
