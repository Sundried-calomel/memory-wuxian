from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "install_transaction_workflow.py"
REUSE_VALIDATOR = PROJECT / "scripts" / "validate_installer_reuse_map.py"
CONTRACT = PROJECT / "docs" / "install-transaction" / "contract.json"
REUSE_MAP = PROJECT / "docs" / "install-transaction" / "replan-reuse-map.json"
ADMISSION = PROJECT / "docs" / "capability-admission" / "v2.20.0-installer-workflow"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_fixture_admission(root: Path) -> None:
    """Model an already-reviewed contract revision inside the isolated fixture."""
    admission = root / "docs" / "capability-admission" / "v2.20.0-installer-workflow"
    contract = root / "docs" / "install-transaction" / "contract.json"
    controller = root / "scripts" / "install_transaction_workflow.py"
    identity = {
        "contract_sha256": sha256(contract),
        "controller_sha256": sha256(controller),
        "reuse_map_sha256": sha256(root / "docs/install-transaction/replan-reuse-map.json"),
        "reuse_validator_sha256": sha256(root / "scripts/validate_installer_reuse_map.py"),
    }
    artifact = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_path = admission / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_sha256"] = artifact
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    binding_path = admission / "binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["capabilities"][0]["manifest_sha256"] = sha256(manifest_path)
    binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")

    review_path = admission / "semantic-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["candidate_manifest_sha256"] = sha256(manifest_path)
    review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")

    receipt_path = admission / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = sha256(manifest_path)
    receipt["binding_sha256"] = sha256(binding_path)
    receipt["semantic_review_sha256"] = sha256(review_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], cwd: Path, *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == expect, result.stdout + result.stderr
    return result


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "docs" / "install-transaction").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    shutil.copy2(REUSE_VALIDATOR, root / "scripts" / REUSE_VALIDATOR.name)
    shutil.copy2(CONTRACT, root / "docs" / "install-transaction" / "contract.json")
    shutil.copy2(REUSE_MAP, root / "docs" / "install-transaction" / "replan-reuse-map.json")
    shutil.copytree(ADMISSION, root / "docs" / "capability-admission" / "v2.20.0-installer-workflow")
    refresh_fixture_admission(root)
    (root / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    run(["git", "init", "-q"], root)
    run(["git", "config", "user.email", "test@example.invalid"], root)
    run(["git", "config", "user.name", "test"], root)
    run(["git", "add", "."], root)
    run(["git", "commit", "-qm", "fixture"], root)
    return root


def workflow(root: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "scripts/install_transaction_workflow.py", *args], root, expect=expect)


def s01_evidence() -> list[str]:
    result: list[str] = []
    for item in (
        "defect-preflight",
        "workflow-governance-check",
        "capability-admission-receipt",
        "state-machine-tests",
        "project-hook-test",
        "failed-candidate-evidence-freeze",
    ):
        result.extend(["--evidence", item])
    return result


class InstallTransactionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_init_status_and_project_scoped_path_gate(self) -> None:
        initialized = json.loads(workflow(self.root, "init").stdout)
        self.assertEqual(initialized["current_step"], "S01")
        status = json.loads(workflow(self.root, "status").stdout)
        self.assertEqual(status["status"], "active")
        workflow(self.root, "hook", "pre-edit", "--path", ".githooks/pre-commit")
        workflow(
            self.root,
            "hook",
            "pre-edit",
            "--path",
            "docs/capability-admission/v2.20.0-installer-workflow/receipt.json",
        )
        denied = workflow(self.root, "hook", "pre-edit", "--path", "scripts/install_codex_autosync_windows.py", expect=2)
        self.assertIn("does not allow protected paths", denied.stderr)

    def test_evidence_first_contract_blocks_production_repair_before_s07(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in contract["steps"]}
        self.assertIn("exact packaged production call chain", steps["S02"]["title"])
        self.assertEqual(steps["S05"]["required_evidence"], ["broker-controller-trace", "exact-root-cause-proof"])
        self.assertIn("redundancy-disposition-table", steps["S06"]["required_evidence"])
        self.assertNotIn("scripts/**", steps["S06"]["allowed_paths"])
        self.assertIn("scripts/**", steps["S07"]["allowed_paths"])

    def test_receipt_is_bound_to_unchanged_worktree(self) -> None:
        workflow(self.root, "init")
        target = self.root / "docs" / "install-transaction" / "note.md"
        target.write_text("verified\n", encoding="utf-8")
        workflow(self.root, "hook", "post-edit")
        workflow(self.root, "verify", "S01", *s01_evidence())
        target.write_text("drifted\n", encoding="utf-8")
        failed = workflow(self.root, "complete", "S01", expect=2)
        self.assertIn("changed after verification", failed.stderr)

    def test_ordering_and_remediation_limit_fail_closed(self) -> None:
        workflow(self.root, "init")
        workflow(self.root, "remediate", "--reason", "integrated correction")
        failed = workflow(self.root, "remediate", "--reason", "second correction", expect=2)
        self.assertIn("explicit replan", failed.stderr)
        state = json.loads((self.root / "docs" / "install-transaction" / "runtime-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "needs_replan")
        self.assertEqual(state["steps"]["S01"]["status"], "blocked")

    def test_completed_step_promotes_baseline_before_next(self) -> None:
        workflow(self.root, "init")
        target = self.root / "docs" / "install-transaction" / "s01.md"
        target.write_text("accepted\n", encoding="utf-8")
        workflow(self.root, "verify", "S01", *s01_evidence())
        workflow(self.root, "complete", "S01")
        workflow(self.root, "next")
        status = json.loads(workflow(self.root, "status").stdout)
        self.assertEqual(status["current_step"], "S02")
        self.assertEqual(status["delta_paths"], [])

    def test_replan_rebinds_contract_and_resumes_requested_step(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs" / "install-transaction" / "runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "needs_replan"
        state["current_step"] = None
        state["steps"]["S01"]["status"] = "blocked"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        contract_path = self.root / "docs" / "install-transaction" / "contract.json"
        contract_path.write_text(contract_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        refresh_fixture_admission(self.root)
        workflow(self.root, "replan", "--resume-step", "S01", "--reason", "fixture", "--approval", "test-approval")
        resumed = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(resumed["current_step"], "S01")
        self.assertEqual(resumed["contract_sha256"], sha256(contract_path))


if __name__ == "__main__":
    unittest.main()
