import json
import unittest
from pathlib import Path

from tests.support.release_contracts import (
    assert_minimum_project_version,
    assert_readme_tokens,
    assert_source_tokens,
)


ROOT = Path(__file__).resolve().parent.parent


class V211ReleaseContractTest(unittest.TestCase):
    def test_mw211_01_continuous_catchup_contract_is_versioned(self):
        assert_minimum_project_version(self, ROOT, (2, 14, 4))
        contract = json.loads((ROOT / "docs/work-contracts/v2.11.0.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["work_item_id"], "memory-wuxian-v2.11.0")
        review = json.loads((ROOT / "docs/promotion-reviews/v2.11.0.json").read_text(encoding="utf-8"))
        self.assertEqual(review["disposition"], "no-candidate")
        self.assertIn("An upgrade may preserve or move the activation boundary earlier but can never move it later.", contract["invariants"])
        for relative in (
            "scripts/collector_activation.py",
            "scripts/maintenance_supervisor.py",
            "scripts/install_maintenance_supervisor.py",
            "scripts/semantic_plan.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        assert_readme_tokens(
            self,
            ROOT,
            ("collector-activation.json", "semantic_plan.py", "900,000"),
        )

    def test_mw211_02_incremental_sync_cannot_overwrite_global_coverage(self):
        source = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        production = source.split("#[cfg(test)]", 1)[0]
        batch = source.split("fn sync_batch(&self", 1)[1].split(
            "fn sync_batch_unlocked", 1
        )[0]
        self.assertNotIn("write_coverage_status", batch)
        self.assertEqual(
            production.count("store.write_coverage_status(&current_paths)?;"), 1
        )
        self.assertEqual(production.count("process_rollout_cycle("), 3)
        self.assertEqual(source.count("store.write_coverage_status(&scoped_paths)?;"), 2)
        cursor = (ROOT / "native-collector/src/store/cursor.rs").read_text(encoding="utf-8")
        self.assertIn("fn requires_sync", cursor)
        self.assertNotIn('if cursor.get("excluded_reason").is_some()', source)

    def test_mw2115_legacy_ai_path_is_absent_from_collector_production(self):
        paths = (
            "native-collector/src/lib.rs",
            "native-collector/src/locking.rs",
            "native-collector/src/runtime.rs",
            "native-collector/src/source/mod.rs",
            "native-collector/src/store/mod.rs",
            "native-collector/src/store/cursor.rs",
            "native-collector/src/store/transaction.rs",
            "native-collector/src/store/wal.rs",
            "native-collector/src/telemetry.rs",
            "native-collector/src/main.rs",
            "native-collector/src/bin/memory-wuxian-core-launcher.rs",
        )
        excluded = {
            "native-collector/src/bin/memory-wuxian-dashboard-launcher.rs",
            "native-collector/src/bin/memory-wuxian-envelope.rs",
        }
        discovered = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "native-collector/src").rglob("*.rs")
        } - excluded
        self.assertEqual(set(paths), discovered)
        production = "\n".join(
            (ROOT / path).read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
            for path in paths
        )
        for forbidden in ("run_one_shot_summary", "semantic_dispatch.py", "command.output()"):
            self.assertNotIn(forbidden, production)
        self.assertIn("self.maybe_create_level_one_job()?", production)

    def test_mw211_03_background_effect_gate_is_explicit(self):
        assert_source_tokens(
            self,
            ROOT,
            "references/release-rehearsal.md",
            present=("synthetic live semantic canary", "pending count decreases from 1 to 0"),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/semantic_dispatch.py",
            present=("str(expanded) if expanded.is_file()",),
        )
        self.assertTrue((ROOT / "scripts/runtime_effect_gate.py").is_file())
        assert_source_tokens(
            self,
            ROOT,
            "scripts/runtime_effect_gate.py",
            present=("semantic-parent-job-missing", "maintenance-supervisor-not-healthy"),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/collector_runtime_effect_gate.py",
            present=("verify_collector_lifecycle",),
            absent=("semantic-parent-job-missing",),
        )
        assert_source_tokens(
            self,
            ROOT,
            "scripts/collector_lifecycle.py",
            present=("collector-watermark-not-converged",),
        )

    def test_mw2116_patch_rehearsal_is_explicitly_bounded(self):
        contract = json.loads(
            (ROOT / "docs/work-contracts/v2.11.6.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["validation_profile"], "targeted-patch")
        scenarios = set(contract["required_rehearsal_scenarios"])
        self.assertIn("v2116-runtime-effect-gate-contract", scenarios)
        self.assertIn("candidate-native-version", scenarios)
        self.assertIn("architecture-contract", scenarios)
        self.assertNotIn("bundled-native-version", scenarios)
        self.assertNotIn("python-regressions", scenarios)
        rehearsal = (ROOT / "scripts/run_release_rehearsal.py").read_text(encoding="utf-8")
        self.assertIn("--contract-profile", rehearsal)
        self.assertIn("--print-validation-profile", rehearsal)


if __name__ == "__main__":
    unittest.main()
