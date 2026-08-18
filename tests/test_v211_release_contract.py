import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class V211ReleaseContractTest(unittest.TestCase):
    def test_mw211_01_continuous_catchup_contract_is_versioned(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), (2, 14, 4))
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
        for readme in ("README.md", "README.zh-CN.md", "README.ja.md"):
            text = (ROOT / readme).read_text(encoding="utf-8")
            self.assertIn("collector-activation.json", text)
            self.assertIn("semantic_plan.py", text)
            self.assertIn("900,000", text)

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
        rehearsal = (ROOT / "references/release-rehearsal.md").read_text(encoding="utf-8")
        self.assertIn("synthetic live semantic canary", rehearsal)
        self.assertIn("pending count decreases from 1 to 0", rehearsal)
        dispatch = (ROOT / "scripts/semantic_dispatch.py").read_text(encoding="utf-8")
        self.assertIn("str(expanded) if expanded.is_file()", dispatch)
        self.assertTrue((ROOT / "scripts/runtime_effect_gate.py").is_file())
        gate = (ROOT / "scripts/runtime_effect_gate.py").read_text(encoding="utf-8")
        collector_gate = (ROOT / "scripts/collector_runtime_effect_gate.py").read_text(encoding="utf-8")
        lifecycle = (ROOT / "scripts/collector_lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("semantic-parent-job-missing", gate)
        self.assertIn("maintenance-supervisor-not-healthy", gate)
        self.assertIn("verify_collector_lifecycle", collector_gate)
        self.assertIn("collector-watermark-not-converged", lifecycle)
        self.assertNotIn("semantic-parent-job-missing", collector_gate)

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
