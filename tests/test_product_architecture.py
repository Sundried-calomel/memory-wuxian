from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProductArchitectureContractTest(unittest.TestCase):
    def test_canonical_contract_and_agent_route_exist(self):
        architecture = (ROOT / "PRODUCT_ARCHITECTURE.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("canonical owner", architecture)
        self.assertIn("PRODUCT_ARCHITECTURE.md", agents)
        self.assertIn("single owner", agents)

    def test_all_versioned_contracts_remain_declared(self):
        architecture = (ROOT / "PRODUCT_ARCHITECTURE.md").read_text(encoding="utf-8")
        contracts = [
            "Source Adapter Contract",
            "Archive Transaction Contract",
            "Summary Job Contract",
            "Retrieval Contract",
            "Environment Artifact Contract",
            "Installer Transaction Contract",
            "Transport Contract",
            "Scheduler Job Contract",
            "Dashboard API Contract",
            "Release Evidence Contract",
        ]

        for contract in contracts:
            self.assertIn(contract, architecture)

    def test_productization_phases_remain_ordered(self):
        architecture = (ROOT / "PRODUCT_ARCHITECTURE.md").read_text(encoding="utf-8")
        phases = [
            "Phase 1: 2.0.x Operational Stabilization",
            "Phase 2: Boundary And Contract Freeze",
            "Phase 3: Shared Platform Foundation",
            "Phase 4: Application Services And Thin Shells",
            "Phase 5: Memory Plane Extraction",
            "Phase 6: Exchange Foundation Unification",
            "Phase 7: Product Quality Layer",
            "Phase 8: Major-Version Decision",
        ]

        positions = [architecture.index(phase) for phase in phases]
        self.assertEqual(positions, sorted(positions))

    def test_refactoring_gate_forbids_mixed_contract_changes(self):
        architecture = (ROOT / "PRODUCT_ARCHITECTURE.md").read_text(encoding="utf-8")

        self.assertIn(
            "Do not combine module extraction with a storage migration, protocol revision,",
            architecture,
        )
        self.assertIn(
            "new feature, changed default, or changed security policy",
            architecture,
        )

    def test_release_candidate_and_installed_update_lifecycle_is_hard_gated(self):
        architecture = (ROOT / "PRODUCT_ARCHITECTURE.md").read_text(encoding="utf-8")
        rehearsal = (ROOT / "references/release-rehearsal.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        normalized_architecture = " ".join(architecture.split())
        normalized_agents = " ".join(agents.split())

        self.assertIn(
            "candidate branch without creating a formal tag, GitHub Release, "
            "or published installer",
            normalized_architecture,
        )
        self.assertIn(
            "Create the immutable formal tag once", normalized_architecture
        )
        self.assertIn(
            "verified user-space update transaction", normalized_architecture
        )
        self.assertIn(
            "first installation, explicit recovery from a damaged or mixed installation",
            normalized_architecture,
        )
        self.assertIn("release-candidate branch", rehearsal)
        self.assertIn("keep candidate iteration untagged", normalized_agents)


if __name__ == "__main__":
    unittest.main()
