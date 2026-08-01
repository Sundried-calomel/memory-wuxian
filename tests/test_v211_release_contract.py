import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class V211ReleaseContractTest(unittest.TestCase):
    def test_mw211_01_continuous_catchup_contract_is_versioned(self):
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(version, "2.11.6")
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
        source = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        batch = source.split("fn sync_batch(&self", 1)[1].split(
            "fn sync_batch_unlocked", 1
        )[0]
        self.assertNotIn("write_coverage_status", batch)
        self.assertEqual(source.count("store.write_coverage_status(&current_paths)?;"), 2)
        self.assertEqual(source.count("store.write_coverage_status(&scoped_paths)?;"), 2)
        self.assertIn("fn cursor_requires_sync", source)
        self.assertNotIn('if cursor.get("excluded_reason").is_some()', source)

    def test_mw2115_legacy_ai_path_is_absent_from_collector_production(self):
        source = (ROOT / "native-collector/src/main.rs").read_text(encoding="utf-8")
        production = source.split("#[cfg(test)]", 1)[0]
        self.assertNotIn("run_one_shot_summary", production)
        self.assertNotIn("semantic_dispatch.py", production)
        self.assertNotIn("command.output()", production)
        self.assertIn("self.maybe_create_level_one_job()?", production)

    def test_mw211_03_background_effect_gate_is_explicit(self):
        rehearsal = (ROOT / "references/release-rehearsal.md").read_text(encoding="utf-8")
        self.assertIn("synthetic live semantic canary", rehearsal)
        self.assertIn("pending count decreases from 1 to 0", rehearsal)
        dispatch = (ROOT / "scripts/semantic_dispatch.py").read_text(encoding="utf-8")
        self.assertIn("str(expanded) if expanded.is_file()", dispatch)
        self.assertTrue((ROOT / "scripts/runtime_effect_gate.py").is_file())
        for token in (
            "semantic-parent-job-missing",
            "semantic-index-stale",
            "incomplete-backup-residue",
            "permanent-maintenance-debt",
            "maintenance-supervisor-not-healthy",
        ):
            self.assertIn(token, (ROOT / "scripts/runtime_effect_gate.py").read_text(encoding="utf-8"))

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
