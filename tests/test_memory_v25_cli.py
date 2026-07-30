from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_cli  # noqa: E402
import memory_environment_capabilities  # noqa: E402


class MemoryV25CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.config = self.base / "config.yaml"
        self.config.write_text("{}\n", encoding="utf-8")
        self.archive = self.base / "must-not-exist"

    def invoke(self, *arguments):
        stdout = io.StringIO()
        with (
            mock.patch.object(
                memory_cli,
                "MemoryStore",
                side_effect=AssertionError("MemoryStore must not be constructed"),
            ),
            mock.patch.object(
                memory_cli,
                "resolve_config",
                side_effect=AssertionError("legacy config loading must be bypassed"),
            ),
            mock.patch.object(
                memory_cli,
                "exclusive_lock",
                side_effect=AssertionError("archive locks must not be used"),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            return_code = memory_cli.main(
                [
                    "--root",
                    str(self.archive),
                    "--config",
                    str(self.config),
                    *arguments,
                ]
            )
        self.assertEqual(0, return_code)
        self.assertFalse(self.archive.exists())
        return json.loads(stdout.getvalue())

    def test_configuration_compile_and_explain_use_configuration_module(self):
        with mock.patch.object(
            memory_cli.memory_configuration,
            "compile_configuration",
            wraps=memory_cli.memory_configuration.compile_configuration,
        ) as compile_configuration:
            compiled = self.invoke("configuration-compile")
            explained = self.invoke("configuration-explain")

        self.assertEqual(
            "memory-wuxian-effective-configuration-v1",
            compiled["contract_id"],
        )
        self.assertEqual(
            {
                "effective_configuration_sha256",
                "root_resolution",
                "value_sources",
            },
            set(explained),
        )
        self.assertEqual(2, compile_configuration.call_count)
        for call in compile_configuration.call_args_list:
            self.assertEqual(self.config, call.args[0])
            self.assertEqual(str(self.archive), call.kwargs["root_argument"])

    def test_capability_status_builds_local_offer_without_environment_state(self):
        with mock.patch.object(
            memory_cli.memory_environment_capabilities,
            "local_device_capability_offer",
            wraps=memory_environment_capabilities.local_device_capability_offer,
        ) as local_offer:
            result = self.invoke("environment-capability-status")

        self.assertEqual("unknown-legacy", result["status"])
        self.assertIsNone(result["compatible"])
        self.assertFalse(result["blocks_existing_sync"])
        self.assertFalse(any(result["authorization"].values()))
        local_offer.assert_called_once()

    def test_capability_status_negotiates_explicit_peer_offer(self):
        project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version_match = re.search(
            r'^version\s*=\s*"([^"]+)"',
            project_text,
            re.MULTILINE,
        )
        self.assertIsNotNone(version_match)
        product_version = version_match.group(1)
        peer_offer = memory_environment_capabilities.local_device_capability_offer(
            product_version,
            memory_cli.local_platform_name(),
            memory_cli.local_runtime_versions([])["python"],
        )
        peer_path = self.base / "peer-offer.json"
        peer_path.write_text(
            json.dumps(peer_offer, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        before = {
            path.relative_to(self.base).as_posix(): path.read_bytes()
            for path in self.base.rglob("*")
            if path.is_file()
        }

        result = self.invoke(
            "environment-capability-status",
            "--peer-offer",
            str(peer_path),
        )

        after = {
            path.relative_to(self.base).as_posix(): path.read_bytes()
            for path in self.base.rglob("*")
            if path.is_file()
        }
        self.assertNotEqual("unknown-legacy", result["status"])
        self.assertIsNotNone(result["compatible"])
        self.assertIsNotNone(result["remote_offer_sha256"])
        self.assertFalse(any(result["authorization"].values()))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
