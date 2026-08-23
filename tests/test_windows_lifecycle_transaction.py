import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_codex_autosync_windows as windows
import platform_scheduler as scheduler


class FakeRunner:
    def __init__(self, old_xml=None, fail_run=False, fail_probe=False, fail_create=False, run_key=None):
        self.task_xml = old_xml
        self.fail_run = fail_run
        self.fail_probe = fail_probe
        self.fail_create = fail_create
        self.run_key = run_key
        self.calls = []

    def __call__(self, arguments, **kwargs):
        command = [str(item) for item in arguments]
        self.calls.append(command)
        if command[0].lower().endswith("collector.exe") and command[1:] == ["--help"]:
            return subprocess.CompletedProcess(
                command,
                1 if self.fail_probe else 0,
                b"usage",
                b"candidate failed" if self.fail_probe else b"",
            )
        if command[:2] == ["reg.exe", "QUERY"]:
            if self.run_key is None:
                return subprocess.CompletedProcess(command, 1, b"", b"missing")
            value_name = command[command.index("/V") + 1]
            payload = f"    {value_name}    REG_SZ    {self.run_key}\r\n".encode("utf-8")
            return subprocess.CompletedProcess(command, 0, payload, b"")
        if command[:2] == ["reg.exe", "DELETE"]:
            self.run_key = None
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if command[:2] == ["reg.exe", "ADD"]:
            self.run_key = command[command.index("/D") + 1]
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if command[:2] == ["schtasks.exe", "/Query"]:
            if self.task_xml is None:
                return subprocess.CompletedProcess(command, 1, b"", b"missing")
            return subprocess.CompletedProcess(command, 0, self.task_xml, b"")
        if command[:2] == ["schtasks.exe", "/Create"]:
            if self.fail_create:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    b"",
                    "错误: 任务 XML 格式不正确。".encode("cp936"),
                )
            definition = Path(command[command.index("/XML") + 1])
            self.task_xml = definition.read_bytes()
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if command[:2] == ["schtasks.exe", "/Delete"]:
            self.task_xml = None
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if command[:2] == ["schtasks.exe", "/Run"] and self.fail_run:
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 1, b"", b"failed")
        return subprocess.CompletedProcess(command, 0, b"", b"")


class ActivePointerContractTests(unittest.TestCase):
    def test_existing_matching_pointer_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "档案 ¥ space"
            pointer = root / "active-root.txt"
            original = (str(archive) + "\r\n\r\n").encode("utf-8")
            pointer.write_bytes(original)
            windows._ensure_active_pointer(pointer, archive, original)
            self.assertEqual(pointer.read_bytes(), original)

    def test_mismatched_existing_pointer_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pointer = root / "active-root.txt"
            previous = str(root / "other").encode("utf-8")
            pointer.write_bytes(previous)
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                windows._ensure_active_pointer(pointer, root / "archive", previous)


class WindowsLifecycleTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / "长い道-￥-emoji-😀" / "archive root"
        self.archive.mkdir(parents=True)
        self.collector = self.base / "候補 collector.exe"
        self.config = self.base / "設定 config.yaml"
        self.sessions = self.base / "会話 sessions"
        self.python = self.base / "Python runtime.exe"
        self.codex = self.base / "Codex CLI.exe"
        self.sessions.mkdir()
        for path in (self.collector, self.config, self.python, self.codex):
            path.write_bytes(b"fixture")
        self.command = windows.collector_command(
            self.collector,
            self.archive,
            self.config,
            self.sessions,
            self.python,
            self.codex,
            "2026-08-18T00:00:00Z",
            400,
        )
        self.manifest = self.base / "state" / "collector-command.json"
        self.pointer = self.base / "state" / "active-root.txt"
        self.journal = self.base / "state" / "install-journal.json"

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def ready_probe(archive_root, **_kwargs):
        return {
            "format_version": 2,
            "phase": "ready",
            "pid": 412,
            "source_watermark": "2026-08-18T00:00:00Z",
            "archive_watermark": "2026-08-18T00:00:00Z",
        }

    def invoke(self, runner):
        return windows.install_transaction(
            task_name=windows.DEFAULT_TASK_NAME,
            command=self.command,
            archive_root=self.archive,
            command_manifest=self.manifest,
            pointer=self.pointer,
            journal_path=self.journal,
            runner=runner,
            readiness_probe=self.ready_probe,
        )

    def test_task_xml_is_direct_hidden_restartable_and_round_trips_unicode_argv(self):
        long_archive = Path("C:/") / ("长い道-￥-emoji-😀-" + ("x" * 280)) / "archive root"
        long_command = list(self.command)
        long_command[long_command.index("--archive-root") + 1] = str(long_archive)
        payload = windows.task_xml(long_command)
        definition = windows.verify_task_definition(payload, long_command)
        self.assertEqual(definition["user_id"], windows.windows_user_id())
        self.assertEqual(definition["command"], str(self.collector))
        self.assertIn(str(long_archive), definition["arguments"])
        self.assertTrue(definition["hidden"])
        self.assertEqual(definition["multiple_instances"], "IgnoreNew")
        self.assertEqual(definition["restart_interval"], "PT1M")
        self.assertEqual(definition["restart_count"], "5")
        self.assertNotIn("powershell", definition["command"].lower())

    def test_registered_task_accepts_only_the_current_accounts_normalized_sid(self):
        sid = "S-1-5-21-4264115984-4109001030-2440231340-1001"
        payload = windows.task_xml(self.command)
        registered = payload.decode("utf-16").replace(windows.windows_user_id(), sid).encode("utf-16")

        with patch.object(scheduler, "windows_user_sid", return_value=sid):
            definition = windows.verify_task_definition(registered, self.command)
        self.assertEqual(definition["user_id"], sid)

        unrelated = registered.decode("utf-16").replace(sid, "S-1-5-21-1-2-3-9999").encode("utf-16")
        with patch.object(scheduler, "windows_user_sid", return_value=sid):
            with self.assertRaisesRegex(RuntimeError, "scheduled task does not match"):
                windows.verify_task_definition(unrelated, self.command)

    def test_task_registration_failure_preserves_native_diagnostic(self):
        runner = FakeRunner(fail_create=True)
        with patch.object(scheduler.locale, "getencoding", return_value="cp936"):
            with self.assertRaisesRegex(
                RuntimeError,
                "scheduled task registration failed with exit code 1: 错误: 任务 XML 格式不正确",
            ):
                self.invoke(runner)
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rollback")
        self.assertIn("任务 XML 格式不正确", journal["history"][-1]["error"])

    def test_old_task_survives_until_candidate_verification_then_commits(self):
        old_command = [str(self.base / "old collector.exe"), "--archive-root", "old"]
        old_xml = windows.task_xml(old_command)
        runner = FakeRunner(old_xml)
        result = self.invoke(runner)
        probe_index = next(i for i, call in enumerate(runner.calls) if call[-1:] == ["--help"])
        delete_index = next(i for i, call in enumerate(runner.calls) if call[:2] == ["schtasks.exe", "/Delete"])
        self.assertLess(probe_index, delete_index)
        self.assertEqual(result["phase"], "commit")
        self.assertEqual([item["phase"] for item in result["history"]], ["prepare", "verify", "commit"])
        self.assertEqual(json.loads(self.journal.read_text(encoding="utf-8"))["phase"], "commit")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["startup_owner"], "task-scheduler")
        lifecycle = json.loads(
            (self.archive / "imports" / "codex" / "collector-lifecycle.json").read_text(encoding="utf-8")
        )
        self.assertEqual(lifecycle["format"], "memory-wuxian-collector-lifecycle-v1")
        self.assertEqual(lifecycle["generation"], result["generation_id"])
        self.assertEqual(lifecycle["archive_root"], str(self.archive))
        self.assertEqual(lifecycle["expected_command"], self.command)
        self.assertEqual(len(lifecycle["startup_owners"]), 1)
        self.assertEqual(lifecycle["startup_owners"][0]["pid_identity"], "required")

    def test_failure_rolls_back_exact_task_pointer_and_manifest(self):
        old_command = [str(self.base / "old collector.exe"), "--archive-root", "old"]
        old_xml = windows.task_xml(old_command)
        old_manifest = b'{"old":true}\n'
        old_pointer = (str(self.archive) + "\r\n").encode("utf-8")
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_bytes(old_manifest)
        self.pointer.write_bytes(old_pointer)
        old_run_key = '"C:\\旧 collector.exe" --archive-root "C:\\旧 archive"'
        runner = FakeRunner(old_xml, fail_run=True, run_key=old_run_key)
        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke(runner)
        self.assertEqual(windows.inspect_task_xml(runner.task_xml), windows.inspect_task_xml(old_xml))
        self.assertEqual(self.manifest.read_bytes(), old_manifest)
        self.assertEqual(self.pointer.read_bytes(), old_pointer)
        self.assertEqual(runner.run_key, old_run_key)
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rollback")
        self.assertEqual(
            [item["phase"] for item in journal["history"]],
            ["prepare", "verify", "rollback"],
        )

    def test_namespaced_run_value_is_bound_into_journal_and_registry_calls(self):
        runner = FakeRunner(run_key="legacy-rehearsal-value")
        run_value = "MemoryWuxianRehearsal-collector"
        result = windows.install_transaction(
            task_name="MemoryWuxianRehearsal-collector",
            command=self.command,
            archive_root=self.archive,
            command_manifest=self.manifest,
            pointer=self.pointer,
            journal_path=self.journal,
            run_value=run_value,
            runner=runner,
            readiness_probe=self.ready_probe,
        )
        self.assertEqual(result["run_value_name"], run_value)
        registry_calls = [call for call in runner.calls if call[:2] == ["reg.exe", "QUERY"]]
        self.assertTrue(registry_calls)
        self.assertEqual(registry_calls[0][registry_calls[0].index("/V") + 1], run_value)

    def test_verified_previous_task_must_pass_effect_probe_after_rollback(self):
        old_command = list(self.command)
        old_command[0] = str(self.base / "old collector.exe")
        old_xml = windows.task_xml(old_command)
        lifecycle = self.archive / "imports" / "codex" / "collector-lifecycle.json"
        lifecycle.parent.mkdir(parents=True)
        lifecycle.write_text(json.dumps({
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": "old-generation",
            "archive_root": str(self.archive),
            "expected_command": old_command,
            "startup_owners": [{
                "owner_id": f"task:{windows.DEFAULT_TASK_NAME}",
                "kind": "windows-task",
                "generation": "old-generation",
                "archive_root": str(self.archive),
                "command": old_command,
                "pid_identity": "required",
            }],
        }) + "\n", encoding="utf-8")
        candidate_watermark = (
            datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        telemetry = self.archive / "imports" / "codex" / "collector-telemetry.json"
        telemetry.write_text(json.dumps({
            "pid": 991,
            "archive_watermark": candidate_watermark,
        }), encoding="utf-8")
        calls = []

        def fail_then_restore(_archive_root, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("candidate effect failed")
            return {
                "format_version": 2,
                "phase": "ready",
                "ready": True,
                "pid": 413,
                "source_watermark": kwargs["minimum_watermark"],
                "archive_watermark": kwargs["minimum_watermark"],
                "last_archive_update": datetime.now(timezone.utc).isoformat(),
            }

        with self.assertRaisesRegex(RuntimeError, "candidate effect failed"):
            windows.install_transaction(
                task_name=windows.DEFAULT_TASK_NAME,
                command=self.command,
                archive_root=self.archive,
                command_manifest=self.manifest,
                pointer=self.pointer,
                journal_path=self.journal,
                runner=FakeRunner(old_xml),
                readiness_probe=fail_then_restore,
            )
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "rollback")
        self.assertEqual(journal["rollback_verification"]["pid"], 413)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["previous_pid"], 991)
        self.assertGreater(
            datetime.fromisoformat(calls[1]["minimum_watermark"].replace("Z", "+00:00")),
            datetime.fromisoformat(candidate_watermark.replace("Z", "+00:00")),
        )

    def test_candidate_probe_failure_never_replaces_old_task(self):
        old_command = [str(self.base / "old collector.exe"), "--archive-root", "old"]
        old_xml = windows.task_xml(old_command)
        runner = FakeRunner(old_xml, fail_probe=True)
        with self.assertRaisesRegex(RuntimeError, "candidate failed"):
            self.invoke(runner)
        self.assertEqual(runner.task_xml, old_xml)
        self.assertFalse(
            any(call[:2] == ["schtasks.exe", "/Delete"] for call in runner.calls)
        )
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual([item["phase"] for item in journal["history"]], ["prepare", "rollback"])

    def test_transaction_never_changes_existing_archive_bytes(self):
        raw = self.archive / "raw" / "2026" / "08" / "record.md"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"exact existing archive bytes\r\n")
        before = raw.read_bytes()
        self.invoke(FakeRunner())
        self.assertEqual(raw.read_bytes(), before)

    def test_generation_failure_restores_old_tree_and_keeps_failed_candidate(self):
        skill_root = self.base / "memory-wuxian"
        candidate = self.base / "候補 generation"
        for root, marker in ((skill_root, b"old"), (candidate, b"new")):
            (root / "bin").mkdir(parents=True)
            (root / "SKILL.md").write_bytes(marker)
            (root / "config.yaml").write_bytes(marker)
            (root / "bin" / "memory-wuxian-collector.exe").write_bytes(marker)
        command = list(self.command)
        command[0] = str(skill_root / "bin" / "memory-wuxian-collector.exe")
        command[command.index("--config") + 1] = str(skill_root / "config.yaml")
        candidate_command = list(command)
        candidate_command[0] = str(candidate / "bin" / "memory-wuxian-collector.exe")
        candidate_command[candidate_command.index("--config") + 1] = str(candidate / "config.yaml")
        with self.assertRaises(subprocess.CalledProcessError):
            windows.install_generation_transaction(
                candidate_root=candidate,
                skill_root=skill_root,
                runtime_directory=self.base / "runtime",
                task_name=windows.DEFAULT_TASK_NAME,
                command=command,
                candidate_command=candidate_command,
                archive_root=self.archive,
                command_manifest=self.manifest,
                pointer=self.pointer,
                runner=FakeRunner(windows.task_xml(command), fail_run=True),
                readiness_probe=self.ready_probe,
                defer_commit=True,
            )
        self.assertEqual((skill_root / "SKILL.md").read_bytes(), b"old")
        failed = list((self.base / "runtime" / "transactions").glob("*/failed-generation/SKILL.md"))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_bytes(), b"new")

    def test_deferred_generation_commit_preserves_previous_generation(self):
        skill_root = self.base / "memory-wuxian"
        candidate = self.base / "candidate"
        for root, marker in ((skill_root, b"old"), (candidate, b"new")):
            (root / "bin").mkdir(parents=True)
            (root / "SKILL.md").write_bytes(marker)
            (root / "config.yaml").write_bytes(marker)
            (root / "bin" / "memory-wuxian-collector.exe").write_bytes(marker)
        command = list(self.command)
        command[0] = str(skill_root / "bin" / "memory-wuxian-collector.exe")
        command[command.index("--config") + 1] = str(skill_root / "config.yaml")
        runner = FakeRunner(windows.task_xml(command))
        journal, journal_path = windows.install_generation_transaction(
            candidate_root=candidate,
            skill_root=skill_root,
            runtime_directory=self.base / "runtime",
            task_name=windows.DEFAULT_TASK_NAME,
            command=command,
            candidate_command=list(command),
            archive_root=self.archive,
            command_manifest=self.manifest,
            pointer=self.pointer,
            runner=runner,
            readiness_probe=self.ready_probe,
            defer_commit=True,
        )
        self.assertEqual(journal["phase"], "verify")
        self.assertEqual((skill_root / "SKILL.md").read_bytes(), b"new")
        previous = Path(journal["generation"]["previous_root"])
        self.assertEqual((previous / "SKILL.md").read_bytes(), b"old")
        committed = windows.commit_transaction(journal_path, runner=runner)
        self.assertEqual(committed["phase"], "commit")
        self.assertTrue(any(call[:2] == ["reg.exe", "DELETE"] for call in runner.calls))

    def test_generation_transaction_uses_exact_outer_journal_path(self):
        skill_root = self.base / "installed-skill"
        candidate = self.base / "candidate-exact-journal"
        for root, marker in ((skill_root, b"old"), (candidate, b"new")):
            (root / "bin").mkdir(parents=True)
            (root / "SKILL.md").write_bytes(marker)
            (root / "config.yaml").write_bytes(marker)
            (root / "bin" / "memory-wuxian-collector.exe").write_bytes(marker)
        command = list(self.command)
        command[0] = str(skill_root / "bin/memory-wuxian-collector.exe")
        command[command.index("--config") + 1] = str(skill_root / "config.yaml")
        candidate_command = list(command)
        candidate_command[0] = str(candidate / "bin/memory-wuxian-collector.exe")
        candidate_command[candidate_command.index("--config") + 1] = str(candidate / "config.yaml")
        exact = self.base / "outer-resources/collector/install-journal.json"
        _journal, observed = windows.install_generation_transaction(
            candidate_root=candidate,
            skill_root=skill_root,
            runtime_directory=self.base / "unused-runtime",
            task_name=windows.DEFAULT_TASK_NAME,
            command=command,
            candidate_command=candidate_command,
            archive_root=self.archive,
            command_manifest=self.manifest,
            pointer=self.pointer,
            runner=FakeRunner(windows.task_xml(command)),
            readiness_probe=self.ready_probe,
            defer_commit=True,
            journal_path=exact,
        )
        self.assertEqual(observed, exact.resolve())
        self.assertTrue(exact.is_file())
        self.assertFalse((self.base / "unused-runtime/transactions").exists())

    def test_generation_rollback_restores_old_tree(self):
        candidate = self.base / "package candidate"
        skill = self.base / ".codex" / "skills" / "memory-wuxian"
        for root, marker in ((candidate, "new"), (skill, "old")):
            (root / "bin").mkdir(parents=True)
            (root / "SKILL.md").write_text(marker, encoding="utf-8")
            (root / "config.yaml").write_text("backup: false\n", encoding="utf-8")
            (root / "bin" / "memory-wuxian-collector.exe").write_bytes(marker.encode())
        command = windows.collector_command(
            skill / "bin" / "memory-wuxian-collector.exe",
            self.archive,
            skill / "config.yaml",
            self.sessions,
            self.python,
            self.codex,
            "2026-08-18T00:00:00Z",
            400,
        )
        candidate_command = list(command)
        candidate_command[0] = str(candidate / "bin" / "memory-wuxian-collector.exe")
        candidate_command[candidate_command.index("--config") + 1] = str(candidate / "config.yaml")
        lifecycle = self.archive / "imports" / "codex" / "collector-lifecycle.json"
        lifecycle.parent.mkdir(parents=True)
        lifecycle.write_text(json.dumps({
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": "old-generation",
            "archive_root": str(self.archive),
            "expected_command": command,
            "startup_owners": [{
                "owner_id": f"task:{windows.DEFAULT_TASK_NAME}",
                "kind": "windows-task",
                "generation": "old-generation",
                "archive_root": str(self.archive),
                "command": command,
                "pid_identity": "required",
            }],
        }) + "\n", encoding="utf-8")
        runner = FakeRunner(windows.task_xml(command))
        journal, journal_path = windows.install_generation_transaction(
            candidate_root=candidate,
            skill_root=skill,
            runtime_directory=self.base / "runtime",
            task_name=windows.DEFAULT_TASK_NAME,
            command=command,
            candidate_command=candidate_command,
            archive_root=self.archive,
            command_manifest=self.manifest,
            pointer=self.pointer,
            runner=runner,
            readiness_probe=self.ready_probe,
            defer_commit=True,
        )
        self.assertEqual(journal["phase"], "verify")
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "new")
        rollback_calls = []

        def restored_probe(_archive_root, **kwargs):
            rollback_calls.append(kwargs)
            if len(rollback_calls) == 1:
                raise RuntimeError("restored collector effect unavailable")
            return {
                "pid": 777,
                "archive_watermark": kwargs["minimum_watermark"],
            }

        with self.assertRaisesRegex(RuntimeError, "effect unavailable"):
            windows.rollback_transaction(
                journal_path,
                runner=runner,
                readiness_probe=restored_probe,
                error="synthetic failure",
            )
        interrupted = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertFalse(interrupted["generation"]["switched"])
        self.assertEqual(
            interrupted["rollback_recovery"]["status"],
            "restored-awaiting-verification",
        )
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "old")
        rolled_back = windows.rollback_transaction(
            journal_path,
            runner=runner,
            readiness_probe=restored_probe,
            error="synthetic failure",
        )
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "old")
        self.assertEqual(rolled_back["rollback_verification"]["status"], "passed")
        self.assertEqual(len(rollback_calls), 2)

    def test_watermark_probe_rejects_stale_or_unmatched_progress(self):
        telemetry = self.archive / "imports" / "codex" / "collector-telemetry.json"
        telemetry.parent.mkdir(parents=True)
        telemetry.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "phase": "ready",
                    "pid": 9,
                    "ready": True,
                    "updated_at": "2026-08-18T00:00:00Z",
                    "source_watermark": "new",
                    "archive_watermark": "old",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "watermark"):
            windows.wait_for_watermark_progress(
                self.archive,
                previous_pid=8,
                started_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
                timeout_seconds=0.01,
                sleep=lambda _seconds: None,
            )

    def test_installed_effect_requires_watermark_newer_than_activation(self):
        telemetry = self.archive / "imports" / "codex" / "collector-telemetry.json"
        telemetry.parent.mkdir(parents=True)
        payload = {
            "format_version": 2,
            "phase": "ready",
            "pid": 9,
            "ready": True,
            "updated_at": "2026-08-18T00:00:03Z",
            "last_archive_update": "2026-08-18T00:00:03Z",
            "source_watermark": "2026-08-18T00:00:01Z",
            "archive_watermark": "2026-08-18T00:00:01Z",
        }
        telemetry.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "effect probe"):
            windows.wait_for_watermark_progress(
                self.archive,
                previous_pid=8,
                started_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
                minimum_watermark="2026-08-18T00:00:02Z",
                timeout_seconds=0.01,
                sleep=lambda _seconds: None,
            )
        payload["source_watermark"] = "2026-08-18T00:00:02Z"
        payload["archive_watermark"] = "2026-08-18T00:00:02Z"
        telemetry.write_text(json.dumps(payload), encoding="utf-8")
        result = windows.wait_for_watermark_progress(
            self.archive,
            previous_pid=8,
            started_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
            minimum_watermark="2026-08-18T00:00:02Z",
            timeout_seconds=0.01,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(result["archive_watermark"], "2026-08-18T00:00:02Z")

    def test_run_key_backend_is_not_a_supported_authority(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            windows.build_parser().parse_args(
                ["--archive-root", str(self.archive), "--backend", "run-key"]
            )

    def test_windows_package_stages_candidate_and_closes_the_journal(self):
        installer = (ROOT / "packaging" / "windows" / "MemoryWuxian.iss").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "packaging" / "windows" / "install.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('DestDir: "{tmp}\\MemoryWuxian\\candidate"', installer)
        self.assertNotIn('Source: "{#SourceRoot}\\*"; DestDir: "{app}"', installer)
        for token in (
            "install_windows_transaction.py", "windows_installer_broker.py",
            "--prepare-only", "--source-entrypoint", "--candidate-root",
            "--launch-manifest", "--request-output", "--nonce-root",
        ):
            self.assertIn(token, script.lower())
        for legacy_token in ("--defer-commit", "--commit-journal", "--rollback-journal"):
            self.assertNotIn(legacy_token, script.lower())
        self.assertNotIn("reg.exe add", script.lower())


if __name__ == "__main__":
    unittest.main()
