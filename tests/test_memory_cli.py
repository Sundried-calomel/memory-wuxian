import base64
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
from platform_lock import exclusive_lock
from memory_dashboard import (
    DashboardSnapshotCache,
    archive_storage_bytes,
    collector_telemetry,
    dashboard_data,
    dashboard_health,
    debt_status_projection,
    estimate_context_tokens,
)
from memory_cli import MemoryStore, filesystem_native_path, resolve_root
from semantic_worker import (
    build_prompt_payload,
    pack_source_records,
    pack_source_summaries,
    unpack_source_records,
    unpack_source_summaries,
)
from semantic_backfill import ordered_pending_jobs, run_backfill

CLI = SKILL_ROOT / "scripts" / "memory_cli.py"
INSTALLER = SKILL_ROOT / "scripts" / "install_codex_autosync.py"
WINDOWS_INSTALLER = SKILL_ROOT / "scripts" / "install_codex_autosync_windows.py"
WINDOWS_BOOTSTRAP = SKILL_ROOT / "scripts" / "bootstrap_windows.ps1"
AGENT_RULES_INSTALLER = SKILL_ROOT / "scripts" / "install_agent_rules.py"
SEMANTIC_WORKER = SKILL_ROOT / "scripts" / "semantic_worker.py"
NATIVE_MANIFEST = SKILL_ROOT / "native-collector" / "Cargo.toml"
NATIVE_BINARY = SKILL_ROOT / "bin" / (
    "memory-wuxian-collector.exe" if sys.platform == "win32" else "memory-wuxian-collector"
)


class MemoryCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "memory"
        self.config = self.base / "config.yaml"
        self.config.write_text(
            """memory:
  root_directory: "./memory"
summaries:
  level_1_trigger_rounds: 2
  level_1_trigger_characters: 20000
  automatic_semantic_jobs: true
  higher_level_trigger_count: 2
  maximum_summary_depth: 4
retrieval:
  verify_against_raw: true
  context_messages_before: 1
  context_messages_after: 1
  maximum_initial_candidates: 10
  log_queries: true
safety:
  redact_secrets: true
""",
            encoding="utf-8",
        )
        self.run_cli("init")

    def tearDown(self):
        self.temporary.cleanup()

    def test_lossless_summary_payload_round_trip(self):
        records = [
            {
                "record_type": "raw_message",
                "sequence": number,
                "message_id": f"codex-thread-{number:08d}-u",
                "conversation_id": "codex:thread",
                "timestamp": f"2026-07-20T12:00:0{number}+09:00",
                "speaker": "user",
                "round_number": 1,
                "round_scope": "conversation",
                "reply_to": None,
                "text": "完全保留的正文" * number,
                "completes_round": False,
                "redacted": False,
                "source": {
                    "kind": "codex-rollout-jsonl",
                    "session_id": "thread",
                    "path": "/tmp/rollout.jsonl",
                    "line": number,
                    "phase": "user",
                },
            }
            for number in (1, 2)
        ]
        from memory_cli import raw_record_sha256
        for record in records:
            record["content_sha256"] = raw_record_sha256(record)
        packed = pack_source_records(records)
        self.assertEqual(unpack_source_records(packed), records)
        prompt_payload = build_prompt_payload({
            "job_id": "job-1",
            "source_message_ids": [record["message_id"] for record in records],
            "source_records": records,
        })
        self.assertNotIn("source_records", prompt_payload["task"])
        self.assertNotIn("source_message_ids", prompt_payload["task"])
        self.assertEqual(
            prompt_payload["allowed_source_message_ids"],
            [record["message_id"] for record in records],
        )
        self.assertEqual(
            unpack_source_records(prompt_payload["lossless_source_records"]),
            records,
        )

    def test_lossless_summary_payload_preserves_sparse_and_null_fields(self):
        records = [
            {
                "record_type": "raw_message",
                "sequence": 1,
                "message_id": "message-1",
                "source": {"kind": "codex", "optional": None},
            },
            {
                "record_type": "raw_message",
                "sequence": 2,
                "message_id": "message-2",
                "source": {"kind": "codex"},
            },
        ]
        from memory_cli import raw_record_sha256
        for record in records:
            record["content_sha256"] = raw_record_sha256(record)

        packed = pack_source_records(records)

        self.assertEqual(unpack_source_records(packed), records)
        optional_index = packed["columns"].index("source.optional")
        self.assertEqual(
            [row[optional_index] for row in packed["presence"]],
            [True, False],
        )

    def test_lossless_parent_summary_payload_round_trip(self):
        summaries = [
            {
                "summary_id": f"L1-{number:06d}",
                "metadata": {
                    "summary_level": 1,
                    "conversation_id": "codex:thread",
                    "source_start_sequence": number * 10,
                    "source_end_sequence": number * 10 + 9,
                    "concepts": ["分层记忆", f"主题 {number}"],
                },
                "content": f"# L1 摘要 {number}\n\n完整语义内容。\n",
                "summary_sha256": f"digest-{number}",
            }
            for number in (1, 2)
        ]
        packed = pack_source_summaries(summaries)
        self.assertEqual(unpack_source_summaries(packed), summaries)
        prompt_payload = build_prompt_payload({
            "job_id": "job-parent",
            "summary_level": 2,
            "source_summaries": [item["summary_id"] for item in summaries],
            "source_summary_payload": summaries,
        })
        self.assertNotIn("source_summary_payload", prompt_payload["task"])
        self.assertEqual(
            unpack_source_summaries(prompt_payload["lossless_source_summaries"]),
            summaries,
        )

    def test_lossless_parent_summary_payload_preserves_sparse_and_null_fields(self):
        summaries = [
            {
                "summary_id": "L1-000001",
                "metadata": {"level": 1, "source_round_numbers": None},
                "content": "first",
            },
            {
                "summary_id": "L1-000002",
                "metadata": {"level": 1},
                "content": "second",
            },
        ]

        packed = pack_source_summaries(summaries)

        self.assertEqual(unpack_source_summaries(packed), summaries)
        optional_index = packed["columns"].index("metadata.source_round_numbers")
        self.assertEqual(
            [row[optional_index] for row in packed["presence"]],
            [True, False],
        )

    def test_lossless_payload_rejects_malformed_presence_bitmap(self):
        packed = pack_source_summaries([
            {"summary_id": "L1-000001", "metadata": {"level": 1}},
            {"summary_id": "L1-000002", "metadata": {"level": 2}},
        ])
        packed["presence"][0].pop()

        with self.assertRaisesRegex(ValueError, "presence width mismatch"):
            unpack_source_summaries(packed)

    def test_heartbeat_owns_archive_lock_for_consistent_audit(self):
        from memory_cli import MemoryStore, load_simple_yaml

        store = MemoryStore(self.root, load_simple_yaml(self.config))
        events = []

        class RecordingLock:
            def __enter__(self):
                events.append("locked")

            def __exit__(self, *_args):
                events.append("unlocked")

        def audit_inside_lock():
            self.assertEqual(events, ["locked"])
            return {
                "status": "ok",
                "integrity_issues": [],
                "repairable_issues": [],
                "warnings": [],
                "missing_sources": [],
                "state_differences": {},
                "failed_jobs": 0,
            }

        with patch("memory_cli.exclusive_lock", return_value=RecordingLock()):
            with patch.object(store, "audit", side_effect=audit_inside_lock):
                result = store.heartbeat(create_jobs=False)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(events, ["locked", "unlocked"])

    def test_heartbeat_cli_does_not_wrap_its_owned_archive_lock(self):
        completed = self.invoke_cli("heartbeat", "--check-only", timeout=5)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "ok")

    def test_semantic_backfill_prioritizes_higher_summary_levels(self):
        from memory_cli import MemoryStore, load_simple_yaml

        pending = self.root / "pending"
        (pending / "job-low.json").write_text(
            json.dumps({
                "job_id": "job-low",
                "summary_level": 1,
                "created_at": "2026-07-24T10:00:00+09:00",
            }),
            encoding="utf-8",
        )
        (pending / "job-high.json").write_text(
            json.dumps({
                "job_id": "job-high",
                "summary_level": 3,
                "created_at": "2026-07-24T11:00:00+09:00",
            }),
            encoding="utf-8",
        )
        store = MemoryStore(self.root, load_simple_yaml(self.config))
        self.assertEqual(
            [path.name for path in ordered_pending_jobs(store)[:2]],
            ["job-high.json", "job-low.json"],
        )

    def test_semantic_backfill_drains_coalesced_backup_debt_without_summary_jobs(self):
        backup_root = self.base / "desktop-backups"
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                f'backup:\n  enabled: true\n  directory: "{backup_root}"\n'
                "  retention_count: 1\n"
            )
        debt_path = self.root / "pending/backup-debt.json"
        debt_path.write_text(
            json.dumps({
                "format_version": 1,
                "status": "pending",
                "mutation_count": 3,
                "latest_reason": "codex-native-sync",
            }),
            encoding="utf-8",
        )

        result = run_backfill(self.root, self.config, max_jobs=1, dry_run=False)

        self.assertEqual(result["completed_jobs"], 0)
        self.assertTrue(result["backup_debt_drained"])
        self.assertFalse(debt_path.exists())
        self.assertTrue(Path(result["backup"]).is_dir())

    def test_overlap_audit_uses_direct_sources_for_each_summary_level(self):
        from memory_cli import MemoryStore

        level_one = [
            {
                "summary_id": "L1-000001",
                "level": 1,
                "conversation_id": "codex:test",
                "source_start_sequence": 10,
                "source_end_sequence": 20,
            },
            {
                "summary_id": "L1-000002",
                "level": 1,
                "conversation_id": "codex:test",
                "source_start_sequence": 20,
                "source_end_sequence": 30,
            },
        ]
        self.assertEqual(len(MemoryStore.overlapping_ranges(level_one, "summary")), 1)

        disjoint_children_with_overlapping_envelopes = [
            {
                "summary_id": "L2-000001",
                "level": 2,
                "conversation_id": "codex:test",
                "source_start_sequence": 10,
                "source_end_sequence": 100,
                "source_summaries": ["L1-000001", "L1-000003"],
            },
            {
                "summary_id": "L2-000002",
                "level": 2,
                "conversation_id": "codex:test",
                "source_start_sequence": 20,
                "source_end_sequence": 30,
                "source_summaries": ["L1-000002", "L1-000004"],
            },
        ]
        self.assertEqual(
            MemoryStore.overlapping_ranges(
                disjoint_children_with_overlapping_envelopes, "summary"
            ),
            [],
        )

        disjoint_children_with_overlapping_envelopes[1]["source_summaries"] = [
            "L1-000003",
            "L1-000004",
        ]
        warnings = MemoryStore.overlapping_ranges(
            disjoint_children_with_overlapping_envelopes, "summary"
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("reuses child summaries", warnings[0])
        self.assertIn("L1-000003", warnings[0])

    def test_single_file_installer_sources_preserve_external_archives(self):
        mac_postinstall = (SKILL_ROOT / "packaging/macos/scripts/postinstall").read_text(encoding="utf-8")
        windows_install = (SKILL_ROOT / "packaging/windows/install.ps1").read_text(encoding="utf-8")
        windows_uninstall = (SKILL_ROOT / "packaging/windows/uninstall.ps1").read_text(encoding="utf-8")
        inno_setup = (SKILL_ROOT / "packaging/windows/MemoryWuxian.iss").read_text(encoding="utf-8")
        release_workflow = (SKILL_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        mac_transaction = (SKILL_ROOT / "scripts/install_macos_transaction.py").read_text(encoding="utf-8")

        self.assertIn("MemoryWuxianArchive", mac_postinstall)
        self.assertIn("install_macos_transaction.py", mac_postinstall)
        self.assertIn('current / "config.yaml"', mac_transaction)
        self.assertIn('candidate / "config.yaml"', mac_transaction)
        self.assertIn("--python-executable", mac_postinstall)
        self.assertIn("init-node --display-name", mac_postinstall)
        self.assertIn("MemoryWuxianArchive", windows_install)
        self.assertIn("init-node", windows_install)
        self.assertIn("$env:COMPUTERNAME", windows_install)
        self.assertNotIn("Remove-Item", windows_uninstall)
        self.assertIn("PrivilegesRequired=lowest", inno_setup)
        self.assertIn("memory\\*", inno_setup)
        self.assertIn('Flags: onlyifdoesntexist', inno_setup)
        self.assertIn("softprops/action-gh-release@v2", release_workflow)
        self.assertIn("install_auto_update.py", mac_postinstall)
        self.assertIn("install_auto_update.py", windows_install)

    def run_cli(self, *arguments, expect_json=True):
        completed = self.invoke_cli(*arguments)
        if completed.returncode != 0:
            self.fail(f"Command failed: {completed.args}\nstdout={completed.stdout}\nstderr={completed.stderr}")
        return json.loads(completed.stdout) if expect_json else completed.stdout

    def invoke_cli(self, *arguments, timeout=None):
        command = [
            sys.executable,
            str(CLI),
            "--root",
            str(self.root),
            "--config",
            str(self.config),
            *arguments,
        ]
        return subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows encoding regression")
    def test_cli_redirected_output_overrides_inherited_gbk_without_losing_unicode(self):
        command = [
            sys.executable,
            "-c",
            (
                "import sys;"
                f"sys.path.insert(0, {str(SKILL_ROOT / 'scripts')!r});"
                "from console_encoding import configure_unicode_stdio;"
                "configure_unicode_stdio();"
                "print('价格 ¥100・日本語・中文')"
            ),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "gbk"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.decode("utf-8").strip(),
            "价格 ¥100・日本語・中文",
        )

    def append_round(self, number):
        self.run_cli("append", "--speaker", "user", "--text", f"第 {number} 轮讨论分层记忆")
        self.run_cli("append", "--speaker", "assistant", "--text", f"第 {number} 轮确认原文必须保留")

    def test_dashboard_reports_verifiable_archive_totals(self):
        self.append_round(1)
        from memory_cli import MemoryStore, load_simple_yaml

        store = MemoryStore(self.root, load_simple_yaml(self.config))
        result = dashboard_data(store)
        self.assertEqual(result["totals"]["messages"], 2)
        self.assertEqual(result["totals"]["conversations"], 1)
        self.assertGreater(result["totals"]["characters"], 0)
        self.assertEqual(
            result["totals"]["estimated_tokens"],
            estimate_context_tokens("第 1 轮讨论分层记忆第 1 轮确认原文必须保留"),
        )
        self.assertEqual(
            result["totals"]["message_estimated_tokens"],
            result["totals"]["estimated_tokens"],
        )
        self.assertEqual(result["totals"]["storage_bytes"], archive_storage_bytes(store))
        self.assertEqual(result["totals"]["verified_retrievals"], 0)
        self.assertEqual(result["totals"]["reported_total_tokens"], 0)
        self.assertEqual(result["totals"]["token_usage_conversations"], 0)
        self.assertEqual(len(result["conversations"]), 1)
        self.assertIn("title", result["conversations"][0])
        self.assertEqual(
            result["conversations"][0]["estimated_archive_tokens"],
            result["totals"]["estimated_tokens"],
        )
        self.assertIsNone(result["conversations"][0]["telemetry"])

    def test_dashboard_reports_persisted_codex_token_usage(self):
        conversation_id = "codex:usage-thread"
        self.run_cli(
            "append",
            "--speaker",
            "user",
            "--conversation-id",
            conversation_id,
            "--text",
            "TOKEN USAGE",
        )
        ledger = self.root / "imports/codex/token-usage/usage-thread.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "measurement": "codex-reported-model-usage",
                    "conversation_id": conversation_id,
                    "session_id": "usage-thread",
                    "reported_usage": {
                        "input_tokens": 900,
                        "cached_input_tokens": 400,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 100,
                        "reasoning_output_tokens": 25,
                        "total_tokens": 1000,
                    },
                    "latest_request_usage": {
                        "input_tokens": 90,
                        "cached_input_tokens": 40,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 2,
                        "total_tokens": 100,
                    },
                    "model_request_count": 7,
                    "counter_reset_count": 1,
                    "model_context_window": 200000,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        from memory_cli import MemoryStore, load_simple_yaml

        result = dashboard_data(MemoryStore(self.root, load_simple_yaml(self.config)))
        self.assertEqual(result["totals"]["reported_total_tokens"], 1000)
        self.assertEqual(result["totals"]["reported_input_tokens"], 900)
        self.assertEqual(result["totals"]["reported_cached_input_tokens"], 400)
        self.assertEqual(result["totals"]["reported_output_tokens"], 100)
        self.assertEqual(result["totals"]["reported_reasoning_output_tokens"], 25)
        self.assertEqual(result["totals"]["token_usage_conversations"], 1)
        self.assertEqual(result["totals"]["token_usage_model_requests"], 7)
        self.assertEqual(result["totals"]["token_usage_counter_resets"], 1)
        telemetry = result["conversations"][0]["telemetry"]
        self.assertEqual(telemetry["reported_total_tokens"], 1000)
        self.assertEqual(telemetry["reported_input_tokens"], 900)
        self.assertEqual(telemetry["reported_cached_input_tokens"], 400)
        self.assertEqual(telemetry["reported_output_tokens"], 100)
        self.assertEqual(telemetry["request_tokens"], 100)
        self.assertEqual(telemetry["model_request_count"], 7)

    def test_dashboard_separates_message_tokens_and_counts_verified_retrievals(self):
        self.append_round(1)
        self.run_cli("append", "--speaker", "tool", "--text", "工具活动 " * 100)
        retrieval_log = self.root / "retrieval/retrieval-log.jsonl"
        retrieval_log.write_text(
            "\n".join([
                json.dumps({
                    "verification": "verified",
                    "raw_files": ["raw/2026/07/one.md", "raw/2026/07/two.md"],
                }),
                json.dumps({
                    "verification": "summary-supported",
                    "raw_files": ["raw/2026/07/three.md"],
                }),
                json.dumps({
                    "verification": "verified",
                    "raw_files": ["raw/2026/07/two.md"],
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        from memory_cli import MemoryStore, load_simple_yaml

        result = dashboard_data(MemoryStore(self.root, load_simple_yaml(self.config)))
        self.assertGreater(
            result["totals"]["estimated_tokens"],
            result["totals"]["message_estimated_tokens"],
        )
        self.assertEqual(result["totals"]["verified_retrievals"], 2)
        self.assertEqual(result["totals"]["retrieval_source_files"], 2)
        self.assertEqual(result["totals"]["max_retrieval_sources"], 2)

    def test_dashboard_reports_collector_activity(self):
        telemetry_path = self.root / "imports/codex/collector-telemetry.json"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(
            json.dumps({
                "format_version": 1,
                "pid": 999999,
                "mode": "idle",
                "fallback_interval_seconds": 30,
                "last_file_event": "2026-07-19T01:00:00Z",
                "last_archive_update": "2026-07-19T01:00:01Z",
                "wakeups_last_hour": 4,
            }),
            encoding="utf-8",
        )
        from memory_cli import MemoryStore, load_simple_yaml

        result = dashboard_data(MemoryStore(self.root, load_simple_yaml(self.config)))
        self.assertEqual(result["collector"]["mode"], "idle")
        self.assertEqual(result["collector"]["fallback_interval_seconds"], 30)
        self.assertEqual(result["collector"]["wakeups_last_hour"], 4)

    def test_dashboard_health_reports_collector_freshness_alerts(self):
        self.assertEqual(
            dashboard_health({}, {"alerts": ["collector-telemetry-stale"]}),
            "attention",
        )
        self.assertEqual(dashboard_health({}, {"alerts": []}), "ok")

    def test_dashboard_reports_live_collector_startup_phase(self):
        telemetry_path = self.root / "imports/codex/collector-telemetry.json"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "pid": os.getpid(),
                    "phase": "starting",
                    "ready": False,
                    "fallback_interval_seconds": 5,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        result = collector_telemetry(self.root)
        self.assertTrue(result["process_running"])
        self.assertTrue(result["startup_pending"])
        self.assertIn("collector-starting", result["alerts"])

    def test_dashboard_reports_pending_coalesced_backup_as_recoverable_debt(self):
        telemetry_path = self.root / "imports/codex/collector-telemetry.json"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(
            json.dumps(
                {
                    "format_version": 2,
                    "pid": os.getpid(),
                    "phase": "ready",
                    "ready": True,
                    "fallback_interval_seconds": 5,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        debt_path = self.root / "pending/backup-debt.json"
        debt_path.write_text(
            json.dumps({"format_version": 1, "mutation_count": 2}),
            encoding="utf-8",
        )
        result = collector_telemetry(self.root)
        self.assertNotIn("backup-pending", result["alerts"])
        self.assertEqual(result["backup_debt"]["mutation_count"], 2)
        debt = debt_status_projection(self.root)
        self.assertEqual(debt["debts"]["backup_debt"]["count"], 2)
        self.assertEqual(dashboard_health({}, result, debt), "catching-up")

    def test_dashboard_separates_archived_conversations_and_groups_projects(self):
        for conversation_id, text in (
            ("codex:active-thread", "ACTIVE"),
            ("codex:archived-thread", "ARCHIVED"),
            ("codex:other-project", "OTHER"),
        ):
            self.run_cli(
                "append", "--speaker", "user", "--conversation-id", conversation_id,
                "--text", text,
            )
        from memory_cli import MemoryStore, load_simple_yaml

        metadata = {
            "active-thread": {"archived": False, "project": "Project A", "cwd": "/a"},
            "archived-thread": {"archived": True, "project": "Project A", "cwd": "/a"},
            "other-project": {"archived": False, "project": "Project B", "cwd": "/b"},
        }
        with patch("memory_dashboard.codex_thread_metadata", return_value=metadata):
            result = dashboard_data(MemoryStore(self.root, load_simple_yaml(self.config)))

        self.assertEqual(result["totals"]["active_conversations"], 2)
        self.assertEqual(result["totals"]["archived_conversations"], 1)
        self.assertEqual(
            {item["conversation_id"] for item in result["active_conversations"]},
            {"codex:active-thread", "codex:other-project"},
        )
        self.assertEqual(
            [item["conversation_id"] for item in result["archived_conversations"]],
            ["codex:archived-thread"],
        )
        self.assertEqual(
            {item["project"] for item in result["active_conversations"]},
            {"Project A", "Project B"},
        )

    def test_dashboard_achievement_settings_are_local_and_hide_empty_levels(self):
        html = (Path(__file__).resolve().parents[1] / "dashboard/index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("memory-wuxian-dashboard-settings-v1", html)
        self.assertIn("memory-wuxian-achievements-v1", html)
        self.assertIn("memory-wuxian-dashboard-status-v1", html)
        self.assertIn("settings.achievementsEnabled", html)
        self.assertIn("settings.animationsEnabled", html)
        self.assertIn("settings.toastsEnabled", html)
        self.assertIn("settings.compactMode", html)
        self.assertIn("[2,[10,50]]", html)
        self.assertIn("[3,[5,10,25]]", html)
        self.assertIn("[4,[1,2,5,10]]", html)
        self.assertIn("[8,[1]]", html)
        self.assertIn("id:`storage-${megabytes}mb`", html)
        self.assertIn("id:`archive-tokens-${tokens}`", html)
        self.assertIn("id:`message-tokens-${tokens}`", html)
        self.assertIn("id:`reported-tokens-${tokens}`", html)
        self.assertIn("reported_total_tokens", html)
        self.assertIn("Codex 报告累计用量", html)
        self.assertIn("load().then(refreshColdSnapshot)", html)
        self.assertIn("fetch('/api/status?refresh=1'", html)
        self.assertIn('class="chart-tooltip"', html)
        self.assertIn('tabindex="0"', html)
        self.assertIn("dailyTip:", html)
        self.assertIn('.bar.local{left:0;right:0;', html)
        self.assertIn('.bar-date{position:absolute;left:50%;bottom:2px;', html)
        self.assertIn('font-size:10px;font-weight:700;color:#36433d', html)
        self.assertIn('background:#2f86c1', html)
        self.assertIn('background:#18a668', html)
        self.assertIn(".bar-wrap:first-child .chart-tooltip{left:0", html)
        self.assertIn(".bar-wrap:last-child .chart-tooltip{left:auto;right:0", html)
        self.assertIn("collector-telemetry-stale", html)
        self.assertIn("id:`conversation-rounds-${rounds}`", html)
        self.assertIn("id:`project-conversations-${count}`", html)
        self.assertIn("id:`project-characters-${characters}`", html)
        self.assertIn("id:`verified-retrievals-${count}`", html)
        self.assertIn("id:`retrieval-sources-${count}`", html)
        self.assertIn("[5000000,'五百万上下文'", html)
        self.assertIn("[5000000,'五百万消息'", html)
        self.assertIn("[25,'项目文库'", html)
        self.assertIn("[5,'五卷联查'", html)
        self.assertIn("+l<=8&&+n>0", html)
        self.assertNotIn("fetch('/api/settings", html)
        self.assertNotIn("fetch('/api/achievements", html)

    def test_dashboard_snapshot_is_reused_and_invalidated(self):
        self.append_round(1)
        from memory_cli import MemoryStore, load_simple_yaml

        store = MemoryStore(self.root, load_simple_yaml(self.config))
        cache = DashboardSnapshotCache(store)
        with (
            patch.object(cache, "source_signature", side_effect=["v1", "v1", "v1", "v2", "v2"]),
            patch("memory_dashboard.dashboard_data", wraps=dashboard_data) as build,
        ):
            first = cache.get()
            second = cache.get()
            third = cache.get()

        self.assertEqual(build.call_count, 2)
        self.assertEqual(first["totals"], second["totals"])
        self.assertEqual(second["totals"], third["totals"])
        self.assertTrue(cache.path.exists())

    def test_dashboard_snapshot_survives_restart_and_recovers_from_corruption(self):
        self.append_round(1)
        from memory_cli import MemoryStore, load_simple_yaml

        store = MemoryStore(self.root, load_simple_yaml(self.config))
        first_cache = DashboardSnapshotCache(store)
        with patch.object(first_cache, "source_signature", return_value="stable"):
            expected = first_cache.get()["totals"]

        restarted = DashboardSnapshotCache(store)
        with (
            patch.object(restarted, "source_signature", return_value="stable"),
            patch("memory_dashboard.dashboard_data", side_effect=AssertionError("must use snapshot")),
        ):
            self.assertEqual(restarted.get()["totals"], expected)

        restarted.path.write_text("{broken", encoding="utf-8")
        recovered = DashboardSnapshotCache(store)
        with (
            patch.object(recovered, "source_signature", return_value="stable"),
            patch("memory_dashboard.dashboard_data", wraps=dashboard_data) as rebuild,
        ):
            self.assertEqual(recovered.get()["totals"], expected)
        self.assertEqual(rebuild.call_count, 1)

    def test_chatgpt_export_import_is_branch_aware_and_idempotent(self):
        export_zip = self.base / "chatgpt-export.zip"
        conversation = {
            "id": "chat-123",
            "title": "Imported Chat title",
            "create_time": 1000,
            "current_node": "assistant-new",
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "message": {
                        "id": "system-1",
                        "author": {"role": "system"},
                        "create_time": 1000,
                        "content": {"content_type": "text", "parts": ["hidden"]},
                    },
                },
                "user": {
                    "id": "user",
                    "parent": "root",
                    "message": {
                        "id": "user-1",
                        "author": {"role": "user"},
                        "create_time": 1001,
                        "content": {"content_type": "text", "parts": ["Remember this chat"]},
                    },
                },
                "assistant-old": {
                    "id": "assistant-old",
                    "parent": "user",
                    "message": {
                        "id": "assistant-old-1",
                        "author": {"role": "assistant"},
                        "create_time": 1002,
                        "content": {"content_type": "text", "parts": ["Discarded branch"]},
                    },
                },
                "assistant-new": {
                    "id": "assistant-new",
                    "parent": "user",
                    "message": {
                        "id": "assistant-new-1",
                        "author": {"role": "assistant"},
                        "create_time": 1003,
                        "content": {"content_type": "text", "parts": ["Visible answer"]},
                    },
                },
            },
        }
        with zipfile.ZipFile(export_zip, "w") as archive:
            archive.writestr(
                "export/conversations.json",
                json.dumps([conversation], ensure_ascii=False),
            )

        first = self.run_cli("import-chatgpt", "--export", str(export_zip))
        second = self.run_cli("import-chatgpt", "--export", str(export_zip))
        self.assertEqual(first["imported_messages"], 2)
        self.assertEqual(first["skipped_items"], 1)
        self.assertEqual(second["imported_messages"], 0)
        self.assertEqual(second["duplicate_messages"], 2)

        from memory_cli import MemoryStore, load_simple_yaml

        store = MemoryStore(self.root, load_simple_yaml(self.config))
        records = [
            item for item in store.read_all_raw()
            if item["conversation_id"] == "chatgpt:chat-123"
        ]
        self.assertEqual([item["text"] for item in records], ["Remember this chat", "Visible answer"])
        self.assertTrue(records[-1]["completes_round"])
        self.assertEqual(records[0]["source"]["kind"], "chatgpt-data-export")
        self.assertEqual(
            next(item for item in dashboard_data(store)["conversations"] if item["conversation_id"] == "chatgpt:chat-123")["title"],
            "Imported Chat title",
        )

    def test_default_root_uses_active_archive_pointer(self):
        codex_home = self.base / "codex-home"
        codex_home.mkdir()
        (codex_home / "memory-wuxian-active-root.txt").write_text(
            f"{self.root}\n", encoding="utf-8"
        )
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
            self.assertEqual(
                resolve_root(None, {"memory": {"root_directory": "./memory"}}),
                self.root,
            )
            self.assertEqual(
                resolve_root(str(self.base / "explicit"), {}),
                self.base / "explicit",
            )

    def ingest_due_summary(self, concept):
        job_result = self.run_cli("make-summary-job")
        self.assertEqual(job_result["status"], "created")
        summary_json = self.base / f"{concept}.json"
        summary_json.write_text(
            json.dumps(
                {
                    "topics": ["分层长期记忆"],
                    "established_conclusions": ["原始对话先持久化"],
                    "open_questions": [],
                    "concepts": [concept],
                    "policy_events": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self.run_cli(
            "ingest-summary",
            "--job",
            job_result["job"],
            "--summary-json",
            str(summary_json),
        )

    def test_level_one_ingest_and_raw_verified_retrieval(self):
        self.append_round(1)
        not_due = self.run_cli("make-summary-job")
        self.assertEqual(not_due["status"], "not-due")
        self.append_round(2)
        ingested = self.ingest_due_summary("分层记忆")
        self.assertTrue(Path(ingested["summary"]).exists())

        retrieval = self.run_cli("retrieve", "--query", "分层记忆", expect_json=False)
        self.assertIn("Confidence: `verified`", retrieval)
        self.assertIn("第 1 轮讨论分层记忆", retrieval)
        status = self.run_cli("status")
        self.assertEqual(status["last_summarized_round"], 2)
        self.assertEqual(status["pending_summary_jobs"], 0)

    def test_pending_parent_jobs_reserve_children_and_repair_legacy_overlap(self):
        for number in range(1, 5):
            self.append_round(number)
            if number % 2 == 0:
                self.ingest_due_summary(f"concept-{number}")

        first_parent_path = Path(self.run_cli("make-summary-job")["job"])
        first_parent = json.loads(first_parent_path.read_text(encoding="utf-8"))
        self.assertEqual(first_parent["summary_level"], 2)

        summary_index = self.root / "indexes/summaries.jsonl"
        summary_records = [
            json.loads(line)
            for line in summary_index.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        discovered = dict(summary_records[0])
        discovered["summary_id"] = "L1-999999"
        discovered["source_start_sequence"] = 0
        discovered["source_end_sequence"] = 0
        source_path = self.root / discovered["path"]
        discovered_path = source_path.with_name("L1-999999.md")
        discovered["path"] = discovered_path.relative_to(self.root).as_posix()
        discovered_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        with summary_index.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(discovered, ensure_ascii=False, sort_keys=True) + "\n")

        self.assertEqual(self.run_cli("make-summary-job")["status"], "not-due")
        self.assertEqual(len(list((self.root / "pending").glob("job-*.json"))), 1)
        summary_index.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in summary_records
            ),
            encoding="utf-8",
        )
        discovered_path.unlink()

        overlapping = dict(first_parent)
        overlapping["job_id"] = "job-999999"
        overlapping["target_summary_id"] = "L2-999999"
        overlapping["created_at"] = "9999-12-31T23:59:59+00:00"
        overlapping["source_summaries"] = list(first_parent["source_summaries"])
        overlapping["source_signature"] = (
            f"conversation:{first_parent['conversation_id']}:children:"
            + ",".join(overlapping["source_summaries"])
        )
        overlap_path = self.root / "pending/job-999999.json"
        overlap_path.write_text(
            json.dumps(overlapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        repaired = self.run_cli("heartbeat", "--repair", "--no-create-jobs")
        self.assertEqual(repaired["status"], "ok")
        self.assertTrue(first_parent_path.exists())
        self.assertFalse(overlap_path.exists())
        repair = repaired["repairs"][0]["pending_parent_jobs"]
        receipt = Path(repair["backup"]) / "receipt.json"
        receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertFalse(receipt_payload["raw_archive_modified"])
        self.assertEqual(
            receipt_payload["quarantined_jobs"][0]["job_id"],
            "job-999999",
        )

    def test_current_policy_retrieval_tracks_explicit_revision(self):
        for number in (1, 2):
            self.run_cli(
                "append",
                "--speaker", "user",
                "--text", f"第 {number} 轮采用每 20 轮生成一级摘要。",
            )
            self.run_cli(
                "append",
                "--speaker", "assistant",
                "--text", "已确认每 20 轮生成一级摘要。",
            )
        first_job_result = self.run_cli("make-summary-job")
        first_job = json.loads(
            Path(first_job_result["job"]).read_text(encoding="utf-8")
        )
        first_summary = self.base / "policy-first.json"
        first_summary.write_text(
            json.dumps(
                {
                    "topics": ["摘要触发规则"],
                    "established_conclusions": ["每 20 轮生成一级摘要"],
                    "open_questions": [],
                    "concepts": ["一级摘要", "触发轮数"],
                    "policy_events": [{
                        "topic": "一级摘要触发",
                        "statement": "每 20 轮生成一级摘要",
                        "scope": "Memory無限摘要调度",
                        "event_type": "adopted",
                        "prior_statement": "",
                        "source_message_ids": [first_job["source_message_ids"][-1]],
                    }],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.run_cli(
            "ingest-summary",
            "--job", first_job_result["job"],
            "--summary-json", str(first_summary),
        )

        for number in (3, 4):
            self.run_cli(
                "append",
                "--speaker", "user",
                "--text", f"第 {number} 轮修订一级摘要触发规则。",
            )
            self.run_cli(
                "append",
                "--speaker", "assistant",
                "--text", "规则更新为每 5 轮或 20000 字符生成一级摘要。",
            )
        second_job_result = self.run_cli("make-summary-job")
        second_job = json.loads(
            Path(second_job_result["job"]).read_text(encoding="utf-8")
        )
        second_summary = self.base / "policy-second.json"
        second_summary.write_text(
            json.dumps(
                {
                    "topics": ["摘要触发规则修订"],
                    "established_conclusions": ["每 5 轮或 20000 字符生成一级摘要"],
                    "open_questions": [],
                    "concepts": ["一级摘要", "触发轮数"],
                    "policy_events": [{
                        "topic": "一级摘要触发",
                        "statement": "每 5 轮或 20000 字符生成一级摘要",
                        "scope": "Memory無限摘要调度",
                        "event_type": "revised",
                        "prior_statement": "每 20 轮生成一级摘要",
                        "source_message_ids": [second_job["source_message_ids"][-1]],
                    }],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.run_cli(
            "ingest-summary",
            "--job", second_job_result["job"],
            "--summary-json", str(second_summary),
        )

        retrieval = self.run_cli(
            "retrieve",
            "--query", "一级摘要触发",
            "--mode", "current-policy",
            expect_json=False,
        )
        self.assertIn("Validity: `superseded`", retrieval)
        self.assertIn("Validity: `active`", retrieval)
        self.assertIn("每 5 轮或 20000 字符生成一级摘要", retrieval)
        current_view = (self.root / "indexes/policies-current.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Status: `active`", current_view)
        self.assertIn("每 5 轮或 20000 字符生成一级摘要", current_view)
        status = self.run_cli("status")
        self.assertEqual(status["policy_events"], 2)
        self.assertEqual(status["active_policies"], 1)

    def test_retrieve_matches_natural_language_terms_and_excludes_pending_echo(self):
        self.run_cli(
            "append",
            "--speaker", "user",
            "--conversation-id", "codex:target-thread",
            "--text", "请定义 reference 合并口径",
        )
        self.run_cli(
            "append",
            "--speaker", "user",
            "--conversation-id", "codex:other-thread",
            "--text", "OTHER THREAD PRIVATE CONTEXT",
        )
        self.run_cli(
            "append",
            "--speaker", "assistant",
            "--conversation-id", "codex:other-thread",
            "--text", "OTHER THREAD FINAL",
        )
        historical = self.run_cli(
            "append",
            "--speaker", "assistant",
            "--conversation-id", "codex:target-thread",
            "--text", (
                "代表性序列必须满足完整 ORF、长度 L +/-51 bp、Magic-BLAST "
                "identity/query coverage/reference coverage >=90%，并执行 reciprocal capture。"
            ),
        )
        self.run_cli(
            "append",
            "--speaker", "user",
            "--conversation-id", "codex:target-thread",
            "--text", (
                "CURRENT QUERY ECHO：回看代表性序列的合并原则，Magic Blast 长度上下 "
                "51 base pair、90% identity 和 reciprocal capture。"
            ),
        )

        retrieval = self.run_cli(
            "retrieve",
            "--query",
            "代表性序列 Magic Blast 长度上下51 base pair 90% identity reciprocal capture",
            expect_json=False,
        )

        self.assertIn("Confidence: `verified`", retrieval)
        self.assertIn(historical["message_id"], retrieval)
        self.assertIn("长度 L +/-51 bp", retrieval)
        self.assertNotIn("CURRENT QUERY ECHO", retrieval)
        self.assertNotIn("OTHER THREAD PRIVATE CONTEXT", retrieval)

    def test_conversation_tail_resolves_title_before_selecting_latest_messages(self):
        for conversation_id, title, marker in (
            ("codex:target-thread", "指定项目对话", "TARGET"),
            ("codex:newest-thread", "最近但不应选中的对话", "NEWEST"),
        ):
            self.run_cli(
                "append", "--speaker", "user", "--conversation-id", conversation_id,
                "--text", title,
            )
            for number in range(1, 4):
                self.run_cli(
                    "append", "--speaker", "assistant", "--conversation-id", conversation_id,
                    "--text", f"{marker} assistant {number}",
                )
                self.run_cli(
                    "append", "--speaker", "user", "--conversation-id", conversation_id,
                    "--text", f"{marker} user {number}",
                )

        output = self.run_cli(
            "conversation-tail", "--title", "指定项目", "--messages", "3",
            expect_json=False,
        )
        self.assertIn("Conversation ID: `codex:target-thread`", output)
        self.assertIn("TARGET user 2", output)
        self.assertIn("TARGET assistant 3", output)
        self.assertIn("TARGET user 3", output)
        self.assertNotIn("TARGET assistant 1", output)
        self.assertNotIn("NEWEST", output)

    def test_conversation_tail_rejects_missing_or_ambiguous_titles(self):
        for conversation_id, title in (
            ("codex:first-thread", "重复标题甲"),
            ("codex:second-thread", "重复标题乙"),
        ):
            self.run_cli(
                "append", "--speaker", "user", "--conversation-id", conversation_id,
                "--text", title,
            )
        ambiguous = self.invoke_cli(
            "conversation-tail", "--title", "重复标题", "--messages", "20",
        )
        missing = self.invoke_cli(
            "conversation-tail", "--title", "不存在的标题", "--messages", "20",
        )
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("ambiguous", ambiguous.stderr)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("No archived conversation matched title", missing.stderr)

    def test_conversation_tail_uses_persisted_title_and_excludes_current_task(self):
        old_id = "codex:old-thread"
        current_id = "codex:current-thread"
        for conversation_id, text in (
            (old_id, "OLD HISTORY"),
            (current_id, "梳理Pilot与近期报告记录"),
        ):
            self.run_cli(
                "append", "--speaker", "user", "--conversation-id", conversation_id,
                "--text", text,
            )
        registered = self.run_cli(
            "register-title",
            "--conversation-id", old_id,
            "--title", "梳理Pilot与近期报告记录",
        )
        self.assertEqual(registered["status"], "registered")

        ambiguous = self.invoke_cli(
            "conversation-tail", "--title", "梳理Pilot与近期报告记录",
        )
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("ambiguous", ambiguous.stderr)

        output = self.run_cli(
            "conversation-tail",
            "--title", "梳理Pilot与近期报告记录",
            "--exclude-conversation-id", current_id,
            "--messages", "5",
            expect_json=False,
        )
        self.assertIn(f"Conversation ID: `{old_id}`", output)
        self.assertIn("OLD HISTORY", output)
        self.assertNotIn("codex:current-thread`", output.split("## Verified Raw Messages", 1)[1])

    def test_default_level_one_threshold_is_five_rounds(self):
        default_root = self.base / "default-memory"
        default_config = self.base / "default-config.yaml"
        default_config.write_text(
            'memory:\n  root_directory: "./default-memory"\n',
            encoding="utf-8",
        )

        def run_default(*arguments):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--root",
                    str(default_root),
                    "--config",
                    str(default_config),
                    *arguments,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        run_default("init")
        for number in range(1, 5):
            run_default("append", "--speaker", "user", "--text", f"user {number}")
            run_default("append", "--speaker", "assistant", "--text", f"assistant {number}")
        self.assertEqual(run_default("make-summary-job")["status"], "not-due")
        run_default("append", "--speaker", "user", "--text", "user 5")
        run_default("append", "--speaker", "assistant", "--text", "assistant 5")
        created = run_default("make-summary-job")
        self.assertEqual(created["status"], "created")
        job = json.loads(Path(created["job"]).read_text(encoding="utf-8"))
        self.assertEqual(job["source_round_end"] - job["source_round_start"] + 1, 5)

    def test_deterministic_index_uses_round_or_character_trigger_without_ai(self):
        hybrid_root = self.base / "hybrid-memory"
        hybrid_config = self.base / "hybrid-config.yaml"
        hybrid_config.write_text(
            """memory:
  root_directory: "./hybrid-memory"
summaries:
  level_1_trigger_rounds: 5
  level_1_trigger_characters: 20
  automatic_semantic_jobs: false
""",
            encoding="utf-8",
        )

        def run_hybrid(*arguments):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--root",
                    str(hybrid_root),
                    "--config",
                    str(hybrid_config),
                    *arguments,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        run_hybrid("init")
        run_hybrid("append", "--speaker", "user", "--text", "一个超过字符阈值的长问题内容")
        run_hybrid(
            "append",
            "--speaker",
            "assistant",
            "--nonfinal-assistant",
            "--text",
            "回答仍在生成，字符已经达到阈值",
        )
        self.assertEqual(run_hybrid("make-summary-job")["status"], "not-due")
        self.assertFalse((hybrid_root / "indexes/deterministic/level-1.jsonl").read_text().strip())
        run_hybrid("append", "--speaker", "assistant", "--text", "对应的完整回答内容现在结束")
        indexes = [
            json.loads(line)
            for line in (hybrid_root / "indexes/deterministic/level-1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(indexes), 1)
        self.assertEqual(indexes[0]["round_count"], 1)
        self.assertGreaterEqual(indexes[0]["visible_characters"], 20)
        self.assertEqual(run_hybrid("status")["pending_summary_jobs"], 0)
        self.assertFalse(run_hybrid("status")["automatic_semantic_jobs"])
        created = run_hybrid("make-summary-job")
        self.assertEqual(created["status"], "created")
        job = json.loads(Path(created["job"]).read_text(encoding="utf-8"))
        self.assertEqual(job["source_round_start"], job["source_round_end"])
        self.assertGreaterEqual(
            sum(len(record["text"]) for record in job["source_records"]),
            20,
        )
        dry_run = subprocess.run(
            [
                sys.executable,
                str(SEMANTIC_WORKER),
                "--root",
                str(hybrid_root),
                "--config",
                str(hybrid_config),
                "--job",
                created["job"],
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertEqual(json.loads(dry_run.stdout)["status"], "dry-run")

    def test_higher_level_job_and_heartbeat_idempotency(self):
        for number in range(1, 3):
            self.append_round(number)
        self.ingest_due_summary("第一段记忆")
        for number in range(3, 5):
            self.append_round(number)
        self.ingest_due_summary("第二段记忆")

        heartbeat = self.run_cli("heartbeat")
        self.assertEqual(heartbeat["status"], "ok")
        self.assertIsNotNone(heartbeat["created_job"])
        pending_before = self.run_cli("status")["pending_summary_jobs"]
        repeated = self.run_cli("heartbeat")
        pending_after = self.run_cli("status")["pending_summary_jobs"]
        self.assertEqual(pending_before, 1)
        self.assertEqual(pending_after, 1)
        self.assertEqual(repeated["created_job"], heartbeat["created_job"])
        job = json.loads(Path(heartbeat["created_job"]).read_text(encoding="utf-8"))
        self.assertEqual(job["summary_level"], 2)
        self.assertEqual(job["source_summaries"], ["L1-000001", "L1-000002"])

    def test_semantic_backlog_triggers_only_when_a_new_round_completes(self):
        backlog_root = self.base / "backlog-memory"
        backlog_config = self.base / "backlog-config.yaml"
        backlog_config.write_text(
            "summaries:\n"
            "  level_1_trigger_rounds: 5\n"
            "  level_1_trigger_characters: 20000\n"
            "  automatic_semantic_jobs: false\n",
            encoding="utf-8",
        )

        def run_backlog(*arguments):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--root",
                    str(backlog_root),
                    "--config",
                    str(backlog_config),
                    *arguments,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        run_backlog("init")
        for number in range(5):
            run_backlog("append", "--speaker", "user", "--text", f"backlog user {number}")
            run_backlog("append", "--speaker", "assistant", "--text", f"backlog final {number}")
        backlog_config.write_text(
            backlog_config.read_text(encoding="utf-8").replace(
                "automatic_semantic_jobs: false",
                "automatic_semantic_jobs: true",
            ),
            encoding="utf-8",
        )
        rollout = self.base / "rollout-backlog-trigger.jsonl"

        def event(event_type, message, phase=None):
            payload = {"type": event_type, "message": message}
            if phase:
                payload["phase"] = phase
            return json.dumps(
                {
                    "timestamp": "2026-07-17T08:00:00Z",
                    "type": "event_msg",
                    "payload": payload,
                }
            ) + "\n"

        rollout.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "backlog-trigger"}}) + "\n"
            + event("user_message", "new user")
            + event("agent_message", "still answering", "commentary"),
            encoding="utf-8",
        )
        commentary_sync = run_backlog("sync-codex", "--session-file", str(rollout))
        self.assertIsNone(commentary_sync["created_summary_job"])
        self.assertEqual(run_backlog("status")["pending_summary_jobs"], 0)
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write(event("agent_message", "answer complete", "final_answer"))
        final_sync = run_backlog("sync-codex", "--session-file", str(rollout))
        self.assertIsNotNone(final_sync["created_summary_job"])

    def test_secret_redaction_is_explicit(self):
        result = self.run_cli("append", "--speaker", "user", "--text", "password=secret-value")
        self.assertTrue(result["text_redacted"])
        raw_text = next(self.root.glob("raw/*/*/*.md")).read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", raw_text)
        self.assertNotIn("secret-value", raw_text)

    def test_sequence_allocation_uses_raw_high_watermark_after_state_rollback(self):
        first = self.run_cli("append", "--speaker", "user", "--text", "first")
        state_path = self.root / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["total_messages"] = 0
        state_path.write_text(json.dumps(state), encoding="utf-8")
        second = self.run_cli("append", "--speaker", "assistant", "--text", "second")
        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)

    def test_conversations_are_archived_separately_and_rebuild_from_raw(self):
        self.run_cli(
            "append",
            "--speaker",
            "user",
            "--conversation-id",
            "codex:thread-alpha",
            "--text",
            "Alpha user text",
        )
        self.run_cli(
            "append",
            "--speaker",
            "assistant",
            "--conversation-id",
            "codex:thread-alpha",
            "--text",
            "Alpha assistant text\r\nnative output\r\n",
        )
        self.run_cli(
            "append",
            "--speaker",
            "user",
            "--conversation-id",
            "codex:thread-beta",
            "--text",
            "Beta user text",
        )
        self.run_cli(
            "append",
            "--speaker",
            "assistant",
            "--conversation-id",
            "codex:thread-beta",
            "--text",
            "Beta assistant text",
        )

        alpha = self.root / "conversations" / "codex-thread-alpha.md"
        beta = self.root / "conversations" / "codex-thread-beta.md"
        self.assertTrue(alpha.exists())
        self.assertTrue(beta.exists())
        alpha_text = alpha.read_text(encoding="utf-8")
        beta_text = beta.read_text(encoding="utf-8")
        self.assertIn("Alpha user text", alpha_text)
        self.assertIn("Alpha assistant text", alpha_text)
        self.assertIn("native output", alpha_text)
        self.assertNotIn("Beta user text", alpha_text)
        self.assertIn("Beta user text", beta_text)
        self.assertIn("Beta assistant text", beta_text)
        self.assertNotIn("Alpha user text", beta_text)

        raw_hashes_before = {
            path: path.read_bytes()
            for path in self.root.glob("raw/*/*/*.md")
        }
        preview = self.run_cli("rebuild-conversations")
        self.assertFalse(preview["changed"])
        self.assertEqual(preview["changed_files"], [])
        alpha.write_text("damaged derived transcript\n", encoding="utf-8")
        checked = self.run_cli("heartbeat", "--check-only")
        self.assertEqual(checked["status"], "attention")
        self.assertIn(
            "conversation transcripts differ from raw records",
            checked["repairable_issues"],
        )
        repaired = self.run_cli("heartbeat", "--repair", "--no-create-jobs")
        self.assertEqual(repaired["status"], "ok")
        self.assertIn("Alpha user text", alpha.read_text(encoding="utf-8"))
        self.assertEqual(
            raw_hashes_before,
            {path: path.read_bytes() for path in self.root.glob("raw/*/*/*.md")},
        )

    def test_hashes_are_persisted_and_source_drift_blocks_ingest(self):
        self.append_round(1)
        self.append_round(2)
        job_result = self.run_cli("make-summary-job")
        job = json.loads(Path(job_result["job"]).read_text(encoding="utf-8"))
        self.assertRegex(job["source_sha256"], r"^[0-9a-f]{64}$")

        raw_path = next(self.root.glob("raw/*/*/*.md"))
        raw_text = raw_path.read_text(encoding="utf-8")
        self.assertIn('"content_sha256":"', raw_text)
        raw_path.write_text(raw_text.replace("第 1 轮讨论分层记忆", "第 1 轮内容已改变", 1), encoding="utf-8")

        summary_json = self.base / "drift-summary.json"
        summary_json.write_text(
            json.dumps(
                {
                    "topics": ["分层长期记忆"],
                    "established_conclusions": [],
                    "open_questions": [],
                    "concepts": ["漂移检查"],
                    "policy_events": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        failed = self.invoke_cli(
            "ingest-summary",
            "--job",
            job_result["job"],
            "--summary-json",
            str(summary_json),
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("source drift detected", failed.stderr)
        self.assertTrue(Path(job_result["job"]).exists())

    def test_preview_and_apply_rebuild_state_and_indexes(self):
        self.append_round(1)
        self.append_round(2)
        self.ingest_due_summary("恢复测试")

        state_path = self.root / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["total_messages"] = 999
        state_path.write_text(json.dumps(state), encoding="utf-8")

        preview = self.run_cli("rebuild-state")
        self.assertFalse(preview["changed"])
        self.assertIn("total_messages", preview["differences"])
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["total_messages"], 999)

        applied = self.run_cli("rebuild-state", "--apply")
        self.assertTrue(applied["changed"])
        self.assertTrue(Path(applied["backup"]).exists())
        self.assertEqual(self.run_cli("status")["total_messages"], 4)

        summary_index = self.root / "indexes" / "summaries.jsonl"
        summary_index.write_text("{broken json\n", encoding="utf-8")
        checked = self.run_cli("heartbeat", "--check-only")
        self.assertEqual(checked["status"], "attention")
        self.assertTrue(checked["repairable_issues"])
        repaired = self.run_cli("heartbeat", "--repair", "--no-create-jobs")
        self.assertEqual(repaired["status"], "ok")
        self.assertTrue(repaired["repairs"])
        self.assertIn("恢复测试", self.run_cli("retrieve", "--query", "恢复测试", expect_json=False))
        workspace_backups = [path for path in (self.root / "archive").iterdir() if path.is_dir()]
        self.assertEqual(len(workspace_backups), 1)
        self.assertTrue(workspace_backups[0].name.startswith("index-rebuild-"))

    def test_index_generation_cli_is_preview_first_for_activation(self):
        self.append_round(1)
        generation = self.run_cli("index-generation-build")
        generation_id = generation["generation_id"]
        pointer = self.root / "indexes" / "active-generation.json"

        selected = self.run_cli(
            "index-generation-status",
            "--generation-id",
            generation_id,
        )
        self.assertEqual(selected["status"], "complete")
        self.assertFalse(pointer.exists())

        preview = self.run_cli(
            "index-generation-activate",
            "--generation-id",
            generation_id,
        )
        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(preview["would_activate"], generation_id)
        self.assertFalse(pointer.exists())

        activated = self.run_cli(
            "index-generation-activate",
            "--generation-id",
            generation_id,
            "--apply",
        )
        self.assertEqual(activated["active_generation_id"], generation_id)
        self.assertTrue(pointer.is_file())

    def test_rebuild_indexes_apply_retains_desktop_backup_contract(self):
        backup_root = self.base / "rebuild-index-backups"
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                f'''backup:
  enabled: true
  directory: "{backup_root}"
'''
            )
        self.append_round(1)
        result = self.run_cli("rebuild-indexes", "--apply")
        self.assertTrue(result["changed"])
        self.assertTrue(Path(result["desktop_backup"]).is_dir())

    def test_maintenance_cli_is_bounded_model_free_and_redacted(self):
        payload = self.base / "semantic-eligibility.json"
        payload.write_text(
            json.dumps(
                {
                    "conversation_id": "codex:maintenance",
                    "completed_round": 1,
                    "round_complete": True,
                }
            ),
            encoding="utf-8",
        )
        queued = self.run_cli(
            "maintenance-enqueue",
            "--kind",
            "semantic-summary-eligibility",
            "--idempotency-key",
            "codex:maintenance:round:1",
            "--payload-json",
            str(payload),
        )
        self.assertTrue(queued["created"])
        tick = self.run_cli("maintenance-tick", "--maximum-jobs", "1")
        self.assertEqual(tick["ai_invocations"], 0)
        self.assertEqual(tick["processed"][0]["state"], "semantic-ready")
        status = self.run_cli("maintenance-status")
        self.assertEqual(status["queue"]["counts"]["semantic-ready"], 1)
        diagnostic = self.run_cli("maintenance-diagnostics")
        self.assertFalse(diagnostic["contains_raw_dialogue"])
        self.assertTrue(Path(diagnostic["path"]).is_file())

    def test_content_shadow_cli_is_preview_first_and_resumable(self):
        source = self.base / "content-source"
        source.mkdir()
        (source / "a.txt").write_bytes(b"alpha\r\n")
        (source / "b.bin").write_bytes(b"\x00\xff")
        preview = self.run_cli(
            "content-shadow-build",
            "--source-root", str(source),
            "--source-id", "fixture:cli",
            "--file", "a.txt",
            "--file", "b.bin",
        )
        self.assertEqual(preview["status"], "preview")
        self.assertFalse((self.root / "shadow-content-v1").exists())
        built = self.run_cli(
            "content-shadow-build",
            "--source-root", str(source),
            "--source-id", "fixture:cli",
            "--file", "a.txt",
            "--file", "b.bin",
            "--apply",
        )
        self.assertTrue(built["applied"])
        manifest_id = built["manifest_id"]
        verified = self.run_cli(
            "content-shadow-verify", "--manifest-id", manifest_id,
            "--source-root", str(source),
        )
        self.assertEqual(verified["status"], "verified")
        restored = self.base / "content-restored"
        reconstruction = self.run_cli(
            "content-shadow-reconstruct", "--manifest-id", manifest_id,
            "--destination", str(restored),
        )
        self.assertEqual(reconstruction["status"], "preview")
        self.assertFalse(restored.exists())
        target = self.base / "target-archive"
        transfer_preview = self.run_cli(
            "content-transfer", "--manifest-id", manifest_id,
            "--target-archive-root", str(target), "--domain", "archive",
            "--target-id", "node-b", "--start", "0", "--count", "2",
        )
        self.assertEqual(transfer_preview["status"], "preview")
        self.assertFalse(target.exists())
        transfer = self.run_cli(
            "content-transfer", "--manifest-id", manifest_id,
            "--target-archive-root", str(target), "--domain", "archive",
            "--target-id", "node-b", "--start", "0", "--count", "2", "--apply",
        )
        self.assertEqual(transfer["status"], "completed")

    def test_summary_hash_drift_is_not_auto_repaired(self):
        self.append_round(1)
        self.append_round(2)
        ingested = self.ingest_due_summary("摘要漂移")
        summary_path = Path(ingested["summary"])
        summary_path.write_text(summary_path.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")

        checked = self.run_cli("heartbeat", "--check-only")
        self.assertEqual(checked["status"], "attention")
        self.assertTrue(any("summary SHA-256 mismatch" in issue for issue in checked["integrity_issues"]))
        repaired = self.run_cli("heartbeat", "--repair", "--no-create-jobs")
        self.assertEqual(repaired["status"], "attention")
        self.assertEqual(repaired["repairs"], [])
        refused = self.invoke_cli("rebuild-indexes", "--apply")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("Refusing to rebuild indexes over integrity failures", refused.stderr)

    def test_backup_failure_cleans_current_and_orphaned_temporary_directories(self):
        from memory_cli import MemoryStore, load_simple_yaml

        backup_root = self.base / "failure-backups"
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                f'backup:\n  enabled: true\n  directory: "{backup_root}"\n'
            )
        orphan = backup_root / ".2026-08-01_120000_000001.tmp-1234"
        orphan.mkdir(parents=True)
        orphan.joinpath("partial.bin").write_bytes(b"partial")
        unrelated = backup_root / ".not-memory-wuxian.tmp-1234"
        unrelated.mkdir()
        store = MemoryStore(self.root, load_simple_yaml(self.config))

        def fail_after_partial(source, destination, **kwargs):
            destination.mkdir(parents=True)
            destination.joinpath("partial.bin").write_bytes(b"partial")
            raise OSError("synthetic interrupted backup")

        with patch("memory_cli.shutil.copytree", side_effect=fail_after_partial):
            with self.assertRaisesRegex(OSError, "synthetic interrupted backup"):
                store.create_backup_snapshot("failure-injection")

        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists())
        temporary = list(backup_root.glob(".*.tmp-*"))
        self.assertEqual(temporary, [unrelated])
        log = [
            json.loads(line)
            for line in (backup_root / "backup-log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(log[-1]["status"], "failed")
        self.assertTrue(log[-1]["temporary_cleaned"])
        self.assertEqual(log[-1]["orphaned_incomplete_removed"], [orphan.name])

    @unittest.skipUnless(os.name == "nt", "Windows extended-path behavior")
    def test_backup_copies_paths_longer_than_legacy_windows_limit(self):
        from memory_cli import MemoryStore, load_simple_yaml

        backup_root = self.base / "long-path-backups"
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                f'backup:\n  enabled: true\n  directory: "{backup_root}"\n'
            )
        relative = Path("environment")
        for index in range(7):
            relative /= f"segment-{index}-" + ("x" * 32)
        relative /= "payload.json"
        source = Path(filesystem_native_path(self.root / relative))
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('{"status":"long-path"}\n', encoding="utf-8")
        store = MemoryStore(self.root, load_simple_yaml(self.config))

        snapshot = store.create_backup_snapshot("windows-long-path")

        self.assertIsNotNone(snapshot)
        copied = Path(filesystem_native_path(Path(snapshot) / relative))
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        shutil.rmtree(filesystem_native_path(self.root / "environment"))
        shutil.rmtree(filesystem_native_path(backup_root))

    def test_codex_incremental_sync_filters_internal_events_and_backs_up(self):
        backup_root = self.base / "desktop-backups"
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                f'''backup:
  enabled: true
  directory: "{backup_root}"
'''
            )
        legacy_snapshot = backup_root / "2026-07-16_1841"
        legacy_snapshot.mkdir(parents=True)
        legacy_snapshot.joinpath("legacy.txt").write_text("legacy snapshot", encoding="utf-8")
        session = self.base / "rollout-2026-07-16T10-00-00-thread-001.jsonl"

        def event(timestamp, outer_type, payload):
            return json.dumps(
                {"timestamp": timestamp, "type": outer_type, "payload": payload},
                ensure_ascii=False,
            ) + "\n"

        session.write_text(
            event(
                "2026-07-16T10:00:00Z",
                "session_meta",
                {"id": "thread-001", "session_id": "thread-001"},
            )
            + event(
                "2026-07-16T10:00:01Z",
                "event_msg",
                {"type": "user_message", "message": "请记录这一轮"},
            )
            + event(
                "2026-07-16T10:00:02Z",
                "event_msg",
                {"type": "agent_message", "phase": "commentary", "message": "正在核对。"},
            )
            + event(
                "2026-07-16T10:00:03Z",
                "event_msg",
                {"type": "agent_reasoning", "text": "不得归档的内部推理"},
            )
            + event(
                "2026-07-16T10:00:04Z",
                "response_item",
                {"type": "function_call", "name": "shell_command", "arguments": json.dumps({"command": "rg -n TODO src"})},
            )
            + event(
                "2026-07-16T10:00:04.500Z",
                "response_item",
                {"type": "function_call_output", "output": "不得归档的工具输出"},
            )
            + event(
                "2026-07-16T10:00:05Z",
                "event_msg",
                {"type": "agent_message", "phase": "final_answer", "message": "这一轮已记录。"},
            ),
            encoding="utf-8",
        )

        first = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(first["imported_messages"], 4)
        self.assertIsNotNone(first["backup"])
        self.assertTrue(Path(first["backup"]).joinpath("backup-manifest.json").exists())
        status = self.run_cli("status")
        self.assertEqual(status["total_messages"], 4)
        self.assertEqual(status["completed_rounds"], 1)
        self.assertEqual(self.run_cli("heartbeat", "--check-only")["status"], "ok")

        raw_text = next(self.root.glob("raw/*/*/*.md")).read_text(encoding="utf-8")
        self.assertIn("请记录这一轮", raw_text)
        self.assertIn("正在核对。", raw_text)
        self.assertIn("这一轮已记录。", raw_text)
        self.assertNotIn("内部推理", raw_text)
        self.assertNotIn("工具输出", raw_text)
        self.assertIn("Ran rg -n TODO src", raw_text)
        transcript_files = [
            path for path in (self.root / "conversations").glob("*.md")
            if path.name != "README.md"
        ]
        self.assertEqual(len(transcript_files), 1)
        transcript = transcript_files[0]
        transcript_text = transcript.read_text(encoding="utf-8")
        self.assertIn("请记录这一轮", transcript_text)
        self.assertIn("正在核对。", transcript_text)
        self.assertIn("这一轮已记录。", transcript_text)
        self.assertNotIn("内部推理", transcript_text)
        self.assertNotIn("工具输出", transcript_text)

        repeated = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(repeated["imported_messages"], 0)
        self.assertEqual(repeated["repaired_transcripts"], 0)
        self.assertIsNone(repeated["backup"])

        transcript.unlink()
        cursor_path = self.root / "imports" / "codex" / "thread-001.json"
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
        cursor["last_line"] = 0
        cursor_path.write_text(json.dumps(cursor), encoding="utf-8")
        repaired_transcript = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(repaired_transcript["imported_messages"], 0)
        self.assertEqual(repaired_transcript["repaired_transcripts"], 4)
        self.assertIsNotNone(repaired_transcript["backup"])
        self.assertIn("请记录这一轮", transcript.read_text(encoding="utf-8"))

        with session.open("a", encoding="utf-8") as handle:
            handle.write(
                event(
                    "2026-07-16T10:01:00Z",
                    "event_msg",
                    {"type": "user_message", "message": "第二轮"},
                )
                + event(
                    "2026-07-16T10:01:01Z",
                    "event_msg",
                    {"type": "agent_message", "phase": "final_answer", "message": "第二轮完成"},
                )
            )
        second = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(second["imported_messages"], 2)
        self.assertEqual(self.run_cli("status")["completed_rounds"], 2)
        backup_entries = [
            json.loads(line)
            for line in (backup_root / "backup-log.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(backup_entries), 3)
        self.assertEqual(backup_entries[-1]["total_messages"], 6)
        snapshots = [path for path in backup_root.iterdir() if path.is_dir()]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].name, Path(second["backup"]).name)

        manual = self.run_cli("backup", "--reason", "test-manual-backup")
        self.assertEqual(manual["status"], "created")
        self.assertEqual(manual["retention_count"], 1)
        snapshots = [path for path in backup_root.iterdir() if path.is_dir()]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].name, Path(manual["backup"]).name)

    def test_codex_subagent_sessions_are_excluded(self):
        session = self.base / "rollout-guardian.jsonl"

        def event(timestamp, outer_type, payload):
            return json.dumps(
                {"timestamp": timestamp, "type": outer_type, "payload": payload},
                ensure_ascii=False,
            ) + "\n"

        session.write_text(
            event(
                "2026-07-16T10:00:00Z",
                "session_meta",
                {
                    "id": "guardian-thread",
                    "source": {"subagent": {"other": "guardian"}},
                    "parent_thread_id": "user-thread",
                },
            )
            + event(
                "2026-07-16T10:00:01Z",
                "event_msg",
                {
                    "type": "user_message",
                    "message": "The following is the Codex agent history whose request action you are assessing.",
                },
            )
            + event(
                "2026-07-16T10:00:02Z",
                "event_msg",
                {"type": "agent_message", "phase": "final_answer", "message": "approved"},
            ),
            encoding="utf-8",
        )

        result = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(result["imported_messages"], 0)
        self.assertEqual(result["sessions"][0]["excluded_reason"], "subagent-session")
        self.assertEqual(self.run_cli("status")["total_messages"], 0)
        cursor = json.loads(
            (self.root / "imports/codex/guardian-thread.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cursor["excluded_reason"], "subagent-session")

    def test_codex_exec_sessions_are_excluded(self):
        session = self.base / "rollout-exec.jsonl"

        def event(timestamp, outer_type, payload):
            return json.dumps(
                {"timestamp": timestamp, "type": outer_type, "payload": payload},
                ensure_ascii=False,
            ) + "\n"

        session.write_text(
            event(
                "2026-07-16T10:00:00Z",
                "session_meta",
                {"id": "exec-thread", "source": "exec", "thread_source": "user"},
            )
            + event(
                "2026-07-16T10:00:01Z",
                "event_msg",
                {"type": "user_message", "message": "Reply with exactly OK."},
            )
            + event(
                "2026-07-16T10:00:02Z",
                "event_msg",
                {"type": "agent_message", "phase": "final_answer", "message": "OK"},
            ),
            encoding="utf-8",
        )

        result = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(result["imported_messages"], 0)
        self.assertEqual(result["sessions"][0]["excluded_reason"], "exec-session")
        self.assertEqual(self.run_cli("status")["total_messages"], 0)
        cursor = json.loads(
            (self.root / "imports/codex/exec-thread.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cursor["excluded_reason"], "exec-session")

    def test_level_one_jobs_and_indexes_are_conversation_scoped(self):
        for conversation_id, label in (("codex:thread-a", "A"), ("codex:thread-b", "B")):
            for number in range(1, 3):
                self.run_cli(
                    "append",
                    "--speaker", "user",
                    "--conversation-id", conversation_id,
                    "--text", f"{label} user {number}",
                )
                self.run_cli(
                    "append",
                    "--speaker", "assistant",
                    "--conversation-id", conversation_id,
                    "--text", f"{label} final {number}",
                )

        first_path = Path(self.run_cli("make-summary-job")["job"])
        second_path = Path(self.run_cli("make-summary-job")["job"])
        first = json.loads(first_path.read_text(encoding="utf-8"))
        second = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        self.assertNotEqual(first["target_summary_id"], second["target_summary_id"])
        for job in (first, second):
            self.assertTrue(job["source_records"])
            self.assertEqual(
                {record["conversation_id"] for record in job["source_records"]},
                {job["conversation_id"]},
            )
            source_rounds = {
                record["round_number"] for record in job["source_records"]
                if record["round_number"] > 0
            }
            self.assertEqual(min(source_rounds), job["source_round_start"])
            self.assertEqual(max(source_rounds), job["source_round_end"])
            self.assertEqual(len(source_rounds), 2)
            self.assertLessEqual(job["start_time"], job["end_time"])

        rebuilt = self.run_cli("rebuild-indexes", "--apply")
        self.assertEqual(rebuilt["raw_messages"], 8)
        for conversation_id in ("codex:thread-a", "codex:thread-b"):
            index_dir = self.root / "indexes/by-conversation" / conversation_id.replace(":", "-")
            self.assertTrue(index_dir.joinpath("messages.jsonl").exists())
            messages = [
                json.loads(line)
                for line in index_dir.joinpath("messages.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(messages), 4)
            self.assertEqual({record["conversation_id"] for record in messages}, {conversation_id})

    def test_concurrent_conversations_keep_rounds_and_replies_isolated(self):
        first_user = self.run_cli(
            "append",
            "--speaker", "user",
            "--conversation-id", "codex:thread-a",
            "--text", "A user",
        )
        second_user = self.run_cli(
            "append",
            "--speaker", "user",
            "--conversation-id", "codex:thread-b",
            "--text", "B user",
        )
        second_final = self.run_cli(
            "append",
            "--speaker", "assistant",
            "--conversation-id", "codex:thread-b",
            "--text", "B final",
        )

        self.assertEqual(first_user["round_number"], 1)
        self.assertEqual(second_user["round_number"], 2)
        self.assertEqual(second_final["round_number"], 2)
        self.assertEqual(second_final["reply_to"], second_user["message_id"])
        intermediate = self.run_cli("status")
        self.assertEqual(intermediate["completed_rounds"], 0)
        self.assertEqual(intermediate["completed_rounds_out_of_order"], [2])
        self.assertEqual(intermediate["unsummarized_completed_rounds"], 1)
        self.assertEqual(
            set(intermediate["pending_rounds"]),
            {"codex:thread-a"},
        )

        first_final = self.run_cli(
            "append",
            "--speaker", "assistant",
            "--conversation-id", "codex:thread-a",
            "--text", "A final",
        )
        self.assertEqual(first_final["round_number"], 1)
        self.assertEqual(first_final["reply_to"], first_user["message_id"])
        completed = self.run_cli("status")
        self.assertEqual(completed["completed_rounds"], 2)
        self.assertEqual(completed["completed_rounds_out_of_order"], [])
        self.assertEqual(completed["unsummarized_completed_rounds"], 2)
        self.assertEqual(completed["pending_rounds"], {})
        self.assertEqual(self.run_cli("heartbeat", "--check-only")["status"], "ok")

        records = [
            json.loads(line)
            for line in (self.root / "indexes" / "conversations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        owners = {record["message_id"]: record["conversation_id"] for record in records}
        self.assertFalse([
            record
            for record in records
            if record.get("reply_to")
            and owners.get(record["reply_to"]) != record["conversation_id"]
        ])

    def test_round_recovery_keeps_duplicate_number_pending_per_conversation(self):
        store = MemoryStore(self.root, self.config)
        records = [
            {"sequence": 1, "round_number": 1, "conversation_id": "codex:a", "speaker": "user", "message_id": "a-u"},
            {"sequence": 2, "round_number": 1, "conversation_id": "codex:a", "speaker": "assistant", "message_id": "a-f", "completes_round": True},
            {"sequence": 3, "round_number": 1, "conversation_id": "codex:b", "speaker": "user", "message_id": "b-u"},
        ]

        recovered = store.recover_round_tracking(records)

        self.assertEqual(recovered["completed_rounds"], 1)
        self.assertEqual(
            recovered["pending_rounds"],
            {
                "codex:b": {
                    "number": 1,
                    "first_user_message_id": "b-u",
                    "latest_user_message_id": "b-u",
                }
            },
        )
        self.assertEqual(recovered["next_round_number"], 2)

    def test_native_collector_matches_python_storage_contract(self):
        self.config.write_text(
            self.config.read_text(encoding="utf-8")
            .replace("level_1_trigger_rounds: 2", "level_1_trigger_rounds: 999")
            .replace(
                "level_1_trigger_characters: 20000",
                "level_1_trigger_characters: 999999\n  level_1_trigger_tokens: 1",
            ),
            encoding="utf-8",
        )
        cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
        completed = subprocess.run(
            [cargo, "build", "--manifest-path", str(NATIVE_MANIFEST)],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        native_binary = SKILL_ROOT / "native-collector" / "target" / "debug" / (
            "memory-wuxian-collector.exe" if sys.platform == "win32" else "memory-wuxian-collector"
        )

        native_root = self.base / "native-memory"
        initialized = subprocess.run(
            [sys.executable, str(CLI), "--root", str(native_root), "--config", str(self.config), "init"],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(self.base / "codex-home")},
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        sessions_root = self.base / "native-sessions"
        sessions_root.mkdir()
        session = sessions_root / "rollout-2026-07-16T10-00-00-native-parity.jsonl"

        def event(timestamp, outer_type, payload):
            return json.dumps(
                {"timestamp": timestamp, "type": outer_type, "payload": payload},
                ensure_ascii=False,
            ) + "\n"

        session.write_text(
            event("2026-07-16T10:00:00Z", "session_meta", {"id": "native-parity"})
            + event("2026-07-16T10:00:01Z", "event_msg", {"type": "user_message", "message": "password=secret-value 请记录"})
            + event("2026-07-16T10:00:02Z", "event_msg", {"type": "agent_message", "phase": "commentary", "message": "正在记录。"})
            + event("2026-07-16T10:00:03Z", "event_msg", {"type": "agent_reasoning", "text": "不得保存"})
            + event("2026-07-16T10:00:03.500Z", "response_item", {"type": "function_call", "name": "shell_command", "arguments": json.dumps({"command": "rg memory"})})
            + event(
                "2026-07-16T10:00:03.750Z",
                "event_msg",
                {
                    "type": "patch_apply_end",
                    "success": True,
                    "changes": {
                        "scripts/example.py": {
                            "type": "update",
                            "move_path": None,
                            "unified_diff": "@@ -1,2 +1,3 @@\n-old = 1\n+new = 2\n keep = True\n+added = True\n",
                        },
                        "docs/new.md": {
                            "type": "create",
                            "move_path": None,
                            "unified_diff": "@@ -0,0 +1 @@\n+Documented change\n",
                        },
                    },
                },
            )
            + event("2026-07-16T10:00:04Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "记录完成。"})
            + event(
                "2026-07-16T10:00:04.500Z",
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 90,
                            "cached_input_tokens": 40,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 3,
                            "total_tokens": 100,
                        },
                        "last_token_usage": {
                            "input_tokens": 90,
                            "cached_input_tokens": 40,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 3,
                            "total_tokens": 100,
                        },
                        "model_context_window": 258400,
                    },
                },
            )
            + event("2026-07-16T10:01:00Z", "event_msg", {"type": "user_message", "message": "第二轮"})
            + event("2026-07-16T10:01:01Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "第二轮完成。"})
            + event(
                "2026-07-16T10:01:01.500Z",
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 220,
                            "cached_input_tokens": 120,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 8,
                            "total_tokens": 250,
                        },
                        "last_token_usage": {
                            "input_tokens": 130,
                            "cached_input_tokens": 80,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                            "total_tokens": 150,
                        },
                        "model_context_window": 258400,
                    },
                },
            )
            + "".join(
                event(
                    "2026-07-16T10:02:00Z",
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "wait",
                        "arguments": json.dumps({"cell_id": f"migration-{index}"}),
                    },
                )
                for index in range(520)
            )
            + event(
                "2026-07-16T10:02:01Z",
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 220,
                            "cached_input_tokens": 120,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 8,
                            "total_tokens": 250,
                        },
                        "last_token_usage": {
                            "input_tokens": 130,
                            "cached_input_tokens": 80,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                            "total_tokens": 150,
                        },
                        "model_context_window": 258400,
                    },
                },
            ),
            encoding="utf-8",
        )
        worker_marker = self.base / "native-semantic-worker-marker.json"
        fake_worker = self.base / "fake-semantic-worker.py"
        fake_worker.write_text(
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(worker_marker)!r}).write_text(json.dumps(sys.argv))\n",
            encoding="utf-8",
        )
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                "ai_summary:\n"
                "  enabled: true\n"
                f"  python_path: {json.dumps(sys.executable)}\n"
                f"  worker_path: {json.dumps(str(fake_worker))}\n"
                f"  dispatcher_path: {json.dumps(str(SKILL_ROOT / 'scripts' / 'semantic_dispatch.py'))}\n"
            )
        python_result = self.run_cli("sync-codex", "--session-file", str(session))
        native_args = [
            str(native_binary),
            "--archive-root", str(native_root),
            "--config", str(self.config),
            "--sessions-root", str(sessions_root),
            "--session-file", str(session),
            "--once",
        ]
        archive_lock = native_root / ".locks" / "archive.lock"
        with exclusive_lock(archive_lock):
            native_process = subprocess.Popen(
                native_args,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.2)
            self.assertIsNone(native_process.poll())
        try:
            native_stdout, native_stderr = native_process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            native_process.kill()
            native_stdout, native_stderr = native_process.communicate()
            self.fail(
                "native parity collector did not finish within 60 seconds:\n"
                + native_stderr
            )
        native = subprocess.CompletedProcess(
            native_args,
            native_process.returncode,
            native_stdout,
            native_stderr,
        )
        self.assertEqual(native.returncode, 0, native.stderr)
        native_result = json.loads(native.stdout)
        self.assertEqual(python_result["imported_messages"], 527)
        self.assertEqual(native_result["imported_messages"], 527)
        self.assertFalse(worker_marker.exists())
        self.assertIsNotNone(native_result["created_summary_job"])
        self.assertNotIn("semantic_worker", native_result)
        self.assertTrue(Path(native_result["created_summary_job"]).is_file())

        def embedded_records(root):
            records = []
            for path in root.glob("raw/*/*/*.md"):
                lines = path.read_text(encoding="utf-8").splitlines()
                records.extend(
                    json.loads(lines[index + 2])
                    for index, line in enumerate(lines[:-2])
                    if line == "<!-- memory-wuxian-record -->" and lines[index + 1] == "```json"
                )
            return sorted(records, key=lambda record: record["sequence"])

        python_records = embedded_records(self.root)
        self.assertEqual(python_records, embedded_records(native_root))
        python_usage = json.loads(
            (self.root / "imports/codex/token-usage/native-parity.json").read_text(
                encoding="utf-8"
            )
        )
        native_usage = json.loads(
            (
                native_root
                / "imports/codex/token-usage/native-parity.json"
            ).read_text(encoding="utf-8")
        )
        for value in (python_usage, native_usage):
            value.pop("updated_at", None)
        self.assertEqual(python_usage, native_usage)
        self.assertEqual(python_usage["reported_usage"]["total_tokens"], 250)
        legacy_native_usage = dict(native_usage)
        legacy_native_usage["format_version"] = 1
        legacy_native_usage.pop("daily_usage", None)
        legacy_native_usage.pop("daily_usage_timezone", None)
        native_usage_path = native_root / "imports/codex/token-usage/native-parity.json"
        native_usage_path.write_text(json.dumps(legacy_native_usage), encoding="utf-8")
        native_cursor_path = native_root / "imports/codex/native-parity.json"
        legacy_cursor = json.loads(native_cursor_path.read_text(encoding="utf-8"))
        legacy_cursor["token_usage_format_version"] = 1
        native_cursor_path.write_text(json.dumps(legacy_cursor), encoding="utf-8")
        migrated = subprocess.run(
            native_args,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        migrated_usage = json.loads(native_usage_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_usage["format_version"], 2)
        self.assertEqual(migrated_usage["reported_usage"]["total_tokens"], 250)
        self.assertEqual(migrated_usage["daily_usage"]["2026-07-16"]["total_tokens"], 250)
        migrated_cursor = json.loads(native_cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_cursor["message_last_line"], migrated_cursor["last_line"])
        damaged_cursor = dict(migrated_cursor)
        damaged_cursor.pop("message_last_line")
        damaged_cursor.pop("committed_byte_offset")
        damaged_cursor["last_line"] = 512
        damaged_cursor["source_size"] = 0
        damaged_cursor["complete"] = False
        native_cursor_path.write_text(json.dumps(damaged_cursor), encoding="utf-8")
        recovered = subprocess.run(
            native_args,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["imported_messages"], 0)
        recovered_cursor = json.loads(native_cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered_cursor["message_last_line"], recovered_cursor["last_line"])
        file_change = next(
            record for record in python_records
            if record.get("source", {}).get("phase") == "file_change"
        )
        self.assertIn("Edited 2 files: +3 -1", file_change["text"])
        self.assertIn("File: scripts/example.py [update] (+2 -1)", file_change["text"])
        self.assertIn("@@ -1,2 +1,3 @@", file_change["text"])
        self.assertIn("-old = 1", file_change["text"])
        self.assertIn("+new = 2", file_change["text"])
        python_deterministic = [
            json.loads(line)
            for line in (self.root / "indexes/deterministic/level-1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        native_deterministic = [
            json.loads(line)
            for line in (native_root / "indexes/deterministic/level-1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(python_deterministic, native_deterministic)
        python_state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        native_state = json.loads((native_root / "state.json").read_text(encoding="utf-8"))
        python_state.pop("last_successful_memory_update")
        native_state.pop("last_successful_memory_update")
        self.assertEqual(python_state, native_state)
        python_job = json.loads(next((self.root / "pending").glob("job-*.json")).read_text(encoding="utf-8"))
        native_job = json.loads(next((native_root / "pending").glob("job-*.json")).read_text(encoding="utf-8"))
        python_job.pop("created_at")
        native_job.pop("created_at")
        self.assertEqual(python_job, native_job)
        self.assertEqual(1, native_job["source_round_end"])
        self.assertEqual(
            json.loads((self.root / "imports/codex/native-parity.json").read_text(encoding="utf-8"))["last_line"],
            json.loads((native_root / "imports/codex/native-parity.json").read_text(encoding="utf-8"))["last_line"],
        )
        stale_cursor = json.loads(native_cursor_path.read_text(encoding="utf-8"))
        stale_cursor["source_path"] = "C:/stale/path/rollout-native-parity.jsonl"
        stale_cursor["complete"] = False
        native_cursor_path.write_text(json.dumps(stale_cursor), encoding="utf-8")
        repeated = subprocess.run(
            native.args,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["imported_messages"], 0)
        converged_cursor = json.loads(native_cursor_path.read_text(encoding="utf-8"))
        self.assertEqual(str(session.resolve()), converged_cursor["source_path"])
        self.assertTrue(converged_cursor["complete"])

        guardian = sessions_root / "rollout-guardian.jsonl"
        guardian.write_text(
            event(
                "2026-07-16T10:01:10Z",
                "session_meta",
                {
                    "id": "native-guardian",
                    "source": {"subagent": {"other": "guardian"}},
                },
            )
            + event(
                "2026-07-16T10:01:11Z",
                "event_msg",
                {"type": "user_message", "message": "hidden approval-review context"},
            ),
            encoding="utf-8",
        )
        guardian_native = subprocess.run(
            [
                str(native_binary),
                "--archive-root", str(native_root),
                "--config", str(self.config),
                "--sessions-root", str(sessions_root),
                "--session-file", str(guardian),
                "--once",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(guardian_native.returncode, 0, guardian_native.stderr)
        guardian_result = json.loads(guardian_native.stdout)
        guardian_session = next(
            item for item in guardian_result["sessions"]
            if item["session_id"] == "native-guardian"
        )
        self.assertEqual(guardian_session["excluded_reason"], "subagent-session")
        self.assertNotIn(
            "hidden approval-review context",
            "\n".join(
                path.read_text(encoding="utf-8")
                for path in native_root.glob("raw/*/*/*.md")
            ),
        )

        concurrent_a = sessions_root / "rollout-concurrent-a.jsonl"
        concurrent_b = sessions_root / "rollout-concurrent-b.jsonl"
        concurrent_a.write_text(
            event("2026-07-16T10:02:00Z", "session_meta", {"id": "concurrent-a"})
            + event("2026-07-16T10:02:01Z", "event_msg", {"type": "user_message", "message": "A user"}),
            encoding="utf-8",
        )
        concurrent_b.write_text(
            event("2026-07-16T10:02:00Z", "session_meta", {"id": "concurrent-b"})
            + event("2026-07-16T10:02:02Z", "event_msg", {"type": "user_message", "message": "B user"})
            + event("2026-07-16T10:02:03Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "B final"}),
            encoding="utf-8",
        )
        self.run_cli(
            "sync-codex",
            "--session-file", str(concurrent_a),
            "--session-file", str(concurrent_b),
        )
        concurrent_native_args = [
            str(native_binary),
            "--archive-root", str(native_root),
            "--config", str(self.config),
            "--sessions-root", str(sessions_root),
            "--session-file", str(concurrent_a),
            "--session-file", str(concurrent_b),
            "--once",
        ]
        concurrent_native = subprocess.run(
            concurrent_native_args,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(concurrent_native.returncode, 0, concurrent_native.stderr)
        python_state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        native_state = json.loads((native_root / "state.json").read_text(encoding="utf-8"))
        python_state.pop("last_successful_memory_update")
        native_state.pop("last_successful_memory_update")
        self.assertEqual(python_state, native_state)
        self.assertEqual(python_state["completed_rounds"], 2)
        self.assertEqual(python_state["completed_rounds_out_of_order"], [4])

        with concurrent_a.open("a", encoding="utf-8") as handle:
            handle.write(
                event("2026-07-16T10:02:04Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "A final"})
            )
        self.run_cli("sync-codex", "--session-file", str(concurrent_a))
        completed_native = subprocess.run(
            concurrent_native_args,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed_native.returncode, 0, completed_native.stderr)
        self.assertEqual(embedded_records(self.root), embedded_records(native_root))
        final_state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(final_state["completed_rounds"], 4)
        self.assertEqual(final_state["completed_rounds_out_of_order"], [])
        self.assertEqual(final_state["pending_rounds"], {})

        owners = {
            record["message_id"]: record["conversation_id"]
            for record in embedded_records(native_root)
        }
        self.assertFalse([
            record
            for record in embedded_records(native_root)
            if record.get("reply_to")
            and owners.get(record["reply_to"]) != record["conversation_id"]
        ])

    def test_sync_backfills_historical_file_changes_once(self):
        session = self.base / "rollout-historical-patch.jsonl"
        events = [
            {"timestamp": "2026-07-16T10:00:00Z", "type": "session_meta", "payload": {"id": "historical-patch"}},
            {"timestamp": "2026-07-16T10:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "old user"}},
            {
                "timestamp": "2026-07-16T10:00:02Z",
                "type": "event_msg",
                "payload": {
                    "type": "patch_apply_end",
                    "success": True,
                    "changes": {
                        "src/app.py": {
                            "type": "update",
                            "move_path": None,
                            "unified_diff": "@@ -1 +1 @@\n-before\n+after\n",
                        }
                    },
                },
            },
            {"timestamp": "2026-07-16T10:00:03Z", "type": "event_msg", "payload": {"type": "agent_message", "phase": "final_answer", "message": "old answer"}},
        ]
        session.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )
        cursor = self.root / "imports/codex/historical-patch.json"
        cursor.write_text(
            json.dumps({"format_version": 1, "session_id": "historical-patch", "last_line": len(events)}),
            encoding="utf-8",
        )

        first = self.run_cli("sync-codex", "--session-file", str(session))
        second = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(first["imported_messages"], 1)
        self.assertEqual(second["imported_messages"], 0)
        self.assertEqual(json.loads(cursor.read_text(encoding="utf-8"))["file_change_format_version"], 1)
        transcript = (self.root / "conversations/codex-historical-patch.md").read_text(encoding="utf-8")
        self.assertIn("Edited 1 file: +1 -1", transcript)
        self.assertIn("@@ -1 +1 @@", transcript)
        self.assertNotIn("old user", transcript)
        self.assertNotIn("old answer", transcript)

    def test_python_commands_wait_for_archive_lock(self):
        archive_lock = self.root / ".locks" / "archive.lock"
        with exclusive_lock(archive_lock):
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(CLI),
                    "--root",
                    str(self.root),
                    "--config",
                    str(self.config),
                    "status",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.2)
            self.assertIsNone(process.poll())
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["total_messages"], 0)

    def test_retrieve_does_not_require_the_archive_write_lock(self):
        self.append_round(1)
        archive_lock = self.root / ".locks" / "archive.lock"
        with exclusive_lock(archive_lock):
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--root",
                    str(self.root),
                    "--config",
                    str(self.config),
                    "retrieve",
                    "--query",
                    "分层长期记忆",
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=5,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Memory無限 Retrieval", completed.stdout)

    def test_launch_agent_installer_uses_native_keepalive_collector(self):
        sessions_root = self.base / "sessions"
        sessions_root.mkdir()
        collector = self.base / "memory-wuxian-collector"
        collector.write_text("test executable\n", encoding="utf-8")
        collector.chmod(0o755)
        plist_path = self.base / "com.memorywuxian.codex-sync.plist"
        completed = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--archive-root",
                str(self.root),
                "--skill-root",
                str(SKILL_ROOT),
                "--sessions-root",
                str(sessions_root),
                "--collector-executable",
                str(collector),
                "--output",
                str(plist_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(self.base / "codex-home")},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
        self.assertEqual(payload["ProgramArguments"][0], str(collector))
        self.assertTrue(payload["KeepAlive"])
        self.assertNotIn("StartInterval", payload)
        self.assertEqual(payload["EnvironmentVariables"], {"RUST_BACKTRACE": "1"})
        self.assertNotIn("kickstart", INSTALLER.read_text(encoding="utf-8"))
        self.assertEqual(
            (
                self.base / "codex-home" / "memory-wuxian-active-root.txt"
            ).read_text(encoding="utf-8").strip(),
            str(self.root.resolve()),
        )

    def test_windows_installer_writes_direct_no_console_command_manifest(self):
        sessions_root = self.base / "sessions"
        sessions_root.mkdir()
        collector = self.base / "memory-wuxian-collector.exe"
        collector.write_bytes(b"test executable\n")
        codex = self.base / "codex.exe"
        codex.write_bytes(b"test executable\n")
        wrapper = self.base / "collector-command.json"
        codex_home = self.base / "codex-home"
        completed = subprocess.run(
            [
                sys.executable,
                str(WINDOWS_INSTALLER),
                "--archive-root", str(self.root),
                "--skill-root", str(SKILL_ROOT),
                "--sessions-root", str(sessions_root),
                "--collector-executable", str(collector),
                "--python-executable", sys.executable,
                "--codex-cli", str(codex),
                "--output", str(wrapper),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = json.loads(wrapper.read_text(encoding="utf-8"))
        command = manifest["command"]
        self.assertEqual(command[0], str(collector.resolve()))
        self.assertIn("--python-executable", command)
        self.assertIn("--codex-cli", command)
        self.assertIn("--sessions-root", command)
        self.assertFalse(manifest["console_window"])
        self.assertNotIn("powershell.exe", " ".join(command).lower())
        self.assertEqual(
            (codex_home / "memory-wuxian-active-root.txt").read_text(encoding="utf-8").strip(),
            str(self.root.resolve()),
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows bootstrap test")
    def test_windows_bootstrap_reports_runtime_versions(self):
        sessions = self.base / "sessions"
        sessions.mkdir()
        collector = self.base / "memory-wuxian-collector.exe"
        collector.write_bytes(b"collector")
        codex = self.base / "codex.exe"
        codex.write_bytes(b"codex")
        completed = subprocess.run(
            [
                "powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(WINDOWS_BOOTSTRAP),
                "-PythonPath", sys.executable,
                "-CodexCliPath", str(codex),
                "-CollectorPath", str(collector),
                "-SessionsRoot", str(sessions),
            ],
            text=True,
            encoding="utf-8-sig",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ready"])
        version = tuple(map(int, result["checks"]["python"]["version"].split(".")))
        self.assertGreaterEqual(version, (3, 9))
        yaml_version = result["checks"]["yaml"]["version"]
        self.assertIsNotNone(yaml_version)
        self.assertEqual(int(yaml_version.split(".", 1)[0]), 6)
        self.assertTrue(result["checks"]["yaml"]["ready"])

    def test_agent_rules_installer_is_idempotent(self):
        agents_file = self.base / "workspace" / "AGENTS.md"
        agents_file.parent.mkdir()
        agents_file.write_text("# Workspace rules\n", encoding="utf-8")
        command = [
            sys.executable,
            str(AGENT_RULES_INSTALLER),
            "--agents-file",
            str(agents_file),
        ]
        first = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        installed = agents_file.read_text(encoding="utf-8")
        second = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(agents_file.read_text(encoding="utf-8"), installed)
        self.assertEqual(installed.count("<!-- memory-wuxian:rules:start -->"), 1)
        self.assertIn("## Memory无限长期记忆约定", installed)

    def test_context_refresh_capsule_is_bounded_and_acknowledged(self):
        session_id = "019f-context-refresh"
        session = self.base / "sessions" / "rollout-context.jsonl"
        session.parent.mkdir()
        events = [
            {
                "type": "session_meta",
                "payload": {"id": session_id, "source": "codex"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"total_tokens": 70000},
                        "model_context_window": 100000,
                    },
                },
            },
        ]
        session.write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
            encoding="utf-8",
        )
        self.config.write_text(
            self.config.read_text(encoding="utf-8")
            + f'\ncodex:\n  sessions_root: "{session.parent.as_posix()}"\n'
            + "context_refresh:\n  enabled: true\n  round_interval: 10\n"
            + "  utilization_low_percent: 65\n  utilization_high_percent: 80\n"
            + "  context_fraction_percent: 1\n  soft_max_tokens: 3000\n"
            + "  absolute_max_tokens: 10000\n",
            encoding="utf-8",
        )
        status = self.run_cli("context-refresh-status")
        self.assertTrue(status["due"])
        self.assertEqual(status["capsule_token_budget"], 1000)
        archive_lock = self.root / ".locks" / "archive.lock"
        with exclusive_lock(archive_lock):
            unlocked_status = self.invoke_cli("context-refresh-status", timeout=5)
            unlocked_capsule = self.invoke_cli("context-capsule", timeout=5)
        self.assertEqual(unlocked_status.returncode, 0, unlocked_status.stderr)
        self.assertEqual(unlocked_capsule.returncode, 0, unlocked_capsule.stderr)
        self.assertTrue(json.loads(unlocked_status.stdout)["due"])
        self.assertIn("# Memory无限运行时记忆胶囊", unlocked_capsule.stdout)
        acknowledged = self.run_cli("ack-context-refresh")
        self.assertEqual(acknowledged["status"], "acknowledged")
        self.assertFalse(self.run_cli("context-refresh-status")["due"])


if __name__ == "__main__":
    unittest.main()
