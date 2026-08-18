import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V217ReleaseContractTests(unittest.TestCase):
    def test_capture_core_paths_and_version_are_registered(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 17, 0))
        contract = json.loads((ROOT / "docs/module-architecture.json").read_text(encoding="utf-8"))
        capture = next(item for item in contract["modules"] if item["id"] == "capture-core")
        self.assertEqual(capture["allowed_dependencies"], ["platform-foundation"])
        for relative in (
            "native-collector/src/lib.rs",
            "native-collector/src/runtime.rs",
            "native-collector/src/source/mod.rs",
            "native-collector/src/store/transaction.rs",
            "native-collector/src/store/cursor.rs",
            "native-collector/src/locking.rs",
            "native-collector/src/telemetry.rs",
            "native-collector/src/bin/memory-wuxian-core-launcher.rs",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_launcher_is_thin_and_watcher_precedes_startup_scan(self):
        launcher = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        core_launcher = (ROOT / "native-collector/src/bin/memory-wuxian-core-launcher.rs").read_text(encoding="utf-8")
        implementation = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        self.assertLess(len(launcher.splitlines()), 10)
        self.assertIn("run_in_thread", launcher)
        self.assertIn("run_in_thread", core_launcher)
        run_block = implementation[implementation.index("fn run() -> Result<()>"):]
        self.assertLess(run_block.index("prepare_watcher"), run_block.index("recent_rollouts"))
        self.assertLess(run_block.index("mark_watcher_ready"), run_block.index("sync_startup_batch"))
        self.assertEqual(
            implementation.count("rollouts_requiring_sync(store, &current_paths)?"),
            2,
        )
        self.assertIn("refreshed_watcher_baseline_cannot_hide_cursor_debt", implementation)


if __name__ == "__main__":
    unittest.main()
