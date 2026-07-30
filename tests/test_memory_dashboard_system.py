from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import memory_dashboard
from memory_dashboard import make_handler


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MemoryDashboardSystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.config = self.root / "dashboard-config.yaml"
        self.config.write_text(
            "memory:\n  timezone: UTC\n",
            encoding="utf-8",
        )
        self.store = SimpleNamespace(root=self.archive)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_system_api_is_read_only_and_uses_supplied_config(self) -> None:
        before = file_hashes(self.root)
        handler_class = make_handler(self.store, self.config)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/system"
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        handler.do_GET()

        payload = json.loads(handler.wfile.getvalue())
        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_any_call("Cache-Control", "no-store")

        self.assertEqual(file_hashes(self.root), before)
        self.assertFalse(self.archive.exists())
        configuration = payload["configuration"]
        self.assertRegex(
            configuration["effective_configuration_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(configuration["root_resolution"]["layer"], "explicit-root")
        self.assertEqual(configuration["root_resolution"]["source"], "--root")
        self.assertEqual(
            configuration["value_sources"]["/memory/timezone"],
            {
                "layer": "configuration-source",
                "source": str(self.config.resolve()),
            },
        )
        capabilities = payload["capabilities"]
        self.assertEqual(capabilities["product"]["id"], "memory-wuxian")
        self.assertEqual(capabilities["platform"], memory_dashboard.local_platform_name())
        self.assertEqual(
            [item["name"] for item in capabilities["protocols"]],
            ["archive-v1", "configuration-v1", "environment-v1"],
        )
        self.assertEqual(capabilities["interfaces"], [])

    def test_system_endpoint_has_no_post_route(self) -> None:
        handler_class = make_handler(self.store, self.config)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/system"
        handler.send_error = Mock()

        handler.do_POST()

        handler.send_error.assert_called_once_with(404)

    def test_make_handler_remains_compatible_without_config_argument(self) -> None:
        handler = make_handler(self.store)
        self.assertTrue(issubclass(handler, memory_dashboard.BaseHTTPRequestHandler))

    def test_main_passes_actual_config_path_to_handler(self) -> None:
        server = Mock(server_port=8765)
        store = Mock()
        handler = object()
        arguments = [
            "memory_dashboard.py",
            "--root",
            str(self.archive),
            "--config",
            str(self.config),
            "--no-browser",
        ]
        with (
            patch.object(sys, "argv", arguments),
            patch("memory_dashboard.MemoryStore", return_value=store),
            patch("memory_dashboard.make_handler", return_value=handler) as factory,
            patch("memory_dashboard.ThreadingHTTPServer", return_value=server),
        ):
            self.assertEqual(memory_dashboard.main(), 0)

        factory.assert_called_once_with(store, self.config.resolve())
        server.serve_forever.assert_called_once_with()

    def test_frontend_system_view_is_multilingual_and_read_only(self) -> None:
        html = (SKILL_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        for marker in (
            'id="system-view-tab"',
            'id="system-view"',
            'id="system-overview"',
            'id="system-protocols"',
            'id="system-interfaces"',
            'id="system-value-sources"',
            "fetch('/api/system'",
            "effectiveConfigurationSha",
            "rootSourceLayer",
            "supportedProtocols",
            "supportedInterfaces",
            "valueSource",
            "有效配置 SHA",
            "Effective config SHA",
            "有効設定 SHA",
        ):
            self.assertIn(marker, html)
        self.assertNotIn("/api/system',{method:'POST'", html)
        self.assertNotIn("privacyScope", html)
        self.assertIn("@media(max-width:850px)", html)
        self.assertIn("@media(max-width:480px)", html)


if __name__ == "__main__":
    unittest.main()
