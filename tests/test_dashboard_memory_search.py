from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_dashboard import make_handler


class DashboardMemorySearchTest(unittest.TestCase):
    def test_keyword_search_returns_readable_verified_raw_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = base / "config.yaml"
            config.write_text(
                "memory:\n  root_directory: ./memory\n"
                "summaries:\n  maximum_summary_depth: 4\n",
                encoding="utf-8",
            )
            store = MemoryStore(base / "memory", load_simple_yaml(config))
            store.init()
            store.append_message(
                "user",
                "后台刷新导致输入窗口失去焦点",
                "2026-07-30T00:00:00+09:00",
                "codex:test",
                "message-1",
                None,
                False,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = (
                    f"http://127.0.0.1:{server.server_port}/api/memory-search"
                    f"?q={quote('窗口失去焦点')}&mode=keyword&limit=5"
                )
                with urlopen(url, timeout=5) as response:
                    payload = json.load(response)
                self.assertTrue(payload["verified_against_raw"])
                self.assertEqual("keyword", payload["mode"])
                self.assertEqual("message-1", payload["results"][0]["message_id"])
                self.assertIn("窗口失去焦点", payload["results"][0]["text"])
                self.assertIsNotNone(payload["results"][0]["raw_line_start"])
                self.assertRegex(payload["results"][0]["record_sha256"], r"^[0-9a-f]{64}$")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
