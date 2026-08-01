import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from collector_activation import FORMAT, resolve_activation_since


class CollectorActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_upgrade_never_moves_boundary_later(self):
        first = resolve_activation_since(self.root, "2026-07-01T12:00:00+09:00")
        second = resolve_activation_since(self.root, "2026-08-01T12:00:00+09:00")
        self.assertEqual(first, second)
        state = json.loads(
            (self.root / "imports/codex/collector-activation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(FORMAT, state["format"])

    def test_upgrade_recovers_earlier_boundary_from_raw_archive(self):
        raw = self.root / "raw/2026/07/2026-07-18.md"
        raw.parent.mkdir(parents=True)
        raw.write_text(
            '# Memory\n\n{"timestamp":"2026-07-18T01:02:03+00:00","text":"x"}\n',
            encoding="utf-8",
        )
        since = resolve_activation_since(self.root, "2026-08-01T08:10:03+09:00")
        self.assertEqual("2026-07-18T01:02:03Z", since)

    def test_existing_command_manifest_is_migration_input(self):
        manifest = self.root / "imports/codex/collector-command.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"command": ["collector", "--since", "2026-07-20T00:00:00Z"]}),
            encoding="utf-8",
        )
        since = resolve_activation_since(self.root)
        self.assertEqual("2026-07-20T00:00:00Z", since)


if __name__ == "__main__":
    unittest.main()
