import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import (
    AuthenticatedOpenResult,
    CloudFolderTransport,
    CommandCrypto,
    TransientCloudArtifactError,
    _AUTHENTICATED_OPEN_AUTHORITY,
    display_path,
    filesystem_native_path,
)
from memory_federation import FederationManager, read_json
from memory_project_evidence import ProjectEvidenceExchangeManager, ProjectEvidenceStore


CLI = SKILL_ROOT / "scripts" / "memory_cli.py"


class CommandCryptoArgumentTest(unittest.TestCase):
    def test_open_accepts_signing_key_that_starts_with_hyphen(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="{}\n",
            stderr="",
        )
        crypto = CommandCrypto(Path("memory-wuxian-envelope"))

        with patch("memory_cloud_transport.subprocess.run", return_value=completed) as run:
            crypto.open(
                Path("input.mwxa"),
                Path("output.json"),
                Path("identity.json"),
                "-fixed-url-safe-signing-key",
                "archive-v1-ack",
                "node-beta",
                "node-alpha",
            )

        command = run.call_args.args[0]
        self.assertIn(
            "--signing-public-key=-fixed-url-safe-signing-key",
            command,
        )
        self.assertNotIn("--signing-public-key", command)

    def test_open_classifies_file_provider_deadlock_as_transient(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="read input: Resource deadlock avoided (os error 11)",
        )
        crypto = CommandCrypto(Path("memory-wuxian-envelope"))

        with patch("memory_cloud_transport.subprocess.run", return_value=completed):
            with self.assertRaises(TransientCloudArtifactError):
                crypto.open(
                    Path("placeholder.mwxe"),
                    Path("output.mwxb"),
                    Path("identity.json"),
                    "signing-key",
                    "environment-v1-bundle",
                    "node-alpha",
                    "node-beta",
                )


class CloudPlaceholderMaterializationTest(unittest.TestCase):
    def test_macos_onedrive_placeholder_is_pinned_then_cleared(self):
        placeholder = Path("/tmp/placeholder.mwxe")
        reads = [OSError(11, "Resource deadlock avoided"), io.BytesIO(b"x")]

        with (
            patch.object(Path, "open", side_effect=reads),
            patch("memory_cloud_transport._is_macos_onedrive_path", return_value=True),
            patch("memory_cloud_transport._set_macos_onedrive_pin") as pin,
            patch("memory_cloud_transport.time.sleep"),
        ):
            CloudFolderTransport._materialize_candidate(placeholder)

        self.assertEqual(
            pin.call_args_list,
            [call(placeholder, "/pin"), call(placeholder, "/clearpin")],
        )

    def test_non_onedrive_placeholder_remains_transient(self):
        placeholder = Path("/tmp/placeholder.mwxe")

        with (
            patch.object(
                Path,
                "open",
                side_effect=OSError(11, "Resource deadlock avoided"),
            ),
            patch("memory_cloud_transport._is_macos_onedrive_path", return_value=False),
        ):
            with self.assertRaises(TransientCloudArtifactError):
                CloudFolderTransport._materialize_candidate(placeholder)

    def test_onedrive_pin_failure_remains_transient(self):
        placeholder = Path("/tmp/placeholder.mwxe")

        with (
            patch.object(
                Path,
                "open",
                side_effect=OSError(11, "Resource deadlock avoided"),
            ),
            patch("memory_cloud_transport._is_macos_onedrive_path", return_value=True),
            patch(
                "memory_cloud_transport._set_macos_onedrive_pin",
                side_effect=OSError("pin failed"),
            ),
        ):
            with self.assertRaisesRegex(
                TransientCloudArtifactError,
                "could not request materialization",
            ):
                CloudFolderTransport._materialize_candidate(placeholder)


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
        return AuthenticatedOpenResult(
            _AUTHENTICATED_OPEN_AUTHORITY,
            {
                **metadata,
                "payload_length": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            },
        )


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
        if os.name == "nt":
            shutil.rmtree("\\\\?\\" + self.temporary.name, ignore_errors=True)
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
            timeout=30,
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

    def test_exchange_directory_selection_normalizes_to_provider_root(self):
        queue_directory = self.exchange / "MemoryWuxianExchange"
        queue_directory.mkdir(exist_ok=True)
        transport = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        result = transport.configure(queue_directory, self.key_a, enabled=True)

        self.assertEqual(result["status"], "configured")
        self.assertEqual(
            Path(transport.status()["exchange_root"]),
            self.exchange.resolve(),
        )
        self.assertEqual(
            transport._exchange_root(),
            Path(
                filesystem_native_path(
                    self.exchange.resolve() / "MemoryWuxianExchange" / "v1"
                )
            ),
        )

    def test_existing_queue_directory_config_is_normalized_when_read(self):
        queue_directory = self.exchange / "MemoryWuxianExchange"
        queue_directory.mkdir(exist_ok=True)
        transport = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        transport.configure(self.exchange, self.key_a, enabled=True)
        transport.config["exchange_root"] = str(queue_directory.resolve())
        transport.save_config()

        reloaded = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        self.assertEqual(
            reloaded._exchange_root(),
            Path(
                filesystem_native_path(
                    self.exchange.resolve() / "MemoryWuxianExchange" / "v1"
                )
            ),
        )

    def test_cloud_queue_rejects_link_like_descendant_before_write(self):
        root = self.transport_a._exchange_root()
        nodes = root / "nodes"
        nodes.mkdir(parents=True, exist_ok=True)
        from memory_cloud_transport import is_link_like as original

        def classify(path):
            return (
                Path(display_path(path)) == Path(display_path(nodes))
                or original(path)
            )

        with patch("memory_cloud_transport.is_link_like", side_effect=classify):
            with self.assertRaisesRegex(ValueError, "link or junction"):
                self.transport_a._outbox("node-beta")

    def test_native_windows_queue_directory_config_is_normalized_when_read(self):
        queue_directory = self.exchange / "MemoryWuxianExchange"
        queue_directory.mkdir(exist_ok=True)
        transport = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        transport.configure(self.exchange, self.key_a, enabled=True)
        transport.config["exchange_root"] = (
            "\\\\?\\" + str(queue_directory.resolve())
        )
        transport.save_config()
        reloaded = CloudFolderTransport(self.manager_a, crypto=self.crypto)

        self.assertEqual(
            reloaded._exchange_root(),
            Path(
                filesystem_native_path(
                    self.exchange.resolve()
                    / "MemoryWuxianExchange"
                    / "v1"
                )
            ),
        )

    def test_existing_outstanding_envelope_migrates_to_canonical_outbox(self):
        self.append_round(self.node_a, "MIGRATE")
        first = self.transport_a.sync(force=True, now=1000)
        canonical = Path(first["published"][0]["path"])
        legacy = (
            self.exchange
            / "MemoryWuxianExchange"
            / "MemoryWuxianExchange"
            / "v1"
            / "nodes"
            / "node-alpha"
            / "outbox"
            / "node-beta"
            / canonical.name
        )
        legacy_native = Path(filesystem_native_path(legacy))
        legacy_native.parent.mkdir(parents=True)
        Path(filesystem_native_path(canonical)).replace(legacy_native)
        config = read_json(self.node_a / "federation/cloud.json")
        config["exchange_root"] = str(
            (self.exchange / "MemoryWuxianExchange").resolve()
        )
        config["outbound"]["node-beta"]["outstanding"]["path"] = str(legacy_native)
        (self.node_a / "federation/cloud.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        restarted = CloudFolderTransport(self.manager_a, crypto=self.crypto)
        migrated = restarted.sync(force=False, now=1010)

        self.assertEqual(len(migrated["migrated"]), 1)
        restored = self.own_outbox("node-alpha", "node-beta") / legacy.name
        self.assertTrue(restored.is_file())
        self.assertTrue(legacy_native.is_file())
        self.assertEqual(restored.read_bytes(), legacy_native.read_bytes())
        state = read_json(self.node_a / "federation/cloud.json")
        self.assertEqual(
            Path(state["outbound"]["node-beta"]["outstanding"]["path"]),
            restored.resolve(),
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
        sync_log_before_replay = self.manager_a.sync_log_path.read_text(
            encoding="utf-8"
        ).splitlines()
        repeated = self.transport_a.sync(force=False, now=1040)
        self.assertEqual(repeated["acks"], [])
        self.assertEqual(repeated["imports"][0]["status"], "no-change")
        self.assertEqual(repeated["counts"]["imported"], 0)
        self.assertEqual(repeated["counts"]["no_change"], 1)
        self.assertEqual(
            self.manager_a.sync_log_path.read_text(
                encoding="utf-8"
            ).splitlines(),
            sync_log_before_replay,
        )

        peer_after = read_json(self.manager_a.peer_path("node-beta"))
        self.assertEqual(peer_after, peer_before)
        self.assertEqual(peer_after["transport"]["type"], "ssh")
        self.assertEqual(peer_after["transport"]["host"], "beta.example")

    def test_ack_older_than_acknowledged_cursor_is_ignored_before_decryption(self):
        state = self.transport_a._peer_state("node-beta")
        state["acknowledged"] = {
            "last_event_sequence": 100,
            "last_bundle_id": f"mwb-{'a' * 32}",
            "last_bundle_sha256": "b" * 64,
            "acknowledged_at": "2026-08-05T00:00:00+00:00",
        }
        self.transport_a.save_config()
        stale = self.transport_a._incoming_acks("node-beta") / (
            f"ack-{50:020d}-mwb-{'c' * 32}.mwxa"
        )
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"unsupported legacy acknowledgement")

        with patch.object(
            self.transport_a,
            "_read_ack",
            side_effect=AssertionError("stale acknowledgement was decrypted"),
        ):
            result = self.transport_a.sync(force=False, now=1045)

        self.assertEqual(result["quarantined"], [])
        self.assertEqual(result["transient"], [])

    def test_project_evidence_uses_authenticated_encrypted_transport(self):
        config = load_simple_yaml(self.config_path)
        store_a = MemoryStore(self.node_a, config)
        store_b = MemoryStore(self.node_b, config)
        source = self.base / "project-evidence-source"
        source.mkdir()
        (source / "PROJECT_AGENTS.md").write_text("# Project rules\n", encoding="utf-8")
        spec = {
            "schema_version": 1,
            "project_id": "project-cloud-test",
            "title": "Project cloud test",
            "source_root": str(source),
            "files": [{"path": "PROJECT_AGENTS.md", "role": "project-rule"}],
        }
        ProjectEvidenceStore(store_a).build(spec, apply=True)
        manager_a = ProjectEvidenceExchangeManager(store_a)
        manager_b = ProjectEvidenceExchangeManager(store_b)
        transport_a = CloudFolderTransport(
            manager_a,
            config_path=manager_a.metadata_root / "cloud.json",
            stream_id="project-evidence-v1",
            crypto=self.crypto,
        )
        transport_b = CloudFolderTransport(
            manager_b,
            config_path=manager_b.metadata_root / "cloud.json",
            stream_id="project-evidence-v1",
            crypto=self.crypto,
        )
        for transport, key in ((transport_a, self.key_a), (transport_b, self.key_b)):
            transport.configure(self.exchange, key, enabled=True)

        published = transport_a.sync(force=True, now=1000)
        self.assertEqual(len(published["published"]), 1)
        envelope_path = Path(published["published"][0]["path"])
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        self.assertEqual(envelope["kind"], "project-evidence-v1-bundle")
        self.assertEqual(envelope["origin_node_id"], "node-alpha")
        self.assertEqual(envelope["target_node_id"], "node-beta")
        self.assertNotIn("Project rules", envelope_path.read_text(encoding="utf-8"))

        imported = transport_b.sync(force=True, now=1010)
        self.assertEqual(imported["imports"][0]["status"], "imported")
        remote = ProjectEvidenceStore(store_b).list("project-cloud-test")["remote"]
        self.assertEqual(len(remote), 1)

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
        encrypted_path = Path(filesystem_native_path(Path(sent["published"][0]["path"])))
        self.assertNotIn(b"encrypted cloud user", encrypted_path.read_bytes())

    def test_stop_and_wait_keeps_one_durable_unacknowledged_envelope(self):
        self.append_round(self.node_a, "ONE")
        first = self.transport_a.sync(force=True, now=2000)
        self.append_round(self.node_a, "TWO")
        second = self.transport_a.sync(force=True, now=2010)
        self.assertEqual(len(first["published"]), 1)
        self.assertEqual(second["published"], [])
        self.assertEqual(len(second["waiting_ack"]), 1)
        self.assertEqual(second["status"], "waiting-ack")
        self.assertEqual(
            len(list(self.own_outbox("node-alpha", "node-beta").glob("*.mwxe"))),
            1,
        )

    def test_waiting_ack_does_not_advance_observation_watermark(self):
        self.append_round(self.node_a, "FIRST")
        first = self.transport_a.sync(force=True, now=2100)
        first_state = read_json(self.node_a / "federation/cloud.json")
        published_before_wait = first_state["schedule"]["published"]

        self.append_round(self.node_a, "SECOND")
        waiting = self.transport_a.sync(force=True, now=2110)
        waiting_state = read_json(self.node_a / "federation/cloud.json")

        self.assertEqual(waiting["status"], "waiting-ack")
        self.assertEqual(waiting["published"], [])
        self.assertEqual(len(waiting["waiting_ack"]), 1)
        self.assertEqual(
            waiting_state["schedule"]["published"], published_before_wait
        )
        self.assertTrue(waiting["schedule"]["changed"])

        received = self.transport_b.sync(force=False, now=2120)
        self.assertEqual(received["counts"]["imported"], 1)
        resumed = self.transport_a.sync(force=False, now=3011)
        self.assertEqual(len(resumed["acks"]), 1)
        self.assertEqual(len(resumed["published"]), 1)
        self.assertEqual(
            resumed["published"][0]["from_event_sequence"],
            first["published"][0]["to_event_sequence"] + 1,
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
                display_path(path.resolve())
                for path in self.own_outbox(
                    "node-alpha", "node-beta"
                ).glob("*.mwxe")
            ],
            [display_path(envelope.resolve())],
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

    def test_unreadable_cloud_placeholder_is_transient_not_quarantined(self):
        self.append_round(self.node_a, "PLACEHOLDER")
        sent = self.transport_a.sync(force=True, now=3500)
        envelope = Path(sent["published"][0]["path"])

        with patch.object(
            self.transport_b,
            "_materialize_candidate",
            side_effect=TransientCloudArtifactError("provider placeholder"),
        ):
            result = self.transport_b.sync(force=False, now=3510)

        self.assertEqual(result["imports"], [])
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(len(result["transient"]), 1)
        self.assertEqual(result["transient"][0]["type"], "bundle")
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["stream_id"], "archive-v1")
        self.assertEqual(result["counts"]["transient"], 1)
        self.assertTrue(envelope.exists())

    def test_tampered_bundle_is_quarantined_without_moving_peer_file(self):
        self.append_round(self.node_a, "TAMPER")
        sent = self.transport_a.sync(force=True, now=4000)
        envelope = Path(sent["published"][0]["path"])
        payload = json.loads(envelope.read_text(encoding="utf-8"))
        payload["signature"] = "0" * 64
        envelope.write_text(json.dumps(payload), encoding="utf-8")

        received = self.transport_b.sync(force=False, now=4010)
        self.assertEqual(received["status"], "degraded")
        self.assertEqual(received["stream_id"], "archive-v1")
        self.assertEqual(received["counts"]["quarantined"], 1)
        self.assertEqual(len(received["quarantined"]), 1)
        self.assertEqual(received["imports"], [])
        self.assertTrue(envelope.exists())
        records = list(
            (self.node_b / "federation/cloud-quarantine").glob("bundle-*.json")
        )
        self.assertEqual(len(records), 1)

    def test_archive_and_environment_errors_are_isolated_and_identified(self):
        environment_a = CloudFolderTransport(
            self.manager_a,
            crypto=self.crypto,
            config_path=self.node_a / "federation/environment-cloud-test.json",
            stream_id="environment-v1",
        )
        environment_b = CloudFolderTransport(
            self.manager_b,
            crypto=self.crypto,
            config_path=self.node_b / "federation/environment-cloud-test.json",
            stream_id="environment-v1",
        )
        environment_a.configure(self.exchange, self.key_a, enabled=True)
        environment_b.configure(self.exchange, self.key_b, enabled=True)
        self.append_round(self.node_a, "ENVIRONMENT-TAMPER")
        sent = environment_a.sync(force=True, now=4100)
        envelope = Path(sent["published"][0]["path"])
        payload = json.loads(envelope.read_text(encoding="utf-8"))
        payload["signature"] = "0" * 64
        envelope.write_text(json.dumps(payload), encoding="utf-8")

        environment_result = environment_b.sync(force=False, now=4110)
        archive_result = self.transport_b.sync(force=False, now=4110)

        self.assertEqual(environment_result["stream_id"], "environment-v1")
        self.assertEqual(environment_result["status"], "degraded")
        self.assertEqual(environment_result["counts"]["quarantined"], 1)
        self.assertEqual(archive_result["stream_id"], "archive-v1")
        self.assertEqual(archive_result["status"], "ok")
        self.assertEqual(archive_result["quarantined"], [])

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
        self.assertEqual(
            at_grace["cleaned"],
            [display_path(envelope)],
        )

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
