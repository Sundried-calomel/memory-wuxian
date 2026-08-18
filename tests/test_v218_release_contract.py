import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V218ReleaseContractTests(unittest.TestCase):
    def test_version_and_wal_contract_are_present(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.18.0")
        wal = (ROOT / "native-collector/src/store/wal.rs").read_text(encoding="utf-8")
        for token in (
            "memory-wuxian-capture-wal-v1",
            "cursor_before_line",
            "cursor_after_line",
            "committed_byte_offset",
            "MAX_WAL_BYTES",
            "invalid capture WAL line",
        ):
            self.assertIn(token, wal)
        for forbidden in ("message", "dialogue", "content_text", "summary"):
            self.assertNotIn(forbidden, wal)

    def test_capture_faults_are_isolated_and_health_is_pure_read(self):
        native = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        health = (ROOT / "scripts/memory_service_state.py").read_text(encoding="utf-8")
        supervisor = (ROOT / "scripts/maintenance_supervisor.py").read_text(encoding="utf-8")
        self.assertIn("for path in paths", native)
        self.assertIn("source sync error", native)
        self.assertIn("collector telemetry error", native)
        self.assertNotIn("queue.recover_expired()", health)
        self.assertIn("return 1 if had_error else 0", supervisor)

    def test_summary_and_cloud_owners_are_untouched_by_capture_core(self):
        changed_surface = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "native-collector/src/store/wal.rs",
                "native-collector/src/telemetry.rs",
                "scripts/memory_service_state.py",
                "scripts/maintenance_supervisor.py",
            )
        )
        self.assertNotIn("memory_cloud_transport", changed_surface)
        self.assertNotIn("semantic_worker.py", changed_surface)
        self.assertNotIn("summary-v2", changed_surface.lower())


if __name__ == "__main__":
    unittest.main()
