import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import install_codex_autosync_windows as windows


class FakeRunner:
    def __init__(self, old_xml=None, fail_run=False, fail_probe=False, run_key=None):
        self.task_xml = old_xml
        self.fail_run = fail_run
        self.fail_probe = fail_probe
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
            payload = f"    {windows.RUN_VALUE}    REG_SZ    {self.run_key}\r\n".encode("utf-8")
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
        self.assertEqual(definition["command"], str(self.collector))
        self.assertIn(str(long_archive), definition["arguments"])
        self.assertTrue(definition["hidden"])
        self.assertEqual(definition["multiple_instances"], "IgnoreNew")
        self.assertEqual(definition["restart_interval"], "PT30S")
        self.assertEqual(definition["restart_count"], "5")
        self.assertNotIn("powershell", definition["command"].lower())

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
        old_pointer = "C:\\旧 archive\r\n".encode("utf-8")
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
        windows.rollback_transaction(journal_path, runner=runner, error="synthetic failure")
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "old")

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
        for token in ("--candidate-root", "--defer-commit", "--commit-journal", "--rollback-journal"):
            self.assertIn(token, script.lower())
        self.assertNotIn("reg.exe add", script.lower())


if __name__ == "__main__":
    unittest.main()
