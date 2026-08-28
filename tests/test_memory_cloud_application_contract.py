import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from memory_cli import (
    MemoryStore,
    dispatch_command,
    environment_cloud_transport,
    project_attachment_cloud_transport,
    project_evidence_cloud_transport,
)
from memory_cloud_transport import CloudFolderTransport
from memory_cloud_streams import (
    CloudApplicationService,
    CloudStreamDefinition,
    CloudStreamRegistry,
)
from memory_dashboard import make_handler
from memory_federation import FederationManager
from memory_project_attachments import ProjectAttachmentExchangeManager
from memory_project_evidence import ProjectEvidenceExchangeManager


FIXTURE = SKILL_ROOT / "tests" / "fixtures" / "cloud-application-contract-v218.json"


class CloudApplicationContractTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "归档 ¥ 日本語 😀 -leading"
        self.store = MemoryStore(
            self.root,
            {"memory": {"backup_after_mutation": False}, "federation": {}},
        )
        self.contract = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    def relative_contract_path(self, value: Path) -> str:
        path = Path(value).resolve()
        root = self.root.resolve()
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
        expected_replica = root.parent / f"{root.name}-federation-cache"
        if path == expected_replica.resolve():
            return "<archive-parent>/<archive-name>-federation-cache"
        self.fail(f"unexpected application assembly path: {path}")

    def transports(self, *, bootstrap: bool = False):
        archive = CloudFolderTransport(FederationManager(self.store))
        return [
            archive,
            environment_cloud_transport(self.store, archive, bootstrap=bootstrap),
            project_evidence_cloud_transport(self.store, archive, bootstrap=bootstrap),
            project_attachment_cloud_transport(self.store, archive, bootstrap=bootstrap),
        ]

    def describe(self, transport: CloudFolderTransport) -> dict:
        manager = transport.manager
        return {
            "stream_id": transport.port.stream_id,
            "manager_class": type(manager).__name__,
            "transport_namespace": transport.stream_id,
            "config_path": self.relative_contract_path(transport.config_path),
            "metadata_root": self.relative_contract_path(manager.metadata_root),
            "cursor_path": self.relative_contract_path(manager.export_state_path),
            "ledger_path": self.relative_contract_path(manager.export_ledger_path),
            "replica_root": self.relative_contract_path(manager.replica_root),
            "lock_path": self.relative_contract_path(manager.exchange_lock_path),
            "ack_failure_state": self.relative_contract_path(transport.config_path),
        }

    def test_four_stream_assembly_matches_frozen_contract(self):
        observed = [self.describe(item) for item in self.transports()]

        self.assertEqual(observed, self.contract["streams"])
        isolated_fields = (
            "config_path",
            "cursor_path",
            "ledger_path",
            "replica_root",
            "lock_path",
            "ack_failure_state",
        )
        for field in isolated_fields:
            values = [item[field] for item in observed]
            self.assertEqual(len(values), len(set(values)), field)

    def test_registry_and_stream_definitions_are_immutable(self):
        registry = CloudStreamRegistry()

        with self.assertRaises(FrozenInstanceError):
            registry.definitions = ()
        with self.assertRaises(FrozenInstanceError):
            registry.definitions[0].stream_id = "changed"
        with self.assertRaises(TypeError):
            registry._by_id["changed"] = registry.definitions[0]

    def test_registry_rejects_duplicate_result_keys(self):
        definitions = CloudStreamRegistry().definitions
        duplicate = CloudStreamDefinition(
            stream_id="duplicate-v1",
            result_key=definitions[1].result_key,
            manager_factory=definitions[1].manager_factory,
            transport_namespace="duplicate-v1",
            bootstrap_readiness="configured-status",
        )

        with self.assertRaisesRegex(
            ValueError, "cloud stream result keys must be unique"
        ):
            CloudStreamRegistry(definitions + (duplicate,))

    def test_bootstrap_copies_archive_defaults_to_each_independent_config(self):
        exchange = self.base / "同步 cloud ¥ 日本語"
        exchange.mkdir()
        identity = self.base / "keys" / "device identity.json"
        archive = CloudFolderTransport(FederationManager(self.store))
        archive.configure(exchange, identity, enabled=False)

        transports = [
            environment_cloud_transport(self.store, archive, bootstrap=True),
            project_evidence_cloud_transport(self.store, archive, bootstrap=True),
            project_attachment_cloud_transport(self.store, archive, bootstrap=True),
        ]
        expected = self.contract["bootstrap"]["copied_fields"]

        for transport in transports:
            self.assertTrue(transport.config_path.is_file())
            self.assertEqual(
                {key: transport.config[key] for key in expected},
                expected,
            )
            self.assertEqual(transport.config["exchange_root"], str(exchange.resolve()))
            self.assertEqual(
                transport.config["identity_private_path"], str(identity.resolve())
            )
        self.assertEqual(len({item.config_path for item in transports}), 3)

    def test_bootstrap_never_overwrites_existing_stream_configuration(self):
        exchange = self.base / "cloud"
        exchange.mkdir()
        identity = self.base / "keys" / "identity.json"
        archive = CloudFolderTransport(FederationManager(self.store))
        archive.configure(exchange, identity, enabled=True)
        factories = (
            environment_cloud_transport,
            project_evidence_cloud_transport,
            project_attachment_cloud_transport,
        )
        expected = {}
        for index, factory in enumerate(factories, start=1):
            transport = factory(self.store, archive, bootstrap=True)
            transport.config["merge_window_seconds"] = index
            transport.save_config()
            expected[transport.port.stream_id] = index

        archive.config["merge_window_seconds"] = 999
        archive.save_config()

        for factory in factories:
            transport = factory(self.store, archive, bootstrap=True)
            self.assertEqual(
                transport.config["merge_window_seconds"],
                expected[transport.port.stream_id],
            )

    def test_dashboard_attachment_inclusion_matches_frozen_contract(self):
        handler_type = make_handler(self.store)
        handler = object.__new__(handler_type)
        attachment_inventory = {"local_manifests": 0, "local_event_sequence": 0}
        attachment_configured = False

        def cloud_status(transport):
            configured = (
                attachment_configured
                if transport.port.stream_id == "project-attachment-v1"
                else True
            )
            return {"configured": configured, "enabled": False, "peers": []}

        with (
            patch.object(
                FederationManager,
                "status",
                return_value={"enabled": True, "devices": [], "recent_sync": []},
            ),
            patch.object(CloudFolderTransport, "status", autospec=True, side_effect=cloud_status),
            patch.object(
                ProjectEvidenceExchangeManager,
                "status",
                return_value={"local_packages": 0, "local_event_sequence": 0},
            ),
            patch.object(
                ProjectAttachmentExchangeManager,
                "status",
                side_effect=lambda: dict(attachment_inventory),
            ),
            patch("memory_dashboard.ProjectEvidenceStore.owner_status", return_value={}),
            patch("memory_dashboard.cloud_scheduler_status", return_value={}),
        ):
            initial = handler.cloud_payload()["cloud"]["streams"]
            attachment_inventory["local_manifests"] = 1
            with_inventory = handler.cloud_payload()["cloud"]["streams"]

        self.assertEqual(
            list(initial), self.contract["dashboard"]["always_visible"]
        )
        self.assertNotIn(self.contract["dashboard"]["conditional_stream"], initial)
        self.assertIn(
            self.contract["dashboard"]["conditional_stream"], with_inventory
        )

    def test_cli_sync_keeps_environment_incoming_as_explicit_post_sync_step(self):
        events = []
        lifecycle = []

        original_transport = CloudApplicationService.transport

        def construct(service, stream_id, **kwargs):
            lifecycle.append(f"construct:{stream_id}")
            return original_transport(service, stream_id, **kwargs)

        def sync(transport, *, force=False):
            self.assertTrue(force)
            events.append(transport.port.stream_id)
            lifecycle.append(f"sync:{transport.port.stream_id}")
            return {"status": "ok", "stream_id": transport.port.stream_id}

        def process(_processor, *, apply=False):
            self.assertTrue(apply)
            events.append("environment-incoming")
            lifecycle.append("environment-incoming")
            return {"status": "processed"}

        output = io.StringIO()
        with (
            patch.object(
                CloudApplicationService,
                "transport",
                autospec=True,
                side_effect=construct,
            ),
            patch.object(CloudFolderTransport, "sync", autospec=True, side_effect=sync),
            patch(
                "memory_cli.EnvironmentIncomingProcessor.process",
                autospec=True,
                side_effect=process,
            ),
            redirect_stdout(output),
        ):
            status = dispatch_command(
                SimpleNamespace(command="cloud-sync", force=True),
                None,
                self.store,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(events, self.contract["cli"]["cloud_sync_order"])
        self.assertEqual(
            lifecycle,
            [
                "construct:archive-v1",
                "sync:archive-v1",
                "construct:environment-v1",
                "sync:environment-v1",
                "environment-incoming",
                "construct:project-evidence-v1",
                "sync:project-evidence-v1",
                "construct:project-attachment-v1",
                "sync:project-attachment-v1",
            ],
        )
        self.assertEqual(payload["environment"]["incoming"], {"status": "processed"})

    def test_sync_all_isolates_one_stream_failure_and_continues(self):
        events = []

        class FakeTransport:
            def __init__(self, stream_id):
                self.stream_id = stream_id

            def sync(self, *, force=False):
                self.assert_force(force)
                events.append(self.stream_id)
                if self.stream_id == "archive-v1":
                    raise ValueError("immutable artifact replay unavailable")
                return {"status": "ok", "stream_id": self.stream_id}

            @staticmethod
            def assert_force(force):
                if not force:
                    raise AssertionError("sync_all did not forward force=True")

        def transport(_service, stream_id, **_kwargs):
            return FakeTransport(stream_id)

        service = CloudApplicationService(self.store)
        with patch.object(
            CloudApplicationService,
            "transport",
            autospec=True,
            side_effect=transport,
        ):
            result = service.sync_all(force=True)

        self.assertEqual(
            events,
            [definition.stream_id for definition in service.registry.definitions],
        )
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["archive"]["status"], "failed")
        self.assertEqual(result["archive"]["error_type"], "ValueError")
        self.assertEqual(result["environment"]["status"], "ok")
        self.assertEqual(result["project_evidence"]["status"], "ok")
        self.assertEqual(result["project_attachments"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
