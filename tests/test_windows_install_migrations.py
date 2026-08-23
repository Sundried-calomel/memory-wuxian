from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from windows_install_migrations import MigrationError, canonical_hash, default_registry


class WindowsInstallMigrationTests(unittest.TestCase):
    def test_v215_fixture_is_additive_idempotent_and_preserves_archive_evidence(self) -> None:
        fixture = json.loads(
            (ROOT / "tests/fixtures/windows-installed-v2.15.0.json").read_text(encoding="utf-8")
        )
        pointer = fixture["active_archive_pointer"]
        raw_records = copy.deepcopy(fixture["raw_records"])
        raw_hash = canonical_hash(raw_records)
        migrated, evidence = default_registry().migrate_document(
            fixture["config"],
            fixture["defaults"],
            from_version=fixture["installed_version"],
            to_version=fixture["target_version"],
        )
        self.assertEqual(migrated["archive"]["backup_keep"], 7)
        self.assertTrue(migrated["cloud"]["enabled"])
        self.assertTrue(migrated["archive"]["incremental"])
        self.assertEqual(migrated["dashboard"]["language"], "zh-CN")
        self.assertTrue(evidence["idempotent"])
        self.assertEqual(fixture["active_archive_pointer"], pointer)
        self.assertEqual(canonical_hash(fixture["raw_records"]), raw_hash)
        self.assertEqual(fixture["raw_records"], raw_records)

    def test_registry_is_ordered_and_rejects_unknown_sources(self) -> None:
        plan = default_registry().plan("2.15.0", "2.20.0")
        self.assertEqual(plan[0].from_version, "2.15.0")
        self.assertEqual(plan[-1].to_version, "2.20.0")
        with self.assertRaisesRegex(MigrationError, "unsupported"):
            default_registry().plan("1.0.0", "2.20.0")

    def test_clean_and_repeat_install_need_no_migration(self) -> None:
        registry = default_registry()
        self.assertEqual(registry.plan(None, "2.20.0"), ())
        self.assertEqual(registry.plan("2.20.0", "2.20.0"), ())


if __name__ == "__main__":
    unittest.main()
