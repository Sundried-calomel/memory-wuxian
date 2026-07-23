import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import urlopen


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_dashboard import DashboardSnapshotCache, make_handler


class MemoryDashboardFederationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SimpleNamespace(root=Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_federation_activity_does_not_invalidate_archive_snapshot(self):
        root = self.store.root
        directories = {
            name: root / name
            for name in ("raw", "conversations", "summaries", "indexes", "pending", "retrieval")
        }
        for directory in directories.values():
            directory.mkdir(parents=True)
        state_path = root / "state.json"
        state_path.write_text("{}\n", encoding="utf-8")
        store = SimpleNamespace(
            root=root,
            state_path=state_path,
            raw_dir=directories["raw"],
            conversation_dir=directories["conversations"],
            summaries_dir=directories["summaries"],
            index_dir=directories["indexes"],
            pending_dir=directories["pending"],
            retrieval_dir=directories["retrieval"],
        )
        cache = DashboardSnapshotCache(store)
        before = cache.source_signature()

        sync_log = root / "federation/sync-log.jsonl"
        sync_log.parent.mkdir(parents=True)
        sync_log.write_text(
            '{"event":"sync-started","node_id":"mw-peer-node"}\n',
            encoding="utf-8",
        )

        self.assertEqual(cache.source_signature(), before)

    def test_devices_api_is_independent_from_archive_snapshot(self):
        federation_status = {
            "enabled": True,
            "protocol_version": 1,
            "node": {
                "node_id": "mw-local-node",
                "display_name": "Local Mac",
            },
            "replica_root": "/tmp/replicas",
            "devices": [
                {
                    "node_id": "mw-peer-node",
                    "display_name": "Work PC",
                    "trusted": True,
                    "transport": "ssh",
                    "last_event_sequence": 42,
                    "last_sync_at": "2026-07-23T10:00:00+09:00",
                    "last_bundle_id": "bundle-42",
                    "replica_bytes": 4096,
                }
            ],
            "recent_sync": [
                {
                    "timestamp": "2026-07-23T10:00:00+09:00",
                    "event": "sync-completed",
                    "node_id": "mw-peer-node",
                    "to_event_sequence": 42,
                }
            ],
        }
        manager = Mock()
        manager.status.return_value = federation_status
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.DashboardSnapshotCache.get",
                side_effect=AssertionError("devices API must not build the archive snapshot"),
            ),
        ):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/devices",
                    timeout=5,
                ) as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(payload, federation_status)
        manager.status.assert_called_once_with()

    def test_dashboard_html_keeps_existing_features_and_adds_federation_views(self):
        html = (SKILL_ROOT / "dashboard/index.html").read_text(encoding="utf-8")

        for contract in (
            'id="archive-view-tab"',
            'id="devices-view-tab"',
            'id="archive-view"',
            'id="devices-view"',
            "fetch('/api/devices'",
            "device.trusted",
            "device.transport",
            "device.last_sync_at",
            "device.last_event_sequence",
            "device.replica_bytes",
            "d.recent_sync",
            "尚未初始化本机联邦节点",
            "The local federation node is not initialized",
            "ローカル連携ノードは未初期化です",
        ):
            self.assertIn(contract, html)

        for preserved_contract in (
            "memory-wuxian-dashboard-settings-v1",
            "memory-wuxian-achievements-v1",
            "settings.achievementsEnabled",
            "settings.animationsEnabled",
            "settings.toastsEnabled",
            "settings.compactMode",
            "memory-wuxian-language",
        ):
            self.assertIn(preserved_contract, html)


if __name__ == "__main__":
    unittest.main()
