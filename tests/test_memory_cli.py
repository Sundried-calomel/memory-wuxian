import fcntl
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
CLI = SKILL_ROOT / "scripts" / "memory_cli.py"
INSTALLER = SKILL_ROOT / "scripts" / "install_codex_autosync.py"
NATIVE_MANIFEST = SKILL_ROOT / "native-collector" / "Cargo.toml"
NATIVE_BINARY = SKILL_ROOT / "bin" / "memory-wuxian-collector"


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

    def run_cli(self, *arguments, expect_json=True):
        completed = self.invoke_cli(*arguments)
        if completed.returncode != 0:
            self.fail(f"Command failed: {completed.args}\nstdout={completed.stdout}\nstderr={completed.stderr}")
        return json.loads(completed.stdout) if expect_json else completed.stdout

    def invoke_cli(self, *arguments):
        command = [
            sys.executable,
            str(CLI),
            "--root",
            str(self.root),
            "--config",
            str(self.config),
            *arguments,
        ]
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def append_round(self, number):
        self.run_cli("append", "--speaker", "user", "--text", f"第 {number} 轮讨论分层记忆")
        self.run_cli("append", "--speaker", "assistant", "--text", f"第 {number} 轮确认原文必须保留")

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

    def test_secret_redaction_is_explicit(self):
        result = self.run_cli("append", "--speaker", "user", "--text", "password=secret-value")
        self.assertTrue(result["text_redacted"])
        raw_text = next(self.root.glob("raw/*/*/*.md")).read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", raw_text)
        self.assertNotIn("secret-value", raw_text)

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
        self.assertEqual(first["imported_messages"], 3)
        self.assertIsNotNone(first["backup"])
        self.assertTrue(Path(first["backup"]).joinpath("backup-manifest.json").exists())
        status = self.run_cli("status")
        self.assertEqual(status["total_messages"], 3)
        self.assertEqual(status["completed_rounds"], 1)
        self.assertEqual(self.run_cli("heartbeat", "--check-only")["status"], "ok")

        raw_text = next(self.root.glob("raw/*/*/*.md")).read_text(encoding="utf-8")
        self.assertIn("请记录这一轮", raw_text)
        self.assertIn("正在核对。", raw_text)
        self.assertIn("这一轮已记录。", raw_text)
        self.assertNotIn("内部推理", raw_text)
        self.assertNotIn("工具输出", raw_text)
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
        self.assertEqual(repaired_transcript["repaired_transcripts"], 3)
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
        self.assertEqual(backup_entries[-1]["total_messages"], 5)
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

    def test_native_collector_matches_python_storage_contract(self):
        cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
        if not NATIVE_BINARY.exists():
            completed = subprocess.run(
                [cargo, "build", "--manifest-path", str(NATIVE_MANIFEST)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            native_binary = SKILL_ROOT / "native-collector" / "target" / "debug" / "memory-wuxian-collector"
        else:
            native_binary = NATIVE_BINARY

        native_root = self.base / "native-memory"
        initialized = subprocess.run(
            [sys.executable, str(CLI), "--root", str(native_root), "--config", str(self.config), "init"],
            text=True,
            capture_output=True,
            check=False,
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
            + event("2026-07-16T10:00:04Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "记录完成。"})
            + event("2026-07-16T10:01:00Z", "event_msg", {"type": "user_message", "message": "第二轮"})
            + event("2026-07-16T10:01:01Z", "event_msg", {"type": "agent_message", "phase": "final_answer", "message": "第二轮完成。"}),
            encoding="utf-8",
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
        with archive_lock.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            native_process = subprocess.Popen(
                native_args,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            time.sleep(0.2)
            self.assertIsNone(native_process.poll())
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            native_stdout, native_stderr = native_process.communicate(timeout=10)
        native = subprocess.CompletedProcess(
            native_args,
            native_process.returncode,
            native_stdout,
            native_stderr,
        )
        self.assertEqual(native.returncode, 0, native.stderr)
        native_result = json.loads(native.stdout)
        self.assertEqual(python_result["imported_messages"], 5)
        self.assertEqual(native_result["imported_messages"], 5)

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

        self.assertEqual(embedded_records(self.root), embedded_records(native_root))
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
        self.assertEqual(
            json.loads((self.root / "imports/codex/native-parity.json").read_text(encoding="utf-8"))["last_line"],
            json.loads((native_root / "imports/codex/native-parity.json").read_text(encoding="utf-8"))["last_line"],
        )
        repeated = subprocess.run(native.args, text=True, capture_output=True, check=False)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["imported_messages"], 0)

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

    def test_python_commands_wait_for_archive_lock(self):
        archive_lock = self.root / ".locks" / "archive.lock"
        with archive_lock.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
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
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)["total_messages"], 0)

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
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with plist_path.open("rb") as handle:
            payload = plistlib.load(handle)
        self.assertEqual(payload["ProgramArguments"][0], str(collector))
        self.assertTrue(payload["KeepAlive"])
        self.assertNotIn("StartInterval", payload)
        self.assertEqual(payload["EnvironmentVariables"], {"RUST_BACKTRACE": "1"})
        self.assertNotIn("kickstart", INSTALLER.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
