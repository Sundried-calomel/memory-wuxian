import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
from memory_environment_exchange import EnvironmentExchangeManager
from memory_environment_governance import GovernanceProposalStore
from memory_federation import FederationManager


class EnvironmentGovernanceProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        config = load_simple_yaml(ROOT / "config.yaml")
        self.store_a = MemoryStore(self.base / "a", config)
        self.store_b = MemoryStore(self.base / "b", config)
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
        self.proposals_a = GovernanceProposalStore(self.store_a)
        self.exchange_a = EnvironmentExchangeManager(self.store_a)
        self.exchange_b = EnvironmentExchangeManager(self.store_b)

    def tearDown(self):
        self.temporary.cleanup()

    def proposal(self):
        return {
            "schema_version": 1,
            "proposal_id": "insight-test-001",
            "origin_node_id": "node-a",
            "observed_problem": "A local product repeated one governance failure.",
        }

    def test_proposal_is_immutable_and_does_not_imply_acceptance(self):
        preview = self.proposals_a.propose(self.proposal())
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(preview["acceptance_implied"])
        recorded = self.proposals_a.propose(self.proposal(), apply=True)
        self.assertEqual(recorded["status"], "recorded")
        repeated = self.proposals_a.propose(self.proposal(), apply=True)
        self.assertEqual(repeated["status"], "no-change")
        changed = self.proposal()
        changed["observed_problem"] = "Different content"
        with self.assertRaisesRegex(ValueError, "different content"):
            self.proposals_a.propose(changed, apply=True)

    def test_remote_proposal_uses_read_only_replica_not_install_staging(self):
        self.proposals_a.propose(self.proposal(), apply=True)
        bundle = self.base / "governance.mwxb"
        exported = self.exchange_a.export_delta(
            bundle, target_node_id="node-b"
        )
        self.assertEqual(exported["artifact_count"], 1)
        imported = self.exchange_b.import_delta(
            bundle, expected_node_id="node-a"
        )
        self.assertEqual(imported["staged_artifacts"], 0)
        self.assertEqual(imported["staged_governance_proposals"], 1)
        self.assertEqual(
            list(
                (
                    self.exchange_b.registry.staging_dir
                    / "incoming"
                    / "node-a"
                ).glob("*.json")
            ),
            [],
        )
        replicas = list(
            (
                self.exchange_b.registry.root
                / "replicas"
                / "peers"
                / "node-a"
                / "governance-proposals"
            ).glob("*.json")
        )
        self.assertEqual(len(replicas), 1)
        record = json.loads(replicas[0].read_text(encoding="utf-8"))
        self.assertFalse(record["automatic_acceptance"])
        self.assertEqual(self.exchange_b.registry.list(), [])

    def test_tampered_proposal_envelope_fails_closed(self):
        self.proposals_a.propose(self.proposal(), apply=True)
        envelope = self.proposals_a.local_events()[0]["payload"]
        tampered = copy.deepcopy(envelope)
        tampered["content_base64"] += "A"
        with self.assertRaisesRegex(ValueError, "encoding|hash"):
            GovernanceProposalStore.validate_envelope(tampered)

    def test_origin_must_be_local_node(self):
        proposal = self.proposal()
        proposal["origin_node_id"] = "node-b"
        with self.assertRaisesRegex(ValueError, "local node"):
            self.proposals_a.propose(proposal, apply=True)


if __name__ == "__main__":
    unittest.main()
