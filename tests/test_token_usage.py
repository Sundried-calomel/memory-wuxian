import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from token_usage import persist_token_usage  # noqa: E402


def event(timestamp, outer_type, payload):
    return json.dumps(
        {"timestamp": timestamp, "type": outer_type, "payload": payload},
        ensure_ascii=False,
    ) + "\n"


def usage(total, *, cached=0, output=0, reasoning=0):
    return {
        "input_tokens": total - output,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": total,
    }


def token_event(timestamp, total, *, cached=0, output=0, reasoning=0):
    current = usage(
        total,
        cached=cached,
        output=output,
        reasoning=reasoning,
    )
    return event(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "total_token_usage": current,
                "last_token_usage": current,
                "model_context_window": 258400,
            },
        },
    )


class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "memory"
        self.root.mkdir()
        self.config = self.base / "config.yaml"
        self.config.write_text(
            (ROOT / "config.yaml")
            .read_text(encoding="utf-8")
            .replace(
                'backup:\n  enabled: true\n  directory: "~/Desktop/Memory無限-记忆归档备份"',
                f'backup:\n  enabled: false\n  directory: "{self.base / "backups"}"',
                1,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_persist_sums_counter_segments_without_double_counting_subfields(self):
        session = self.base / "rollout-reset.jsonl"
        session.write_text(
            event("2026-07-01T00:00:00Z", "session_meta", {"id": "reset"})
            + token_event("2026-07-01T00:00:01Z", 100, cached=20, output=10)
            + token_event("2026-07-01T00:00:02Z", 250, cached=80, output=20)
            + token_event("2026-07-01T00:00:03Z", 250, cached=80, output=20)
            + token_event("2026-07-01T00:00:04Z", 50, cached=10, output=5)
            + token_event("2026-07-01T00:00:05Z", 120, cached=30, output=10),
            encoding="utf-8",
        )

        first = persist_token_usage(self.root, session)
        self.assertEqual(first["reported_total_tokens"], 370)
        self.assertEqual(first["reported_usage"]["cached_input_tokens"], 110)
        self.assertEqual(first["model_request_count"], 4)
        self.assertEqual(first["counter_reset_count"], 1)
        ledger = json.loads(Path(first["ledger"]).read_text(encoding="utf-8"))
        self.assertEqual(ledger["token_event_count"], 5)
        self.assertEqual(ledger["reported_usage"]["total_tokens"], 370)
        self.assertEqual(ledger["daily_usage_timezone"], "Asia/Tokyo")
        self.assertEqual(ledger["daily_usage"]["2026-07-01"]["total_tokens"], 370)

        repeated = persist_token_usage(self.root, session)
        self.assertEqual(repeated["status"], "unchanged")
        self.assertEqual(repeated["changed_events"], 0)
        self.assertEqual(repeated["reported_total_tokens"], 370)

    def test_subagent_usage_is_excluded(self):
        session = self.base / "rollout-subagent.jsonl"
        session.write_text(
            event(
                "2026-07-01T00:00:00Z",
                "session_meta",
                {
                    "id": "subagent",
                    "source": {"subagent": {"other": "reviewer"}},
                },
            )
            + token_event("2026-07-01T00:00:01Z", 100),
            encoding="utf-8",
        )
        result = persist_token_usage(self.root, session)
        self.assertEqual(result["status"], "excluded")
        self.assertEqual(result["excluded_reason"], "subagent-session")
        self.assertFalse((self.root / "imports/codex/token-usage").exists())

    def test_cli_backfill_previews_applies_and_is_idempotent(self):
        sessions = self.base / "sessions"
        sessions.mkdir()
        session = sessions / "rollout-top.jsonl"
        session.write_text(
            event("2026-07-01T00:00:00Z", "session_meta", {"id": "top"})
            + token_event("2026-07-01T00:00:01Z", 90, output=10)
            + token_event("2026-07-01T00:00:02Z", 160, output=20),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(SCRIPTS / "memory_cli.py"),
            "--root",
            str(self.root),
            "--config",
            str(self.config),
        ]
        initialized = subprocess.run(
            [*command, "init"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        appended = subprocess.run(
            [
                *command,
                "append",
                "--speaker",
                "user",
                "--conversation-id",
                "codex:archive-authority",
                "--text",
                "authoritative raw text",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)

        def authoritative_hashes():
            paths = [self.root / "state.json"]
            for relative in ("raw", "conversations", "summaries", "indexes"):
                paths.extend(
                    path
                    for path in (self.root / relative).rglob("*")
                    if path.is_file()
                )
            return {
                str(path.relative_to(self.root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in paths
            }

        authority_before = authoritative_hashes()

        preview = subprocess.run(
            [*command, "token-usage-backfill", "--sessions-root", str(sessions)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        preview_value = json.loads(preview.stdout)
        self.assertEqual(preview_value["status"], "preview")
        self.assertEqual(preview_value["reported_total_tokens"], 160)
        self.assertFalse((self.root / "imports/codex/token-usage/top.json").exists())

        applied = subprocess.run(
            [
                *command,
                "token-usage-backfill",
                "--sessions-root",
                str(sessions),
                "--apply",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        applied_value = json.loads(applied.stdout)
        self.assertEqual(applied_value["changed_token_events"], 2)
        self.assertTrue((self.root / "imports/codex/token-usage/top.json").exists())
        self.assertEqual(authoritative_hashes(), authority_before)

        repeated = subprocess.run(
            [
                *command,
                "token-usage-backfill",
                "--sessions-root",
                str(sessions),
                "--apply",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["changed_token_events"], 0)
        status = subprocess.run(
            [*command, "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        status_value = json.loads(status.stdout)
        self.assertEqual(
            status_value["codex_reported_token_usage"]["reported_usage"][
                "total_tokens"
            ],
            160,
        )


if __name__ == "__main__":
    unittest.main()
