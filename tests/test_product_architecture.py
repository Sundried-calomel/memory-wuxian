from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import check_architecture_contract


ROOT = Path(__file__).resolve().parents[1]


class ProductArchitectureContractTest(unittest.TestCase):
    def test_machine_readable_module_contract_passes(self):
        completed = subprocess.run(
            [sys.executable, "scripts/check_architecture_contract.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_every_declared_module_has_one_architecture_owner(self):
        contract = json.loads(
            (ROOT / "docs/module-architecture.json").read_text(encoding="utf-8")
        )
        module_ids = [module["id"] for module in contract["modules"]]
        self.assertEqual(len(module_ids), len(set(module_ids)))
        self.assertTrue(
            all(module.get("architecture_owner") for module in contract["modules"])
        )

    def test_unowned_production_file_fails_closed(self):
        actual = check_architecture_contract.production_files

        def with_unowned(contract):
            return actual(contract) + ["scripts/new_unowned_feature.py"]

        with patch.object(
            check_architecture_contract, "production_files", side_effect=with_unowned
        ):
            errors = check_architecture_contract.validate()
        self.assertIn(
            "unowned production file: scripts/new_unowned_feature.py", errors
        )

    def test_domain_to_dashboard_dependency_fails_closed(self):
        with (
            patch.object(
                check_architecture_contract,
                "production_files",
                return_value=["scripts/token_usage.py"],
            ),
            patch.object(
                check_architecture_contract,
                "imported_modules",
                return_value={"memory_dashboard"},
            ),
        ):
            errors = check_architecture_contract.validate()
        self.assertIn(
            "forbidden dependency: scripts/token_usage.py "
            "(memory-plane) -> memory_dashboard",
            errors,
        )

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
            "rerun the smallest deterministic gate that covers the changed contract",
            normalized_architecture,
        )
        self.assertIn(
            "Do not rebuild installers, rerun unrelated full matrices, create tags, "
            "or upload artifacts while diagnosing intermediate candidate defects",
            normalized_architecture,
        )
        self.assertIn(
            "Convert every confirmed release failure mode into a regression test",
            normalized_architecture,
        )
        self.assertIn(
            "Only a frozen candidate runs the complete release gate",
            normalized_architecture,
        )
        self.assertIn(
            "Build and publish the formal installers only from the exact commit "
            "that passed the complete gate",
            normalized_architecture,
        )
        self.assertIn(
            "bounded deterministic shards on the pinned supported runner",
            normalized_architecture,
        )
        self.assertIn(
            "Do not collapse them into one long Windows job, launch a high-fan-out "
            "Windows matrix, or skip tests",
            normalized_architecture,
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
        self.assertIn(
            "classify each failure before rerunning only its affected gate",
            normalized_agents,
        )
        self.assertIn(
            "Do not rebuild installers or rerun unrelated full matrices for "
            "intermediate fixes",
            normalized_agents,
        )
        self.assertIn(
            "Windows CI in bounded ordered serial shards with complete coverage",
            normalized_agents,
        )


if __name__ == "__main__":
    unittest.main()
