from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from collector_lifecycle import (
    create_installed_effect_probe,
    inspect_live_effect,
    inspect_startup_owner,
    remove_installed_effect_probe,
    run_isolated_watermark_probe,
    verify_collector_lifecycle,
    watermark_reached,
)
from platform_process import _unique_command_argument


class CollectorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.archive = self.base / ("归档 日￥😀 " + "长" * 80)
        self.command = [
            str(self.base / "collector executable.exe"),
            "--archive-root",
            str(self.archive),
            "--generation",
            "v2.16",
        ]
        self.owner = {
            "owner_id": "launchd:memory-wuxian",
            "kind": "launch-agent",
            "generation": "v2.16",
            "archive_root": str(self.archive),
            "command": self.command,
            "pid_identity": "required",
        }
        self.manifest = {
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": "v2.16",
            "archive_root": str(self.archive),
            "expected_command": self.command,
            "startup_owners": [self.owner],
        }
        self.telemetry = {
            "pid": 404,
            "ready": True,
            "phase": "ready",
            "source_watermark": "synthetic-1",
            "archive_watermark": "synthetic-1",
            "updated_at": "2026-08-18T00:00:00Z",
        }
        self.now = datetime(2026, 8, 18, tzinfo=timezone.utc)

    def test_installed_effect_probe_is_content_free_and_removable(self) -> None:
        sessions = self.base / "会話 sessions"
        sessions.mkdir()
        probe = create_installed_effect_probe(sessions)
        path = Path(probe["path"])
        self.assertEqual(path.parent.name, "0000-memory-wuxian-install-effect")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["type"], "session_meta")
        self.assertNotIn("message", path.read_text(encoding="utf-8"))
        self.assertFalse(probe["contains_visible_messages"])
        self.assertTrue(watermark_reached(probe["watermark"], probe["watermark"]))
        remove_installed_effect_probe(probe)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_installed_effect_probe_advances_from_current_second(self) -> None:
        sessions = self.base / "same-second-sessions"
        sessions.mkdir()
        previous = datetime.now(timezone.utc).replace(microsecond=0)
        probe = create_installed_effect_probe(
            sessions,
            previous_watermark=previous.isoformat().replace("+00:00", "Z"),
        )
        try:
            observed = datetime.fromisoformat(probe["watermark"].replace("Z", "+00:00"))
            self.assertGreater(observed, previous)
        finally:
            remove_installed_effect_probe(probe)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exactly_one_owner_with_expected_identity_passes(self) -> None:
        owner = inspect_startup_owner(self.manifest)
        self.assertTrue(owner["ok"])
        effect = inspect_live_effect(
            self.telemetry,
            expected_command=self.command,
            generation="v2.16",
            pid_identity="required",
            process_inspector=lambda pid: {
                "running": pid == 404,
                "command": self.command,
            },
            now=self.now,
        )
        self.assertTrue(effect["ok"])
        self.assertTrue(effect["watermarks_converged"])

    def test_unique_command_argument_preserves_special_values_and_fails_closed(self) -> None:
        value = "-前导/日本語/￥/emoji-😀/" + "长" * 260 + "/archive root"
        command = ["collector executable", "--archive-root", value, "--flag"]
        self.assertEqual(
            _unique_command_argument(command, "--archive-root"),
            value,
        )
        self.assertIsNone(
            _unique_command_argument(
                command + ["--archive-root", "other"], "--archive-root"
            )
        )
        self.assertIsNone(
            _unique_command_argument(["collector", "--archive-root"], "--archive-root")
        )

    def test_owner_count_and_command_archive_root_fail_closed(self) -> None:
        duplicate = dict(self.manifest)
        duplicate["startup_owners"] = [self.owner, dict(self.owner)]
        result = inspect_startup_owner(duplicate)
        self.assertEqual(
            result["reason_codes"][0],
            {
                "code": "collector-startup-owner-count-invalid",
                "observed": 2,
                "expected": 1,
            },
        )

        wrong = dict(self.manifest)
        wrong["expected_command"] = [
            self.command[0],
            "--archive-root",
            str(self.archive / "other"),
        ]
        result = inspect_startup_owner(wrong)
        codes = {item["code"] for item in result["reason_codes"]}
        self.assertIn("collector-command-archive-root-mismatch", codes)
        self.assertIn("collector-owner-command-mismatch", codes)

    def test_readiness_freshness_and_watermark_convergence_are_required(self) -> None:
        telemetry = dict(
            self.telemetry,
            ready=False,
            phase="starting",
            source_watermark="event-2",
            archive_watermark="event-1",
            updated_at="2026-08-17T00:00:00Z",
        )
        result = inspect_live_effect(
            telemetry,
            expected_command=self.command,
            generation="v2.16",
            pid_identity="required",
            process_inspector=lambda _pid: {
                "running": True,
                "command": self.command,
            },
            now=self.now,
        )
        codes = {item["code"] for item in result["reason_codes"]}
        self.assertEqual(
            codes,
            {
                "collector-not-ready",
                "collector-telemetry-stale",
                "collector-watermark-not-converged",
            },
        )

    def test_pid_identity_unavailable_or_mismatched_fails(self) -> None:
        unavailable = inspect_live_effect(
            self.telemetry,
            expected_command=self.command,
            generation="v2.16",
            pid_identity="required",
            process_inspector=lambda _pid: {"running": True},
            now=self.now,
        )
        self.assertEqual(
            unavailable["reason_codes"],
            [{"code": "collector-live-identity-unavailable"}],
        )
        mismatched = inspect_live_effect(
            self.telemetry,
            expected_command=self.command,
            generation="v2.16",
            pid_identity="required",
            process_inspector=lambda _pid: {
                "running": True,
                "executable": str(self.base / "other.exe"),
            },
            now=self.now,
        )
        self.assertEqual(
            mismatched["reason_codes"],
            [{"code": "collector-live-executable-mismatch"}],
        )

    def test_verifier_reads_only_two_configured_json_files(self) -> None:
        lifecycle_path = self.base / "lifecycle.json"
        telemetry_path = self.base / "telemetry.json"
        lifecycle_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8"
        )
        telemetry_path.write_text(
            json.dumps(self.telemetry, ensure_ascii=False), encoding="utf-8"
        )
        result = verify_collector_lifecycle(
            lifecycle_path,
            telemetry_path,
            process_inspector=lambda _pid: {
                "running": True,
                "command": self.command,
            },
            now=self.now,
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["probe"])

    def test_explicit_synthetic_probe_handles_utf8_and_long_paths(self) -> None:
        helper = self.base / "probe helper.py"
        helper.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "from datetime import datetime, timezone",
                    "from pathlib import Path",
                    "archive, source, telemetry = map(Path, sys.argv[1:4])",
                    "payload = source.read_text(encoding='utf-8')",
                    "assert '日本語 中文 ￥ 😀 --leading' in payload",
                    "mark = 'synthetic-' + str(len(payload.encode('utf-8')))",
                    "data = {'ready': True, 'phase': 'ready', 'source_watermark': mark, 'archive_watermark': mark, 'updated_at': datetime.now(timezone.utc).isoformat()}",
                    "telemetry.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')",
                ]
            ),
            encoding="utf-8",
        )
        probe_parent = self.base / ("探针 " + "深" * 80)
        probe = {
            "command": [
                sys.executable,
                str(helper),
                "{archive_root}",
                "{source_path}",
                "{telemetry_path}",
            ],
            "payload": "日本語 中文 ￥ 😀 --leading\n",
            "timeout_seconds": 10,
        }
        result = run_isolated_watermark_probe(probe, probe_parent=probe_parent)
        self.assertTrue(result["ok"])
        self.assertTrue(result["watermark_advanced"])
        self.assertEqual(
            result["payload_bytes"], len(probe["payload"].encode("utf-8"))
        )
        self.assertEqual(list(probe_parent.iterdir()), [])

    def test_probe_rejects_escape_without_execution(self) -> None:
        result = run_isolated_watermark_probe(
            {
                "command": [sys.executable, "-c", "raise SystemExit(99)"],
                "source_relative": "../outside.jsonl",
            }
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["probe_executed"])
        self.assertEqual(
            result["reason_codes"],
            [{"code": "collector-probe-relative-path-invalid"}],
        )


if __name__ == "__main__":
    unittest.main()
