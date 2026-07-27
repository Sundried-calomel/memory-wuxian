from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

from memory_cli import MemoryStore, load_simple_yaml
from memory_dashboard import make_handler


class DashboardRealtimeTest(unittest.TestCase):
    def test_sse_emits_status_after_archive_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "memory"
            config_path = Path(__file__).resolve().parent.parent / "config.yaml"
            store = MemoryStore(root, load_simple_yaml(config_path))
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=8
            )
            try:
                connection.request("GET", "/api/events")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Type"),
                    "text/event-stream; charset=utf-8",
                )
                event_path = root / "dashboard/events.jsonl"
                event_path.parent.mkdir(parents=True, exist_ok=True)
                event_path.write_text(
                    json.dumps({"event_id": 1, "kind": "archive-updated"}) + "\n",
                    encoding="utf-8",
                )
                lines = []
                while len(lines) < 20:
                    line = response.readline().decode("utf-8").strip()
                    lines.append(line)
                    if line == "event: status":
                        break
                self.assertIn("event: status", lines)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

