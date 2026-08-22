from __future__ import annotations

import json
import plistlib
import subprocess
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import install_macos_transaction as transaction
from scripts.collector_lifecycle import inspect_startup_owner
from tests.support.macos import (
    RecordingRunner,
    temporary_root,
    write_launch_agent_plist,
)


class FakeRunner(RecordingRunner):
    def __init__(self, *, fail_dashboard: bool = False) -> None:
        super().__init__()
        self.fail_dashboard = fail_dashboard

    def __call__(self, command, **kwargs):
        command = self.record(command)
        if command[:2] == ["/bin/launchctl", "print"]:
            return self.completed(command, stdout="pid = 42\n")
        if self.fail_dashboard and "install_dashboard_app_macos.py" in " ".join(command):
            raise subprocess.CalledProcessError(1, command, stderr="dashboard failed")
        return self.completed(command)


@contextmanager
def quiesced(*args, **kwargs):
    yield {"status": "quiesced", "previous_pid": 41, "recovery_debt_present": False}


class MacosTransactionV216Test(unittest.TestCase):
    def setUp(self):
        self.temporary, temporary_root_path = temporary_root(
            ignore_cleanup_errors=True
        )
        root = temporary_root_path / ("用户 目录 " + "很长" * 4)
        self.home = root / "home"
        self.source = root / "候选 包"
        self.skill = self.home / ".codex" / "skills" / "memory-wuxian"
        self.archive = root / "记忆 归档"
        self.sessions = root / "会话 sessions"
        self.python = root / "runtime" / "python"
        self.codex = root / "应用" / "codex"
        for directory in (self.source / "bin", self.source / "scripts", self.skill, self.archive, self.sessions):
            directory.mkdir(parents=True, exist_ok=True)
        for path in (
            self.source / "bin" / "memory-wuxian-collector",
            self.source / "bin" / "memory-wuxian-envelope",
            self.source / "scripts" / "install_codex_autosync.py",
            self.source / "scripts" / "install_maintenance_supervisor.py",
            self.source / "scripts" / "install_dashboard_app_macos.py",
            self.python,
            self.codex,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"tool\n")
        (self.source / "config.yaml").write_text("format_version: 1\n", encoding="utf-8")
        (self.source / "SKILL.md").write_text("# MemoryWuxian\n", encoding="utf-8")
        (self.skill / "config.yaml").write_text("format_version: 1\n", encoding="utf-8")
        (self.skill / "marker").write_bytes(b"prior-generation")
        sentinel = self.archive / "raw" / "sentinel.bin"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"\x00archive-bytes\xff")
        self.sentinel = sentinel

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, runner: FakeRunner):
        expected_command = [
            str(self.skill / "bin" / "memory-wuxian-collector"),
            "--archive-root",
            str(self.archive),
            "--config",
            str(self.skill / "config.yaml"),
            "--sessions-root",
            str(self.sessions),
            "--since",
            "2026-08-01T00:00:00Z",
            "--debounce-ms",
            "400",
        ]
        telemetry = {
            "format_version": 2,
            "ready": True,
            "phase": "ready",
            "pid": 42,
            "updated_at": "2026-08-18T00:00:00Z",
            "source_watermark": "2026-08-18T00:00:00Z",
            "archive_watermark": "2026-08-18T00:00:00Z",
            "last_archive_update": "2026-08-18T00:00:00Z",
        }
        with (
            patch("pathlib.Path.home", return_value=self.home),
            patch.object(transaction.os, "getuid", create=True, return_value=501),
            patch.object(transaction, "probe_candidate", return_value={"status": "passed"}),
            patch.object(transaction, "quiesce_collector_for_cutover", quiesced),
            patch.object(transaction, "wait_for_collector", return_value=telemetry),
            patch.object(
                transaction,
                "validate_installed_launch_contract",
                return_value={"launchd_pid": 42, "expected_command": expected_command},
            ),
            patch.object(transaction, "retire_legacy_macos_semantic_backfill", return_value={"status": "retired"}),
            patch.object(transaction, "migrate_config", return_value={}),
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

    def test_commit_receipt_precedes_pruning_and_second_run_is_idempotent(self):
        runner = FakeRunner()
        first = self.invoke(runner)
        self.assertEqual(first["status"], "installed")
        self.assertTrue(Path(first["commit_receipt"]).is_file())
        self.assertIsNone(first["prior_generation_path"])
        self.assertTrue(first["rollback_generation_pruned_after_commit"])
        journal = json.loads((Path(first["commit_receipt"]).parent / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "commit")
        lifecycle_path = self.archive / "imports" / "codex" / "collector-lifecycle.json"
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        self.assertTrue(inspect_startup_owner(lifecycle)["ok"])
        self.assertEqual(lifecycle["generation"], first["candidate_generation"])
        self.assertEqual(lifecycle["archive_root"], str(self.archive))
        self.assertEqual(lifecycle["verified_telemetry"]["pid"], 42)
        self.assertEqual(len(lifecycle["startup_owners"]), 1)
        self.assertEqual(lifecycle["startup_owners"][0]["pid_identity"], "required")
        self.assertEqual(self.sentinel.read_bytes(), b"\x00archive-bytes\xff")

        calls_before = len(runner.calls)
        second = self.invoke(runner)
        self.assertEqual(second["status"], "installed")
        self.assertTrue(second["idempotent"])
        new_calls = runner.calls[calls_before:]
        self.assertFalse(any("install_codex_autosync.py" in " ".join(call) for call in new_calls))
        self.assertEqual(self.sentinel.read_bytes(), b"\x00archive-bytes\xff")

    def test_failure_restores_prior_and_retains_rollback_receipt(self):
        lifecycle_path = self.archive / "imports" / "codex" / "collector-lifecycle.json"
        lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
        old_command = [
            str(self.skill / "bin" / "memory-wuxian-collector"),
            "--archive-root", str(self.archive),
            "--config", str(self.skill / "config.yaml"),
            "--sessions-root", str(self.sessions),
            "--since", "2026-08-01T00:00:00Z",
            "--debounce-ms", "400",
        ]
        old_lifecycle = (json.dumps({
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": "old-generation",
            "archive_root": str(self.archive),
            "expected_command": old_command,
            "startup_owners": [{
                "owner_id": f"launchd:{transaction.COLLECTOR_LABEL}",
                "kind": "launch-agent",
                "generation": "old-generation",
                "archive_root": str(self.archive),
                "command": old_command,
                "pid_identity": "required",
            }],
        }) + "\n").encode("utf-8")
        lifecycle_path.write_bytes(old_lifecycle)
        plist = write_launch_agent_plist(self.home, transaction.COLLECTOR_LABEL, {
            "Label": transaction.COLLECTOR_LABEL,
            "ProgramArguments": old_command,
            "EnvironmentVariables": {
                "RUST_BACKTRACE": "1",
                "MEMORY_WUXIAN_PYTHON": str(self.python),
                "MEMORY_WUXIAN_CODEX": str(self.codex),
            },
        })
        with self.assertRaisesRegex(RuntimeError, "dashboard failed"):
            self.invoke(FakeRunner(fail_dashboard=True))
        self.assertEqual((self.skill / "marker").read_bytes(), b"prior-generation")
        transaction_roots = list((self.home / ".codex" / "updates" / "memory-wuxian" / "transactions").iterdir())
        self.assertEqual(len(transaction_roots), 1)
        self.assertTrue((transaction_roots[0] / "rollback-receipt.json").is_file())
        rollback = json.loads(
            (transaction_roots[0] / "rollback-receipt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(rollback["restored_effect"]["launchd_pid"], 42)
        journal = json.loads((transaction_roots[0] / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["state"], "rollback")
        self.assertEqual(journal["events"][-1]["details"]["phase"], "restored")
        self.assertEqual(lifecycle_path.read_bytes(), old_lifecycle)
        self.assertEqual(self.sentinel.read_bytes(), b"\x00archive-bytes\xff")

    def test_interrupted_switch_recovers_once_and_second_recovery_is_noop(self):
        update = self.home / ".codex/updates/memory-wuxian"
        staging = update / "staging"
        generations = update / "generations"
        transactions = update / "transactions"
        failed = update / "failed"
        for path in (staging, generations, transactions, failed):
            path.mkdir(parents=True)
        candidate = staging / "candidate-build"
        transaction.prepare_candidate(self.source, candidate, self.skill)
        candidate_generation, candidate_manifest = transaction.tree_generation(candidate)
        final_candidate = staging / candidate_generation
        candidate.replace(final_candidate)
        prior_generation, prior_manifest = transaction.tree_generation(self.skill)
        paths = {
            "source_root": str(self.source),
            "skill_root": str(self.skill),
            "archive_root": str(self.archive),
            "sessions_root": str(self.sessions),
            "python_executable": str(self.python),
            "codex_cli": str(self.codex),
        }
        transaction_id = transaction.transaction_identifier(candidate_generation, paths)
        transaction_root = transactions / transaction_id
        transaction_root.mkdir()
        prior_path = generations / f"{prior_generation}-{transaction_id}"
        self.skill.replace(prior_path)
        final_candidate.replace(self.skill)
        plist = self.home / "Library/LaunchAgents/com.memorywuxian.codex-sync.plist"
        maintenance = self.home / "Library/LaunchAgents/maintenance.plist"
        pointer = self.home / ".codex/memory-wuxian-active-root.txt"
        old_plist = plistlib.dumps({"Label": transaction.COLLECTOR_LABEL})
        old_lifecycle = b'{"format":"memory-wuxian-collector-lifecycle-v1"}\n'
        (transaction_root / "collector.plist").write_bytes(old_plist)
        (transaction_root / "collector-lifecycle.json").write_bytes(old_lifecycle)
        journal = {
            "format_version": 1,
            "transaction_id": transaction_id,
            "state": "prepare",
            "created_at": "2026-08-18T00:00:00+00:00",
            "updated_at": "2026-08-18T00:00:00+00:00",
            "events": [{"stage": "prepare", "timestamp": "2026-08-18T00:00:00+00:00", "details": {}}],
            "paths": paths,
            "candidate_generation": candidate_generation,
            "candidate_manifest": candidate_manifest,
            "prior_generation": prior_generation,
            "prior_manifest": prior_manifest,
            "snapshots": {
                "collector_plist": {
                    "existed": True,
                    "sha256": transaction.hashlib.sha256(old_plist).hexdigest(),
                },
                "maintenance_plist": {"existed": False},
                "active_root_pointer": {"existed": False},
                "collector_lifecycle": {
                    "existed": True,
                    "sha256": transaction.hashlib.sha256(old_lifecycle).hexdigest(),
                },
            },
        }
        transaction.atomic_json(transaction_root / "journal.json", journal)
        runner = FakeRunner()
        restored_effect = {"status": "passed", "generation": "old-generation"}

        with (
            patch.object(transaction, "quiesce_collector_for_cutover", quiesced),
            patch.object(transaction.os, "getuid", create=True, return_value=501),
            patch.object(
                transaction,
                "verify_restored_collector_effect",
                return_value=restored_effect,
            ) as verify_restored,
        ):
            transaction.recover_interrupted_transactions(
                transactions_root=transactions,
                staging_root=staging,
                generations_root=generations,
                failed_root=failed,
                skill_root=self.skill,
                plist=plist,
                maintenance_plist=maintenance,
                active_root_pointer=pointer,
                runner=runner,
            )
            calls_after_first = len(runner.calls)
            transaction.recover_interrupted_transactions(
                transactions_root=transactions,
                staging_root=staging,
                generations_root=generations,
                failed_root=failed,
                skill_root=self.skill,
                plist=plist,
                maintenance_plist=maintenance,
                active_root_pointer=pointer,
                runner=runner,
            )

        self.assertEqual((self.skill / "marker").read_bytes(), b"prior-generation")
        self.assertTrue((transaction_root / "rollback-receipt.json").is_file())
        rollback = json.loads((transaction_root / "rollback-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(rollback["restored_effect"], restored_effect)
        verify_restored.assert_called_once()
        self.assertEqual(len(runner.calls), calls_after_first)

    def test_exact_launch_contract_rejects_archive_argument_drift(self):
        activation = self.archive / "imports" / "codex" / "collector-activation.json"
        activation.parent.mkdir(parents=True, exist_ok=True)
        activation.write_text(json.dumps({"since": "2026-08-01T00:00:00Z"}), encoding="utf-8")
        plist = self.home / "Library" / "LaunchAgents" / "collector.plist"
        plist.parent.mkdir(parents=True)
        arguments = [
            str(self.skill / "bin" / "memory-wuxian-collector"), "--archive-root", str(self.archive),
            "--config", str(self.skill / "config.yaml"), "--sessions-root", str(self.sessions),
            "--since", "2026-08-01T00:00:00Z", "--debounce-ms", "400",
        ]
        payload = {
            "Label": transaction.COLLECTOR_LABEL,
            "ProgramArguments": arguments,
            "StandardOutPath": str(activation.parent / "launch-agent.stdout.log"),
            "StandardErrorPath": str(activation.parent / "launch-agent.stderr.log"),
            "EnvironmentVariables": {
                "RUST_BACKTRACE": "1",
                "MEMORY_WUXIAN_PYTHON": str(self.python),
                "MEMORY_WUXIAN_CODEX": str(self.codex),
            },
        }
        plist.write_bytes(plistlib.dumps(payload))
        pointer = self.home / ".codex" / "memory-wuxian-active-root.txt"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_bytes(f"{self.archive}\n".encode("utf-8"))
        telemetry = {
            "pid": 42,
            "ready": True,
            "phase": "ready",
            "source_watermark": "2026-08-18T00:00:00Z",
            "archive_watermark": "2026-08-18T00:00:00Z",
            "last_archive_update": "2026-08-18T00:00:00Z",
        }
        with patch.object(transaction.os, "getuid", create=True, return_value=501):
            transaction.validate_installed_launch_contract(
                skill_root=self.skill, archive_root=self.archive, sessions_root=self.sessions,
                python_executable=self.python, codex_cli=self.codex,
                plist=plist, active_root_pointer=pointer, telemetry=telemetry,
                previous_archive_watermark=None, runner=FakeRunner(),
            )
        with (
            patch.object(transaction.os, "getuid", create=True, return_value=501),
            self.assertRaisesRegex(RuntimeError, "effect probe watermark"),
        ):
            transaction.validate_installed_launch_contract(
                skill_root=self.skill, archive_root=self.archive, sessions_root=self.sessions,
                python_executable=self.python, codex_cli=self.codex,
                plist=plist, active_root_pointer=pointer, telemetry=telemetry,
                previous_archive_watermark=None,
                required_effect_watermark="2026-08-18T00:00:01Z",
                effect_started_at=datetime(2026, 8, 18, 0, 0, 0, tzinfo=timezone.utc),
                runner=FakeRunner(),
            )
        payload["ProgramArguments"][2] = str(self.archive.parent / "wrong")
        plist.write_bytes(plistlib.dumps(payload))
        with (
            patch.object(transaction.os, "getuid", create=True, return_value=501),
            self.assertRaisesRegex(RuntimeError, "command does not exactly match"),
        ):
            transaction.validate_installed_launch_contract(
                skill_root=self.skill, archive_root=self.archive, sessions_root=self.sessions,
                python_executable=self.python, codex_cli=self.codex,
                plist=plist, active_root_pointer=pointer, telemetry=telemetry,
                previous_archive_watermark=None, runner=FakeRunner(),
            )

    def test_postinstall_does_not_recursively_reown_or_reinitialize_known_archive(self):
        script = (Path(__file__).parents[1] / "packaging" / "macos" / "scripts" / "postinstall").read_text(encoding="utf-8")
        self.assertNotIn('chown -R "$console_user":staff "$archive_root"', script)
        self.assertIn('if [[ ! -f "$archive_root/state.json" ]]', script)
        self.assertIn("refuses to initialize a non-empty unknown archive", script)


if __name__ == "__main__":
    unittest.main()
