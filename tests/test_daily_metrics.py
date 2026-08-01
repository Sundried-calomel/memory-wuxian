import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from daily_metrics import build_federated_daily_metrics  # noqa: E402
from memory_cli import MemoryStore  # noqa: E402
from memory_federation import FederationManager, atomic_write_json, atomic_write_jsonl  # noqa: E402


def usage(total):
    return {
        "input_tokens": total,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": total,
    }


class DailyMetricsTest(unittest.TestCase):
    def test_nested_totals_include_each_trusted_device_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "archive"
            store = MemoryStore(
                root,
                {"federation": {"replica_directory": str(base / "replicas")}},
            )
            root.mkdir()
            manager = FederationManager(store)
            manager.init_layout()
            manager.init_node("Windows", "node-windows")
            manager.add_peer("node-mac", display_name="Mac")
            peer_root = manager.replica_peer_root("node-mac")
            atomic_write_jsonl(
                peer_root / "raw-records.jsonl",
                [
                    {
                        "message_id": "mac-message",
                        "timestamp": "2026-07-31T16:30:00Z",
                        "text": "remote",
                    }
                ],
            )
            atomic_write_json(
                peer_root / "replica-state.json",
                {
                    "format_version": 1,
                    "origin_node_id": "node-mac",
                    "last_event_sequence": 1,
                    "last_sync_at": "2020-01-01T00:00:00Z",
                    "artifact_hashes": {},
                },
            )
            remote_ledger = {
                "format_version": 2,
                "measurement": "codex-reported-model-usage",
                "session_id": "mac-session",
                "token_event_count": 1,
                "reported_usage": usage(200),
                "daily_usage": {"2026-08-01": usage(200)},
                "updated_at": "2026-08-01T09:00:00+09:00",
            }
            token_dir = peer_root / "token-usage"
            token_dir.mkdir(parents=True)
            (token_dir / "one.json").write_text(json.dumps(remote_ledger), encoding="utf-8")
            (token_dir / "duplicate.json").write_text(json.dumps(remote_ledger), encoding="utf-8")

            local_dir = root / "imports" / "codex" / "token-usage"
            local_dir.mkdir(parents=True)
            local_ledger = {
                "format_version": 2,
                "measurement": "codex-reported-model-usage",
                "session_id": "windows-session",
                "token_event_count": 1,
                "reported_usage": usage(100),
                "daily_usage": {"2026-08-01": usage(100)},
                "updated_at": "2026-08-01T09:00:00+09:00",
            }
            (local_dir / "windows.json").write_text(json.dumps(local_ledger), encoding="utf-8")
            local_records = [
                {
                    "message_id": "windows-message",
                    "timestamp": "2026-08-01T00:00:00+09:00",
                    "text": "local",
                }
            ]

            result = build_federated_daily_metrics(store, local_records)
            day = result["daily"][0]
            self.assertEqual(day["local"]["messages"], 1)
            self.assertEqual(day["all_devices"]["messages"], 2)
            self.assertEqual(day["local"]["reported_tokens"], 100)
            self.assertEqual(day["all_devices"]["reported_tokens"], 300)
            self.assertEqual(result["devices_included"], 2)
            self.assertEqual(result["token_telemetry_devices"], 2)
            self.assertEqual(result["stale_devices"], ["node-mac"])

            manager.revoke_peer("node-mac")
            revoked = build_federated_daily_metrics(store, local_records)
            self.assertEqual(revoked["devices_included"], 1)
            self.assertEqual(revoked["daily"][0]["all_devices"]["messages"], 1)


if __name__ == "__main__":
    unittest.main()
