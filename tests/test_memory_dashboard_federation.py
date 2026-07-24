import json
import io
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_cli import MemoryStore
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
        cloud_status = {
            "enabled": True,
            "configured": True,
            "encrypted": True,
            "identity_ready": True,
            "schedule": {
                "last_attempt_at": "2026-07-23T10:01:00+09:00",
                "pending_since": None,
            },
            "peers": [
                {
                    "node_id": "mw-peer-node",
                    "display_name": "Work PC",
                    "ssh_transport": True,
                    "cloud_ready": True,
                    "cloud_fingerprint": "1234567890abcdef",
                    "acknowledged": {"last_event_sequence": 40},
                    "outstanding": {"to_event_sequence": 42},
                    "last_sync_at": "2026-07-23T10:00:00+09:00",
                }
            ],
        }
        manager = Mock()
        manager.status.return_value = federation_status
        cloud_transport = Mock()
        cloud_transport.status.return_value = cloud_status
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.CloudFolderTransport",
                return_value=cloud_transport,
            ) as cloud_factory,
            patch(
                "memory_dashboard.cloud_scheduler_status",
                return_value={
                    "platform": "macos",
                    "installed": True,
                    "running": True,
                },
            ),
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

        self.assertEqual(
            payload,
            {
                **federation_status,
                "cloud": {
                    **cloud_status,
                    "scheduler": {
                        "platform": "macos",
                        "installed": True,
                        "running": True,
                    },
                },
            },
        )
        manager.status.assert_called_once_with()
        cloud_factory.assert_called_once_with(manager)
        cloud_transport.status.assert_called_once_with()

    def test_devices_api_does_not_create_federation_cloud_or_snapshot_files(self):
        root = self.store.root
        store = MemoryStore(root, {"federation": {}})
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/api/devices",
                timeout=5,
            ) as response:
                payload = json.load(response)
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["cloud"]["configured"])
        self.assertFalse((root / "federation").exists())
        self.assertFalse((root / "dashboard/status-snapshot.json").exists())

    def test_cloud_api_enables_transport_and_scheduler(self):
        manager = Mock()
        manager.status.return_value = {
            "enabled": True,
            "devices": [],
            "recent_sync": [],
        }
        transport = Mock()
        transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "exchange_root": "/OneDrive/MemoryWuxianExchange",
        }
        scheduler = {
            "platform": "macos",
            "installed": True,
            "running": True,
        }
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.CloudFolderTransport",
                return_value=transport,
            ),
            patch("memory_dashboard.set_cloud_scheduler", return_value=scheduler),
            patch("memory_dashboard.cloud_scheduler_status", return_value=scheduler),
        ):
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(self.store)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/cloud",
                    data=json.dumps({"action": "enable"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    payload = json.load(response)
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(payload["result"]["status"], "enabled")
        transport.set_enabled.assert_called_once_with(True)

    def test_cloud_api_rejects_cross_origin_requests(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/cloud",
                data=json.dumps({"action": "disable"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://example.com",
                },
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
            raised.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_chatgpt_import_api_streams_into_existing_importer(self):
        root = self.store.root
        store = MemoryStore(root, {"memory": {"backup_after_mutation": False}})
        conversation = {
            "id": "dashboard-chat-1",
            "title": "Dashboard import",
            "current_node": "assistant",
            "mapping": {
                "user": {
                    "id": "user",
                    "parent": None,
                    "message": {
                        "id": "dashboard-user",
                        "author": {"role": "user"},
                        "create_time": 1001,
                        "content": {"content_type": "text", "parts": ["Imported locally"]},
                    },
                },
                "assistant": {
                    "id": "assistant",
                    "parent": "user",
                    "message": {
                        "id": "dashboard-assistant",
                        "author": {"role": "assistant"},
                        "create_time": 1002,
                        "content": {"content_type": "text", "parts": ["Stored locally"]},
                    },
                },
            },
        }
        export = io.BytesIO()
        with zipfile.ZipFile(export, "w") as archive:
            archive.writestr(
                "export/conversations.json",
                json.dumps([conversation], ensure_ascii=False),
            )
        payload_bytes = export.getvalue()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/import-chatgpt",
                data=payload_bytes,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Filename": "chatgpt-export.zip",
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                first = json.load(response)["result"]
            with urlopen(request, timeout=5) as response:
                second = json.load(response)["result"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(first["imported_messages"], 2)
        self.assertEqual(second["imported_messages"], 0)
        self.assertEqual(second["duplicate_messages"], 2)
        self.assertEqual(first["source"], "chatgpt-export.zip")
        records = store.read_all_raw()
        self.assertEqual(
            [record["text"] for record in records],
            ["Imported locally", "Stored locally"],
        )

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
            "d.cloud",
            "cloudPeer.ssh_transport",
            "cloudPeer.cloud_ready",
            "cloudPeer.cloud_fingerprint",
            "cloudPeer.last_sync_at",
            "cloudPeer.acknowledged",
            "cloudPeer.outstanding",
            "cloudFailureAlertsEnabled",
            "data-cloud-toggle",
            "data-cloud-action",
            "fetch('/api/cloud'",
            "云同步设置已更新",
            "Cloud sync settings updated",
            "クラウド同期設定を更新しました",
            "云同步失败提醒",
            "Cloud sync failure alerts",
            "クラウド同期失敗通知",
            "data-chatgpt-import",
            "data-chatgpt-file",
            "fetch('/api/import-chatgpt'",
            "尚未使用真实用户导出包验证",
            "no real user export has been tested yet",
            "実際のユーザー書き出しでは未検証です",
            "加密并签名",
            "Encrypted and signed",
            "暗号化・署名済み",
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
            "settings.cloudFailureAlertsEnabled",
            "memory-wuxian-language",
        ):
            self.assertIn(preserved_contract, html)


if __name__ == "__main__":
    unittest.main()
