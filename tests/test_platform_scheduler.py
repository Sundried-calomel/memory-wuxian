from __future__ import annotations

import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import platform_scheduler as scheduler
from tests.support.macos import temporary_root


class PlatformSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = temporary_root()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def macos_spec(self) -> scheduler.MacOSJobSpec:
        return scheduler.MacOSJobSpec(
            label="com.example.memory",
            command=("/Users/研究 者/Runtime ¥ 🧠/python3", "--once"),
            interval_seconds=300,
            run_at_load=True,
            keep_alive=False,
            process_type="Background",
            stdout_path=Path("/tmp/記憶 stdout.log"),
            stderr_path=Path("/tmp/記憶 stderr.log"),
        )

    def windows_spec(self) -> scheduler.WindowsTaskSpec:
        return scheduler.WindowsTaskSpec(
            task_name="MemoryWuxianTest",
            description="Run 記憶 synchronization.",
            command=Path(r"C:\Users\研究 者\Runtime ¥ 🧠\pythonw.exe"),
            arguments=(r"C:\Skill 路径\memory_cli.py", "--value", "-先頭"),
            interval="PT5M",
            execution_limit="PT10M",
            priority="7",
            allow_hard_terminate=True,
            multiple_instances="IgnoreNew",
            disallow_start_on_batteries=False,
            stop_on_batteries=False,
            start_when_available=True,
            network_required=False,
            hidden=True,
            logon_type="InteractiveToken",
            run_level="LeastPrivilege",
        )

    def test_specs_are_immutable_and_render_explicit_policy(self) -> None:
        spec = self.macos_spec()
        with self.assertRaises(FrozenInstanceError):
            spec.interval_seconds = 60  # type: ignore[misc]
        self.assertEqual(
            scheduler.render_macos_plist(spec),
            {
                "Label": spec.label,
                "ProgramArguments": list(spec.command),
                "RunAtLoad": True,
                "StartInterval": 300,
                "KeepAlive": False,
                "ProcessType": "Background",
                "StandardOutPath": str(spec.stdout_path),
                "StandardErrorPath": str(spec.stderr_path),
            },
        )

    def test_windows_renderer_round_trips_unicode_and_optional_policy(self) -> None:
        payload = scheduler.render_windows_task_xml(
            self.windows_spec(),
            user_id="研究室\\研究 者",
            start_boundary="2026-08-19T12:34:56+09:00",
        )
        root = ET.fromstring(payload)
        namespace = {"t": scheduler.TASK_XML_NAMESPACE}
        self.assertEqual(
            root.findtext(".//t:UserId", namespaces=namespace),
            "研究室\\研究 者",
        )
        self.assertEqual(
            root.findtext(".//t:Command", namespaces=namespace),
            str(self.windows_spec().command),
        )
        arguments = root.findtext(".//t:Arguments", namespaces=namespace) or ""
        self.assertIn("Skill 路径", arguments)
        self.assertIn("-先頭", arguments)
        self.assertEqual(root.findtext(".//t:Priority", namespaces=namespace), "7")
        self.assertEqual(
            root.findtext(".//t:AllowHardTerminate", namespaces=namespace),
            "true",
        )

        without_optional = scheduler.WindowsTaskSpec(
            task_name="NoOptionalPolicy",
            description="No optional policy.",
            command=Path("python.exe"),
            arguments=("worker.py",),
            interval="PT5M",
            execution_limit="PT20M",
            priority=None,
            allow_hard_terminate=None,
            multiple_instances="IgnoreNew",
            disallow_start_on_batteries=False,
            stop_on_batteries=False,
            start_when_available=True,
            network_required=False,
            hidden=True,
            logon_type="InteractiveToken",
            run_level="LeastPrivilege",
        )
        root = ET.fromstring(
            scheduler.render_windows_task_xml(
                without_optional,
                user_id="Researcher",
                start_boundary="2026-08-19T12:34:56+09:00",
            )
        )
        self.assertIsNone(root.find(".//t:Priority", namespaces=namespace))
        self.assertIsNone(root.find(".//t:AllowHardTerminate", namespaces=namespace))

    def test_windows_logon_trigger_and_restart_policy_round_trip(self) -> None:
        spec = self.windows_spec()
        spec = scheduler.WindowsTaskSpec(
            **{
                **spec.__dict__,
                "trigger_kind": "logon",
                "restart_interval": "PT1M",
                "restart_count": 5,
            }
        )
        payload = scheduler.render_windows_task_xml(spec, user_id="DOMAIN\\user")
        root = ET.fromstring(payload)
        namespace = {"t": scheduler.TASK_XML_NAMESPACE}
        self.assertIsNotNone(root.find(".//t:LogonTrigger", namespaces=namespace))
        self.assertIsNone(root.find(".//t:TimeTrigger", namespaces=namespace))
        inspected = scheduler.inspect_windows_task_xml(payload)
        self.assertEqual(inspected["restart_interval"], "PT1M")
        self.assertEqual(inspected["restart_count"], "5")

    def test_windows_query_preserves_utf16_xml_and_decodes_local_error(self) -> None:
        payload = scheduler.render_windows_task_xml(
            self.windows_spec(),
            user_id="DOMAIN\\user",
            start_boundary="2026-08-19T12:34:56+09:00",
        )

        def runner(_arguments, **_kwargs):
            return subprocess.CompletedProcess([], 0, payload, b"")

        self.assertEqual(
            scheduler.query_windows_task_xml(
                "MemoryWuxianTest", schtasks="schtasks.exe", runner=runner
            ),
            payload,
        )
        with patch.object(scheduler.locale, "getencoding", return_value="cp936"):
            self.assertEqual(
                scheduler.decode_windows_output("错误: 拒绝访问".encode("cp936")),
                "错误: 拒绝访问",
            )

    def test_windows_inspection_normalizes_schtasks_console_xml_declaration(self) -> None:
        payload = scheduler.render_windows_task_xml(
            self.windows_spec(),
            user_id="DOMAIN\\研究者",
            start_boundary="2026-08-19T12:34:56+09:00",
        )
        console_bytes = payload.decode("utf-16").encode("utf-8")
        self.assertRegex(console_bytes, rb"encoding=['\"]utf-16['\"]")

        inspected = scheduler.inspect_windows_task_xml(console_bytes)

        self.assertEqual(inspected["user_id"], "DOMAIN\\研究者")
        self.assertEqual(inspected["command"], str(self.windows_spec().command))

    def test_windows_user_sid_parses_structured_whoami_output(self) -> None:
        sid = "S-1-5-21-4264115984-4109001030-2440231340-1001"

        def runner(arguments, **kwargs):
            self.assertEqual(arguments, ["whoami.exe", "/user", "/fo", "csv", "/nh"])
            self.assertEqual(kwargs, {"check": False, "capture_output": True})
            return subprocess.CompletedProcess(arguments, 0, f'"马焱一的惠普\\56453","{sid}"\r\n'.encode(), b"")

        self.assertEqual(scheduler.windows_user_sid(runner), sid)

    def test_windows_user_sid_rejects_malformed_output(self) -> None:
        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 0, b'"account","not-a-sid"\r\n', b"")

        with self.assertRaisesRegex(RuntimeError, "invalid SID"):
            scheduler.windows_user_sid(runner)

    def test_windows_task_equivalence_accepts_only_current_account_sid(self) -> None:
        sid = "S-1-5-21-4264115984-4109001030-2440231340-1001"
        expected = scheduler.render_windows_task_xml(
            self.windows_spec(),
            user_id="DOMAIN\\user",
            start_boundary="2026-08-19T12:34:56+09:00",
        )
        observed = expected.decode("utf-16").replace("DOMAIN\\user", sid).encode("utf-16")

        def runner(arguments, **_kwargs):
            return subprocess.CompletedProcess(arguments, 0, f'"DOMAIN\\user","{sid}"\r\n'.encode(), b"")

        with patch.dict(scheduler.os.environ, {"USERDOMAIN": "DOMAIN", "USERNAME": "user"}):
            self.assertTrue(scheduler.windows_task_xml_equivalent(observed, expected, runner=runner))
            unrelated = observed.decode("utf-16").replace(sid, "S-1-5-21-1-2-3-9999").encode("utf-16")
            self.assertFalse(scheduler.windows_task_xml_equivalent(unrelated, expected, runner=runner))

        drifted = observed.decode("utf-16").replace("pythonw.exe", "other.exe").encode("utf-16")
        self.assertFalse(
            scheduler.windows_task_xml_equivalent(
                drifted,
                expected,
                runner=lambda *_args, **_kwargs: self.fail("SID lookup must be skipped for other drift"),
            )
        )

    def test_macos_install_and_uninstall_preserve_runner_calls(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        writes: dict[str, bytes] = {}

        def runner(arguments, **kwargs):
            calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with patch.object(Path, "home", return_value=self.root):
            output = scheduler.install_macos_job(
                self.macos_spec(),
                load=True,
                runner=runner,
                write_bytes=lambda path, data: writes.__setitem__(str(path), data),
                domain="gui/501",
                bootout_kwargs={"stdout": subprocess.DEVNULL},
            )
            removed = scheduler.uninstall_macos_job(
                self.macos_spec().label,
                runner=runner,
                domain="gui/501",
            )

        self.assertEqual(removed, output)
        self.assertIn(str(output), writes)
        self.assertEqual(
            calls,
            [
                (["/bin/launchctl", "bootout", "gui/501", str(output)], {"check": False, "stdout": subprocess.DEVNULL}),
                (["/bin/launchctl", "bootstrap", "gui/501", str(output)], {"check": True}),
                (["/bin/launchctl", "bootout", "gui/501", str(output)], {"check": False}),
            ],
        )

    def test_macos_install_without_load_needs_no_domain_or_runner_call(self) -> None:
        writes: list[Path] = []
        runner = Mock()
        with patch.object(Path, "home", return_value=self.root):
            scheduler.install_macos_job(
                self.macos_spec(),
                load=False,
                runner=runner,
                write_bytes=lambda path, _data: writes.append(path),
                domain=None,
            )
        self.assertEqual(len(writes), 1)
        runner.assert_not_called()

    def test_windows_install_uses_structured_calls_and_cleans_temporary_xml(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        written: list[Path] = []

        def runner(arguments, **kwargs):
            calls.append(([str(item) for item in arguments], kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        def writer(path: Path, payload: bytes) -> None:
            written.append(path)
            path.write_bytes(payload)

        scheduler.install_windows_task(
            self.windows_spec(),
            b"xml-payload",
            temporary_prefix=".scheduler-test.",
            schtasks=Path(r"C:\Windows\System32\schtasks.exe"),
            load=True,
            runner=runner,
            write_bytes=writer,
            runner_kwargs={"creationflags": 0x08000000},
        )

        self.assertEqual(len(written), 1)
        self.assertFalse(written[0].exists())
        self.assertEqual(calls[0][0][1:4], ["/Create", "/TN", "MemoryWuxianTest"])
        self.assertEqual(calls[1][0][1:], ["/Run", "/TN", "MemoryWuxianTest"])
        self.assertEqual(calls[0][1], {"check": True, "creationflags": 0x08000000})
        self.assertEqual(calls[1][1], {"check": True, "creationflags": 0x08000000})

    def test_windows_registration_failure_cleans_xml_and_does_not_start(self) -> None:
        calls: list[list[str]] = []
        written: list[Path] = []

        def runner(arguments, **_kwargs):
            calls.append([str(item) for item in arguments])
            raise subprocess.CalledProcessError(1, arguments)

        def writer(path: Path, payload: bytes) -> None:
            written.append(path)
            path.write_bytes(payload)

        with self.assertRaises(subprocess.CalledProcessError):
            scheduler.install_windows_task(
                self.windows_spec(),
                b"xml-payload",
                temporary_prefix=".scheduler-test.",
                schtasks="schtasks.exe",
                load=True,
                runner=runner,
                write_bytes=writer,
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("/Create", calls[0])
        self.assertFalse(written[0].exists())

    def test_windows_uninstall_keeps_job_specific_end_policy(self) -> None:
        calls: list[list[str]] = []

        def runner(arguments, **_kwargs):
            calls.append([str(item) for item in arguments])
            return subprocess.CompletedProcess(arguments, 0, "", "")

        scheduler.uninstall_windows_task(
            "WithEnd",
            schtasks="schtasks.exe",
            runner=runner,
            end_first=True,
        )
        scheduler.uninstall_windows_task(
            "DeleteOnly",
            schtasks="schtasks.exe",
            runner=runner,
            end_first=False,
        )
        self.assertEqual(
            [call[1] for call in calls],
            ["/End", "/Delete", "/Delete"],
        )


if __name__ == "__main__":
    unittest.main()
