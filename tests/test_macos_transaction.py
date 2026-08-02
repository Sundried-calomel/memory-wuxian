import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name != "nt":
    import fcntl
else:
    fcntl = None


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import install_macos_transaction as transaction


class FakeRunner:
    def __init__(
        self,
        fail_dashboard=False,
        stop_sticks=False,
        autosync_probe=None,
        maintenance_probe=None,
    ):
        self.calls = []
        self.fail_dashboard = fail_dashboard
        self.stop_sticks = stop_sticks
        self.autosync_probe = autosync_probe
        self.maintenance_probe = maintenance_probe
        self.collector_running = True

    def __call__(self, arguments, **kwargs):
        command = [str(item) for item in arguments]
        self.calls.append(command)
        if command[:2] == ["/bin/launchctl", "print"]:
            if self.collector_running:
                return subprocess.CompletedProcess(command, 0, "pid = 41\n", "")
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:2] == ["/bin/launchctl", "bootout"]:
            if not self.stop_sticks:
                self.collector_running = False
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["/bin/launchctl", "bootstrap"]:
            self.collector_running = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if self.fail_dashboard and command and command[1].endswith(
            "install_dashboard_app_macos.py"
        ):
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="synthetic dashboard self-check failure",
            )
        if command and len(command) > 1 and command[1].endswith(
            "install_codex_autosync.py"
        ):
            if self.autosync_probe is not None:
                self.autosync_probe()
        if command and len(command) > 1 and command[1].endswith(
            "install_maintenance_supervisor.py"
        ):
            if self.maintenance_probe is not None:
                self.maintenance_probe()
        return subprocess.CompletedProcess(command, 0, "", "")


@unittest.skipUnless(sys.platform == "darwin", "macOS transaction contract")
class MacosTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.source = self.home / "source"
        self.skill = self.home / ".codex" / "skills" / "memory-wuxian"
        self.archive = self.home / "archive"
        self.sessions = self.home / "sessions"
        self.python = self.home / "python3"
        self.codex = self.home / "codex"
        self.archive.mkdir()
        self.sessions.mkdir()
        self.python.write_text("", encoding="utf-8")
        self.codex.write_text("", encoding="utf-8")
        for root, marker in ((self.source, "new"), (self.skill, "old")):
            (root / "bin").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "marker").write_text(marker, encoding="utf-8")
            (root / "config.yaml").write_text("backup:\n  enabled: false\n")
            for name in ("memory-wuxian-collector", "memory-wuxian-envelope"):
                path = root / "bin" / name
                path.write_text("", encoding="utf-8")
                path.chmod(0o755)
            (root / "scripts" / "install_codex_autosync.py").write_text("")
            (root / "scripts" / "install_maintenance_supervisor.py").write_text("")
            (root / "scripts" / "install_dashboard_app_macos.py").write_text("")
        for excluded in (".git", "target", "outputs", "dist", "memory", "__pycache__"):
            path = self.source / excluded
            path.mkdir()
            (path / "excluded").write_text("not installed", encoding="utf-8")
        self.plist = self.home / "Library" / "LaunchAgents" / (
            f"{transaction.COLLECTOR_LABEL}.plist"
        )
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({"Label": transaction.COLLECTOR_LABEL}))
        self.active_root_pointer = (
            self.home / ".codex" / "memory-wuxian-active-root.txt"
        )
        self.active_root_pointer.write_text("/old/archive\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, runner, ready_probe=None):
        def ready_result(*_args, **kwargs):
            if ready_probe is not None:
                ready_probe()
            self.assertEqual(
                kwargs.get("timeout_seconds"),
                transaction.COLLECTOR_READY_TIMEOUT_SECONDS,
            )
            return {"pid": 42, "updated_at": "2026-07-30T00:00:00Z"}

        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch.object(transaction, "probe_candidate", return_value={"status": "passed"}),
            patch.object(
                transaction,
                "wait_for_collector",
                side_effect=ready_result,
            ),
        ):
            return transaction.install(
                source_root=self.source,
                skill_root=self.skill,
                archive_root=self.archive,
                sessions_root=self.sessions,
                python_executable=self.python,
                codex_cli=self.codex,
                runner=runner,
            )

    def test_success_switches_only_after_probe_and_discards_rollback(self):
        runner = FakeRunner()
        result = self.invoke(runner)
        self.assertEqual(result["active_pid"], 42)
        self.assertEqual(result["cutover"]["status"], "quiesced")
        bootout = next(
            index
            for index, command in enumerate(runner.calls)
            if command[:2] == ["/bin/launchctl", "bootout"]
        )
        install = next(
            index
            for index, command in enumerate(runner.calls)
            if "install_codex_autosync.py" in " ".join(command)
        )
        self.assertLess(bootout, install)
        self.assertEqual((self.skill / "marker").read_text(), "new")
        for excluded in (".git", "target", "outputs", "dist", "memory", "__pycache__"):
            self.assertFalse((self.skill / excluded).exists())
        self.assertFalse(
            (self.home / ".codex" / "updates" / "memory-wuxian" / "rollback-current").exists()
        )

    def test_new_collector_starts_only_after_cutover_lock_is_released(self):
        def assert_archive_lock_is_free():
            lock_path = self.archive / ".locks" / "archive.lock"
            with lock_path.open("a+", encoding="utf-8") as competing:
                fcntl.flock(competing.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(competing.fileno(), fcntl.LOCK_UN)

        result = self.invoke(FakeRunner(autosync_probe=assert_archive_lock_is_free))
        self.assertEqual(result["cutover"]["status"], "quiesced")

    def test_maintenance_loads_only_after_replacement_collector_is_ready(self):
        state = {"ready": False}

        def mark_ready():
            state["ready"] = True

        def require_ready():
            self.assertTrue(state["ready"])

        runner = FakeRunner(maintenance_probe=require_ready)
        self.invoke(runner, ready_probe=mark_ready)
        autosync = next(
            command
            for command in runner.calls
            if "install_codex_autosync.py" in " ".join(command)
        )
        self.assertIn("--defer-maintenance", autosync)

    def test_probe_failure_does_not_cut_over_or_touch_launch_agent(self):
        before = self.plist.read_bytes()
        runner = FakeRunner()
        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch.object(
                transaction,
                "probe_candidate",
                side_effect=ValueError("synthetic probe failure"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "synthetic probe failure"):
                transaction.install(
                    source_root=self.source,
                    skill_root=self.skill,
                    archive_root=self.archive,
                    sessions_root=self.sessions,
                    python_executable=self.python,
                    codex_cli=self.codex,
                    runner=runner,
                )
        self.assertEqual((self.skill / "marker").read_text(), "old")
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertEqual(
            self.active_root_pointer.read_text(encoding="utf-8"),
            "/old/archive\n",
        )
        self.assertFalse(
            any(
                command[:2] == ["/bin/launchctl", "bootout"]
                or "install_codex_autosync.py" in " ".join(command)
                for command in runner.calls
            )
        )

    def test_post_switch_failure_restores_old_skill_and_plist(self):
        before = self.plist.read_bytes()
        with self.assertRaisesRegex(
            RuntimeError, "synthetic dashboard self-check failure"
        ):
            self.invoke(FakeRunner(fail_dashboard=True))
        self.assertEqual((self.skill / "marker").read_text(), "old")
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertEqual(
            self.active_root_pointer.read_text(encoding="utf-8"),
            "/old/archive\n",
        )

    def test_new_format_telemetry_requires_ready_phase(self):
        telemetry = self.archive / "imports" / "codex" / "collector-telemetry.json"
        telemetry.parent.mkdir(parents=True)
        telemetry.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "pid": 123,
                    "ready": False,
                    "updated_at": "2026-07-30T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        with (
            patch.object(transaction.os, "kill", return_value=None),
            patch.object(transaction.time, "monotonic", side_effect=[0.0, 0.0, 1.0]),
            patch.object(transaction.time, "sleep", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "still completing startup"):
                transaction.wait_for_collector(
                    self.archive,
                    previous_pid=None,
                    timeout_seconds=0.5,
                )

    def test_cutover_refuses_persisted_recovery_debt(self):
        marker = self.archive / "pending" / "native-recovery-debt.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}\n", encoding="utf-8")
        runner = FakeRunner()
        with patch("pathlib.Path.home", return_value=self.home):
            with self.assertRaisesRegex(RuntimeError, "recovery debt remains"):
                with transaction.quiesce_collector_for_cutover(
                    self.archive,
                    self.plist,
                    runner=runner,
                    timeout_seconds=0.1,
                ):
                    pass
        self.assertTrue(runner.collector_running)

    def test_cutover_holds_archive_lock_while_stopping_collector(self):
        runner = FakeRunner()
        with patch("pathlib.Path.home", return_value=self.home):
            with transaction.quiesce_collector_for_cutover(
                self.archive,
                self.plist,
                runner=runner,
                timeout_seconds=0.1,
            ) as result:
                self.assertEqual(result["previous_pid"], 41)
                lock_path = self.archive / ".locks" / "archive.lock"
                with lock_path.open("a+", encoding="utf-8") as competing:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(
                            competing.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
        self.assertFalse(runner.collector_running)

    def test_cutover_stop_timeout_restores_previous_collector(self):
        runner = FakeRunner(stop_sticks=True)
        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch.object(
                transaction.time,
                "monotonic",
                side_effect=[0.0, 0.0, 31.0],
            ),
            patch.object(transaction.time, "sleep", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not stop"):
                with transaction.quiesce_collector_for_cutover(
                    self.archive,
                    self.plist,
                    runner=runner,
                    timeout_seconds=0.1,
                ):
                    pass
        self.assertTrue(runner.collector_running)
        self.assertTrue(
            any(command[:2] == ["/bin/launchctl", "bootstrap"] for command in runner.calls)
        )

    def test_first_directory_switch_failure_restores_previous_collector(self):
        runner = FakeRunner()
        real_replace = transaction.os.replace

        def fail_current_switch(source, destination):
            if Path(source) == self.skill:
                raise OSError("synthetic current-skill switch failure")
            return real_replace(source, destination)

        with patch.object(transaction.os, "replace", side_effect=fail_current_switch):
            with self.assertRaisesRegex(OSError, "synthetic current-skill switch failure"):
                self.invoke(runner)
        self.assertTrue(runner.collector_running)
        self.assertEqual((self.skill / "marker").read_text(), "old")


@unittest.skipUnless(sys.platform == "darwin", "native macOS candidate probe")
class MacosCandidateProbeIntegrationTest(unittest.TestCase):
    def test_current_candidate_binary_captures_exact_probe_round(self):
        result = transaction.probe_candidate(SKILL_ROOT)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["imported_messages"], 2)
        self.assertEqual(result["raw_records"], 2)


if __name__ == "__main__":
    unittest.main()
