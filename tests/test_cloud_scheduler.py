import base64
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import install_cloud_sync as scheduler


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.task_xml = None

    def __call__(self, arguments, **kwargs):
        command = [str(item) for item in arguments]
        self.calls.append((command, kwargs))
        if "/XML" in command:
            xml_path = Path(command[command.index("/XML") + 1])
            self.task_xml = xml_path.read_bytes()
        return subprocess.CompletedProcess(command, 0, "", "")


class CloudSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = self.base / "User Home"
        self.archive = self.home / "Documents" / "Memory無限 Archive"
        self.skill = self.home / ".codex" / "skills" / "memory-wuxian"
        self.python = self.home / "Runtime With Spaces" / "python executable"
        self.archive.mkdir(parents=True)
        (self.skill / "scripts").mkdir(parents=True)
        self.python.parent.mkdir(parents=True)
        self.python.write_text("", encoding="utf-8")
        (self.skill / "config.yaml").write_text("cloud_transport:\n  enabled: false\n")
        (self.skill / "scripts" / "memory_cli.py").write_text("# test CLI\n")

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, *extra):
        return [
            "--archive-root",
            str(self.archive),
            "--skill-root",
            str(self.skill),
            "--python-executable",
            str(self.python),
            *extra,
        ]

    def test_macos_plist_is_one_shot_and_uses_exact_paths(self):
        runner = FakeRunner()
        with patch("pathlib.Path.home", return_value=self.home):
            scheduler.main(self.args("--load"), platform_name="darwin", runner=runner)

        plist_path = (
            self.home
            / "Library"
            / "LaunchAgents"
            / f"{scheduler.MACOS_LABEL}.plist"
        )
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
        self.assertEqual(payload["Label"], scheduler.MACOS_LABEL)
        self.assertEqual(payload["StartInterval"], 300)
        self.assertTrue(payload["RunAtLoad"])
        self.assertFalse(payload["KeepAlive"])
        self.assertEqual(
            payload["ProgramArguments"],
            [
                str(self.python.resolve()),
                str((self.skill / "scripts" / "memory_cli.py").resolve()),
                "--root",
                str(self.archive.resolve()),
                "--config",
                str((self.skill / "config.yaml").resolve()),
                "cloud-sync",
            ],
        )
        self.assertEqual(
            payload["StandardOutPath"],
            str(self.archive.resolve() / "federation" / "cloud-sync.stdout.log"),
        )
        self.assertEqual(
            payload["StandardErrorPath"],
            str(self.archive.resolve() / "federation" / "cloud-sync.stderr.log"),
        )
        self.assertEqual(runner.calls[-1][0][1], "bootstrap")

    def test_macos_install_is_idempotent_and_uninstall_preserves_data(self):
        runner = FakeRunner()
        cloud_file = self.archive / "federation" / "cloud-envelope.mwxe"
        key_file = self.archive / "federation" / "node-key"
        cloud_file.parent.mkdir(parents=True, exist_ok=True)
        cloud_file.write_text("ciphertext", encoding="utf-8")
        key_file.write_text("key", encoding="utf-8")
        with patch("pathlib.Path.home", return_value=self.home):
            scheduler.main(self.args(), platform_name="darwin", runner=runner)
            plist_path = (
                self.home
                / "Library"
                / "LaunchAgents"
                / f"{scheduler.MACOS_LABEL}.plist"
            )
            first = plist_path.read_bytes()
            scheduler.main(self.args(), platform_name="darwin", runner=runner)
            self.assertEqual(plist_path.read_bytes(), first)
            scheduler.main(
                self.args("--uninstall"),
                platform_name="darwin",
                runner=runner,
            )
        self.assertFalse(plist_path.exists())
        self.assertEqual(cloud_file.read_text(encoding="utf-8"), "ciphertext")
        self.assertEqual(key_file.read_text(encoding="utf-8"), "key")

    def test_windows_task_uses_current_user_five_minutes_and_ignore_new(self):
        runner = FakeRunner()
        environment = {
            **os.environ,
            "SystemRoot": r"C:\Windows",
            "USERDOMAIN": "LAB",
            "USERNAME": "Researcher",
        }
        with patch.dict(os.environ, environment, clear=True):
            scheduler.main(self.args("--load"), platform_name="win32", runner=runner)

        self.assertIsNotNone(runner.task_xml)
        root = ET.fromstring(runner.task_xml)
        namespace = {"t": scheduler.TASK_XML_NAMESPACE}
        self.assertEqual(
            root.findtext(".//t:Repetition/t:Interval", namespaces=namespace),
            "PT5M",
        )
        self.assertEqual(
            root.findtext(".//t:MultipleInstancesPolicy", namespaces=namespace),
            "IgnoreNew",
        )
        self.assertEqual(
            root.findtext(".//t:Principal/t:UserId", namespaces=namespace),
            r"LAB\Researcher",
        )
        self.assertEqual(
            root.findtext(".//t:Principal/t:LogonType", namespaces=namespace),
            "InteractiveToken",
        )
        self.assertTrue(
            root.findtext(".//t:Exec/t:Command", namespaces=namespace).endswith(
                r"System32\WindowsPowerShell\v1.0\powershell.exe"
            )
        )
        create_call = next(call for call, _ in runner.calls if "/Create" in call)
        run_call = next(call for call, _ in runner.calls if "/Run" in call)
        self.assertIn(scheduler.WINDOWS_TASK_NAME, create_call)
        self.assertIn(scheduler.WINDOWS_TASK_NAME, run_call)

    def test_windows_wrapper_quotes_exact_paths_and_invokes_cloud_sync(self):
        runner = FakeRunner()
        with patch.dict(
            os.environ,
            {"SystemRoot": r"C:\Windows", "USERNAME": "Researcher"},
            clear=True,
        ):
            scheduler.main(self.args(), platform_name="win32", runner=runner)

        wrapper = self.archive / "federation" / "run-cloud-sync.ps1"
        raw_wrapper = wrapper.read_bytes()
        self.assertTrue(raw_wrapper.startswith(b"\xef\xbb\xbf"))
        text = raw_wrapper.decode("utf-8-sig")
        self.assertIn(scheduler.powershell_quote(self.python.resolve()), text)
        self.assertIn(
            scheduler.powershell_quote(
                (self.skill / "scripts" / "memory_cli.py").resolve()
            ),
            text,
        )
        self.assertIn(
            f"{scheduler.powershell_quote('--root')} "
            f"{scheduler.powershell_quote(self.archive.resolve())}",
            text,
        )
        self.assertIn(
            f"{scheduler.powershell_quote('--config')} "
            f"{scheduler.powershell_quote((self.skill / 'config.yaml').resolve())}",
            text,
        )
        self.assertIn("'cloud-sync'", text)
        self.assertNotIn("Get-Command", text)
        task_arguments = ET.fromstring(runner.task_xml).findtext(
            f".//{{{scheduler.TASK_XML_NAMESPACE}}}Arguments"
        )
        encoded = task_arguments.split("-EncodedCommand ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-16le")
        self.assertIn(scheduler.powershell_quote(wrapper.resolve()), decoded)

    def test_windows_uninstall_is_idempotent_and_preserves_data(self):
        runner = FakeRunner()
        cloud_file = self.archive / "federation" / "cloud-envelope.mwxe"
        identity = self.archive / "federation" / "node-identity.json"
        cloud_file.parent.mkdir(parents=True, exist_ok=True)
        cloud_file.write_text("ciphertext", encoding="utf-8")
        identity.write_text("identity", encoding="utf-8")
        with patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}, clear=True):
            scheduler.main(
                self.args("--uninstall"),
                platform_name="win32",
                runner=runner,
            )
            scheduler.main(
                self.args("--uninstall"),
                platform_name="win32",
                runner=runner,
            )
        delete_calls = [call for call, _ in runner.calls if "/Delete" in call]
        self.assertEqual(len(delete_calls), 2)
        self.assertTrue(all(scheduler.WINDOWS_TASK_NAME in call for call in delete_calls))
        self.assertEqual(cloud_file.read_text(encoding="utf-8"), "ciphertext")
        self.assertEqual(identity.read_text(encoding="utf-8"), "identity")

    def test_main_installers_do_not_enable_cloud_by_default(self):
        windows_install = (
            SKILL_ROOT / "packaging" / "windows" / "install.ps1"
        ).read_text(encoding="utf-8")
        windows_uninstall = (
            SKILL_ROOT / "packaging" / "windows" / "uninstall.ps1"
        ).read_text(encoding="utf-8")
        mac_postinstall = (
            SKILL_ROOT / "packaging" / "macos" / "scripts" / "postinstall"
        ).read_text(encoding="utf-8")
        self.assertNotIn("install_cloud_sync.py", windows_install)
        self.assertNotIn(scheduler.WINDOWS_TASK_NAME, windows_install)
        self.assertNotIn("install_cloud_sync.py", mac_postinstall)
        self.assertNotIn(scheduler.MACOS_LABEL, mac_postinstall)
        self.assertIn(scheduler.WINDOWS_TASK_NAME, windows_uninstall)
        self.assertNotIn("Remove-Item", windows_uninstall)


if __name__ == "__main__":
    unittest.main()
