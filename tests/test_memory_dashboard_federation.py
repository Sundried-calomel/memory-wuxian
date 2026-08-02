import json
import io
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_cli import MemoryStore
from memory_dashboard import (
    DashboardSnapshotCache,
    dashboard_health,
    debt_status_projection,
    make_handler,
)


class MemoryDashboardFederationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SimpleNamespace(root=Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_dashboard_health_ignores_normal_concurrent_round_order(self):
        self.assertEqual(
            dashboard_health({"completed_rounds_out_of_order": [4, 7, 9]}),
            "ok",
        )
        self.assertEqual(
            dashboard_health(
                {
                    "completed_rounds_out_of_order": [4, 7, 9],
                    "integrity_issues": ["missing source"],
                }
            ),
            "attention",
        )

    def test_dashboard_reads_bounded_debt_projections_and_classifies_recovery(self):
        root = self.store.root
        maintenance = root / "maintenance/status-projection.json"
        maintenance.parent.mkdir(parents=True)
        maintenance.write_text(
            json.dumps(
                {
                    "format": "memory-wuxian-maintenance-projection-v1",
                    "semantic_debt": {
                        "pending_summary_jobs": 190,
                        "maintenance": {
                            "counts": {
                                "queued": 0,
                                "running": 1,
                                "retry": 2,
                                "semantic-ready": 187,
                                "completed": 0,
                                "quarantined": 0,
                            }
                        },
                    },
                    "mechanical_debt": {},
                    "backup_debt": {"present": True, "mutation_count": 1948},
                }
            ),
            encoding="utf-8",
        )
        coverage = root / "imports/codex/coverage-status.json"
        coverage.parent.mkdir(parents=True)
        coverage.write_text(
            json.dumps(
                {
                    "status": "catching-up",
                    "missing_cursor_rollouts": 1,
                    "incomplete_rollouts": 2,
                    "pending_bytes": 25,
                    "observed_bytes": 100,
                }
            ),
            encoding="utf-8",
        )

        projection = debt_status_projection(root)

        self.assertEqual(projection["debts"]["coverage_debt"]["count"], 3)
        self.assertEqual(projection["debts"]["coverage_debt"]["progress"], 75.0)
        self.assertEqual(projection["debts"]["semantic_debt"]["count"], 190)
        self.assertEqual(projection["debts"]["semantic_debt"]["in_progress"], 1)
        self.assertEqual(projection["debts"]["backup_debt"]["count"], 1948)
        self.assertEqual(dashboard_health({}, {"alerts": []}, projection), "catching-up")

    def test_dashboard_health_reserves_attention_and_error_for_real_failures(self):
        quarantined = {
            "debts": {
                "semantic_debt": {
                    "count": 1,
                    "state": "pending",
                    "quarantined": 1,
                }
            }
        }
        corrupt = {
            "debts": {
                "coverage_debt": {"count": 1, "state": "integrity-failure"}
            }
        }
        self.assertEqual(dashboard_health({}, {"alerts": []}, quarantined), "attention")
        self.assertEqual(dashboard_health({}, {"alerts": []}, corrupt), "error")

    def test_dashboard_exposes_runtime_blocked_semantic_debt(self):
        root = self.store.root
        maintenance = root / "maintenance/status-projection.json"
        maintenance.parent.mkdir(parents=True)
        maintenance.write_text(
            json.dumps(
                {
                    "semantic_debt": {
                        "pending_summary_jobs": 2,
                        "maintenance": {"counts": {"semantic-ready": 2}},
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "maintenance/supervisor-state.json").write_text(
            json.dumps(
                {
                    "result": {
                        "completed_jobs": 0,
                        "skipped": [
                            {
                                "reason": "runtime-unavailable",
                                "error": "Codex CLI executable is unavailable",
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        projection = debt_status_projection(root)
        semantic = projection["debts"]["semantic_debt"]
        self.assertEqual(semantic["state"], "blocked")
        self.assertIn("Codex CLI", semantic["last_error"])
        self.assertEqual(projection["health"], "attention")

    def test_live_cache_health_can_downgrade_after_debt_clears(self):
        cache = DashboardSnapshotCache(self.store)
        cache._payload = {"health": "attention", "archive_health": {}}
        cache._refreshing = True
        projection_path = self.store.root / "maintenance/status-projection.json"
        projection_path.parent.mkdir(parents=True)
        projection_path.write_text(
            json.dumps({"semantic_debt": {"pending_summary_jobs": 1}}),
            encoding="utf-8",
        )
        with patch("memory_dashboard.collector_telemetry", return_value={"alerts": []}):
            self.assertEqual(cache.get_fast()["health"], "catching-up")
            projection_path.write_text(
                json.dumps({"semantic_debt": {"pending_summary_jobs": 0}}),
                encoding="utf-8",
            )
            self.assertEqual(cache.get_fast()["health"], "ok")

    def test_fast_cache_does_not_wait_for_a_background_rebuild_lock(self):
        cache = DashboardSnapshotCache(self.store)
        cache._payload = {"health": "ok", "archive_health": {}}
        cache._refreshing = True
        cache._lock.acquire()
        completed = threading.Event()
        result = {}

        def read_fast():
            result.update(cache.get_fast())
            completed.set()

        worker = threading.Thread(target=read_fast)
        worker.start()
        try:
            self.assertTrue(completed.wait(0.5), "fast cache waited for rebuild lock")
            self.assertEqual(result["snapshot"]["persisted"], True)
        finally:
            cache._lock.release()
            worker.join(timeout=2)

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

    def test_federated_daily_sources_invalidate_archive_snapshot(self):
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
            config={},
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

        peer_root = root.parent / f"{root.name}-federation-cache/peers/mw-peer-node"
        peer_root.mkdir(parents=True)
        (peer_root / "raw-records.jsonl").write_text(
            '{"message_id":"peer-message-1"}\n', encoding="utf-8"
        )
        (peer_root / "replica-state.json").write_text(
            '{"last_sync_at":"2026-08-01T10:00:00+09:00"}\n',
            encoding="utf-8",
        )

        self.assertNotEqual(cache.source_signature(), before)

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
        environment_cloud_status = {
            **cloud_status,
            "stream_id": "environment-v1",
            "peers": [],
        }
        environment_cloud_transport = Mock()
        environment_cloud_transport.status.return_value = environment_cloud_status
        project_evidence_status = {
            **cloud_status,
            "stream_id": "project-evidence-v1",
            "peers": [],
            "inventory": {"status": "ok", "local_packages": 0, "local_event_sequence": 0},
        }
        project_evidence_transport = Mock()
        project_evidence_transport.status.return_value = project_evidence_status
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.CloudFolderTransport",
                return_value=cloud_transport,
            ) as cloud_factory,
            patch(
                "memory_dashboard.environment_cloud_transport",
                return_value=environment_cloud_transport,
            ),
            patch(
                "memory_dashboard.project_evidence_cloud_transport",
                return_value=project_evidence_transport,
            ),
            patch(
                "memory_dashboard.ProjectEvidenceExchangeManager"
            ) as evidence_manager,
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
            evidence_manager.return_value.status.return_value = {
                "status": "ok",
                "local_packages": 0,
                "local_event_sequence": 0,
            }
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
                    "streams": {
                        "archive-v1": {
                            "stream_id": "archive-v1",
                            **cloud_status,
                        },
                        "environment-v1": environment_cloud_status,
                        "project-evidence-v1": project_evidence_status,
                    },
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
        evidence_manager.return_value.status.assert_called_once_with()

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
        environment_transport = Mock()
        environment_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "environment-v1",
        }
        project_evidence_transport = Mock()
        project_evidence_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "project-evidence-v1",
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
            patch(
                "memory_dashboard.environment_cloud_transport",
                return_value=environment_transport,
            ),
            patch(
                "memory_dashboard.project_evidence_cloud_transport",
                return_value=project_evidence_transport,
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
        environment_transport.set_enabled.assert_called_once_with(True)
        project_evidence_transport.set_enabled.assert_called_once_with(True)

    def test_cloud_sync_serializes_concurrent_dashboard_requests(self):
        manager = Mock()
        manager.status.return_value = {
            "enabled": True,
            "devices": [],
            "recent_sync": [],
        }
        active = 0
        maximum_active = 0
        active_lock = threading.Lock()

        def synchronized_result(*, force=False):
            self.assertTrue(force)
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.1)
            with active_lock:
                active -= 1
            return {"status": "ok"}

        transport = Mock()
        transport.status.return_value = {"configured": True, "enabled": True}
        transport.sync.side_effect = synchronized_result
        environment_transport = Mock()
        environment_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "environment-v1",
        }
        environment_transport.sync.return_value = {"status": "ok"}
        project_evidence_transport = Mock()
        project_evidence_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "project-evidence-v1",
        }
        project_evidence_transport.sync.return_value = {"status": "ok"}
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.CloudFolderTransport",
                return_value=transport,
            ),
            patch(
                "memory_dashboard.environment_cloud_transport",
                return_value=environment_transport,
            ),
            patch(
                "memory_dashboard.project_evidence_cloud_transport",
                return_value=project_evidence_transport,
            ),
            patch(
                "memory_dashboard.cloud_scheduler_status",
                return_value={"installed": True, "running": True},
            ),
        ):
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(self.store)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            barrier = threading.Barrier(3)
            responses = []
            request_errors = []

            def post_sync():
                try:
                    barrier.wait()
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/cloud",
                        data=json.dumps({"action": "sync"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=15) as response:
                        responses.append((response.status, json.load(response)))
                except Exception as error:
                    body = None
                    if hasattr(error, "read"):
                        try:
                            body = error.read().decode("utf-8", errors="replace")
                        except Exception:
                            body = None
                    request_errors.append((repr(error), body))

            callers = [threading.Thread(target=post_sync) for _ in range(2)]
            for caller in callers:
                caller.start()
            barrier.wait()
            for caller in callers:
                caller.join(timeout=15)
                self.assertFalse(caller.is_alive())
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(request_errors, [])
        self.assertEqual(len(responses), 2)
        self.assertEqual(maximum_active, 1)
        self.assertTrue(all(status == 200 for status, _ in responses))
        self.assertEqual(transport.sync.call_count, 2)
        self.assertEqual(project_evidence_transport.sync.call_count, 2)

    def test_cloud_sync_reports_partial_success_per_stream(self):
        manager = Mock()
        manager.status.return_value = {
            "enabled": True,
            "devices": [],
            "recent_sync": [],
        }
        transport = Mock()
        transport.status.return_value = {"configured": True, "enabled": True}
        transport.sync.return_value = {"status": "ok", "published": ["bundle"]}
        environment_transport = Mock()
        environment_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "environment-v1",
        }
        environment_transport.sync.side_effect = RuntimeError(
            "environment unavailable"
        )
        project_evidence_transport = Mock()
        project_evidence_transport.status.return_value = {
            "configured": True,
            "enabled": True,
            "stream_id": "project-evidence-v1",
        }
        project_evidence_transport.sync.return_value = {"status": "ok"}
        with (
            patch("memory_dashboard.FederationManager", return_value=manager),
            patch(
                "memory_dashboard.CloudFolderTransport",
                return_value=transport,
            ),
            patch(
                "memory_dashboard.environment_cloud_transport",
                return_value=environment_transport,
            ),
            patch(
                "memory_dashboard.project_evidence_cloud_transport",
                return_value=project_evidence_transport,
            ),
            patch(
                "memory_dashboard.cloud_scheduler_status",
                return_value={"installed": True, "running": True},
            ),
        ):
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(self.store)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/cloud",
                    data=json.dumps({"action": "sync"}).encode("utf-8"),
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

        self.assertEqual(payload["result"]["status"], "partial")
        self.assertEqual(
            payload["result"]["streams"]["archive"],
            {
                "status": "ok",
                "result": {"status": "ok", "published": ["bundle"]},
            },
        )
        self.assertEqual(
            payload["result"]["streams"]["environment"],
            {"status": "error", "error": "environment unavailable"},
        )
        self.assertEqual(
            payload["result"]["streams"]["project_evidence"],
            {"status": "ok", "result": {"status": "ok"}},
        )

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

        for debt_contract in (
            'id="debt-title"',
            'id="debt-grid"',
            "const debtCopy=",
            "coverage_debt:q.coverage",
            "mechanical_debt:q.mechanical",
            "semantic_debt:q.semantic",
            "backup_debt:q.backup",
            "'catching-up':q.catchingUp",
        ):
            self.assertIn(debt_contract, html)

        for preserved_contract in (
            "memory-wuxian-dashboard-settings-v1",
            "memory-wuxian-achievements-v1",
            "settings.achievementsEnabled",
            "settings.animationsEnabled",
            "settings.toastsEnabled",
            "settings.compactMode",
            "settings.cloudFailureAlertsEnabled",
            "memory-wuxian-language",
            "memory-wuxian-daily-mode",
            'data-daily-mode="messages"',
            'data-daily-mode="reported_tokens"',
            "complete_token_coverage",
            "renderDailyDrilldown",
            "全设备",
            "All devices",
            "全デバイス",
        ):
            self.assertIn(preserved_contract, html)


if __name__ == "__main__":
    unittest.main()
