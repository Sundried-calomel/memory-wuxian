from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from runtime_effect_gate import check_runtime_effects


class RuntimeEffectGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name) / "归档 root"
        self.lifecycle = self.root / "imports" / "codex" / "collector-lifecycle.json"
        self.telemetry = self.root / "imports" / "codex" / "collector-telemetry.json"
        self.lifecycle.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry.parent.mkdir(parents=True, exist_ok=True)
        self.command = [
            str(Path(self.temporary.name) / "collector.exe"),
            "--archive-root",
            str(self.root),
        ]
        self.manifest = {
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": "generation-2.16",
            "archive_root": str(self.root),
            "expected_command": self.command,
            "startup_owners": [
                {
                    "owner_id": "task:MemoryWuxianCollector",
                    "kind": "windows-task",
                    "generation": "generation-2.16",
                    "archive_root": str(self.root),
                    "command": self.command,
                    "pid_identity": "required",
                }
            ],
        }
        self.status = {
            "format_version": 2,
            "pid": 731,
            "phase": "ready",
            "ready": True,
            "source_watermark": "event-41",
            "archive_watermark": "event-41",
            "updated_at": "2026-08-18T00:00:00+00:00",
        }
        self.now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        self._write_state()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_state(self) -> None:
        self.lifecycle.write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8"
        )
        self.telemetry.write_text(
            json.dumps(self.status, ensure_ascii=False), encoding="utf-8"
        )

    def _inspect(self, _pid: int) -> dict[str, object]:
        return {"running": True, "command": self.command}

    def test_default_paths_prove_configured_and_live_effect(self) -> None:
        result = check_runtime_effects(
            self.root, now=self.now, process_inspector=self._inspect
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["format"], "memory-wuxian-runtime-effect-v2")
        self.assertEqual(result["reason_codes"], [])

    def test_missing_manifest_fails_closed_with_structured_code(self) -> None:
        self.lifecycle.unlink()
        result = check_runtime_effects(
            self.root, now=self.now, process_inspector=self._inspect
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason_codes"], [{"code": "collector-lifecycle-manifest-missing"}]
        )

    def test_duplicate_startup_owner_fails_closed(self) -> None:
        self.manifest["startup_owners"].append(
            dict(self.manifest["startup_owners"][0], owner_id="duplicate")
        )
        self._write_state()
        result = check_runtime_effects(
            self.root, now=self.now, process_inspector=self._inspect
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason_codes"][0]["code"],
            "collector-startup-owner-count-invalid",
        )
        self.assertEqual(result["reason_codes"][0]["observed"], 2)

    def test_command_archive_generation_and_pid_identity_are_checked(self) -> None:
        owner = self.manifest["startup_owners"][0]
        owner["generation"] = "old"
        owner["archive_root"] = str(self.root / "wrong")
        owner["command"] = [self.command[0], "--archive-root", str(self.root / "wrong")]
        self._write_state()
        result = check_runtime_effects(
            self.root, now=self.now, process_inspector=self._inspect
        )
        codes = {item["code"] for item in result["reason_codes"]}
        self.assertIn("collector-owner-generation-mismatch", codes)
        self.assertIn("collector-owner-archive-root-mismatch", codes)
        self.assertIn("collector-owner-command-mismatch", codes)

        owner.update(
            {
                "generation": self.manifest["generation"],
                "archive_root": str(self.root),
                "command": self.command,
            }
        )
        self._write_state()
        result = check_runtime_effects(
            self.root,
            now=self.now,
            process_inspector=lambda _pid: {
                "running": True,
                "command": ["wrong.exe"],
            },
        )
        self.assertEqual(
            result["reason_codes"], [{"code": "collector-live-command-mismatch"}]
        )

    def test_gate_never_recursively_scans_archive(self) -> None:
        raw = self.root / "raw" / "deep"
        raw.mkdir(parents=True)
        (raw / "event.jsonl").write_text('{"sequence": 999}\n', encoding="utf-8")
        with mock.patch.object(Path, "rglob", side_effect=AssertionError("archive scan")):
            result = check_runtime_effects(
                self.root, now=self.now, process_inspector=self._inspect
            )
        self.assertTrue(result["ok"])

    def test_optional_shortcut_remains_bounded_and_fail_closed(self) -> None:
        shortcut = Path(self.temporary.name) / "Memory Wuxian.lnk"
        result = check_runtime_effects(
            self.root,
            now=self.now,
            process_inspector=self._inspect,
            windows_shortcut=shortcut,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason_codes"], [{"code": "collector-windows-shortcut-missing"}]
        )


if __name__ == "__main__":
    unittest.main()
