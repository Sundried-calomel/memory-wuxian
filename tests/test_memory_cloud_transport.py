import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import CloudFolderTransport
from memory_federation import FederationManager, read_json


CLI = SKILL_ROOT / "scripts" / "memory_cli.py"


class FakeCrypto:
    """Authenticated test envelope; production cryptography is tested in Rust."""

    def __init__(self, identities):
        self.identities = {
            str(Path(path).resolve()): dict(identity)
            for path, identity in identities.items()
        }

    def show_identity(self, identity_private_path):
        return dict(self.identities[str(Path(identity_private_path).resolve())])

    def init_identity(self, identity_private_path, node_id):
        return self.show_identity(identity_private_path)

    def seal(
        self,
        source,
        destination,
        identity_private_path,
        recipients,
        kind,
        origin_node_id,
        target_node_id,
    ):
        sender = self.show_identity(identity_private_path)
        payload = Path(source).read_bytes()
        metadata = {
            "kind": kind,
            "origin_node_id": origin_node_id,
            "target_node_id": target_node_id,
        }
        signed = (
            sender["signing_public_key"].encode("utf-8")
            + b"\0"
            + json.dumps(metadata, sort_keys=True).encode("utf-8")
            + b"\0"
            + payload
        )
        envelope = {
            "sender": sender,
            "recipients": sorted(set(recipients)),
            **metadata,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signature": hashlib.sha256(signed).hexdigest(),
        }
        Path(destination).write_text(
            json.dumps(envelope, sort_keys=True), encoding="utf-8"
        )
        return {"status": "sealed"}

    def open(
        self,
        source,
        destination,
        identity_private_path,
        signing_public_key,
        kind,
        origin_node_id,
        target_node_id,
    ):
        envelope = json.loads(Path(source).read_text(encoding="utf-8"))
        local = self.show_identity(identity_private_path)
        if local["encryption_public_key"] not in envelope["recipients"]:
            raise ValueError("Envelope is not encrypted for this node")
        if envelope["kind"] != kind:
            raise ValueError("Envelope kind is invalid")
        if envelope["origin_node_id"] != origin_node_id:
            raise ValueError("Envelope origin is invalid")
        if envelope["target_node_id"] != target_node_id:
            raise ValueError("Envelope target is invalid")
        if envelope["sender"]["signing_public_key"] != signing_public_key:
            raise ValueError("Envelope signing key is invalid")
        payload = base64.b64decode(envelope["payload"], validate=True)
        sender = envelope["sender"]
        metadata = {
            "kind": envelope["kind"],
            "origin_node_id": envelope["origin_node_id"],
            "target_node_id": envelope["target_node_id"],
        }
        signed = (
            sender["signing_public_key"].encode("utf-8")
            + b"\0"
            + json.dumps(metadata, sort_keys=True).encode("utf-8")
            + b"\0"
            + payload
        )
        if hashlib.sha256(signed).hexdigest() != envelope["signature"]:
            raise ValueError("Envelope signature is invalid")
        Path(destination).write_bytes(payload)
        return metadata


class MemoryCloudTransportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config_path = self.base / "config.yaml"
        self.config_path.write_text(
            """memory:
  root_directory: "./memory"
summaries:
  level_1_trigger_rounds: 50
  level_1_trigger_characters: 20000
  automatic_semantic_jobs: false
  higher_level_trigger_count: 10
retrieval:
  log_queries: false
backup:
  enabled: false
federation:
  enabled: true
  replica_directory: ""
safety:
  redact_secrets: true
""",
            encoding="utf-8",
        )
        self.exchange = self.base / "cloud"
        self.exchange.mkdir()
        self.node_a = self.base / "archive-a"
        self.node_b = self.base / "archive-b"
        self.key_a = self.base / "node-a.identity"
        self.key_b = self.base / "node-b.identity"
        self.key_a.write_text("private-a", encoding="utf-8")
        self.key_b.write_text("private-b", encoding="utf-8")
        self.identity_a = self.identity("node-alpha")
        self.identity_b = self.identity("node-beta")
        self.crypto = FakeCrypto(
            {self.key_a: self.identity_a, self.key_b: self.identity_b}
        )
        self.run_cli(self.node_a, "init")
        self.run_cli(
            self.node_a,
            "init-node",
            "--node-id",
            "node-alpha",
            "--display-name",
            "Alpha",
        )
        self.run_cli(self.node_b, "init")
        self.run_cli(
            self.node_b,
            "init-node",
            "--node-id",
            "node-beta",
            "--display-name",
            "Beta",
        )
        self.manager_a = self.manager(self.node_a)
        self.manager_b = self.manager(self.node_b)
        self.register_cloud_peer(
            self.manager_a,
            "node-beta",
            self.identity_b,
            host="beta.example",
        )
        self.register_cloud_peer(
            self.manager_b,
            "node-alpha",
            self.identity_a,
            host="alpha.example",
        )
        self.transport_a = self.transport(
            self.manager_a, self.key_a, cleanup_grace_seconds=100
        )
        self.transport_b = self.transport(
            self.manager_b, self.key_b, cleanup_grace_seconds=100
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def identity(node_id):
        return {
            "encryption_public_key": f"age-{node_id}",
            "signing_public_key": f"ed25519-{node_id}",
            "fingerprint": hashlib.sha256(node_id.encode("ascii")).hexdigest(),
        }

    def run_cli(self, root, *arguments):
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--root",
                str(root),
                "--config",
                str(self.config_path),
                *arguments,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if completed.returncode != 0:
            self.fail(
                f"Command failed: {completed.args}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return json.loads(completed.stdout)

    def manager(self, root):
        config = load_simple_yaml(self.config_path)
        return FederationManager(MemoryStore(root, config))

    def register_cloud_peer(self, manager, node_id, identity, host=None):
        manager.add_peer(
            node_id,
            host=host,
            remote_root="/remote/archive" if host else None,
            remote_config="/remote/config.yaml" if host else None,
            remote_cli="/remote/memory_cli.py" if host else None,
        )
        path = manager.peer_path(node_id)
        peer = read_json(path)
        peer["cloud_identity"] = dict(identity)
        path.write_text(
            json.dumps(peer, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return peer

    def transport(
        self,
        manager,
        key,
        merge_window_seconds=900,
        early_flush_bytes=1024 * 1024,
        maximum_pending_seconds=3600,
        cleanup_grace_seconds=100,
    ):
        transport = CloudFolderTransport(manager, crypto=self.crypto)
        transport.configure(
            self.exchange,
            key,
            enabled=True,
            merge_window_seconds=merge_window_seconds,
            early_flush_bytes=early_flush_bytes,
            maximum_pending_seconds=maximum_pending_seconds,
            cleanup_grace_seconds=cleanup_grace_seconds,
        )
        return transport

    def append_message(self, root, speaker, message_id, text):
        return self.run_cli(
            root,
            "append",
            "--speaker",
            speaker,
            "--conversation-id",
            "codex:cloud-test",
            "--message-id",
            message_id,
            "--text",
            text,
        )

    def append_round(self, root, label, text_suffix=""):
        self.append_message(
            root, "user", f"{label}-user", f"{label} cloud user {text_suffix}"
        )
        self.append_message(
            root,
            "assistant",
            f"{label}-assistant",
            f"{label} cloud assistant {text_suffix}",
        )

    def own_outbox(self, node_id, target_id):
        return (
            self.exchange
            / "MemoryWuxianExchange"
            / "v1"
            / "nodes"
            / node_id
            / "outbox"
            / target_id
        )

    def test_bidirectional_exchange_ack_idempotency_and_ssh_preservation(self):
        peer_before = read_json(self.manager_a.peer_path("node-beta"))
        self.append_round(self.node_a, "ALPHA")
        first = self.transport_a.sync(force=True, now=1000)
        self.assertEqual(len(first["published"]), 1)
        envelope_a = Path(first["published"][0]["path"])
        self.assertTrue(envelope_a.is_file())

        self.append_round(self.node_b, "BETA")
        received_b = self.transport_b.sync(force=True, now=1010)
        self.assertEqual(received_b["imports"][0]["status"], "imported")
        self.assertEqual(len(received_b["published"]), 1)
        self.assertEqual(
            self.manager_b.replica_state("node-alpha")["last_event_sequence"],
            first["published"][0]["to_event_sequence"],
        )

        received_a = self.transport_a.sync(force=True, now=1020)
        self.assertEqual(len(received_a["acks"]), 1)
        self.assertEqual(received_a["imports"][0]["status"], "imported")
        self.assertEqual(len(received_a["published"]), 0)

        acknowledged_b = self.transport_b.sync(force=True, now=1030)
        self.assertEqual(len(acknowledged_b["acks"]), 1)
        repeated = self.transport_a.sync(force=False, now=1040)
        self.assertEqual(repeated["acks"], [])
        self.assertEqual(repeated["imports"][0]["status"], "no-change")

        peer_after = read_json(self.manager_a.peer_path("node-beta"))
        self.assertEqual(peer_after, peer_before)
        self.assertEqual(peer_after["transport"]["type"], "ssh")
        self.assertEqual(peer_after["transport"]["host"], "beta.example")

    def test_cli_pairing_and_real_envelope_round_trip(self):
        helper_name = (
            "memory-wuxian-envelope.exe"
            if os.name == "nt"
            else "memory-wuxian-envelope"
        )
        helper = (
            SKILL_ROOT
            / "native-collector"
            / "target"
            / "debug"
            / helper_name
        )
        if not helper.is_file():
            self.skipTest("Rust envelope helper has not been built")
        cli_exchange = self.base / "real-cloud"
        cli_exchange.mkdir()
        real_a = self.base / "real-a"
        real_b = self.base / "real-b"
        self.run_cli(real_a, "init")
        self.run_cli(
            real_a,
            "init-node",
            "--node-id",
            "real-alpha",
            "--display-name",
            "Real Alpha",
        )
        self.run_cli(real_b, "init")
        self.run_cli(
            real_b,
            "init-node",
            "--node-id",
            "real-beta",
            "--display-name",
            "Real Beta",
        )
        key_a = self.base / "keys" / "real-alpha.json"
        key_b = self.base / "keys" / "real-beta.json"
        configured_a = self.run_cli(
            real_a,
            "cloud-configure",
            "--directory",
            str(cli_exchange),
            "--identity-path",
            str(key_a),
            "--envelope-binary",
            str(helper),
        )
        configured_b = self.run_cli(
            real_b,
            "cloud-configure",
            "--directory",
            str(cli_exchange),
            "--identity-path",
            str(key_b),
            "--envelope-binary",
            str(helper),
        )
        self.assertNotEqual(
            configured_a["identity"]["fingerprint"],
            configured_b["identity"]["fingerprint"],
        )
        pair_a = self.base / "real-alpha-pairing.json"
        pair_b = self.base / "real-beta-pairing.json"
        self.run_cli(real_a, "cloud-pair-export", "--output", str(pair_a))
        self.run_cli(real_b, "cloud-pair-export", "--output", str(pair_b))
        self.run_cli(real_a, "cloud-pair-import", "--pairing-file", str(pair_b))
        self.run_cli(real_b, "cloud-pair-import", "--pairing-file", str(pair_a))

        self.run_cli(
            real_a,
            "append",
            "--speaker",
            "user",
            "--conversation-id",
            "codex:real-cloud",
            "--message-id",
            "real-cloud-u",
            "--text",
            "encrypted cloud user",
        )
        self.run_cli(
            real_a,
            "append",
            "--speaker",
            "assistant",
            "--conversation-id",
            "codex:real-cloud",
            "--message-id",
            "real-cloud-a",
            "--text",
            "encrypted cloud assistant",
        )
        sent = self.run_cli(real_a, "cloud-sync", "--force")
        received = self.run_cli(real_b, "cloud-sync", "--force")
        ack_deadline = time.monotonic() + 2.0
        while True:
            acknowledged = self.run_cli(real_a, "cloud-sync", "--force")
            if acknowledged["acks"] or time.monotonic() >= ack_deadline:
                break
            time.sleep(0.05)
        self.assertEqual(len(sent["published"]), 1, sent)
        self.assertEqual(received["imports"][0]["status"], "imported")
        self.assertEqual(len(acknowledged["acks"]), 1, acknowledged)
        encrypted_path = Path(sent["published"][0]["path"])
        self.assertNotIn(b"encrypted cloud user", encrypted_path.read_bytes())

    def test_stop_and_wait_keeps_one_durable_unacknowledged_envelope(self):
        self.append_round(self.node_a, "ONE")
        first = self.transport_a.sync(force=True, now=2000)
        self.append_round(self.node_a, "TWO")
        second = self.transport_a.sync(force=True, now=2010)
        self.assertEqual(len(first["published"]), 1)
        self.assertEqual(second["published"], [])
        self.assertEqual(len(second["waiting_ack"]), 1)
        self.assertEqual(
            len(list(self.own_outbox("node-alpha", "node-beta").glob("*.mwxe"))),
            1,
        )

    def test_outstanding_is_recovered_after_state_write_interruption(self):
        self.append_round(self.node_a, "RECOVER")
        first = self.transport_a.sync(force=True, now=2500)
        envelope = Path(first["published"][0]["path"])
        cloud_config_path = self.node_a / "federation/cloud.json"
        cloud_config = read_json(cloud_config_path)
        cloud_config["outbound"]["node-beta"]["outstanding"] = None
        cloud_config_path.write_text(
            json.dumps(cloud_config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        restarted = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        result = restarted.sync(force=True, now=2510)
        self.assertEqual(result["published"], [])
        self.assertEqual(len(result["waiting_ack"]), 1)
        self.assertEqual(
            result["waiting_ack"][0]["bundle_id"],
            first["published"][0]["bundle_id"],
        )
        self.assertEqual(
            [
                path.resolve()
                for path in self.own_outbox(
                    "node-alpha", "node-beta"
                ).glob("*.mwxe")
            ],
            [envelope.resolve()],
        )

    def test_out_of_order_waits_and_partial_and_zero_files_are_transient(self):
        incoming = (
            self.exchange
            / "MemoryWuxianExchange/v1/nodes/node-alpha/outbox/node-beta"
        )
        incoming.mkdir(parents=True)
        partial = incoming / ".upload.partial"
        partial.write_bytes(b"partial")
        zero = incoming / (
            "00000000000000000001-00000000000000000001-"
            f"mwb-{'a' * 32}-{'b' * 64}.mwxe"
        )
        zero.touch()
        gap = incoming / (
            "00000000000000000002-00000000000000000002-"
            f"mwb-{'c' * 32}-{'d' * 64}.mwxe"
        )
        gap.write_bytes(b"not-opened-because-gap")

        result = self.transport_b.sync(force=False, now=3000)
        kinds = {item["type"] for item in result["transient"]}
        self.assertIn("bundle", kinds)
        self.assertIn("bundle-gap", kinds)
        self.assertIn(
            str(partial.resolve()),
            {
                str(Path(item["path"]).resolve())
                for item in result["transient"]
                if item.get("path")
            },
        )
        self.assertEqual(result["quarantined"], [])
        self.assertTrue(gap.exists())
        self.assertTrue(zero.exists())
        self.assertTrue(partial.exists())

    def test_tampered_bundle_is_quarantined_without_moving_peer_file(self):
        self.append_round(self.node_a, "TAMPER")
        sent = self.transport_a.sync(force=True, now=4000)
        envelope = Path(sent["published"][0]["path"])
        payload = json.loads(envelope.read_text(encoding="utf-8"))
        payload["signature"] = "0" * 64
        envelope.write_text(json.dumps(payload), encoding="utf-8")

        received = self.transport_b.sync(force=False, now=4010)
        self.assertEqual(len(received["quarantined"]), 1)
        self.assertEqual(received["imports"], [])
        self.assertTrue(envelope.exists())
        records = list(
            (self.node_b / "federation/cloud-quarantine").glob("bundle-*.json")
        )
        self.assertEqual(len(records), 1)

    def test_revoked_peer_is_not_read_or_written(self):
        self.append_round(self.node_a, "REVOKED")
        sent = self.transport_a.sync(force=True, now=5000)
        envelope = Path(sent["published"][0]["path"])
        self.manager_b.revoke_peer("node-alpha")
        result = self.transport_b.sync(force=True, now=5010)
        self.assertEqual(result["imports"], [])
        self.assertEqual(result["quarantined"], [])
        self.assertTrue(envelope.exists())
        self.assertFalse(
            (
                self.exchange
                / "MemoryWuxianExchange/v1/nodes/node-beta/acks/node-alpha"
            ).exists()
        )

    def test_cleanup_only_removes_own_confirmed_outbox_after_grace(self):
        self.append_round(self.node_a, "CLEAN")
        sent = self.transport_a.sync(force=True, now=6000)
        envelope = Path(sent["published"][0]["path"])
        os.utime(envelope, (6000, 6000))
        self.transport_b.sync(force=False, now=6010)
        before_grace = self.transport_a.sync(force=False, now=6099)
        self.assertTrue(envelope.exists())
        self.assertEqual(before_grace["cleaned"], [])
        at_grace = self.transport_a.sync(force=False, now=6100)
        self.assertFalse(envelope.exists())
        self.assertEqual(at_grace["cleaned"], [str(envelope)])

        foreign = (
            self.exchange
            / "MemoryWuxianExchange/v1/nodes/node-beta/outbox/node-alpha"
            / envelope.name
        )
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"foreign")
        os.utime(foreign, (6000, 6000))
        self.transport_a.sync(force=False, now=6200)
        self.assertTrue(foreign.exists())

    def test_cleanup_prunes_old_ack_files_for_receive_only_peer(self):
        ack_outbox = (
            self.exchange
            / "MemoryWuxianExchange/v1/nodes/node-beta/acks/node-alpha"
        )
        ack_outbox.mkdir(parents=True)
        old_ack = ack_outbox / (
            "ack-00000000000000000001-"
            "mwb-11111111111111111111111111111111.mwxa"
        )
        newest_ack = ack_outbox / (
            "ack-00000000000000000002-"
            "mwb-22222222222222222222222222222222.mwxa"
        )
        old_ack.write_bytes(b"old")
        newest_ack.write_bytes(b"new")
        os.utime(old_ack, (1, 1))
        os.utime(newest_ack, (1, 1))

        result = self.transport_b.sync(now=200)

        self.assertFalse(old_ack.exists())
        self.assertTrue(newest_ack.exists())
        self.assertIn(old_ack.resolve(), [Path(item) for item in result["cleaned"]])

    def test_incomplete_round_does_not_trigger_but_completed_round_does(self):
        baseline = self.transport_a.sync(force=True, now=7000)
        self.assertEqual(baseline["published"], [])
        self.append_message(
            self.node_a, "user", "OPEN-user", "an incomplete user message"
        )
        incomplete = self.transport_a.sync(force=False, now=8000)
        self.assertFalse(incomplete["schedule"]["changed"])
        self.assertEqual(incomplete["published"], [])
        self.append_message(
            self.node_a, "assistant", "OPEN-assistant", "the completed answer"
        )
        pending = self.transport_a.sync(force=False, now=8010)
        self.assertTrue(pending["schedule"]["changed"])
        self.assertEqual(pending["published"], [])
        due = self.transport_a.sync(force=False, now=8910)
        self.assertEqual(len(due["published"]), 1)

    def test_early_byte_maximum_age_and_force_triggers(self):
        base_time = time.time()
        early = self.transport(
            self.manager_a,
            self.key_a,
            merge_window_seconds=10_000,
            early_flush_bytes=1024,
            maximum_pending_seconds=3600,
        )
        self.append_round(self.node_a, "LARGE", "x" * 2048)
        early_result = early.sync(force=False, now=base_time)
        self.assertGreaterEqual(
            early_result["schedule"]["estimated_new_bytes"], 1024
        )
        self.assertEqual(len(early_result["published"]), 1)

        # A fresh peer cursor remains blocked by stop-and-wait; use a fresh archive
        # pair to isolate the maximum-age state machine.
        node_c = self.base / "archive-c"
        key_c = self.base / "node-c.identity"
        key_c.write_text("private-c", encoding="utf-8")
        identity_c = self.identity("node-gamma")
        self.crypto.identities[str(key_c.resolve())] = identity_c
        self.run_cli(node_c, "init")
        self.run_cli(
            node_c,
            "init-node",
            "--node-id",
            "node-gamma",
            "--display-name",
            "Gamma",
        )
        manager_c = self.manager(node_c)
        self.register_cloud_peer(manager_c, "node-beta", self.identity_b)
        delayed = self.transport(
            manager_c,
            key_c,
            merge_window_seconds=10_000,
            early_flush_bytes=10 * 1024 * 1024,
            maximum_pending_seconds=3600,
        )
        self.append_round(node_c, "DELAYED")
        first = delayed.sync(force=False, now=base_time + 100)
        self.assertEqual(first["published"], [])
        maximum = delayed.sync(force=False, now=base_time + 3700)
        self.assertEqual(len(maximum["published"]), 1)

        node_d = self.base / "archive-d"
        key_d = self.base / "node-d.identity"
        key_d.write_text("private-d", encoding="utf-8")
        identity_d = self.identity("node-delta")
        self.crypto.identities[str(key_d.resolve())] = identity_d
        self.run_cli(node_d, "init")
        self.run_cli(
            node_d,
            "init-node",
            "--node-id",
            "node-delta",
            "--display-name",
            "Delta",
        )
        manager_d = self.manager(node_d)
        self.register_cloud_peer(manager_d, "node-beta", self.identity_b)
        forced = self.transport(manager_d, key_d)
        self.append_round(node_d, "FORCED")
        forced_result = forced.sync(force=True, now=base_time + 4000)
        self.assertEqual(len(forced_result["published"]), 1)

    def test_closed_summary_or_title_marker_can_trigger_without_new_round(self):
        self.transport_a.sync(force=True, now=15_000)
        title_index = self.node_a / "indexes/conversation-titles.jsonl"
        title_index.write_text(
            json.dumps(
                {
                    "conversation_id": "codex:closed-range",
                    "title": "Closed range title",
                    "timestamp": "2026-07-23T00:00:00+09:00",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        pending = self.transport_a.sync(force=False, now=15_010)
        self.assertTrue(pending["schedule"]["changed"])
        self.assertEqual(pending["published"], [])
        sent = self.transport_a.sync(force=False, now=15_910)
        self.assertEqual(len(sent["published"]), 1)

    def test_cloud_configuration_contains_private_path_only_locally(self):
        cloud_config = read_json(self.node_a / "federation/cloud.json")
        self.assertEqual(
            cloud_config["identity_private_path"], str(self.key_a.resolve())
        )
        exchange_files = [
            path
            for path in self.exchange.rglob("*")
            if path.is_file()
        ]
        self.assertEqual(exchange_files, [])
        peer = read_json(self.manager_a.peer_path("node-beta"))
        self.assertNotIn("identity_private_path", peer["cloud_identity"])


if __name__ == "__main__":
    unittest.main()
