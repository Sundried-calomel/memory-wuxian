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
        self.assertFalse(self.processor().completed_root.exists())
        self.assertFalse(self.processor().batch_root.exists())
        self.assertEqual(self.processor().status()["staged_events"], 1)
        self.assertEqual(self.processor().status()["processed_events"], 0)
        second = self.processor().process(apply=True)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(second["results"][0]["decision"], "no-change")

    def test_no_change_entries_do_not_starve_newer_event(self):
        current = self.revision("same\n", 1)
        self.register(current, "same\n")
        self.stage(current, "same\n")
        newer = self.revision("newer\n", 2, base=current["revision_id"])
        self.stage(newer, "newer\n")
        first = self.processor().process(apply=True, maximum_events=1)
        self.assertEqual(
            [item["decision"] for item in first["results"]],
            ["no-change", "ready-fast-forward"],
        )
        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["examined"], 2)
        self.assertEqual(first["results"][1]["revision_id"], newer["revision_id"])
        self.assertFalse(
            (
                self.processor().completed_root
                / (
                    hashlib.sha256(
                        (
                            self.registry.staging_dir
                            / "incoming"
                            / "node-a"
                            / "00000000000000000001-shared.json"
                        ).read_bytes()
                    ).hexdigest()
                    + ".json"
                )
            ).exists()
        )

    def test_batch_failure_persists_completed_item_evidence(self):
        first = self.revision("first\n", 1)
        first_path = self.stage(first, "first\n")
        invalid_path = (
            self.registry.staging_dir
            / "incoming"
            / "node-z"
            / "00000000000000000002-invalid.json"
        )
        invalid_path.parent.mkdir(parents=True, exist_ok=True)
        invalid_path.write_text("{", encoding="utf-8")
        result = self.processor().process(apply=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 1)
        self.assertIn("JSONDecodeError", result["error"])
        evidence = Path(result["batch_evidence_path"])
        self.assertTrue(evidence.is_file())
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["completed_stage_sha256"],
            [hashlib.sha256(first_path.read_bytes()).hexdigest()],
        )
        self.assertEqual(self.processor().status()["processed_events"], 1)

    def test_zero_and_n_event_batches_are_deterministic(self):
        empty = self.processor().process(apply=True)
        self.assertEqual(empty["processed"], 0)
        for version in range(1, 4):
            content = f"remote-{version}\n"
            self.stage(self.revision(content, version), content)
        result = self.processor().process(apply=True, maximum_events=3)
        self.assertEqual(result["processed"], 3)
        self.assertEqual(
            [item["event_sequence"] for item in result["results"]],
            [1, 2, 3],
        )
        self.assertEqual(self.processor().status()["staged_events"], 0)

    def test_explicit_accept_registers_compatible_stage_without_installing(self):
        remote = self.revision("new\n", 1)
        path = self.stage(remote, "new\n")
        processed = self.processor().process(apply=True)
        self.assertEqual(processed["processed"], 1)
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
