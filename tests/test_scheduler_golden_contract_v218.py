from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import install_cloud_sync as cloud
import install_governance_ai as governance
import install_maintenance_supervisor as maintenance
import platform_scheduler


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "scheduler-golden-v218.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
NS = {"t": cloud.TASK_XML_NAMESPACE}


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls.fromisoformat(FIXTURE["fixed_start_boundary"])
        return value if tz is None else value.astimezone(tz)

    def astimezone(self, tz=None):
        return self if tz is None else super().astimezone(tz)


class GoldenWindowsPath(PureWindowsPath):
    def exists(self) -> bool:
        return True

    def is_file(self) -> bool:
        return True

    def mkdir(self, *args, **kwargs) -> None:
        return None

    def unlink(self, *args, **kwargs) -> None:
        return None


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def xml_values(payload: bytes) -> dict[str, str | None]:
    root = ET.fromstring(payload)
    find = lambda path: root.findtext(path, namespaces=NS)
    return {
        "start_boundary": find(".//t:StartBoundary"),
        "interval": find(".//t:Repetition/t:Interval"),
        "multiple_instances": find(".//t:MultipleInstancesPolicy"),
        "disallow_start_on_batteries": find(".//t:DisallowStartIfOnBatteries"),
        "stop_on_batteries": find(".//t:StopIfGoingOnBatteries"),
        "start_when_available": find(".//t:StartWhenAvailable"),
        "network_required": find(".//t:RunOnlyIfNetworkAvailable"),
        "hidden": find(".//t:Hidden"),
        "execution_limit": find(".//t:ExecutionTimeLimit"),
        "priority": find(".//t:Priority"),
        "user_id": find(".//t:Principal/t:UserId"),
        "logon_type": find(".//t:Principal/t:LogonType"),
        "run_level": find(".//t:Principal/t:RunLevel"),
        "command": find(".//t:Exec/t:Command"),
        "arguments": find(".//t:Exec/t:Arguments"),
    }


class SchedulerGoldenContractV218Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        mac = FIXTURE["macos"]
        cls.mac_python = PurePosixPath(mac["python"])
        cls.mac_skill = PurePosixPath(mac["skill"])
        cls.mac_archive = PurePosixPath(mac["archive"])
        windows = FIXTURE["windows"]
        cls.win_python = GoldenWindowsPath(windows["python"])
        cls.win_skill = GoldenWindowsPath(windows["skill"])
        cls.win_archive = GoldenWindowsPath(windows["archive"])

    def test_exact_command_arrays_and_policy_differences(self) -> None:
        commands = {
            "cloud": cloud.cloud_command(
                self.mac_python, self.mac_skill, self.mac_archive
            ),
            "governance": governance.scheduler_command(
                self.mac_python, self.mac_skill, self.mac_archive
            ),
            "maintenance": maintenance.maintenance_command(
                self.mac_python, self.mac_skill, self.mac_archive
            ),
        }
        self.assertEqual(
            commands["cloud"],
            [
                str(self.mac_python),
                str(self.mac_skill / "scripts" / "memory_cli.py"),
                "--root",
                str(self.mac_archive),
                "--config",
                str(self.mac_skill / "config.yaml"),
                "cloud-sync",
            ],
        )
        self.assertEqual(
            commands["governance"],
            [
                str(self.mac_python),
                str(self.mac_skill / "scripts" / "memory_cli.py"),
                "--root",
                str(self.mac_archive),
                "--config",
                str(self.mac_skill / "config.yaml"),
                *FIXTURE["policy"]["governance"]["command_tail"],
            ],
        )
        self.assertEqual(
            commands["maintenance"],
            [
                str(self.mac_python),
                str(self.mac_skill / "scripts" / "maintenance_supervisor.py"),
                "--root",
                str(self.mac_archive),
                "--config",
                str(self.mac_skill / "config.yaml"),
                *FIXTURE["policy"]["maintenance"]["command_tail"],
            ],
        )
        self.assertNotEqual(commands["cloud"], commands["governance"])
        self.assertNotEqual(commands["cloud"], commands["maintenance"])

    def test_exact_macos_plist_values_and_bytes(self) -> None:
        builders = {
            "cloud": cloud.macos_plist,
            "governance": governance.macos_plist,
            "maintenance": maintenance.macos_plist,
        }
        expected_labels = {
            "cloud": cloud.MACOS_LABEL,
            "governance": governance.MACOS_LABEL,
            "maintenance": maintenance.MACOS_LABEL,
        }
        for name, builder in builders.items():
            with self.subTest(name=name):
                payload = builder(self.mac_python, self.mac_skill, self.mac_archive)
                policy = FIXTURE["policy"][name]
                self.assertEqual(payload["Label"], expected_labels[name])
                self.assertEqual(payload["StartInterval"], 300)
                self.assertIs(payload["RunAtLoad"], policy["run_at_load"])
                self.assertIs(payload["KeepAlive"], policy["keep_alive"])
                self.assertEqual(payload["ProcessType"], "Background")
                self.assertEqual(
                    payload["StandardOutPath"],
                    str(self.mac_archive / PurePosixPath(policy["stdout"])),
                )
                self.assertEqual(
                    payload["StandardErrorPath"],
                    str(self.mac_archive / PurePosixPath(policy["stderr"])),
                )
                encoded = plistlib.dumps(payload, sort_keys=True)
                self.assertTrue(encoded.startswith(b"<?xml version=\"1.0\""))
                self.assertIn("研究 者".encode("utf-8"), encoded)
                self.assertEqual(
                    sha256(encoded),
                    FIXTURE["macos"]["plist_sha256"][name],
                )

    def test_exact_windows_xml_bytes_and_policy(self) -> None:
        fixed_dt = SimpleNamespace(datetime=FixedDateTime)
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(governance, "dt", fixed_dt),
            patch.object(maintenance, "dt", fixed_dt),
            patch.object(governance, "windows_user_id", return_value=FIXTURE["windows"]["user_id"]),
            patch.object(maintenance, "_windows_user_id", return_value=FIXTURE["windows"]["user_id"]),
        ):
            payloads = {
                "cloud": cloud.windows_task_xml(
                    self.win_python,
                    self.win_skill,
                    self.win_archive,
                    user_id=FIXTURE["windows"]["user_id"],
                    start_boundary=FIXTURE["fixed_start_boundary"],
                ),
                "governance": governance.windows_xml(
                    self.win_python, self.win_skill, self.win_archive
                ),
                "maintenance": maintenance.windows_xml(
                    self.win_python, self.win_skill, self.win_archive
                ),
            }

        for name, payload in payloads.items():
            with self.subTest(name=name):
                self.assertTrue(payload.startswith((b"\xff\xfe", b"\xfe\xff")))
                self.assertEqual(
                    sha256(payload), FIXTURE["windows"]["xml_sha256"][name]
                )
                values = xml_values(payload)
                common = FIXTURE["windows_common"]
                for key, expected in common.items():
                    self.assertEqual(values[key], expected, key)
                policy = FIXTURE["policy"][name]
                self.assertEqual(values["start_boundary"], FIXTURE["fixed_start_boundary"])
                self.assertEqual(values["execution_limit"], policy["execution_limit"])
                self.assertEqual(values["priority"], policy["priority"])
                self.assertEqual(values["user_id"], FIXTURE["windows"]["user_id"])
                self.assertEqual(values["command"], str(self.win_python.with_name("pythonw.exe")))
                for argument in policy["command_tail"]:
                    self.assertIn(argument, values["arguments"] or "")
                self.assertNotIn("powershell", (values["command"] or "").lower())

        wrapper_text = cloud.windows_wrapper(
            self.win_python, self.win_skill, self.win_archive
        )
        wrapper_bytes = wrapper_text.replace("\n", "\r\n").encode("utf-8-sig")
        self.assertTrue(wrapper_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", wrapper_bytes.replace(b"\r\n", b""))
        self.assertIn(
            str(self.win_archive / "federation" / "cloud-sync.stdout.log"),
            wrapper_bytes.decode("utf-8-sig"),
        )
        self.assertIn(
            str(self.win_archive / "federation" / "cloud-sync.stderr.log"),
            wrapper_bytes.decode("utf-8-sig"),
        )
        self.assertEqual(
            sha256(wrapper_bytes), FIXTURE["windows"]["cloud_wrapper_sha256"]
        )

    def test_cloud_runner_calls_are_exact_without_real_scheduler(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(arguments, **kwargs):
            calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        home = PurePosixPath("/Users/研究 者")
        writes: dict[str, bytes] = {}
        with (
            patch.object(Path, "home", return_value=home),
            patch.object(PurePosixPath, "mkdir", create=True),
            patch.object(cloud, "launchctl_domain", return_value="gui/501"),
            patch.object(cloud, "atomic_write_bytes", side_effect=lambda path, data: writes.__setitem__(str(path), data)),
        ):
            output = cloud.install_macos(
                self.mac_archive,
                self.mac_skill,
                self.mac_python,
                load=True,
                runner=runner,
            )

        expected_output = home / "Library" / "LaunchAgents" / f"{cloud.MACOS_LABEL}.plist"
        self.assertEqual(output, expected_output)
        self.assertEqual(sha256(writes[str(output)]), FIXTURE["macos"]["plist_sha256"]["cloud"])
        self.assertEqual(calls[0][0], ["/bin/launchctl", "bootout", "gui/501", str(output)])
        self.assertEqual(calls[0][1], {"check": False, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
        self.assertEqual(calls[1], (["/bin/launchctl", "bootstrap", "gui/501", str(output)], {"check": True}))

    def test_remaining_macos_runner_calls_are_exact_without_real_scheduler(self) -> None:
        home = Path("C:/Golden Home/研究 者")
        expected_calls = lambda label: [
            (["/bin/launchctl", "bootout", "gui/501", str(home / "Library" / "LaunchAgents" / f"{label}.plist")], {"check": False}),
            (["/bin/launchctl", "bootstrap", "gui/501", str(home / "Library" / "LaunchAgents" / f"{label}.plist")], {"check": True}),
        ]

        maintenance_calls: list[tuple[list[str], dict[str, object]]] = []

        def maintenance_runner(arguments, **kwargs):
            maintenance_calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with (
            patch.object(Path, "home", return_value=home),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "mkdir"),
            patch.object(maintenance, "launchctl_domain", return_value="gui/501"),
            patch.object(maintenance, "atomic_write_bytes"),
        ):
            maintenance.install(
                self.win_archive,
                self.win_skill,
                self.win_python,
                platform_name="darwin",
                load=True,
                runner=maintenance_runner,
            )
        self.assertEqual(maintenance_calls, expected_calls(maintenance.MACOS_LABEL))

        governance_calls: list[tuple[list[str], dict[str, object]]] = []

        def governance_runner(arguments, **kwargs):
            governance_calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        argv = [
            "install_governance_ai.py",
            "--archive-root",
            str(self.win_archive),
            "--skill-root",
            str(self.win_skill),
            "--python-executable",
            str(self.win_python),
            "--load",
        ]
        with (
            patch.object(governance.sys, "argv", argv),
            patch.object(governance.sys, "platform", "darwin"),
            patch.object(governance, "executable_entry_path", return_value=self.win_python),
            patch.object(governance, "launchctl_domain", return_value="gui/501"),
            patch.object(governance.subprocess, "run", side_effect=governance_runner),
            patch.object(governance, "atomic_write_bytes"),
            patch.object(Path, "home", return_value=home),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "mkdir"),
            patch("builtins.print"),
        ):
            self.assertEqual(governance.main(), 0)
        governance_expected = expected_calls(governance.MACOS_LABEL)
        governance_expected[0][1].update(
            {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        )
        self.assertEqual(governance_calls, governance_expected)

    def test_governance_windows_runner_calls_are_exact_without_real_scheduler(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        writes: dict[str, bytes] = {}
        temporary = GoldenWindowsPath("C:/MemoryWuxianGolden/governance.xml")

        def runner(arguments, **kwargs):
            calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        def mkstemp(**_kwargs):
            return os.open(os.devnull, os.O_RDONLY), str(temporary)

        argv = [
            "install_governance_ai.py",
            "--archive-root",
            str(self.win_archive),
            "--skill-root",
            str(self.win_skill),
            "--python-executable",
            str(self.win_python),
            "--load",
        ]
        with (
            patch.object(governance.sys, "argv", argv),
            patch.object(governance.sys, "platform", "win32"),
            patch.object(governance, "executable_entry_path", return_value=self.win_python),
            patch.object(governance, "windows_user_id", return_value=FIXTURE["windows"]["user_id"]),
            patch.object(governance, "windows_system_executable", return_value=GoldenWindowsPath("C:/Windows/System32/schtasks.exe")),
            patch.object(governance, "dt", SimpleNamespace(datetime=FixedDateTime)),
            patch.object(platform_scheduler.tempfile, "mkstemp", side_effect=mkstemp),
            patch.object(governance, "atomic_write_bytes", side_effect=lambda path, data: writes.__setitem__(str(path), data)),
            patch.object(governance.subprocess, "run", side_effect=runner),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "mkdir"),
            patch.object(Path, "unlink"),
            patch("builtins.print"),
        ):
            self.assertEqual(governance.main(), 0)

        executable = str(GoldenWindowsPath("C:/Windows/System32/schtasks.exe"))
        self.assertEqual(
            calls,
            [
                ([executable, "/Create", "/TN", governance.WINDOWS_TASK_NAME, "/XML", str(temporary), "/F"], {"check": True}),
                ([executable, "/Run", "/TN", governance.WINDOWS_TASK_NAME], {"check": True}),
            ],
        )
        self.assertEqual(
            sha256(writes[str(temporary)]),
            FIXTURE["windows"]["xml_sha256"]["governance"],
        )

    def test_windows_runner_calls_are_exact_without_real_scheduler(self) -> None:
        cases = (
            ("cloud", cloud, cloud.install_windows, cloud.WINDOWS_TASK_NAME),
            ("maintenance", maintenance, maintenance.install, maintenance.WINDOWS_TASK_NAME),
        )
        for name, module, installer, task_name in cases:
            with self.subTest(name=name):
                calls: list[tuple[list[str], dict[str, object]]] = []
                writes: dict[str, bytes] = {}
                temporary = GoldenWindowsPath(f"C:/MemoryWuxianGolden/{name}.xml")

                def runner(arguments, **kwargs):
                    calls.append(([str(item) for item in arguments], kwargs))
                    return subprocess.CompletedProcess(arguments, 0, "", "")

                def mkstemp(**_kwargs):
                    return os.open(os.devnull, os.O_RDONLY), str(temporary)

                patches = (
                    patch.object(Path, "exists", return_value=True),
                    patch.object(Path, "is_file", return_value=True),
                    patch.object(Path, "mkdir"),
                    patch.object(Path, "unlink"),
                    patch.object(platform_scheduler.tempfile, "mkstemp", side_effect=mkstemp),
                    patch.object(module, "atomic_write_bytes", side_effect=lambda path, data: writes.__setitem__(str(path), data)),
                )
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                    if name == "cloud":
                        with (
                            patch.object(cloud, "windows_user_id", return_value=FIXTURE["windows"]["user_id"]),
                            patch.object(cloud, "windows_system_executable", return_value=GoldenWindowsPath("C:/Windows/System32/schtasks.exe")),
                            patch.object(cloud, "dt", SimpleNamespace(datetime=FixedDateTime)),
                        ):
                            installer(self.win_archive, self.win_skill, self.win_python, load=True, runner=runner)
                    else:
                        with (
                            patch.object(maintenance, "_windows_user_id", return_value=FIXTURE["windows"]["user_id"]),
                            patch.object(maintenance, "dt", SimpleNamespace(datetime=FixedDateTime)),
                            patch.object(maintenance, "no_window_kwargs", return_value={"creationflags": 0x08000000}),
                        ):
                            installer(self.win_archive, self.win_skill, self.win_python, platform_name="win32", load=True, runner=runner)

                create = next(call for call in calls if "/Create" in call[0])
                start = next(call for call in calls if "/Run" in call[0])
                executable = str(GoldenWindowsPath("C:/Windows/System32/schtasks.exe")) if name == "cloud" else "schtasks.exe"
                self.assertEqual(create[0], [executable, "/Create", "/TN", task_name, "/XML", str(temporary), "/F"])
                self.assertEqual(start[0], [executable, "/Run", "/TN", task_name])
                expected_kwargs = {"check": True}
                if name == "maintenance":
                    expected_kwargs["creationflags"] = 0x08000000
                self.assertEqual(create[1], expected_kwargs)
                self.assertEqual(start[1], expected_kwargs)
                self.assertEqual(sha256(writes[str(temporary)]), FIXTURE["windows"]["xml_sha256"][name])


if __name__ == "__main__":
    unittest.main()
