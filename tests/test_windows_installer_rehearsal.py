from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.run_windows_installer_rehearsal import (
    PRODUCTION_RUN_VALUES,
    PRODUCTION_TASKS,
    _cleanup,
    build_namespace,
    parser,
)


class WindowsInstallerRehearsalTests(unittest.TestCase):
    def test_direct_clean_mode_does_not_require_the_historical_fixture(self) -> None:
        args = parser().parse_args([
            "--candidate-root", "candidate",
            "--runtime-bundle-root", "runtime",
            "--python-executable", "python.exe",
            "--codex-cli", "codex.exe",
            "--work-root", "work",
            "--output", "receipt.json",
            "--scenario", "clean-install",
        ])
        self.assertEqual(args.scenario, "clean-install")
        self.assertIsNone(args.v215_source)

    def test_rehearsal_namespace_cannot_collide_with_production_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            namespace = build_namespace(Path(temporary), "0123456789ab")
        task_names = {
            namespace.collector_task_name,
            namespace.maintenance_task_name,
            namespace.auto_update_task_name,
        }
        run_values = {
            namespace.collector_run_value,
            namespace.auto_update_run_value,
        }
        self.assertFalse(task_names.intersection(PRODUCTION_TASKS))
        self.assertFalse(run_values.intersection(PRODUCTION_RUN_VALUES))
        self.assertTrue(all(name.startswith("MemoryWuxianRehearsal-") for name in task_names | run_values))

    def test_cleanup_removes_and_verifies_only_the_namespaced_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            namespace = build_namespace(Path(temporary), "0123456789ab")
            shortcut = namespace.dashboard_desktop / namespace.dashboard_shortcut_name
            shortcut.parent.mkdir(parents=True)
            shortcut.write_bytes(b"rehearsal shortcut")
            absent = subprocess.CompletedProcess([], 1, b"", b"")
            with (
                mock.patch("scripts.run_windows_installer_rehearsal.uninstall_windows_task"),
                mock.patch("scripts.run_windows_installer_rehearsal.query_windows_task_xml", return_value=None),
                mock.patch("scripts.run_windows_installer_rehearsal.subprocess.run", return_value=absent),
            ):
                evidence = _cleanup(namespace)
            self.assertFalse(shortcut.exists())
            self.assertFalse(evidence["shortcut"]["exists"])
            self.assertTrue(all(not item["exists"] for item in evidence["tasks"].values()))
            self.assertTrue(all(not item["exists"] for item in evidence["run_values"].values()))


if __name__ == "__main__":
    unittest.main()
