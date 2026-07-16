import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
CLI = SKILL_ROOT / "scripts" / "memory_cli.py"


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

        repeated = self.run_cli("sync-codex", "--session-file", str(session))
        self.assertEqual(repeated["imported_messages"], 0)
        self.assertIsNone(repeated["backup"])

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
        self.assertEqual(len(backup_entries), 2)
        self.assertEqual(backup_entries[-1]["total_messages"], 5)


if __name__ == "__main__":
    unittest.main()
