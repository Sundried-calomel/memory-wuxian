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
from tests.support.macos import (
    RecordingRunner,
    temporary_root,
    write_launch_agent_plist,
)


class FakeRunner(RecordingRunner):
    def __init__(
        self,
        fail_dashboard=False,
        stop_sticks=False,
        autosync_probe=None,
        maintenance_probe=None,
    ):
        super().__init__()
        self.fail_dashboard = fail_dashboard
        self.stop_sticks = stop_sticks
        self.autosync_probe = autosync_probe
        self.maintenance_probe = maintenance_probe
        self.collector_running = True
        self.current_pid = 41

    def __call__(self, arguments, **kwargs):
        command = self.record(arguments)
        if command[:2] == ["/bin/launchctl", "print"]:
            if self.collector_running:
                return self.completed(command, stdout=f"pid = {self.current_pid}\n")
            return self.completed(command, returncode=1, stderr="not found")
        if command[:2] == ["/bin/launchctl", "bootout"]:
            if command[-1].endswith(f"{transaction.COLLECTOR_LABEL}.plist") and not self.stop_sticks:
                self.collector_running = False
            return self.completed(command)
        if command[:2] == ["/bin/launchctl", "bootstrap"]:
            self.collector_running = True
            return self.completed(command)
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
            values = {
                command[index]: command[index + 1]
                for index in range(2, len(command) - 1)
                if command[index].startswith("--")
            }
            archive = Path(values["--archive-root"])
            skill = Path(values["--skill-root"])
            sessions = Path(values["--sessions-root"])
            since = "2026-01-01T00:00:00Z"
            activation = archive / "imports" / "codex" / "collector-activation.json"
            activation.parent.mkdir(parents=True, exist_ok=True)
            activation.write_text(json.dumps({"since": since}), encoding="utf-8")
            write_launch_agent_plist(
                Path.home(),
                transaction.COLLECTOR_LABEL,
                {
                    "Label": transaction.COLLECTOR_LABEL,
                    "ProgramArguments": [
                        str(skill / "bin" / "memory-wuxian-collector"),
                        "--archive-root",
                        str(archive),
                        "--config",
                        str(skill / "config.yaml"),
                        "--sessions-root",
                        str(sessions),
                        "--since",
                        since,
                        "--debounce-ms",
                        "400",
                    ],
                    "StandardOutPath": str(
                        archive / "imports/codex/launch-agent.stdout.log"
                    ),
                    "StandardErrorPath": str(
                        archive / "imports/codex/launch-agent.stderr.log"
                    ),
                    "EnvironmentVariables": {
                        "RUST_BACKTRACE": "1",
                        "MEMORY_WUXIAN_PYTHON": values["--python-executable"],
                        "MEMORY_WUXIAN_CODEX": values["--codex-cli"],
                    },
                },
            )
            pointer = skill.parent.parent / "memory-wuxian-active-root.txt"
            pointer.write_text(f"{archive}\n", encoding="utf-8")
            self.current_pid = 42
            self.collector_running = True
            if self.autosync_probe is not None:
                self.autosync_probe()
        if command and len(command) > 1 and command[1].endswith(
            "install_maintenance_supervisor.py"
        ):
            if self.maintenance_probe is not None:
                self.maintenance_probe()
        return self.completed(command)


@unittest.skipUnless(sys.platform == "darwin", "macOS transaction contract")
class MacosTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temporary, self.home = temporary_root()
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
            (root / "SKILL.md").write_text("---\nname: memory-wuxian\n---\n")
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
        self.plist = write_launch_agent_plist(
            self.home,
            transaction.COLLECTOR_LABEL,
            {"Label": transaction.COLLECTOR_LABEL},
        )
        self.legacy_plist = self.home / "Library" / "LaunchAgents" / (
            "com.memorywuxian.semantic-backfill.plist"
        )
        self.legacy_plist.write_bytes(b"legacy")
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
            self.assertIn(
                kwargs.get("timeout_seconds"),
                (30, transaction.COLLECTOR_READY_TIMEOUT_SECONDS),
            )
            watermark = kwargs.get("minimum_watermark") or "2026-07-29T00:00:00Z"
            return {
                "pid": 42,
                "ready": True,
                "phase": "ready",
                "updated_at": "2026-07-30T00:00:00Z",
                "last_archive_update": "2026-07-30T00:00:00Z",
                "source_watermark": watermark,
                "archive_watermark": watermark,
            }

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
        update_root = self.home / ".codex" / "updates" / "memory-wuxian"
        self.assertEqual(list((update_root / "generations").iterdir()), [])
        self.assertTrue(Path(result["commit_receipt"]).is_file())
        journal = transaction.read_json(Path(result["commit_receipt"]).parent / "journal.json")
        self.assertEqual([item["stage"] for item in journal["events"]], [
            "prepare", "prepare", "prepare", "verify", "commit"
        ])
        self.assertFalse(self.legacy_plist.exists())
        self.assertEqual(result["legacy_semantic_backfill"]["status"], "retired")

    def test_second_run_is_idempotent_and_preserves_archive_bytes(self):
        sentinel = self.archive / "raw" / "多语言" / "exact.bin"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(bytes(range(256)) * 8)
        before = sentinel.read_bytes()
        runner = FakeRunner()

        first = self.invoke(runner)
        calls_after_first = len(runner.calls)
        second = self.invoke(runner)

        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "installed")
        self.assertTrue(second["idempotent"])
        self.assertEqual(sentinel.read_bytes(), before)
        later = runner.calls[calls_after_first:]
        self.assertFalse(
            any(command[:2] == ["/bin/launchctl", "bootout"] for command in later)
        )

    def test_commit_receipt_failure_rolls_back_before_pruning_evidence(self):
        original_atomic_json = transaction.atomic_json

        def fail_commit_receipt(path, payload):
            if Path(path).name == "commit-receipt.json":
                raise OSError("synthetic commit receipt failure")
            return original_atomic_json(path, payload)

        with patch.object(transaction, "atomic_json", side_effect=fail_commit_receipt):
            with self.assertRaisesRegex(OSError, "synthetic commit receipt failure"):
                self.invoke(FakeRunner())

        self.assertEqual((self.skill / "marker").read_text(encoding="utf-8"), "old")
        transaction_roots = list(
            (self.home / ".codex/updates/memory-wuxian/transactions").iterdir()
        )
        self.assertEqual(len(transaction_roots), 1)
        self.assertFalse((transaction_roots[0] / "commit-receipt.json").exists())
        self.assertTrue((transaction_roots[0] / "rollback-receipt.json").is_file())

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
        self.assertTrue(self.legacy_plist.exists())

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


class MacosTransactionPortableContractTest(unittest.TestCase):
    def test_lifecycle_alignment_keeps_stable_identity_and_refreshes_telemetry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "collector-lifecycle.json"
            command = ["/installed/memory-wuxian-collector", "--archive-root", str(root)]
            old = {
                "pid": 42,
                "ready": True,
                "phase": "ready",
                "updated_at": "2026-08-18T00:00:00Z",
                "source_watermark": "2026-08-18T00:00:00Z",
                "archive_watermark": "2026-08-18T00:00:00Z",
            }
            new = {
                **old,
                "updated_at": "2026-08-18T00:00:02Z",
                "source_watermark": "2026-08-18T00:00:02Z",
                "archive_watermark": "2026-08-18T00:00:02Z",
            }
            transaction.persist_collector_lifecycle(
                path,
                generation="generation-a",
                archive_root=root,
                expected_command=command,
                telemetry=old,
                launchd_pid=42,
            )
            transaction.verify_collector_lifecycle_alignment(
                path,
                generation="generation-a",
                archive_root=root,
                expected_command=command,
                telemetry=new,
                launchd_pid=42,
            )
            with self.assertRaisesRegex(RuntimeError, "does not align"):
                transaction.verify_collector_lifecycle_alignment(
                    path,
                    generation="generation-a",
                    archive_root=root,
                    expected_command=[*command, "--unexpected"],
                    telemetry=new,
                    launchd_pid=42,
                )
            refreshed = transaction.persist_collector_lifecycle(
                path,
                generation="generation-a",
                archive_root=root,
                expected_command=command,
                telemetry=new,
                launchd_pid=42,
            )
            self.assertEqual(refreshed["verified_telemetry"]["source_watermark"], "2026-08-18T00:00:02Z")

    def test_journal_retains_prepare_verify_commit_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal_path = Path(temporary) / "事务 かな € 😀" / "journal.json"
            journal = {
                "state": "prepare",
                "events": [],
                "transaction_id": "mwt-test",
            }
            transaction.transition(journal_path, journal, "prepare", phase="staged")
            transaction.transition(journal_path, journal, "verify", effect={"status": "passed"})
            receipt = journal_path.parent / "commit-receipt.json"
            transaction.atomic_json(receipt, {"status": "committed"})
            transaction.transition(journal_path, journal, "commit", commit_receipt=str(receipt))

            persisted = transaction.read_json(journal_path)
            self.assertEqual(
                [event["stage"] for event in persisted["events"]],
                ["prepare", "verify", "commit"],
            )
            self.assertTrue(receipt.is_file())

    def test_multilingual_long_candidate_preserves_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / ("来源 かな € 😀 " + "a" * 80)
            candidate = root / ("候选 " + "b" * 80)
            current = root / "current"
            (source / "bin").mkdir(parents=True)
            (source / "scripts").mkdir()
            (source / "config.yaml").write_bytes(b"backup:\n  enabled: false\n")
            payload = bytes(range(256)) * 4
            (source / "精确字节.bin").write_bytes(payload)
            for name in ("memory-wuxian-collector", "memory-wuxian-envelope"):
                executable = source / "bin" / name
                executable.write_bytes(b"binary\x00payload")
                executable.chmod(0o755)

            transaction.prepare_candidate(source, candidate, current)

            self.assertEqual((candidate / "精确字节.bin").read_bytes(), payload)
            self.assertEqual(
                transaction.tree_generation(source)[0],
                transaction.tree_generation(candidate)[0],
            )

    def test_exact_launch_contract_rejects_archive_root_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "技能 空间"
            archive = root / "归档 かな"
            sessions = root / "sessions"
            for path in (skill / "bin", archive / "imports/codex", sessions):
                path.mkdir(parents=True)
            pointer = root / "active-root.txt"
            pointer.write_text(f"{archive}\n", encoding="utf-8")
            activation = archive / "imports/codex/collector-activation.json"
            activation.write_text(
                json.dumps({"since": "2026-01-01T00:00:00Z"}), encoding="utf-8"
            )
            plist = root / "collector.plist"
            plist.write_bytes(
                plistlib.dumps(
                    {
                        "Label": transaction.COLLECTOR_LABEL,
                        "ProgramArguments": [
                            str(skill / "bin/memory-wuxian-collector"),
                            "--archive-root",
                            str(root / "wrong archive"),
                            "--config",
                            str(skill / "config.yaml"),
                            "--sessions-root",
                            str(sessions),
                            "--since",
                            "2026-01-01T00:00:00Z",
                            "--debounce-ms",
                            "400",
                        ],
                        "StandardOutPath": str(archive / "imports/codex/launch-agent.stdout.log"),
                        "StandardErrorPath": str(archive / "imports/codex/launch-agent.stderr.log"),
                        "EnvironmentVariables": {
                            "RUST_BACKTRACE": "1",
                            "MEMORY_WUXIAN_PYTHON": str(root / "python"),
                            "MEMORY_WUXIAN_CODEX": str(root / "codex"),
                        },
                    }
                )
            )
            telemetry = {
                "pid": 42,
                "last_archive_update": "2026-01-01T00:00:01Z",
                "source_watermark": "2026-01-01T00:00:00Z",
                "archive_watermark": "2026-01-01T00:00:00Z",
            }
            runner = lambda command, **kwargs: subprocess.CompletedProcess(
                command, 0, "pid = 42\n", ""
            )
            with self.assertRaisesRegex(RuntimeError, "does not exactly match"):
                transaction.validate_installed_launch_contract(
                    skill_root=skill,
                    archive_root=archive,
                    sessions_root=sessions,
                    python_executable=root / "python",
                    codex_cli=root / "codex",
                    plist=plist,
                    active_root_pointer=pointer,
                    telemetry=telemetry,
                    previous_archive_watermark=None,
                    runner=runner,
                )

    def test_relative_archive_argument_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "absolute path"):
            transaction.exact_macos_root("relative/归档", "archive root")


@unittest.skipUnless(sys.platform == "darwin", "native macOS candidate probe")
class MacosCandidateProbeIntegrationTest(unittest.TestCase):
    def test_current_candidate_binary_captures_exact_probe_round(self):
        result = transaction.probe_candidate(SKILL_ROOT)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["imported_messages"], 2)
        self.assertEqual(result["raw_records"], 2)


if __name__ == "__main__":
    unittest.main()
