import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import (
    AuthenticatedOpenResult,
    _AUTHENTICATED_OPEN_AUTHORITY,
)
from memory_environment_exchange import EnvironmentExchangeManager
from memory_federation import FederationManager
from memory_project_evidence import (
    MAX_FILE_BYTES,
    ProjectEvidenceExchangeManager,
    ProjectEvidenceStore,
)


def authenticated_import(manager, bundle):
    manifest = manager.read_bundle_manifest(bundle)
    return manager._import_authenticated_delta(
        bundle,
        expected_node_id=manifest["origin_node_id"],
        authenticated_open_result=AuthenticatedOpenResult(
            _AUTHENTICATED_OPEN_AUTHORITY,
            {
                "origin_node_id": manifest["origin_node_id"],
                "target_node_id": manager.node()["node_id"],
                "payload_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            },
        ),
    )


class ProjectEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config = load_simple_yaml(ROOT / "config.yaml")
        self.store_a = MemoryStore(self.base / "a", config)
        self.store_b = MemoryStore(self.base / "b", config)
        self.store_a.init()
        self.store_b.init()
        FederationManager(self.store_a).init_node("A", requested_node_id="node-a")
        FederationManager(self.store_b).init_node("B", requested_node_id="node-b")
        FederationManager(self.store_a).add_peer("node-b")
        FederationManager(self.store_b).add_peer("node-a")
        self.source = self.base / "project"
        self.source.mkdir()
        (self.source / "PROJECT_AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        (self.source / "status.txt").write_bytes(b"ready\r\n")

    def tearDown(self):
        self.temporary.cleanup()

    def spec(self, predecessor=None):
        value = {
            "schema_version": 1,
            "project_id": "project-alpha",
            "title": "Project Alpha evidence",
            "source_root": str(self.source),
            "files": [
                {"path": "PROJECT_AGENTS.md", "role": "project-rule"},
                {"path": "status.txt", "role": "status"},
            ],
        }
        if predecessor is not None:
            value["predecessor_generation_id"] = predecessor
        return value

    def test_build_is_explicit_immutable_and_exactly_reconstructable(self):
        store = ProjectEvidenceStore(self.store_a)
        expected_rules = (self.source / "PROJECT_AGENTS.md").read_bytes()
        expected_status = (self.source / "status.txt").read_bytes()
        preview = store.build(self.spec())
        self.assertEqual(preview["status"], "preview")
        recorded = store.build(self.spec(), apply=True)
        self.assertEqual(recorded["status"], "recorded")
        persisted = Path(recorded["path"]).read_text(encoding="utf-8")
        self.assertNotIn(str(self.source), persisted)
        self.assertEqual(store.build(self.spec(), apply=True)["status"], "no-change")
        destination = self.base / "restored"
        restored = store.reconstruct(recorded["generation_id"], destination, apply=True)
        self.assertEqual(restored["status"], "completed")
        self.assertEqual((destination / "PROJECT_AGENTS.md").read_bytes(), expected_rules)
        self.assertEqual((destination / "status.txt").read_bytes(), expected_status)
        matched = store.query("Rules", project_id="project-alpha", role="project-rule")
        self.assertEqual(len(matched["results"]), 1)
        self.assertEqual(matched["results"][0]["path"], "PROJECT_AGENTS.md")
        self.assertEqual(matched["results"][0]["authority"], "local")
        self.assertTrue(matched["results"][0]["is_current_head"])

    def test_reconstruct_conflict_has_no_partial_writes(self):
        store = ProjectEvidenceStore(self.store_a)
        generation = store.build(self.spec(), apply=True)["generation_id"]
        destination = self.base / "conflict"
        destination.mkdir()
        (destination / "status.txt").write_text("different", encoding="utf-8")
        result = store.reconstruct(generation, destination, apply=True)
        self.assertEqual(result["status"], "conflict")
        self.assertFalse((destination / "PROJECT_AGENTS.md").exists())

    def test_secret_and_unregistered_predecessor_are_rejected(self):
        store = ProjectEvidenceStore(self.store_a)
        (self.source / "status.txt").write_text("api_key=abcdefghijklmnop", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "probable secret"):
            store.build(self.spec())
        (self.source / "status.txt").write_text("ready", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "predecessor"):
            store.build(self.spec("project-evidence:" + "0" * 64))

    def test_independent_stream_imports_read_only_and_old_environment_is_unchanged(self):
        local = ProjectEvidenceStore(self.store_a).build(self.spec(), apply=True)
        manager_a = ProjectEvidenceExchangeManager(self.store_a)
        manager_b = ProjectEvidenceExchangeManager(self.store_b)
        bundle = self.base / "evidence.zip"
        exported = manager_a.export_delta(bundle, target_node_id="node-b")
        imported = authenticated_import(manager_b, bundle)
        self.assertEqual(exported["artifact_count"], 1)
        self.assertEqual(imported["imported"], 1)
        remote = ProjectEvidenceStore(self.store_b).list("project-alpha")["remote"]
        self.assertEqual(remote[0]["package"]["generation_id"], local["generation_id"])
        self.assertFalse(remote[0]["package"].get("automatic_activation", False))
        self.assertEqual(EnvironmentExchangeManager(self.store_b).status()["local_event_sequence"], 0)
        with self.assertRaises(TypeError):
            manager_b.import_delta(bundle)

    def test_corrupt_event_range_is_rejected_before_replica_write(self):
        ProjectEvidenceStore(self.store_a).build(self.spec(), apply=True)
        manager_a = ProjectEvidenceExchangeManager(self.store_a)
        manager_b = ProjectEvidenceExchangeManager(self.store_b)
        bundle = self.base / "valid.zip"
        manager_a.export_delta(bundle, target_node_id="node-b")
        corrupt = self.base / "corrupt.zip"
        with zipfile.ZipFile(bundle, "r") as source, zipfile.ZipFile(corrupt, "w") as target:
            manifest = json.loads(source.read("manifest.json"))
            payload = source.read(manifest["payload_path"])
            manifest["artifact_count"] = 2
            target.writestr("manifest.json", json.dumps(manifest))
            target.writestr(manifest["payload_path"], payload)
        with self.assertRaisesRegex(ValueError, "sequence manifest"):
            authenticated_import(manager_b, corrupt)
        self.assertEqual(ProjectEvidenceStore(self.store_b).list()["remote"], [])

    def test_predecessor_query_marks_old_generation_as_superseded(self):
        store = ProjectEvidenceStore(self.store_a)
        first = store.build(self.spec(), apply=True)
        (self.source / "status.txt").write_text("ready version two", encoding="utf-8")
        second = store.build(self.spec(first["generation_id"]), apply=True)
        results = store.query("Rules", project_id="project-alpha", role="project-rule")["results"]
        by_generation = {item["generation_id"]: item for item in results}
        self.assertTrue(by_generation[second["generation_id"]]["is_current_head"])
        self.assertFalse(by_generation[first["generation_id"]]["is_current_head"])
        self.assertEqual(
            by_generation[first["generation_id"]]["successor_generation_ids"],
            [second["generation_id"]],
        )

    def test_owner_refresh_is_explicit_idempotent_and_predecessor_linked(self):
        store = ProjectEvidenceStore(self.store_a)
        self.assertEqual(store.register_owner(self.spec())["status"], "preview")
        self.assertEqual(store.register_owner(self.spec(), apply=True)["status"], "recorded")
        self.assertEqual(store.register_owner(self.spec(), apply=True)["status"], "no-change")
        first = store.refresh_owner("project-alpha", apply=True)
        self.assertEqual(first["status"], "recorded")
        unchanged = store.refresh_owner("project-alpha", apply=True)
        self.assertEqual(unchanged["status"], "no-change")
        self.assertEqual(unchanged["persistent_mutations"], 0)
        (self.source / "status.txt").write_text("owner version two", encoding="utf-8")
        second = store.refresh_owner("project-alpha", apply=True)
        self.assertEqual(second["status"], "recorded")
        packages = store.list("project-alpha")["local"]
        by_id = {item["generation_id"]: item for item in packages}
        self.assertEqual(
            by_id[second["generation_id"]]["predecessor_generation_id"],
            first["generation_id"],
        )
        status = store.owner_status()
        self.assertEqual(status["count"], 1)
        self.assertEqual(status["owners"][0]["current_generation_id"], second["generation_id"])

    def test_owner_refresh_rejects_unstable_source(self):
        store = ProjectEvidenceStore(self.store_a)
        store.register_owner(self.spec(), apply=True)
        original = store._package_from_spec

        def mutate_after_read(spec):
            package = original(spec)
            (self.source / "status.txt").write_text("changed during refresh", encoding="utf-8")
            return package

        with mock.patch.object(store, "_package_from_spec", side_effect=mutate_after_read):
            with self.assertRaisesRegex(ValueError, "changed during refresh"):
                store.refresh_owner("project-alpha", apply=True)
        self.assertEqual(store.list("project-alpha")["local"], [])

    def test_owner_rejects_missing_linked_and_oversized_sources(self):
        store = ProjectEvidenceStore(self.store_a)
        missing = self.spec()
        missing["files"].append({"path": "missing.txt", "role": "status"})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            store.register_owner(missing)

        linked = self.source / "linked.txt"
        linked.symlink_to(self.source / "status.txt")
        linked_spec = self.spec()
        linked_spec["files"].append({"path": "linked.txt", "role": "status"})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            store.register_owner(linked_spec)

        oversized = self.source / "oversized.txt"
        with oversized.open("wb") as handle:
            handle.truncate(MAX_FILE_BYTES + 1)
        oversized_spec = self.spec()
        oversized_spec["files"].append({"path": "oversized.txt", "role": "status"})
        with self.assertRaisesRegex(ValueError, "exceeds the byte limit"):
            store.register_owner(oversized_spec)

    def test_owner_maintenance_restart_is_idempotent(self):
        first_process = ProjectEvidenceStore(self.store_a)
        first_process.register_owner(self.spec(), apply=True)
        first = first_process.refresh_owners(apply=True)
        self.assertEqual(first["created"], 1)

        restarted_process = ProjectEvidenceStore(self.store_a)
        second = restarted_process.refresh_owners(apply=True)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len(restarted_process.list("project-alpha")["local"]), 1)

    def test_rollback_boundary_preserves_existing_evidence(self):
        store = ProjectEvidenceStore(self.store_a)
        generation = store.build(self.spec(), apply=True)
        local_path = Path(generation["path"])
        before = local_path.read_bytes()

        store.register_owner(self.spec(), apply=True)
        self.assertEqual(local_path.read_bytes(), before)
        self.assertEqual(EnvironmentExchangeManager(self.store_a).status()["local_event_sequence"], 0)
        self.assertTrue(local_path.is_file())

    def test_imported_evidence_does_not_create_owner(self):
        ProjectEvidenceStore(self.store_a).register_owner(self.spec(), apply=True)
        ProjectEvidenceStore(self.store_a).refresh_owner("project-alpha", apply=True)
        manager_a = ProjectEvidenceExchangeManager(self.store_a)
        manager_b = ProjectEvidenceExchangeManager(self.store_b)
        bundle = self.base / "owner-evidence.zip"
        manager_a.export_delta(bundle, target_node_id="node-b")
        authenticated_import(manager_b, bundle)
        self.assertEqual(ProjectEvidenceStore(self.store_b).owner_status()["count"], 0)

    def test_bounded_owner_refresh_is_failure_isolated(self):
        store = ProjectEvidenceStore(self.store_a)
        store.register_owner(self.spec(), apply=True)
        bad_source = self.base / "bad-project"
        bad_source.mkdir()
        (bad_source / "status.txt").write_text("ready", encoding="utf-8")
        bad = {
            "schema_version": 1,
            "project_id": "project-bad",
            "title": "Bad project",
            "source_root": str(bad_source),
            "files": [{"path": "status.txt", "role": "status"}],
        }
        store.register_owner(bad, apply=True)
        (bad_source / "status.txt").unlink()
        result = store.refresh_owners(apply=True)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(len(store.list("project-alpha")["local"]), 1)


if __name__ == "__main__":
    unittest.main()
