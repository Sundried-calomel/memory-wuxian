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

import memory_environment_exchange
import memory_federation
import memory_project_attachments
import memory_project_evidence
from memory_cli import MemoryStore, load_simple_yaml
from memory_cloud_transport import (
    AuthenticatedOpenResult,
    _AUTHENTICATED_OPEN_AUTHORITY,
)
from memory_environment_exchange import EnvironmentExchangeManager
from memory_federation import FederationManager, canonical_sha256
from memory_project_attachments import (
    ProjectAttachmentExchangeManager,
    ProjectAttachmentStore,
)
from memory_project_evidence import (
    ProjectEvidenceExchangeManager,
    ProjectEvidenceStore,
)
from tests import test_memory_environment_exchange as environment_test_support
from tests import test_memory_project_attachments as attachment_test_support
from tests import test_memory_project_evidence as evidence_test_support


SNAPSHOT = json.loads(
    (ROOT / "tests" / "fixtures" / "exchange-contract-v218.json").read_text(
        encoding="utf-8"
    )
)
FIXED_TIME = "2026-08-19T12:00:00+00:00"


def authenticated_result(bundle, origin, target):
    return AuthenticatedOpenResult(
        _AUTHENTICATED_OPEN_AUTHORITY,
        {
            "origin_node_id": origin,
            "target_node_id": target,
            "payload_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        },
    )


def import_environment(manager, bundle, *, target="node-b"):
    return manager._import_authenticated_delta(
        bundle,
        expected_node_id="node-a",
        authenticated_open_result=authenticated_result(bundle, "node-a", target),
    )


def tamper_payload(source, destination, payload_path):
    with zipfile.ZipFile(source, "r") as incoming:
        files = {name: incoming.read(name) for name in incoming.namelist()}
    payload = bytearray(files[payload_path])
    payload[max(0, len(payload) - 2)] ^= 1
    files[payload_path] = bytes(payload)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, content in files.items():
            output.writestr(name, content)


class ExchangeContractSnapshotTests(unittest.TestCase):
    maxDiff = None

    def test_authenticated_binding_is_single_use(self):
        with tempfile.TemporaryDirectory() as raw:
            bundle = Path(raw) / "binding.bin"
            bundle.write_bytes(b"exchange-contract")
            result = authenticated_result(bundle, "node-a", "node-b")
            self.assertEqual(
                result.consume_environment_binding(),
                (
                    "node-a",
                    "node-b",
                    hashlib.sha256(bundle.read_bytes()).hexdigest(),
                ),
            )
            with self.assertRaisesRegex(ValueError, "already consumed"):
                result.consume_environment_binding()

    def assert_manifest_contract(self, stream, manifest, *, base, predecessor):
        contract = SNAPSHOT["streams"][stream]
        expected_fields = set(SNAPSHOT["common_manifest_fields"])
        expected_fields.update(contract["manifest_only_fields"])
        self.assertEqual(set(manifest), expected_fields)
        self.assertEqual(manifest["payload_path"], contract["payload_path"])
        self.assertEqual(manifest["base_event_sequence"], base)
        self.assertEqual(manifest["from_event_sequence"], base + 1)
        self.assertGreaterEqual(manifest["to_event_sequence"], base + 1)
        self.assertEqual(
            manifest["artifact_count"],
            manifest["to_event_sequence"] - manifest["from_event_sequence"] + 1,
        )
        self.assertEqual(manifest["previous_bundle_sha256"], predecessor)
        identity = {key: value for key, value in manifest.items() if key != "bundle_id"}
        self.assertEqual(
            manifest["bundle_id"], "mwb-" + canonical_sha256(identity)[:32]
        )
        self.assertRegex(manifest["payload_sha256"], r"^[0-9a-f]{64}$")

    def new_pair(self, base):
        config = load_simple_yaml(ROOT / "config.yaml")
        source = MemoryStore(base / "node-a", config)
        receiver = MemoryStore(base / "node-b", config)
        source.init()
        receiver.init()
        source_federation = FederationManager(source)
        receiver_federation = FederationManager(receiver)
        source_federation.init_node("A", requested_node_id="node-a")
        receiver_federation.init_node("B", requested_node_id="node-b")
        source_federation.add_peer("node-b")
        receiver_federation.add_peer("node-a")
        return source, receiver

    def assert_other_streams_initial(self, store, *, except_stream):
        managers = {
            "archive-v1": FederationManager(store),
            "environment-v1": EnvironmentExchangeManager(store),
            "project-evidence-v1": ProjectEvidenceExchangeManager(store),
            "project-attachment-v1": ProjectAttachmentExchangeManager(store),
        }
        for stream, manager in managers.items():
            if stream != except_stream:
                self.assertEqual(
                    manager.replica_state("node-a")["last_event_sequence"],
                    0,
                    stream,
                )

    def test_archive_manifest_cursor_identity_replay_gap_tamper_and_isolation(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source, receiver = self.new_pair(base)
            source.append_message(
                "user", "归档 ¥ first", FIXED_TIME, "codex:r4", "r4-u", None, False
            )
            source.append_message(
                "assistant",
                "归档 second",
                "2026-08-19T12:00:01+00:00",
                "codex:r4",
                "r4-a",
                "r4-u",
                False,
            )
            sender = FederationManager(source)
            target = FederationManager(receiver)
            first_path = base / "archive-first.mwxb"
            with mock.patch.object(memory_federation, "MAX_ARTIFACTS", 1):
                first = sender.export_delta(first_path, target_node_id="node-b")
            first_manifest = sender.read_bundle(first_path)[0]
            self.assert_manifest_contract(
                "archive-v1", first_manifest, base=0, predecessor=None
            )
            second_path = base / "archive-second.mwxb"
            with mock.patch.object(memory_federation, "MAX_ARTIFACTS", 1):
                sender.export_delta(
                    second_path,
                    first["to_event_sequence"],
                    target_node_id="node-b",
                    previous_bundle_sha256=first["sha256"],
                )
            second_manifest = sender.read_bundle(second_path)[0]
            self.assert_manifest_contract(
                "archive-v1",
                second_manifest,
                base=first["to_event_sequence"],
                predecessor=first["sha256"],
            )

            with self.assertRaisesRegex(ValueError, "gap"):
                target.import_delta(second_path, expected_node_id="node-a")
            self.assertEqual(target.replica_state("node-a")["last_event_sequence"], 0)
            imported = target.import_delta(first_path, expected_node_id="node-a")
            self.assertEqual(imported["status"], "imported")
            replay = target.import_delta(first_path, expected_node_id="node-a")
            self.assertEqual(replay["status"], SNAPSHOT["streams"]["archive-v1"]["replay_status"])
            self.assert_other_streams_initial(receiver, except_stream="archive-v1")

            wrong_target = base / "archive-wrong-target.mwxb"
            sender.export_delta(wrong_target, target_node_id="node-c")
            fresh_store = self.new_pair(base / "wrong-target")[1]
            with self.assertRaisesRegex(ValueError, "another node"):
                FederationManager(fresh_store).import_delta(
                    wrong_target, expected_node_id="node-a"
                )
            self.assertEqual(
                FederationManager(fresh_store).replica_state("node-a")["last_event_sequence"],
                0,
            )

            corrupted = base / "archive-tampered.mwxb"
            tamper_payload(first_path, corrupted, first_manifest["payload_path"])
            tamper_store = self.new_pair(base / "tamper")[1]
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                FederationManager(tamper_store).import_delta(
                    corrupted, expected_node_id="node-a"
                )
            self.assertEqual(
                FederationManager(tamper_store).replica_state("node-a")[
                    "last_event_sequence"
                ],
                0,
            )

    def test_environment_manifest_cursor_binding_replay_gap_tamper_and_isolation(self):
        harness = environment_test_support.EnvironmentExchangeTests(methodName="runTest")
        harness.setUp()
        try:
            harness.a.registry.register(
                {
                    "schema_version": 1,
                    "artifacts": [harness.rule_item("one"), harness.rule_item("two")],
                    "projects": [],
                },
                apply=True,
            )
            first_path = harness.base / "environment-first.mwxb"
            with mock.patch.object(memory_environment_exchange, "MAX_ARTIFACTS", 1):
                first = harness.a.export_delta(first_path, target_node_id="node-b")
            first_manifest = harness.a.read_bundle(first_path)[0]
            self.assert_manifest_contract(
                "environment-v1", first_manifest, base=0, predecessor=None
            )
            second_path = harness.base / "environment-second.mwxb"
            with mock.patch.object(memory_environment_exchange, "MAX_ARTIFACTS", 1):
                harness.a.export_delta(
                    second_path,
                    first["to_event_sequence"],
                    target_node_id="node-b",
                    previous_bundle_sha256=first["sha256"],
                )
            second_manifest = harness.a.read_bundle(second_path)[0]
            self.assert_manifest_contract(
                "environment-v1",
                second_manifest,
                base=first["to_event_sequence"],
                predecessor=first["sha256"],
            )

            with self.assertRaisesRegex(ValueError, "gap|contiguous|initial"):
                import_environment(harness.b, second_path)
            self.assertEqual(harness.b.replica_state("node-a")["last_event_sequence"], 0)
            imported = import_environment(harness.b, first_path)
            self.assertEqual(imported["status"], "imported")
            replay = import_environment(harness.b, first_path)
            self.assertEqual(
                replay["status"],
                SNAPSHOT["streams"]["environment-v1"]["replay_status"],
            )
            self.assert_other_streams_initial(
                harness.store_b, except_stream="environment-v1"
            )

            fresh = EnvironmentExchangeManager(self.new_pair(harness.base / "fresh")[1])
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                import_environment(fresh, first_path, target="node-c")
            self.assertEqual(fresh.replica_state("node-a")["last_event_sequence"], 0)

            corrupted = harness.base / "environment-tampered.mwxb"
            tamper_payload(first_path, corrupted, first_manifest["payload_path"])
            tampered = EnvironmentExchangeManager(
                self.new_pair(harness.base / "tampered")[1]
            )
            with self.assertRaisesRegex(ValueError, "hash mismatch|integrity"):
                import_environment(tampered, corrupted)
            self.assertEqual(tampered.replica_state("node-a")["last_event_sequence"], 0)
        finally:
            harness.tearDown()

    def test_project_evidence_manifest_cursor_binding_replay_gap_tamper_and_isolation(self):
        harness = evidence_test_support.ProjectEvidenceTests(methodName="runTest")
        harness.setUp()
        try:
            evidence = ProjectEvidenceStore(harness.store_a)
            first_generation = evidence.build(harness.spec(), apply=True)
            (harness.source / "status.txt").write_text(
                "ready version two", encoding="utf-8"
            )
            evidence.build(
                harness.spec(first_generation["generation_id"]), apply=True
            )
            sender = ProjectEvidenceExchangeManager(harness.store_a)
            receiver = ProjectEvidenceExchangeManager(harness.store_b)
            first_path = harness.base / "evidence-first.mwxb"
            with mock.patch.object(memory_project_evidence, "MAX_EVENTS_PER_BUNDLE", 1):
                first = sender.export_delta(first_path, target_node_id="node-b")
            first_manifest = sender.read_bundle_manifest(first_path)
            self.assert_manifest_contract(
                "project-evidence-v1", first_manifest, base=0, predecessor=None
            )
            second_path = harness.base / "evidence-second.mwxb"
            with mock.patch.object(memory_project_evidence, "MAX_EVENTS_PER_BUNDLE", 1):
                sender.export_delta(
                    second_path,
                    first["to_event_sequence"],
                    target_node_id="node-b",
                    previous_bundle_sha256=first["sha256"],
                )
            second_manifest = sender.read_bundle_manifest(second_path)
            self.assert_manifest_contract(
                "project-evidence-v1",
                second_manifest,
                base=first["to_event_sequence"],
                predecessor=first["sha256"],
            )

            with self.assertRaisesRegex(ValueError, "not contiguous"):
                evidence_test_support.authenticated_import(receiver, second_path)
            self.assertEqual(receiver.replica_state("node-a")["last_event_sequence"], 0)
            evidence_test_support.authenticated_import(receiver, first_path)
            with self.assertRaisesRegex(ValueError, "not contiguous"):
                evidence_test_support.authenticated_import(receiver, first_path)
            self.assert_other_streams_initial(
                harness.store_b, except_stream="project-evidence-v1"
            )

            fresh = ProjectEvidenceExchangeManager(
                self.new_pair(harness.base / "wrong-target")[1]
            )
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                fresh._import_authenticated_delta(
                    first_path,
                    expected_node_id="node-a",
                    authenticated_open_result=authenticated_result(
                        first_path, "node-a", "node-c"
                    ),
                )
            self.assertEqual(fresh.replica_state("node-a")["last_event_sequence"], 0)

            corrupted = harness.base / "evidence-tampered.mwxb"
            tamper_payload(first_path, corrupted, first_manifest["payload_path"])
            tampered = ProjectEvidenceExchangeManager(
                self.new_pair(harness.base / "tampered")[1]
            )
            with self.assertRaisesRegex(ValueError, "integrity"):
                evidence_test_support.authenticated_import(tampered, corrupted)
            self.assertEqual(tampered.replica_state("node-a")["last_event_sequence"], 0)
        finally:
            harness.tearDown()

    def test_project_attachment_manifest_cursor_binding_replay_gap_tamper_and_isolation(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source_store, receiver_store = self.new_pair(base)
            source = base / "文献 ¥ project"
            source.mkdir()
            document = source / "研究结果.pdf"
            document.write_bytes(b"%PDF-1.4\nfirst attachment\n")
            spec = {
                "schema_version": 1,
                "project_id": "literature-alpha",
                "title": "Literature alpha",
                "source_root": str(source),
                "conversation_ids": ["codex:r4-contract"],
                "files": [{"path": document.name, "role": "source-paper"}],
            }
            ProjectAttachmentStore(source_store).build(spec, apply=True)
            sender = ProjectAttachmentExchangeManager(source_store)
            receiver = ProjectAttachmentExchangeManager(receiver_store)
            first_path = base / "attachment-first.mwxb"
            with mock.patch.object(
                memory_project_attachments, "MAX_EVENTS_PER_BUNDLE", 1
            ):
                first = sender.export_delta(first_path, target_node_id="node-b")
            first_manifest = sender.read_bundle_manifest(first_path)
            self.assert_manifest_contract(
                "project-attachment-v1", first_manifest, base=0, predecessor=None
            )
            second_path = base / "attachment-second.mwxb"
            with mock.patch.object(
                memory_project_attachments, "MAX_EVENTS_PER_BUNDLE", 1
            ):
                sender.export_delta(
                    second_path,
                    first["to_event_sequence"],
                    target_node_id="node-b",
                    previous_bundle_sha256=first["sha256"],
                )
            second_manifest = sender.read_bundle_manifest(second_path)
            self.assert_manifest_contract(
                "project-attachment-v1",
                second_manifest,
                base=first["to_event_sequence"],
                predecessor=first["sha256"],
            )

            with self.assertRaisesRegex(ValueError, "not contiguous"):
                attachment_test_support.authenticated_import(receiver, second_path)
            self.assertEqual(receiver.replica_state("node-a")["last_event_sequence"], 0)
            attachment_test_support.authenticated_import(receiver, first_path)
            with self.assertRaisesRegex(ValueError, "not contiguous"):
                attachment_test_support.authenticated_import(receiver, first_path)
            self.assert_other_streams_initial(
                receiver_store, except_stream="project-attachment-v1"
            )

            fresh = ProjectAttachmentExchangeManager(
                self.new_pair(base / "wrong-target")[1]
            )
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                fresh._import_authenticated_delta(
                    first_path,
                    expected_node_id="node-a",
                    authenticated_open_result=authenticated_result(
                        first_path, "node-a", "node-c"
                    ),
                )
            self.assertEqual(fresh.replica_state("node-a")["last_event_sequence"], 0)

            corrupted = base / "attachment-tampered.mwxb"
            tamper_payload(first_path, corrupted, first_manifest["payload_path"])
            tampered = ProjectAttachmentExchangeManager(
                self.new_pair(base / "tampered")[1]
            )
            with self.assertRaisesRegex(ValueError, "integrity"):
                attachment_test_support.authenticated_import(tampered, corrupted)
            self.assertEqual(tampered.replica_state("node-a")["last_event_sequence"], 0)


if __name__ == "__main__":
    unittest.main()
