import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import CloudFolderTransport
from memory_environment import revision_id_for
from memory_environment_exchange import EnvironmentExchangeManager
from memory_environment_incoming import EnvironmentIncomingProcessor
from memory_environment_skills import (
    EnvironmentSkillInstaller,
    skill_package_contract_bytes,
)
from memory_federation import FederationManager
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

    def register_and_cache_skill(self):
        files = {
            "SKILL.md": (
                "---\nname: demo-sync\ndescription: Synced test Skill\n---\n"
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
            "version": "1.0.0",
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
            "version": 1,
            "base_revision_id": None,
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
        package = self.base / "demo-sync.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "skill-package-manifest.json",
                json.dumps(manifest, sort_keys=True),
            )
            for path, payload in files.items():
                archive.writestr(path, payload)
        target_root = self.base / "global-skills"
        target_root.mkdir()
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
        self.assertEqual(len(published["published"]), 1)
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


if __name__ == "__main__":
    unittest.main()
