import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import memory_federation
from memory_cli import MemoryStore, load_simple_yaml
from memory_federation import (
    BUNDLE_FORMAT,
    PROTOCOL_VERSION,
    FederationManager,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
)

CLI = SKILL_ROOT / "scripts" / "memory_cli.py"


class MemoryFederationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config = self.base / "config.yaml"
        self.config.write_text(
            """memory:
  root_directory: "./memory"
summaries:
  level_1_trigger_rounds: 1
  level_1_trigger_characters: 20000
  automatic_semantic_jobs: false
  higher_level_trigger_count: 2
retrieval:
  context_messages_before: 1
  context_messages_after: 1
  maximum_initial_candidates: 10
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
        self.node_a = self.base / "node-a"
        self.node_b = self.base / "node-b"
        self.node_c = self.base / "node-c"
        for root, node_id in (
            (self.node_a, "node-alpha"),
            (self.node_b, "node-beta"),
            (self.node_c, "node-gamma"),
        ):
            self.run_cli(root, "init")
            self.run_cli(
                root,
                "init-node",
                "--node-id",
                node_id,
                "--display-name",
                node_id,
            )

    def tearDown(self):
        self.temporary.cleanup()

    def invoke_cli(self, root, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--root",
                str(root),
                "--config",
                str(self.config),
                *arguments,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def run_cli(self, root, *arguments, expect_json=True):
        completed = self.invoke_cli(root, *arguments)
        if completed.returncode != 0:
            self.fail(
                f"Command failed: {completed.args}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        return json.loads(completed.stdout) if expect_json else completed.stdout

    def append_round(self, root, label):
        self.run_cli(
            root,
            "append",
            "--speaker",
            "user",
            "--conversation-id",
            "codex:shared-name",
            "--message-id",
            f"{label}-user",
            "--text",
            f"{label} 讨论联邦记忆",
        )
        self.run_cli(
            root,
            "append",
            "--speaker",
            "assistant",
            "--conversation-id",
            "codex:shared-name",
            "--message-id",
            f"{label}-assistant",
            "--text",
            f"{label} 确认远端副本只读",
        )

    def add_offline_peer(self, root, node_id):
        return self.run_cli(root, "add-peer", "--node-id", node_id)

    def test_two_nodes_exchange_read_only_deltas_and_search_globally(self):
        self.add_offline_peer(self.node_a, "node-beta")
        self.add_offline_peer(self.node_b, "node-alpha")
        self.append_round(self.node_a, "ALPHA")
        self.append_round(self.node_b, "BETA")
        state_before = (self.node_b / "state.json").read_bytes()

        bundle_a = self.base / "alpha-to-beta.mwxb"
        exported = self.run_cli(
            self.node_a,
            "export-delta",
            "--target-node-id",
            "node-beta",
            "--output",
            str(bundle_a),
        )
        self.assertEqual(exported["status"], "created")
        inspected = self.run_cli(
            self.node_b,
            "inspect-bundle",
            "--bundle",
            str(bundle_a),
        )
        self.assertEqual(inspected["status"], "valid")
        self.assertEqual(inspected["manifest"]["origin_node_id"], "node-alpha")

        imported = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(bundle_a),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(imported["status"], "imported")
        self.assertEqual((self.node_b / "state.json").read_bytes(), state_before)
        self.assertFalse((self.node_b / "raw").joinpath("node-alpha").exists())

        repeated = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(bundle_a),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(repeated["status"], "no-change")
        global_messages = (
            self.base / "node-b-federation-cache/global-index/messages.jsonl"
        )
        global_messages.unlink()
        rebuilt = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(bundle_a),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(rebuilt["status"], "no-change")
        self.assertTrue(global_messages.exists())

        peer_root = (
            self.base / "node-b-federation-cache/peers/node-alpha"
        )
        receipt = next((peer_root / "receipts").glob("*.json"))
        manifest = json.loads(receipt.read_text(encoding="utf-8"))["manifest"]
        receipt.unlink()
        (peer_root / ".importing.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "bundle_id": manifest["bundle_id"],
                    "origin_node_id": "node-alpha",
                }
            ),
            encoding="utf-8",
        )
        recovered = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(bundle_a),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(recovered["status"], "recovered")
        self.assertFalse((peer_root / ".importing.json").exists())

        output = self.run_cli(
            self.node_b,
            "retrieve-global",
            "--query",
            "ALPHA 联邦记忆",
            expect_json=False,
        )
        self.assertIn("node-alpha", output)
        self.assertIn("ALPHA 讨论联邦记忆", output)
        self.assertIn("remote-replica", output)

        bundle_b = self.base / "beta-to-alpha.mwxb"
        self.run_cli(
            self.node_b,
            "export-delta",
            "--target-node-id",
            "node-alpha",
            "--output",
            str(bundle_b),
        )
        self.run_cli(
            self.node_a,
            "import-delta",
            "--bundle",
            str(bundle_b),
            "--expected-node-id",
            "node-beta",
        )
        output = self.run_cli(
            self.node_a,
            "retrieve-global",
            "--query",
            "BETA 远端副本",
            expect_json=False,
        )
        self.assertIn("node-beta", output)

    def test_late_summary_is_exported_after_raw_cursor(self):
        self.add_offline_peer(self.node_b, "node-alpha")
        self.append_round(self.node_a, "LATE")
        first_bundle = self.base / "first.mwxb"
        first = self.run_cli(
            self.node_a,
            "export-delta",
            "--output",
            str(first_bundle),
        )
        self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(first_bundle),
            "--expected-node-id",
            "node-alpha",
        )

        job = self.run_cli(self.node_a, "make-summary-job")
        summary_result = self.base / "summary.json"
        summary_result.write_text(
            json.dumps(
                {
                    "topics": ["联邦记忆"],
                    "established_conclusions": ["远端副本只读"],
                    "open_questions": [],
                    "concepts": ["联邦记忆"],
                    "policy_events": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.run_cli(
            self.node_a,
            "ingest-summary",
            "--job",
            job["job"],
            "--summary-json",
            str(summary_result),
        )
        second_bundle = self.base / "second.mwxb"
        second = self.run_cli(
            self.node_a,
            "export-delta",
            "--after-event-sequence",
            str(first["to_event_sequence"]),
            "--previous-bundle-sha256",
            first["sha256"],
            "--output",
            str(second_bundle),
        )
        self.assertEqual(second["artifact_count"], 1)
        imported = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(second_bundle),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(imported["status"], "imported")
        replica_summaries = (
            self.base / "node-b-federation-cache/peers/node-alpha/summaries"
        )
        self.assertEqual(len(list(replica_summaries.glob("*.json"))), 1)

    def test_export_recovers_state_and_pages_large_deltas(self):
        self.append_round(self.node_a, "PAGED")
        manager = FederationManager(
            MemoryStore(self.node_a, load_simple_yaml(self.config))
        )
        original_atomic_write_json = memory_federation.atomic_write_json

        def interrupt_state_write(path, payload):
            if path == manager.export_state_path:
                raise RuntimeError("simulated export-state interruption")
            return original_atomic_write_json(path, payload)

        with patch(
            "memory_federation.atomic_write_json",
            side_effect=interrupt_state_write,
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                manager.refresh_export_ledger()

        manager.refresh_export_ledger()
        ledger = memory_federation.read_jsonl(manager.export_ledger_path)
        self.assertEqual(
            [entry["event_sequence"] for entry in ledger],
            list(range(1, len(ledger) + 1)),
        )
        self.assertEqual(len({entry["artifact_id"] for entry in ledger}), len(ledger))

        first_bundle = self.base / "paged-1.mwxb"
        with patch("memory_federation.MAX_ARTIFACTS", 1):
            first = manager.export_delta(first_bundle)
        self.assertEqual(first["artifact_count"], 1)
        self.assertTrue(first["has_more"])

        second_bundle = self.base / "paged-2.mwxb"
        with patch("memory_federation.MAX_ARTIFACTS", 1):
            second = manager.export_delta(
                second_bundle,
                first["to_event_sequence"],
                previous_bundle_sha256=first["sha256"],
            )
        self.assertEqual(second["artifact_count"], 1)
        self.assertEqual(
            second["from_event_sequence"],
            first["to_event_sequence"] + 1,
        )

    def test_gap_corruption_conflict_and_revocation_are_rejected(self):
        self.add_offline_peer(self.node_b, "node-alpha")
        self.add_offline_peer(self.node_c, "node-alpha")
        self.append_round(self.node_a, "FIRST")
        first_bundle = self.base / "first-gap.mwxb"
        first = self.run_cli(
            self.node_a,
            "export-delta",
            "--output",
            str(first_bundle),
        )
        self.append_round(self.node_a, "SECOND")
        second_bundle = self.base / "second-gap.mwxb"
        self.run_cli(
            self.node_a,
            "export-delta",
            "--after-event-sequence",
            str(first["to_event_sequence"]),
            "--previous-bundle-sha256",
            first["sha256"],
            "--output",
            str(second_bundle),
        )

        gap = self.invoke_cli(
            self.node_c,
            "import-delta",
            "--bundle",
            str(second_bundle),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertNotEqual(gap.returncode, 0)
        self.assertIn("sequence gap", gap.stderr)

        corrupted = self.base / "corrupted.mwxb"
        with zipfile.ZipFile(first_bundle, "r") as source:
            manifest = source.read("manifest.json")
            payload = bytearray(source.read("payload/artifacts.jsonl"))
        payload[-2] ^= 1
        with zipfile.ZipFile(corrupted, "w") as destination:
            destination.writestr("manifest.json", manifest)
            destination.writestr("payload/artifacts.jsonl", bytes(payload))
        rejected = self.invoke_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(corrupted),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("SHA-256 mismatch", rejected.stderr)

        self.run_cli(self.node_b, "revoke-peer", "--node-id", "node-alpha")
        revoked = self.invoke_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(first_bundle),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertNotEqual(revoked.returncode, 0)
        self.assertIn("revoked", revoked.stderr)

    def write_bundle(self, path, manifest, payload):
        manifest_base = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        manifest = {
            **manifest_base,
            "bundle_id": f"mwb-{canonical_sha256(manifest_base)[:32]}",
        }
        with zipfile.ZipFile(path, "w") as destination:
            destination.writestr(
                "manifest.json",
                json.dumps(manifest, sort_keys=True).encode("utf-8") + b"\n",
            )
            destination.writestr("payload/artifacts.jsonl", payload)

    def test_bundle_bounds_sequences_and_contains_untrusted_summary_ids(self):
        self.add_offline_peer(self.node_b, "node-alpha")
        self.append_round(self.node_a, "BOUNDARY")
        bundle = self.base / "boundary.mwxb"
        self.run_cli(
            self.node_a,
            "export-delta",
            "--target-node-id",
            "node-beta",
            "--output",
            str(bundle),
        )

        with zipfile.ZipFile(bundle, "r") as source:
            manifest = json.loads(source.read("manifest.json"))
            payload = source.read("payload/artifacts.jsonl")

        oversized = self.base / "oversized-range.mwxb"
        self.write_bundle(
            oversized,
            {**manifest, "to_event_sequence": 10**12},
            payload,
        )
        rejected = self.invoke_cli(
            self.node_b,
            "inspect-bundle",
            "--bundle",
            str(oversized),
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("range does not match", rejected.stderr)

        summary_payload = {
            "record": {
                "summary_id": "../../escape",
                "level": 1,
                "conversation_id": "codex:malicious",
                "concepts": [],
            },
            "content": "untrusted summary identifier",
        }
        artifact = {
            "event_sequence": 1,
            "artifact_type": "summary",
            "artifact_id": "summary:../../escape",
            "sha256": canonical_sha256(summary_payload),
            "payload": summary_payload,
        }
        malicious_payload = canonical_bytes(artifact) + b"\n"
        malicious_manifest = {
            "format": BUNDLE_FORMAT,
            "protocol_version": PROTOCOL_VERSION,
            "minimum_protocol_version": 1,
            "origin_node_id": "node-alpha",
            "target_node_id": "node-beta",
            "base_event_sequence": 0,
            "previous_bundle_sha256": None,
            "from_event_sequence": 1,
            "to_event_sequence": 1,
            "artifact_count": 1,
            "payload_path": "payload/artifacts.jsonl",
            "payload_bytes": len(malicious_payload),
            "payload_sha256": bytes_sha256(malicious_payload),
        }
        malicious = self.base / "malicious-summary-id.mwxb"
        self.write_bundle(malicious, malicious_manifest, malicious_payload)
        imported = self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(malicious),
            "--expected-node-id",
            "node-alpha",
        )
        self.assertEqual(imported["status"], "imported")
        peer_root = self.base / "node-b-federation-cache/peers/node-alpha"
        summary_files = list((peer_root / "summaries").glob("*.json"))
        self.assertEqual(len(summary_files), 1)
        self.assertTrue(summary_files[0].resolve().is_relative_to(peer_root.resolve()))
        self.assertFalse((peer_root.parent.parent / "escape.json").exists())

    def test_replica_directory_must_be_outside_primary_archive(self):
        config = load_simple_yaml(self.config)
        config["federation"]["replica_directory"] = str(self.node_a / "replicas")
        with self.assertRaisesRegex(ValueError, "separate from the primary archive"):
            FederationManager(MemoryStore(self.node_a, config))

    def test_ssh_transport_uses_strict_host_authentication_and_same_bundle(self):
        self.append_round(self.node_a, "SSH")
        source_manager = FederationManager(
            MemoryStore(self.node_a, load_simple_yaml(self.config))
        )
        bundle = self.base / "ssh-source.mwxb"
        source_manager.export_delta(bundle, target_node_id="node-beta")
        self.run_cli(
            self.node_b,
            "add-peer",
            "--node-id",
            "node-alpha",
            "--host",
            "archive.example",
            "--remote-root",
            "/srv/memory",
            "--remote-config",
            "/srv/skill/config.yaml",
            "--remote-cli",
            "/srv/skill/scripts/memory_cli.py",
        )
        target_manager = FederationManager(
            MemoryStore(self.node_b, load_simple_yaml(self.config))
        )
        fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=bundle.read_bytes(),
            stderr=b"",
        )
        with patch("memory_federation.subprocess.run", return_value=fake) as run:
            result = target_manager.sync_peer("node-alpha")
        self.assertEqual(result["status"], "imported")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ConnectTimeout=15", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 600)
        self.assertIn("archive.example", command)
        self.assertIn("export-delta", command[-1])
        self.assertIn("--output -", command[-1])

        self.run_cli(
            self.node_c,
            "add-peer",
            "--node-id",
            "node-alpha",
            "--host",
            "windows.example",
            "--remote-root",
            "C:/Memory/Archive",
            "--remote-config",
            "C:/Memory/Skill/config.yaml",
            "--remote-cli",
            "C:/Memory/Skill/scripts/memory_cli.py",
            "--remote-python",
            "python.exe",
            "--remote-shell",
            "powershell",
        )
        windows_manager = FederationManager(
            MemoryStore(self.node_c, load_simple_yaml(self.config))
        )
        windows_bundle = self.base / "ssh-windows-source.mwxb"
        source_manager.export_delta(
            windows_bundle,
            target_node_id="node-gamma",
        )
        windows_fake = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=windows_bundle.read_bytes(),
            stderr=b"",
        )
        with patch(
            "memory_federation.subprocess.run",
            return_value=windows_fake,
        ) as windows_run:
            windows_manager.sync_peer("node-alpha")
        windows_command = windows_run.call_args.args[0][-1]
        self.assertTrue(windows_command.startswith("& 'python.exe'"))
        self.assertIn("'C:/Memory/Skill/scripts/memory_cli.py'", windows_command)

        with patch(
            "memory_federation.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ssh", 600),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                target_manager.sync_peer("node-alpha")

    def test_global_retrieval_rechecks_replica_source_hash(self):
        self.add_offline_peer(self.node_b, "node-alpha")
        self.append_round(self.node_a, "VERIFY")
        bundle = self.base / "verify.mwxb"
        self.run_cli(self.node_a, "export-delta", "--output", str(bundle))
        self.run_cli(
            self.node_b,
            "import-delta",
            "--bundle",
            str(bundle),
            "--expected-node-id",
            "node-alpha",
        )
        replica = (
            self.base
            / "node-b-federation-cache/peers/node-alpha/raw-records.jsonl"
        )
        replica.write_text(
            replica.read_text(encoding="utf-8").replace(
                "VERIFY 讨论联邦记忆",
                "TAMPERED 讨论联邦记忆",
            ),
            encoding="utf-8",
        )
        rejected = self.invoke_cli(
            self.node_b,
            "retrieve-global",
            "--query",
            "VERIFY 联邦记忆",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Replica source hash mismatch", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
