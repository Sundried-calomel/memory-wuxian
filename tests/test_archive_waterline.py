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
        self.source.write_bytes("".join(self.lines).encode("utf-8"))

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

    def test_each_physical_segment_requires_its_own_cursor(self):
        self.source.unlink()
        session_id = "01a041df-3694-7bd2-b9c6-d8c0c8e12f3f"
        segment_id = "01a041e8-e542-7b80-a315-06a9a1c66cb8"
        first = self.sessions / f"rollout-2026-08-27T15-19-02-{session_id}.jsonl"
        continuation = self.sessions / (
            f"rollout-2026-08-27T15-29-37-{session_id}_{segment_id}.jsonl"
        )
        payload = json.dumps(
            {"timestamp": "2026-08-27T06:30:00Z", "type": "event_msg"}
        ) + "\n"
        first.write_text(payload, encoding="utf-8")
        continuation.write_text(payload, encoding="utf-8")
        for identity, source in ((session_id, first), (segment_id, continuation)):
            (self.archive / "imports" / "codex" / f"{identity}.json").write_text(
                json.dumps({"source_path": str(source), "source_size": source.stat().st_size}),
                encoding="utf-8",
            )
        result = evaluate(
            self.archive,
            self.sessions,
            datetime(2026, 8, 27, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "covered")


if __name__ == "__main__":
    unittest.main()
