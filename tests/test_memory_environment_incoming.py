import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment import EnvironmentRegistry, revision_id_for
from memory_environment_incoming import EnvironmentIncomingProcessor


class EnvironmentIncomingProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name) / "memory"
        self.registry = EnvironmentRegistry(self.archive)
        self.artifact = {
            "schema_version": 1,
            "artifact_id": "global-rule:shared",
            "object_class": "global-rule",
            "scope": "global",
            "project_id": None,
            "display_name": "Shared",
            "created_at": "2026-07-29T00:00:00+00:00",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def revision(self, content, version, base=None, platforms=None):
        digest = hashlib.sha256(content.encode()).hexdigest()
        value = {
            "schema_version": 1,
            "revision_id": "rev:" + "0" * 64,
            "artifact_id": self.artifact["artifact_id"],
            "origin_node_id": "node-a" if version == 1 else "node-b",
            "version": version,
            "base_revision_id": base,
            "content_sha256": digest,
            "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
            "supported_platforms": platforms or ["macos", "windows"],
            "runtime_requirements": {"python": ">=3.10"},
            "provenance": {"source": "test"},
            "lifecycle_state": "staged",
            "created_at": "2026-07-29T00:00:00+00:00",
        }
        value["revision_id"] = revision_id_for(value)
        return value

    def register(self, revision, content):
        self.registry.register(
            {
                "schema_version": 1,
                "artifacts": [
                    {
                        "artifact": self.artifact,
                        "revision": revision,
                        "content": content,
                    }
                ],
                "projects": [],
            },
            apply=True,
        )

    def stage(self, revision, content):
        origin = revision["origin_node_id"]
        path = (
            self.registry.staging_dir
            / "incoming"
            / origin
            / f"{revision['version']:020d}-shared.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": revision["version"],
                    "artifact": self.artifact,
                    "revision": revision,
                    "content_base64": base64.b64encode(content.encode()).decode(),
                    "package_attachment": None,
                    "received_bundle_id": "mwb-" + "a" * 32,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return path

    def processor(self):
        return EnvironmentIncomingProcessor(
            self.archive,
            platform="macos",
            runtime_versions={"python": "3.12.0"},
        )

    def test_new_artifact_is_reviewed_without_registry_mutation(self):
        remote = self.revision("remote\n", 2)
        self.stage(remote, "remote\n")
        result = self.processor().process(apply=True)
        self.assertEqual(result["results"][0]["decision"], "pending-review")
        self.assertEqual(result["results"][0]["reason"], "new-artifact")
        self.assertEqual(self.registry.list(), [])

    def test_fast_forward_rule_can_be_registered_only_with_explicit_policy(self):
        local = self.revision("local\n", 1)
        self.register(local, "local\n")
        remote = self.revision("remote\n", 2, base=local["revision_id"])
        self.stage(remote, "remote\n")
        preview = self.processor().process()
        self.assertEqual(preview["results"][0]["decision"], "ready-fast-forward")
        self.assertEqual(
            self.registry.show(self.artifact["artifact_id"])["revision"]["revision_id"],
            local["revision_id"],
        )
        applied = self.processor().process(
            apply=True, auto_register_compatible_rules=True
        )
        self.assertEqual(
            applied["results"][0]["decision"], "registered-fast-forward"
        )
        self.assertEqual(
            self.registry.show(self.artifact["artifact_id"])["revision"]["revision_id"],
            remote["revision_id"],
        )

    def test_divergence_queues_one_effective_conflict(self):
        local = self.revision("local\n", 1)
        self.register(local, "local\n")
        remote = self.revision("remote\n", 2, base="rev:" + "f" * 64)
        self.stage(remote, "remote\n")
        result = self.processor().process(apply=True)
        self.assertEqual(result["results"][0]["decision"], "conflict")
        self.assertEqual(self.processor().status()["pending_conflicts"], 1)

    def test_platform_incompatibility_never_registers(self):
        local = self.revision("local\n", 1)
        self.register(local, "local\n")
        remote = self.revision(
            "remote\n", 2, base=local["revision_id"], platforms=["windows"]
        )
        self.stage(remote, "remote\n")
        result = self.processor().process(
            apply=True, auto_register_compatible_rules=True
        )
        self.assertEqual(result["results"][0]["reason"], "platform-incompatible")
        self.assertFalse(result["results"][0]["registered"])

    def test_already_current_creates_no_decision_file(self):
        remote = self.revision("same\n", 1)
        self.register(remote, "same\n")
        self.stage(remote, "same\n")
        result = self.processor().process(apply=True)
        self.assertEqual(result["results"][0]["decision"], "no-change")
        self.assertFalse(self.processor().decisions_root.exists())

    def test_explicit_accept_registers_compatible_stage_without_installing(self):
        remote = self.revision("new\n", 1)
        path = self.stage(remote, "new\n")
        stage_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        preview = self.processor().accept(stage_hash)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.registry.list(), [])
        accepted = self.processor().accept(stage_hash, apply=True)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(
            self.registry.show(self.artifact["artifact_id"])["revision"]["revision_id"],
            remote["revision_id"],
        )
        self.assertTrue(Path(accepted["acceptance_path"]).is_file())

    def test_explicit_accept_rejects_divergent_stage(self):
        local = self.revision("local\n", 1)
        self.register(local, "local\n")
        remote = self.revision("remote\n", 2, base="rev:" + "f" * 64)
        path = self.stage(remote, "remote\n")
        stage_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "diverges"):
            self.processor().accept(stage_hash, apply=True)


if __name__ == "__main__":
    unittest.main()
