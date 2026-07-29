import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_environment_evolution import ProductEvolutionStore
from memory_environment_exchange import EnvironmentExchangeManager
from memory_federation import FederationManager


class EnvironmentProductEvolutionTests(unittest.TestCase):
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
        self.records_a = ProductEvolutionStore(self.store_a)
        self.exchange_a = EnvironmentExchangeManager(self.store_a)
        self.exchange_b = EnvironmentExchangeManager(self.store_b)

    def tearDown(self):
        self.temporary.cleanup()

    def record(self):
        return {
            "schema_version": 1,
            "record_id": "evolution-test-001",
            "origin_node_id": "node-a",
            "product_id": "test-product",
            "history": [{"kind": "correction"}],
        }

    def test_record_is_immutable_and_does_not_imply_remediation(self):
        preview = self.records_a.record(self.record())
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(preview["remediation_implied"])
        self.assertFalse(preview["governance_acceptance_implied"])
        recorded = self.records_a.record(self.record(), apply=True)
        self.assertEqual(recorded["status"], "recorded")
        repeated = self.records_a.record(self.record(), apply=True)
        self.assertEqual(repeated["status"], "no-change")
        changed = self.record()
        changed["history"] = [{"kind": "release"}]
        with self.assertRaisesRegex(ValueError, "different content"):
            self.records_a.record(changed, apply=True)

    def test_remote_record_is_read_only_and_not_install_staging(self):
        self.records_a.record(self.record(), apply=True)
        bundle = self.base / "evolution.mwxb"
        exported = self.exchange_a.export_delta(bundle, target_node_id="node-b")
        self.assertEqual(exported["artifact_count"], 1)
        imported = self.exchange_b.import_delta(bundle, expected_node_id="node-a")
        self.assertEqual(imported["staged_artifacts"], 0)
        self.assertEqual(imported["staged_product_evolution_records"], 1)
        replicas = list(
            (
                self.exchange_b.registry.root
                / "replicas"
                / "peers"
                / "node-a"
                / "product-evolution"
            ).glob("*.json")
        )
        self.assertEqual(len(replicas), 1)
        value = json.loads(replicas[0].read_text(encoding="utf-8"))
        self.assertFalse(value["automatic_remediation"])
        self.assertFalse(value["automatic_governance_acceptance"])
        self.assertEqual(self.exchange_b.registry.list(), [])

    def test_tampered_record_fails_closed(self):
        self.records_a.record(self.record(), apply=True)
        envelope = self.records_a.local_events()[0]["payload"]
        tampered = copy.deepcopy(envelope)
        tampered["content_base64"] += "A"
        with self.assertRaisesRegex(ValueError, "encoding|hash"):
            ProductEvolutionStore.validate_envelope(tampered)

    def test_origin_must_be_local_node(self):
        value = self.record()
        value["origin_node_id"] = "node-b"
        with self.assertRaisesRegex(ValueError, "local node"):
            self.records_a.record(value, apply=True)


if __name__ == "__main__":
    unittest.main()
