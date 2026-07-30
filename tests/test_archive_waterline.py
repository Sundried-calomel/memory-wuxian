import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from archive_waterline import evaluate


class ArchiveWaterlineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.sessions = self.root / "sessions"
        (self.archive / "imports" / "codex").mkdir(parents=True)
        self.sessions.mkdir()
        self.source = self.sessions / "rollout-test.jsonl"
        self.lines = [
            json.dumps({"timestamp": "2026-07-30T00:00:00Z", "type": "session_meta"}) + "\n",
            json.dumps({"timestamp": "2026-07-30T01:00:00Z", "type": "event_msg"}) + "\n",
            json.dumps({"timestamp": "2026-07-30T03:00:00Z", "type": "event_msg"}) + "\n",
        ]
        self.source.write_text("".join(self.lines), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def write_cursor(self, size):
        (self.archive / "imports" / "codex" / "test.json").write_text(
            json.dumps({"source_path": str(self.source), "source_size": size}),
            encoding="utf-8",
        )

    def test_cutoff_is_covered_when_cursor_passes_required_boundary(self):
        self.write_cursor(len("".join(self.lines[:2]).encode()))
        result = evaluate(
            self.archive,
            self.sessions,
            datetime(2026, 7, 30, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "covered")

    def test_cutoff_reports_exact_missing_source_bytes(self):
        first = len(self.lines[0].encode())
        required = len("".join(self.lines[:2]).encode())
        self.write_cursor(first)
        result = evaluate(
            self.archive,
            self.sessions,
            datetime(2026, 7, 30, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "lagging")
        self.assertEqual(
            result["lagging_sources"][0]["missing_bytes_through_cutoff"],
            required - first,
        )


if __name__ == "__main__":
    unittest.main()
