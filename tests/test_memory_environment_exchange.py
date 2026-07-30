import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import CloudFolderTransport, filesystem_native_path
from memory_environment import revision_id_for
from memory_environment_exchange import EnvironmentExchangeManager
from memory_environment_incoming import EnvironmentIncomingProcessor
from memory_environment_skills import (
    EnvironmentSkillInstaller,
    skill_package_contract_bytes,
)
from memory_federation import FederationManager, canonical_sha256
from tests.test_memory_cloud_transport import FakeCrypto


class EnvironmentExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config = load_simple_yaml(ROOT / "config.yaml")
        self.store_a = MemoryStore(self.base / "a", self.config)
        self.store_b = MemoryStore(self.base / "b", self.config)
        self.store_a.init()
        self.store_b.init()
        FederationManager(self.store_a).init_node(
            "A", requested_node_id="node-a"
        )
        FederationManager(self.store_b).init_node(
            "B", requested_node_id="node-b"
        )
        FederationManager(self.store_a).add_peer("node-b")
        FederationManager(self.store_b).add_peer("node-a")
        self.a = EnvironmentExchangeManager(self.store_a)
        self.b = EnvironmentExchangeManager(self.store_b)

    def tearDown(self):
        native_base = str(self.base.resolve())
        if os.name == "nt":
            native_base = "\\\\?\\" + native_base
        shutil.rmtree(native_base, ignore_errors=True)
        self.temporary.cleanup()

    def register_rule(self):
        content = "shared rule\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": "global-rule:shared",
            "origin_node_id": "node-a",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {},
            "provenance": {"source": "test"},
            "lifecycle_state": "staged",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "artifact": {
                            "schema_version": 1,
                            "artifact_id": "global-rule:shared",
                            "object_class": "global-rule",
                            "scope": "global",
                            "project_id": None,
                            "display_name": "Shared",
                            "created_at": "2026-07-28T12:00:00+00:00",
                        },
                        "revision": revision,
                        "content": content,
                    }
                ],
                "projects": [],
            },
            apply=True,
        )

    def rule_item(self, name):
        content = f"{name} rule\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        artifact_id = f"global-rule:{name}"
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact_id,
            "origin_node_id": "node-a",
            "version": 1,
            "base_revision_id": None,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {},
            "provenance": {"source": "test"},
            "lifecycle_state": "staged",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        return {
            "artifact": {
                "schema_version": 1,
                "artifact_id": artifact_id,
                "object_class": "global-rule",
                "scope": "global",
                "project_id": None,
                "display_name": name,
                "created_at": "2026-07-28T12:00:00+00:00",
            },
            "revision": revision,
            "content": content,
        }

    def register_and_cache_skill(
        self,
        *,
        version=1,
        base_revision_id=None,
        marker="v1",
    ):
        files = {
            "SKILL.md": (
                "---\nname: demo-sync\n"
                f"description: Synced test Skill {marker}\n---\n"
            ).encode(),
            "agents/openai.yaml": (
                "interface:\n"
                '  display_name: "Demo Sync"\n'
                '  short_description: "Synced test"\n'
                '  default_prompt: "Use $demo-sync for this task."\n'
            ).encode(),
        }
        manifest = {
            "schema_version": 1,
            "skill_id": "demo-sync",
            "version": f"{version}.0.0",
            "scope": "global",
            "project_id": None,
            "source_revision": "rev:" + "0" * 64,
            "files": [
                {
                    "path": path,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "executable": False,
                }
                for path, payload in sorted(files.items())
            ],
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.10"},
            "network_access": {"enabled": False, "destinations": []},
            "persistent_components": [],
            "checks": [{"type": "utf8", "path": "SKILL.md"}],
            "rollback": {"strategy": "one-verified-version"},
        }
        contract = skill_package_contract_bytes(manifest)
        digest = hashlib.sha256(contract).hexdigest()
        artifact = {
            "schema_version": 1,
            "artifact_id": "global-skill:demo-sync",
            "object_class": "global-skill",
            "scope": "global",
            "project_id": None,
            "display_name": "Demo Sync",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": artifact["artifact_id"],
            "origin_node_id": "node-a",
            "version": version,
            "base_revision_id": base_revision_id,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.10"},
            "provenance": {"source": "test-package"},
            "lifecycle_state": "staged",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        revision["revision_id"] = revision_id_for(revision)
        manifest["source_revision"] = revision["revision_id"]
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "artifact": artifact,
                        "revision": revision,
                        "content": contract.decode("utf-8"),
                    }
                ],
                "projects": [],
            },
            apply=True,
        )
        package = self.base / f"demo-sync-{version}.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "skill-package-manifest.json",
                json.dumps(manifest, sort_keys=True),
            )
            for path, payload in files.items():
                archive.writestr(path, payload)
        target_root = self.base / "global-skills"
        target_root.mkdir(exist_ok=True)
        EnvironmentSkillInstaller(
            self.a.registry,
            target_node_id="node-a",
            platform="macos",
            runtime_versions={"python": "3.12.0"},
            global_skill_bindings={
                "global-demo-sync": {
                    "skill_id": "demo-sync",
                    "root": str(target_root),
                    "relative_path": "demo-sync",
                    "enabled": True,
                    "pinned_version": None,
                }
            },
        ).install(
            package_path=package,
            artifact_id=artifact["artifact_id"],
            revision_id=revision["revision_id"],
            target_binding="global-demo-sync",
            apply=True,
        )
        return revision

    def test_superseded_uncached_skill_does_not_block_current_package(self):
        old_revision = self.register_and_cache_skill(version=1, marker="old")
        old_reference = (
            self.a.registry.root
            / "packages"
            / "by-revision"
            / f"{old_revision['revision_id'].split(':', 1)[1]}.json"
        )
        old_reference.unlink()
        current_revision = self.register_and_cache_skill(
            version=2,
            base_revision_id=old_revision["revision_id"],
            marker="current",
        )
        bundle = self.base / "superseded-skill.mwxb"
        exported = self.a.export_delta(bundle, target_node_id="node-b")
        self.assertEqual(exported["artifact_count"], 1)
        self.assertEqual(exported["from_event_sequence"], 1)
        self.assertEqual(exported["to_event_sequence"], 1)
        _, records = self.a.read_bundle(bundle)
        self.assertEqual(
            records[0]["payload"]["revision"]["revision_id"],
            current_revision["revision_id"],
        )

    def test_current_uncached_skill_still_fails_closed(self):
        revision = self.register_and_cache_skill()
        reference = (
            self.a.registry.root
            / "packages"
            / "by-revision"
            / f"{revision['revision_id'].split(':', 1)[1]}.json"
        )
        reference.unlink()
        with self.assertRaisesRegex(
            ValueError, "no verified package attachment"
        ):
            self.a.export_delta(
                self.base / "current-uncached.mwxb",
                target_node_id="node-b",
            )

    def test_independent_delta_stages_remote_without_installing(self):
        self.register_rule()
        bundle = self.base / "environment.mwxb"
        exported = self.a.export_delta(bundle, target_node_id="node-b")
        self.assertEqual(exported["status"], "created")
        imported = self.b.import_delta(bundle, expected_node_id="node-a")
        self.assertEqual(imported["status"], "imported")
        self.assertEqual(imported["staged_artifacts"], 1)
        self.assertEqual(self.b.registry.list(), [])
        staged = list(
            (self.b.registry.staging_dir / "incoming" / "node-a").glob("*.json")
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(
            json.loads(staged[0].read_text())["stream_id"], "environment-v1"
        )
        marker = next(
            (
                self.b.registry.root
                / "replicas"
                / "peers"
                / "node-a"
                / "transactions"
            ).glob("*/transaction.json")
        )
        marker_text = marker.read_text(encoding="utf-8")
        self.assertIn('"status": "committed"', marker_text)
        self.assertNotIn("content_base64", marker_text)

    def test_uncommitted_transaction_is_invisible_to_incoming_processor(self):
        self.register_rule()
        bundle = self.base / "environment-uncommitted.mwxb"
        self.a.export_delta(bundle, target_node_id="node-b")
        original = __import__(
            "memory_environment_exchange"
        ).atomic_write_json

        def fail_receipt(path, value):
            if "receipts" in path.parts:
                raise OSError("injected receipt failure")
            return original(path, value)

        with mock.patch(
            "memory_environment_exchange.atomic_write_json",
            side_effect=fail_receipt,
        ):
            with self.assertRaisesRegex(OSError, "injected receipt failure"):
                self.b.import_delta(bundle, expected_node_id="node-a")
        processor = EnvironmentIncomingProcessor(
            self.store_b.root,
            platform="macos",
            runtime_versions={"python": "3.12.0"},
        )
        self.assertEqual(processor.status()["staged_events"], 0)
        self.assertEqual(self.b.replica_state("node-a")["last_event_sequence"], 0)

    def test_receipt_failure_can_be_retried_without_sequence_gap(self):
        self.register_rule()
        bundle = self.base / "environment-retry.mwxb"
        self.a.export_delta(bundle, target_node_id="node-b")
        original = __import__(
            "memory_environment_exchange"
        ).atomic_write_json
        failed = False

        def fail_once(path, value):
            nonlocal failed
            if "receipts" in path.parts and not failed:
                failed = True
                raise OSError("injected receipt failure")
            return original(path, value)

        with mock.patch(
            "memory_environment_exchange.atomic_write_json",
            side_effect=fail_once,
        ):
            with self.assertRaisesRegex(OSError, "injected receipt failure"):
                self.b.import_delta(bundle, expected_node_id="node-a")
        imported = self.b.import_delta(bundle, expected_node_id="node-a")
        self.assertEqual(imported["status"], "imported")
        self.assertEqual(self.b.replica_state("node-a")["last_event_sequence"], 1)
        staged = list(
            (self.b.registry.staging_dir / "incoming" / "node-a").glob("*.json")
        )
        self.assertEqual(len(staged), 1)

    def test_batch_registration_exports_every_artifact_with_unique_identity(self):
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [self.rule_item("one"), self.rule_item("two")],
                "projects": [],
            },
            apply=True,
        )
        bundle = self.base / "batch.mwxb"
        exported = self.a.export_delta(bundle, target_node_id="node-b")
        self.assertEqual(exported["artifact_count"], 2)
        _, records = self.a.read_bundle(bundle)
        self.assertEqual(len({item["source_event_id"] for item in records}), 2)
        self.assertEqual(
            {item["artifact_id"] for item in records},
            {"global-rule:one", "global-rule:two"},
        )
        self.assertEqual(
            self.a.export_delta(
                self.base / "empty.mwxb",
                after_event_sequence=2,
                previous_bundle_sha256=exported["sha256"],
                target_node_id="node-b",
            )["status"],
            "no-change",
        )

    def test_zero_artifact_delta_remains_no_change(self):
        result = self.a.export_delta(
            self.base / "zero.mwxb",
            target_node_id="node-b",
        )
        self.assertEqual(result["status"], "no-change")

    def test_legacy_transaction_key_exports_only_missing_batch_items(self):
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [self.rule_item("legacy-one"), self.rule_item("legacy-two")],
                "projects": [],
            },
            apply=True,
        )
        registry = self.a.registry._read_registry()
        first = registry["events"][0]
        self.a.init_layout()
        artifact = self.a.registry._read_relative_json(
            first["artifact_path"], "artifact_path"
        )
        revision = self.a.registry._read_relative_json(
            first["revision_path"], "revision_path"
        )
        content = self.a.registry._resolve_relative(
            revision["object_path"], "object_path"
        ).read_bytes()
        payload = {
            "artifact": artifact,
            "revision": revision,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "package_attachment": None,
        }
        legacy = {
            "event_sequence": 1,
            "source_event_id": "recovered-artifact-revision-0001",
            "event_kind": "artifact-revision",
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["revision_id"],
            "payload_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        self.a.export_ledger_path.write_text(
            json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.a.export_state_path.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "next_event_sequence": 2,
                    "source_events": {first["event_id"]: 1},
                }
            ),
            encoding="utf-8",
        )
        ledger = self.a.refresh_export_ledger()
        self.assertEqual(len(ledger), 2)
        self.assertEqual(ledger[1]["artifact_id"], "global-rule:legacy-two")

    def test_project_registration_is_replicated_read_only(self):
        project = {
            "schema_version": 1,
            "project_id": "project-one",
            "display_name": "Project One",
            "local_root": "C:/source/project-one",
            "active": True,
            "rule_bindings": [],
            "skill_bindings": [],
        }
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [],
                "projects": [project],
            },
            apply=True,
        )
        bundle = self.base / "project.mwxb"
        exported = self.a.export_delta(bundle, target_node_id="node-b")
        self.assertEqual(exported["artifact_count"], 1)
        imported = self.b.import_delta(bundle, expected_node_id="node-a")
        self.assertEqual(imported["staged_projects"], 1)
        self.assertEqual(self.b.registry.list(), [])
        replica = next(
            (self.b.registry.root / "replicas" / "peers" / "node-a" / "projects").glob(
                "*.json"
            )
        )
        value = json.loads(replica.read_text(encoding="utf-8"))
        self.assertFalse(value["automatic_registration"])
        self.assertEqual(value["project"], project)

    def test_environment_cursor_is_independent_from_archive_cursor(self):
        self.register_rule()
        bundle = self.base / "environment.mwxb"
        self.a.export_delta(bundle, target_node_id="node-b")
        self.b.import_delta(bundle, expected_node_id="node-a")
        environment_state = self.b.replica_state("node-a")
        archive_state = FederationManager(self.store_b).replica_state("node-a")
        self.assertEqual(environment_state["last_event_sequence"], 1)
        self.assertEqual(archive_state["last_event_sequence"], 0)

    def test_skill_delta_carries_verified_package_only_into_staging(self):
        revision = self.register_and_cache_skill()
        bundle = self.base / "skill-environment.mwxb"
        self.a.export_delta(bundle, target_node_id="node-b")
        imported = self.b.import_delta(bundle, expected_node_id="node-a")
        self.assertEqual(imported["staged_artifacts"], 1)
        staged = next(
            (self.b.registry.staging_dir / "incoming" / "node-a").glob("*.json")
        )
        record = json.loads(staged.read_text())
        self.assertEqual(record["revision"]["revision_id"], revision["revision_id"])
        self.assertIsNotNone(record["package_attachment"])
        self.assertEqual(self.b.registry.list(), [])
        stage_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        accepted = EnvironmentIncomingProcessor(
            self.store_b.root,
            platform="macos",
            runtime_versions={"python": "3.12.0"},
        ).accept(stage_hash, apply=True)
        self.assertTrue(Path(accepted["package_path"]).is_file())
        self.assertEqual(
            self.b.registry.show("global-skill:demo-sync")["revision"]["revision_id"],
            revision["revision_id"],
        )

    def test_predecessor_gap_and_tamper_fail_closed(self):
        self.register_rule()
        bundle = self.base / "environment.mwxb"
        self.a.export_delta(bundle, target_node_id="node-b")
        raw = bytearray(bundle.read_bytes())
        raw[-8] ^= 1
        tampered = self.base / "tampered.mwxb"
        tampered.write_bytes(raw)
        with self.assertRaises(Exception):
            self.b.import_delta(tampered, expected_node_id="node-a")

    def test_excessive_zip_compression_ratio_is_rejected_before_payload_read(self):
        bundle = self.base / "compressed-bomb.mwxb"
        with zipfile.ZipFile(
            bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("payload/environment.jsonl", b"0" * (2 * 1024 * 1024))
        with self.assertRaisesRegex(ValueError, "compression ratio"):
            self.b.read_bundle_manifest(bundle)

    def test_cloud_stream_is_encrypted_and_cursor_isolated(self):
        exchange = self.base / "cloud"
        exchange.mkdir()
        key_a = self.base / "a.identity"
        key_b = self.base / "b.identity"
        key_a.write_text("a", encoding="utf-8")
        key_b.write_text("b", encoding="utf-8")
        identity_a = {
            "encryption_public_key": "age-node-a",
            "signing_public_key": "ed25519-node-a",
            "fingerprint": "a" * 64,
        }
        identity_b = {
            "encryption_public_key": "age-node-b",
            "signing_public_key": "ed25519-node-b",
            "fingerprint": "b" * 64,
        }
        crypto = FakeCrypto({key_a: identity_a, key_b: identity_b})
        for manager, peer_id, identity in (
            (FederationManager(self.store_a), "node-b", identity_b),
            (FederationManager(self.store_b), "node-a", identity_a),
        ):
            peer_path = manager.peer_path(peer_id)
            peer = json.loads(peer_path.read_text())
            peer["cloud_identity"] = identity
            peer_path.write_text(json.dumps(peer), encoding="utf-8")
        cloud_a = CloudFolderTransport(
            self.a,
            crypto=crypto,
            config_path=self.a.metadata_root / "cloud.json",
            stream_id="environment-v1",
        )
        cloud_b = CloudFolderTransport(
            self.b,
            crypto=crypto,
            config_path=self.b.metadata_root / "cloud.json",
            stream_id="environment-v1",
        )
        cloud_a.configure(exchange, key_a, enabled=True)
        cloud_b.configure(exchange, key_b, enabled=True)
        self.register_rule()
        published = cloud_a.sync(force=True, now=1000)
        self.assertEqual(len(published["published"]), 1, published)
        envelope = Path(published["published"][0]["path"])
        self.assertIn("environment-v1", str(envelope))
        self.assertNotIn("shared rule", envelope.read_text(encoding="utf-8"))
        imported = cloud_b.sync(force=True, now=1001)
        self.assertEqual(len(imported["imports"]), 1)
        self.assertEqual(self.b.replica_state("node-a")["last_event_sequence"], 1)
        self.assertEqual(
            FederationManager(self.store_b).replica_state("node-a")[
                "last_event_sequence"
            ],
            0,
        )

    def test_cloud_stream_recovers_verified_overlapping_prefix(self):
        exchange = self.base / "cloud-overlap"
        exchange.mkdir()
        key_a = self.base / "overlap-a.identity"
        key_b = self.base / "overlap-b.identity"
        key_a.write_text("a", encoding="utf-8")
        key_b.write_text("b", encoding="utf-8")
        identity_a = {
            "encryption_public_key": "age-overlap-a",
            "signing_public_key": "ed25519-overlap-a",
            "fingerprint": "c" * 64,
        }
        identity_b = {
            "encryption_public_key": "age-overlap-b",
            "signing_public_key": "ed25519-overlap-b",
            "fingerprint": "d" * 64,
        }
        crypto = FakeCrypto({key_a: identity_a, key_b: identity_b})
        for manager, peer_id, identity in (
            (FederationManager(self.store_a), "node-b", identity_b),
            (FederationManager(self.store_b), "node-a", identity_a),
        ):
            peer_path = manager.peer_path(peer_id)
            peer = json.loads(peer_path.read_text())
            peer["cloud_identity"] = identity
            peer_path.write_text(json.dumps(peer), encoding="utf-8")
        cloud_a = CloudFolderTransport(
            self.a,
            crypto=crypto,
            config_path=self.a.metadata_root / "overlap-cloud.json",
            stream_id="environment-v1",
        )
        cloud_b = CloudFolderTransport(
            self.b,
            crypto=crypto,
            config_path=self.b.metadata_root / "overlap-cloud.json",
            stream_id="environment-v1",
        )
        cloud_a.configure(exchange, key_a, enabled=True)
        cloud_b.configure(exchange, key_b, enabled=True)
        self.register_rule()
        first = cloud_a.sync(force=True, now=2000)
        self.assertEqual(len(first["published"]), 1, first)
        first_envelope = Path(first["published"][0]["path"])
        cloud_b.sync(force=True, now=2001)
        first_cursor = self.b.replica_state("node-a")["last_event_sequence"]
        self.b._replica_events_path("node-a").unlink()

        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [self.rule_item("second")],
                "projects": [],
            },
            apply=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "overlap.mwxb"
            exported = self.a.export_delta(
                bundle,
                after_event_sequence=0,
                target_node_id="node-b",
            )
            overlap_envelope = cloud_a._outbox("node-b") / (
                f"{int(exported['from_event_sequence']):020d}-"
                f"{int(exported['to_event_sequence']):020d}-"
                f"{exported['bundle_id']}-{exported['sha256']}.mwxe"
            )
            cloud_a._atomic_seal(
                bundle,
                overlap_envelope,
                [
                    identity_a["encryption_public_key"],
                    identity_b["encryption_public_key"],
                ],
                "environment-v1-bundle",
                "node-b",
            )
            overlap_envelope = Path(filesystem_native_path(overlap_envelope))
        first_envelope.touch()
        overlap_envelope.touch()

        recovered = cloud_b.sync(force=False, now=2010)

        self.assertGreater(exported["to_event_sequence"], first_cursor)
        self.assertEqual(recovered["quarantined"], [])
        self.assertTrue(
            any(
                item["status"] == "imported"
                and item["bundle_id"] == exported["bundle_id"]
                for item in recovered["imports"]
            )
        )
        self.assertEqual(
            self.b.replica_state("node-a")["last_event_sequence"],
            exported["to_event_sequence"],
        )
        receipt = json.loads(
            (
                self.b._peer_root("node-a")
                / "receipts"
                / f"{exported['bundle_id']}.json"
            ).read_text()
        )
        self.assertTrue(receipt["overlap_recovery"])

    def test_overlap_recovery_rejects_conflicting_persisted_prefix(self):
        self.register_rule()
        first_bundle = self.base / "first.mwxb"
        self.a.export_delta(first_bundle, target_node_id="node-b")
        self.b.import_delta(first_bundle, expected_node_id="node-a")
        self.b._replica_events_path("node-a").unlink()
        staged = next(
            (
                self.b.registry.staging_dir / "incoming" / "node-a"
            ).glob("*.json")
        )
        staged_record = json.loads(staged.read_text())
        staged_record["content_base64"] = base64.b64encode(
            b"conflicting local prefix\n"
        ).decode("ascii")
        staged.write_text(json.dumps(staged_record), encoding="utf-8")
        self.a.registry.register(
            {
                "schema_version": 1,
                "artifacts": [self.rule_item("second-conflict")],
                "projects": [],
            },
            apply=True,
        )
        overlap_bundle = self.base / "overlap-conflict.mwxb"
        self.a.export_delta(
            overlap_bundle,
            after_event_sequence=0,
            target_node_id="node-b",
        )

        with self.assertRaisesRegex(ValueError, "overlap conflicts"):
            self.b.import_delta(overlap_bundle, expected_node_id="node-a")

    def test_status_uses_environment_sync_log_not_archive_log(self):
        FederationManager(self.store_a).log_sync(
            "cloud-folder-sync",
            "node-b",
            {"published": 9, "imported": 8, "acknowledged": 7},
        )
        self.a.log_sync(
            "cloud-folder-sync",
            "node-b",
            {"published": 1, "imported": 2, "acknowledged": 3},
        )

        recent = self.a.status()["recent_sync"]

        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["published"], 1)
        self.assertEqual(recent[0]["imported"], 2)
        self.assertEqual(recent[0]["acknowledged"], 3)


if __name__ == "__main__":
    unittest.main()
