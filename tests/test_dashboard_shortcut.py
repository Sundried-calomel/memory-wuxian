import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_dashboard_app_macos as installer
from scripts.install_dashboard_app_macos import launcher_payload


SKILL_ROOT = Path(__file__).resolve().parent.parent


class DashboardShortcutTest(unittest.TestCase):
    def test_native_launcher_uses_an_os_assigned_port(self):
        source = (
            SKILL_ROOT
            / "native-collector"
            / "src"
            / "bin"
            / "memory-wuxian-dashboard-launcher.rs"
        ).read_text(encoding="utf-8")
        self.assertIn('.arg("--port")', source)
        self.assertIn('.arg("0")', source)
        self.assertNotIn('.arg("8765")', source)

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
        self.assertIn("ProfileList\\$currentSid", install)
        self.assertIn("$SkillRoot = $expectedSkillRoot", install)
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

    def test_macos_installer_rebuilds_versioned_dashboard(self):
        postinstall = (
            SKILL_ROOT / "packaging/macos/scripts/postinstall"
        ).read_text(encoding="utf-8")
        package = (
            SKILL_ROOT / "packaging/macos/build_pkg.sh"
        ).read_text(encoding="utf-8")
        dashboard_build = (
            SKILL_ROOT / "packaging/macos/build_dashboard_app.sh"
        ).read_text(encoding="utf-8")
        workflow = (
            SKILL_ROOT / ".github/workflows/release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("memory-wuxian-active-root.txt", postinstall)
        self.assertIn("preserved_archive_root", postinstall)
        transaction = (
            SKILL_ROOT / "scripts/install_macos_transaction.py"
        ).read_text(encoding="utf-8")
        self.assertIn("install_macos_transaction.py", postinstall)
        self.assertIn("install_dashboard_app_macos.py", transaction)
        self.assertIn("probe_candidate", transaction)
        self.assertIn("wait_for_collector", transaction)
        self.assertIn("build_dashboard_app.sh", package)
        self.assertIn("CFBundleShortVersionString", dashboard_build)
        self.assertIn("MemoryWuxianProductVersion", dashboard_build)
        self.assertIn("MemoryWuxianProductVersion", package)
        self.assertIn('package_version="$version"', package)
        self.assertIn("pkgbuild --analyze", package)
        self.assertIn("BundleIsRelocatable false", package)
        self.assertIn('--component-plist "$component_plist"', package)
        self.assertIn("Unexpected macOS package bundle path", package)
        self.assertIn("Memory無限操作台.app/Contents/MacOS/MemoryDashboard", workflow)

    def test_macos_dashboard_is_config_driven_and_self_checkable(self):
        source = (
            SKILL_ROOT / "packaging/macos/MemoryDashboard.swift"
        ).read_text(encoding="utf-8")
        installer = (
            SKILL_ROOT / "scripts/install_dashboard_app_macos.py"
        ).read_text(encoding="utf-8")
        self.assertIn("memory-wuxian-dashboard-launcher.json", source)
        self.assertIn("MEMORY_WUXIAN_DASHBOARD_CONFIG", source)
        self.assertIn("--self-check", source)
        self.assertIn("--no-browser", source)
        self.assertNotIn("/opt/homebrew/bin/python3", source)
        self.assertNotIn("/Users/mayanyi", source)
        self.assertIn("replace_app", installer)
        self.assertIn("dashboard version mismatch", installer)
        self.assertIn("executable_sha256", installer)

    def test_macos_launcher_payload_uses_current_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python3"
            skill = root / "skill"
            archive = root / "archive"
            payload = launcher_payload(python, skill, archive)
        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "python_executable": str(python),
                "skill_root": str(skill),
                "archive_root": str(archive),
            },
        )

    def test_macos_dashboard_install_restores_all_artifacts_after_self_check_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.app"
            destination = root / "installed.app"
            executable = destination / "Contents/MacOS/MemoryDashboard"
            source.mkdir()
            destination.mkdir()
            (destination / "old-marker").write_text("old", encoding="utf-8")
            config = root / "launcher.json"
            installation = root / "installation.json"
            config.write_text('{"version":"old"}\n', encoding="utf-8")
            installation.write_text('{"version":"old"}\n', encoding="utf-8")

            def synthetic_replace(_source, target):
                (target / "Contents/MacOS").mkdir(parents=True)
                (target / "Contents/MacOS/MemoryDashboard").write_text(
                    "new", encoding="utf-8"
                )

            with (
                patch.object(installer, "replace_app", side_effect=synthetic_replace),
                patch.object(
                    installer,
                    "app_metadata",
                    return_value=(executable, "2.4.5"),
                ),
                patch.object(installer, "sha256", return_value="abc"),
                patch.object(
                    installer,
                    "verify",
                    side_effect=RuntimeError("synthetic self-check failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic self-check failure"):
                    installer.install(
                        source=source,
                        destination=destination,
                        config_path=config,
                        installation_path=installation,
                        python_executable=root / "python3",
                        skill_root=root / "skill",
                        archive_root=root / "archive",
                        version="2.4.5",
                    )

            self.assertEqual((destination / "old-marker").read_text(), "old")
            self.assertEqual(json.loads(config.read_text()), {"version": "old"})
            self.assertEqual(json.loads(installation.read_text()), {"version": "old"})

    def test_macos_dashboard_replace_uses_in_place_ditto_when_desktop_rename_is_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.app"
            destination = root / "destination.app"
            for app, marker in ((source, "new"), (destination, "old")):
                executable = app / "Contents/MacOS/MemoryDashboard"
                executable.parent.mkdir(parents=True)
                executable.write_text(marker, encoding="utf-8")

            def synthetic_run(command, **kwargs):
                if command[0] == "/usr/bin/ditto":
                    shutil.copytree(
                        Path(command[1]), Path(command[2]), dirs_exist_ok=True
                    )
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch.object(installer.os, "replace", side_effect=PermissionError(13)),
                patch.object(installer.subprocess, "run", side_effect=synthetic_run),
            ):
                installer.replace_app(source, destination)

            self.assertEqual(
                (destination / "Contents/MacOS/MemoryDashboard").read_text(),
                "new",
            )


if __name__ == "__main__":
    unittest.main()
