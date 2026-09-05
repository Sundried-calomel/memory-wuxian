from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "install_transaction_workflow.py"
CONTRACT = PROJECT / "docs" / "install-transaction" / "contract.json"
EVIDENCE_SCHEMA = (
    PROJECT / "docs" / "install-transaction" / "evidence-manifest-schema.json"
)
VERIFIER_POLICY = (
    PROJECT / "docs" / "install-transaction" / "verifier-policy.json"
)
PLATFORM_FILES = (
    PROJECT / "scripts" / "platform_atomic.py",
    PROJECT / "scripts" / "platform_lock.py",
    PROJECT / "scripts" / "platform_transaction.py",
)
ADMISSION = (
    PROJECT
    / "docs"
    / "capability-admission"
    / "v2.20.0-installer-workflow"
)
CANDIDATE_BYTES = b"memory-wuxian-test-installer\n"
CANDIDATE_SHA = hashlib.sha256(CANDIDATE_BYTES).hexdigest()
FIXTURE_RESULTS = {
    "defect-preflight": {"status": "passed"},
    "workflow-governance-check": {"status": "passed"},
    "capability-admission-receipt": {"status": "allowed"},
    "state-machine-tests": {"failures": 0, "tests_run": 22},
    "project-hook-test": {"failures": 0, "tests_run": 2},
    "failed-candidate-evidence-freeze": {
        "identity_bound": True,
        "frozen_artifact_count": 1,
    },
    "entrypoint-inventory": {"complete": True, "entrypoint_count": 4},
    "packaged-call-chain-map": {"complete": True, "boundary_count": 5},
    "single-owner-decision": {"decision": "single-owner"},
    "recovery-architecture-contract": {
        "status": "frozen",
    },
    "exact-artifact-diagnostic-harness": {
        "status": "passed",
        "assertions": 4,
    },
    "no-product-write-proof": {"product_write_count": 0, "checks_run": 4},
    "broker-controller-trace": {"complete": True, "boundary_count": 4},
    "exact-root-cause-proof": {"status": "proven"},
    "owner-reachability-audit": {"complete": True, "owner_count": 1},
    "package-membership-audit": {"complete": True, "artifact_count": 12},
    "redundancy-disposition-table": {
        "unclassified_count": 0,
        "classified_count": 12,
    },
    "minimal-root-cause-diff": {"scope_status": "bounded"},
    "redundant-path-deletion-proof": {"unsupported_deletion_count": 0},
    "defect-rule-conformance": {"status": "passed"},
    "entrypoint-equivalence-tests": {"failures": 0, "tests_run": 8},
    "historical-regression-tests": {"failures": 0, "tests_run": 20},
    "diff-simplification-review": {
        "blocking_findings": 0,
        "reviewed_files": 7,
    },
    "candidate-source-commit": {"committed": True},
    "ci-artifact-sha256": {
        "hash_matches": True,
        "candidate_sha256": CANDIDATE_SHA,
    },
    "immutable-candidate-build": {
        "build_count": 1,
        "candidate_sha256": CANDIDATE_SHA,
    },
    "packaged-chain-rehearsal": {
        "status": "passed",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 12,
        "route_id": "inno-bootstrap-broker-controller-v1",
        "lane": "packaged-production",
    },
    "direct-rollback-supplement": {
        "lane": "direct-supplement",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 7,
    },
    "assertion-diagnostic-receipt": {
        "unclassified_failure_count": 0,
        "candidate_sha256": CANDIDATE_SHA,
        "assertion_count": 12,
    },
    "diagnostic-redaction-receipt": {
        "forbidden_field_count": 0,
        "candidate_sha256": CANDIDATE_SHA,
        "fields_checked": 9,
    },
    "clean-install-receipt": {
        "status": "passed",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 8,
        "route_id": "inno-bootstrap-broker-controller-v1",
        "lane": "packaged-production",
    },
    "upgrade-receipt": {
        "status": "passed",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 8,
        "route_id": "inno-bootstrap-broker-controller-v1",
        "lane": "packaged-production",
    },
    "repeat-install-receipt": {
        "status": "passed",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 8,
        "route_id": "inno-bootstrap-broker-controller-v1",
        "lane": "packaged-production",
    },
    "controlled-rollback-receipt": {
        "status": "passed",
        "candidate_sha256": CANDIDATE_SHA,
        "assertions": 8,
        "route_id": "inno-bootstrap-broker-controller-v1",
        "lane": "packaged-production",
    },
    "installed-runtime-effect-receipt": {
        "failed_effects": 0,
        "checked_effects": 7,
        "candidate_sha256": CANDIDATE_SHA,
        "effect_ids": [
            "archive-pointer",
            "focus",
            "process",
            "shortcut",
            "task",
            "telemetry",
            "watermark",
        ],
    },
    "cross-platform-ci": {
        "failed_jobs": 0,
        "jobs_run": 3,
        "candidate_sha256": CANDIDATE_SHA,
        "platform_ids": ["linux", "macos", "windows"],
        "job_ids": ["macos-candidate", "ubuntu-candidate", "windows-candidate"],
    },
    "full-relevant-tests": {
        "failures": 0,
        "tests_run": 120,
        "candidate_sha256": CANDIDATE_SHA,
        "suite_ids": [
            "installer-transaction",
            "platform-scheduler",
            "release-workflow-gate",
            "windows-installer",
            "windows-lifecycle-transaction",
        ],
    },
    "candidate-promotion": {
        "status": "promoted",
        "candidate_sha256": CANDIDATE_SHA,
    },
    "exact-ci-artifact-download": {
        "hash_match": True,
        "byte_length": len(CANDIDATE_BYTES),
        "candidate_sha256": CANDIDATE_SHA,
    },
    "real-target-upgrade-receipt": {
        "status": "passed",
        "assertions": 9,
        "candidate_sha256": CANDIDATE_SHA,
    },
    "no-rebuild-proof": {
        "rebuild_count": 0,
        "checks_run": 2,
        "candidate_sha256": CANDIDATE_SHA,
    },
    "release-asset-sha256-match": {
        "hash_match": True,
        "byte_length": len(CANDIDATE_BYTES),
        "candidate_sha256": CANDIDATE_SHA,
        "release_sha256": CANDIDATE_SHA,
    },
    "official-asset-reinstall": {
        "status": "passed",
        "assertions": 9,
        "candidate_sha256": CANDIDATE_SHA,
    },
    "defect-completion": {"status": "complete", "rules_checked": 4},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    command: list[str],
    cwd: Path,
    *,
    expect: int = 0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == expect, result.stdout + result.stderr
    return result


def git_paths(root: Path, *args: str) -> list[str]:
    output = subprocess.check_output(["git", *args], cwd=root)
    return [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in output.split(b"\0")
        if item
    ]


def current_overlay(root: Path) -> dict[str, str]:
    contract = json.loads(
        (root / "docs/install-transaction/contract.json").read_text(
            encoding="utf-8"
        )
    )

    def matched(path: str, patterns: list[str]) -> bool:
        return any(
            (
                path == pattern[:-3]
                or path.startswith(pattern[:-3] + "/")
            )
            if pattern.endswith("/**")
            else fnmatch.fnmatchcase(path, pattern)
            for pattern in patterns
        )

    paths = set(git_paths(root, "diff", "--name-only", "-z", "HEAD"))
    paths.update(
        git_paths(root, "ls-files", "--others", "--exclude-standard", "-z")
    )
    result: dict[str, str] = {}
    for relative in sorted(paths):
        if not matched(relative, contract["protected_paths"]) or matched(
            relative, contract["snapshot_policy"]["excluded_patterns"]
        ):
            continue
        path = root / relative
        result[relative] = sha256(path) if path.is_file() else "<deleted>"
    return result


def json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def refresh_admission(root: Path) -> None:
    admission = (
        root
        / "docs"
        / "capability-admission"
        / "v2.20.0-installer-workflow"
    )
    contract = json.loads(
        (root / "docs/install-transaction/contract.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_path = admission / "manifest.json"
    registry_path = admission / "registry.json"
    binding_path = admission / "binding.json"
    review_path = admission / "semantic-review.json"
    receipt_path = admission / "receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_sha256"] = json_sha256(
        {
            relative: sha256(root / relative)
            for relative in contract["control_plane_files"]
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_sha = sha256(manifest_path)
    registry_sha = sha256(registry_path)
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    candidate = binding["capabilities"][0]
    candidate["revision"] = manifest["revision"]
    candidate["manifest_sha256"] = manifest_sha
    binding["registry_sha256"] = registry_sha
    binding_path.write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = root / "docs/promotion-reviews/test-admission-evaluation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_paths = list(contract["control_plane_files"])
    report = {
        "schema_version": "1.0",
        "evaluation_id": "test-admission-evaluation",
        "artifact": {
            "paths": artifact_paths,
            "sha256": [sha256(root / path) for path in artifact_paths],
            "producer_identity": manifest["producer_identity"],
        },
        "evaluator": {
            "identity": "test-independent-evaluator",
            "independent": True,
        },
        "contract": {
            "source": "docs/install-transaction/contract.json",
            "pass_threshold": 90,
        },
        "dimensions": [
            {
                "id": "control-plane-correctness",
                "weight": 1.0,
                "score": 100,
                "mandatory": True,
                "evidence": [
                    {
                        "claim": "Fixture control-plane hashes are exact",
                        "source": "tests/test_install_transaction_workflow.py",
                        "method": "deterministic_test",
                    }
                ],
            }
        ],
        "deterministic_checks": [
            {
                "id": "fixture-hashes",
                "status": "pass",
                "evidence": "tests/test_install_transaction_workflow.py",
            }
        ],
        "overall_score": 100,
        "verdict": "pass",
        "blocking_findings": [],
        "limitations": ["Synthetic unit-test evaluator fixture."],
        "iteration": {"current": 1, "maximum": 1},
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence_ref = {
        "path": report_path.relative_to(root).as_posix(),
        "role": "independent-evaluation",
        "sha256": sha256(report_path),
    }
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review.update(
        {
            "candidate_id": manifest["capability_id"],
            "candidate_revision": manifest["revision"],
            "candidate_manifest_sha256": manifest_sha,
            "registry_sha256": registry_sha,
            "owner_id": manifest["owner_id"],
            "reviewer_identity": "test-independent-evaluator",
            "decision": "allow",
            "evidence": [
                json.dumps(
                    evidence_ref,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ],
        }
    )
    review_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    actions = {item["action_id"]: item for item in manifest["actions"]}
    allowed_actions = sorted(candidate["allowed_actions"])
    receipt = {
        "schema_version": 1,
        "receipt_id": "",
        "candidate_id": manifest["capability_id"],
        "candidate_revision": manifest["revision"],
        "owner_id": manifest["owner_id"],
        "project_id": binding["project_id"],
        "manifest_sha256": manifest_sha,
        "registry_sha256": registry_sha,
        "semantic_review_sha256": sha256(review_path),
        "binding_sha256": sha256(binding_path),
        "allowed_actions": allowed_actions,
        "authorization_gated_actions": sorted(
            action_id
            for action_id in allowed_actions
            if actions[action_id]["risk"] == "authorization-gated"
        ),
        "deterministic_check_version": 1,
        "status": "allowed",
    }
    payload = dict(receipt)
    payload.pop("receipt_id")
    receipt["receipt_id"] = f"car:{json_sha256(payload)}"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "docs" / "install-transaction").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    for platform_file in PLATFORM_FILES:
        shutil.copy2(platform_file, root / "scripts" / platform_file.name)
    shutil.copy2(
        CONTRACT, root / "docs" / "install-transaction" / "contract.json"
    )
    shutil.copy2(
        EVIDENCE_SCHEMA,
        root
        / "docs"
        / "install-transaction"
        / "evidence-manifest-schema.json",
    )
    shutil.copy2(
        VERIFIER_POLICY,
        root / "docs" / "install-transaction" / "verifier-policy.json",
    )
    shutil.copytree(
        ADMISSION,
        root
        / "docs"
        / "capability-admission"
        / "v2.20.0-installer-workflow",
    )
    (root / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    (root / "workflow-governance.json").write_text("{}\n", encoding="utf-8")
    support = root / "test-support"
    support.mkdir()
    (support / "sitecustomize.py").write_text(
        "import hashlib, json, os, subprocess\n"
        "from pathlib import Path\n"
        "_real_read_bytes = Path.read_bytes\n"
        "def _guarded_read_bytes(self):\n"
        "    if os.environ.get('MW_TEST_FORBID_STATE_READ_BYTES') == '1' and self.name == 'runtime-state.json':\n"
        "        raise AssertionError('runtime-state must use the bounded same-buffer reader')\n"
        "    return _real_read_bytes(self)\n"
        "Path.read_bytes = _guarded_read_bytes\n"
        "_real_run = subprocess.run\n"
        "def _fake_run(args, *positional, **kwargs):\n"
        "    values = [str(value) for value in args] if isinstance(args, (list, tuple)) else []\n"
        "    if values[:2] == ['gh', 'api'] and '/actions/runs/' in values[2]:\n"
        "        run_id = int(values[2].rsplit('/', 1)[1])\n"
        "        workflow = '.github/workflows/release.yml' if run_id == 43 else '.github/workflows/test.yml'\n"
        "        conclusion = 'failure' if run_id == 99 else 'success'\n"
        "        payload = {'id': run_id, 'run_attempt': 1, 'head_sha': os.environ['MW_TEST_HEAD'], 'path': workflow, 'status': 'completed', 'conclusion': conclusion}\n"
        "        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b'')\n"
        "    if values[:3] == ['gh', 'attestation', 'verify']:\n"
        "        digest = hashlib.sha256(Path(values[3]).read_bytes()).hexdigest()\n"
        "        if os.environ.get('MW_TEST_BAD_ATTESTATION') == '1':\n"
        "            digest = '0' * 64\n"
        "        payload = [{'attestation': {}, 'verificationResult': {'statement': {'subject': [{'digest': {'sha256': digest}}]}}}]\n"
        "        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b'')\n"
        "    return _real_run(args, *positional, **kwargs)\n"
        "subprocess.run = _fake_run\n",
        encoding="utf-8",
    )
    refresh_admission(root)
    run(["git", "init", "-q"], root)
    run(["git", "config", "user.email", "test@example.invalid"], root)
    run(["git", "config", "user.name", "test"], root)
    run(["git", "add", "."], root)
    run(["git", "commit", "-qm", "fixture"], root)
    return root


def workflow(
    root: Path,
    *args: str,
    expect: int = 0,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    support = root / "test-support"
    environment["PYTHONPATH"] = str(support) + os.pathsep + environment.get(
        "PYTHONPATH", ""
    )
    environment["MW_TEST_HEAD"] = run(
        ["git", "rev-parse", "HEAD"], root
    ).stdout.strip()
    if env_overrides:
        environment.update(env_overrides)
    return run(
        [sys.executable, "scripts/install_transaction_workflow.py", *args],
        root,
        expect=expect,
        env=environment,
    )


def capture_baseline(
    root: Path, *, legacy_state_sha256: str | None = None
) -> tuple[str, str]:
    document = {
        "schema_version": 1,
        "purpose": "test-explicit-baseline",
        "git_head": run(["git", "rev-parse", "HEAD"], root).stdout.strip(),
        "legacy_state_sha256": legacy_state_sha256,
        "overlay": current_overlay(root),
    }
    path = (
        root
        / "docs"
        / "install-transaction"
        / "evidence"
        / "baseline.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    return path.relative_to(root).as_posix(), sha256(path)


def status(root: Path) -> dict[str, object]:
    return json.loads(workflow(root, "status").stdout)


def write_evidence(
    root: Path,
    evidence_ids: list[str],
    *,
    invalid_artifacts_for: str | None = None,
    failing_result_for: str | None = None,
) -> list[str]:
    source_sha = str(status(root)["current_worktree_sha256"])
    arguments: list[str] = []
    evidence_dir = root / "docs" / "install-transaction" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    policy = json.loads(
        (
            root / "docs/install-transaction/verifier-policy.json"
        ).read_text(encoding="utf-8")
    )
    source_commit = run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    state = json.loads(
        (root / "docs/install-transaction/runtime-state.json").read_text(
            encoding="utf-8"
        )
    )
    step_id = str(state["current_step"])
    attempt = int(state["steps"][step_id]["attempt"])
    authorization = state["steps"][step_id].get("authorization")
    evaluator_ids = {
        "independent-architecture-evaluation",
        "independent-candidate-evaluation",
        "independent-final-evaluation",
    }
    for evidence_id in evidence_ids:
        requirement = policy["requirements"][evidence_id]
        if evidence_id not in FIXTURE_RESULTS and evidence_id not in evaluator_ids:
            raise AssertionError(f"No independent fixture for {evidence_id}")
        result = evidence_dir / f"{evidence_id}-result.json"
        result_document = dict(FIXTURE_RESULTS.get(evidence_id, {}))
        if evidence_id == "failed-candidate-evidence-freeze":
            result_document["frozen_snapshot_sha256"] = state["steps"]["S01"][
                "predecessor_snapshot_sha256"
            ]
        if evidence_id == "capability-admission-receipt":
            contract = json.loads(
                (root / "docs/install-transaction/contract.json").read_text(
                    encoding="utf-8"
                )
            )
            admission_receipt = (
                root
                / "docs/capability-admission/v2.20.0-installer-workflow"
                / "receipt.json"
            )
            result_document.update(
                {
                    "project_id": contract["project_id"],
                    "artifact_sha256": json_sha256(
                        {
                            relative: sha256(root / relative)
                            for relative in contract["control_plane_files"]
                        }
                    ),
                    "receipt_sha256": sha256(admission_receipt),
                }
            )
        if failing_result_for == evidence_id:
            if evidence_id == "state-machine-tests":
                result_document = {"failures": 1}
            else:
                raise AssertionError(
                    f"No negative fixture for {evidence_id}"
                )
        producer_kind = requirement["producer_kinds"][0]
        profile = policy["producer_profiles"][producer_kind]
        if producer_kind == "evaluator":
            if evidence_id == "independent-architecture-evaluation":
                evaluated_path = (
                    evidence_dir / "recovery-architecture-contract-result.json"
                )
            else:
                evaluated_path = root / "dist/MemoryWuxian-test-Setup.exe"
                evaluated_path.parent.mkdir(parents=True, exist_ok=True)
                evaluated_path.write_bytes(CANDIDATE_BYTES)
            evaluated_relative = evaluated_path.relative_to(root).as_posix()
            result_document = {
                "schema_version": "1.0",
                "evaluation_id": f"fixture-{evidence_id}",
                "artifact": {
                    "paths": [evaluated_relative],
                    "sha256": [sha256(evaluated_path)],
                    "producer_identity": "fixture-primary-producer",
                },
                "evaluator": {
                    "identity": "agent-output-evaluator",
                    "independent": True,
                },
                "contract": {
                    "source": "docs/install-transaction/contract.json",
                    "pass_threshold": 90,
                },
                "dimensions": [
                    {
                        "id": "correctness",
                        "weight": 1.0,
                        "score": 100,
                        "mandatory": True,
                        "evidence": [
                            {
                                "claim": "Fixture deterministic evidence passed",
                                "source": evaluated_relative,
                                "method": "deterministic_test",
                            }
                        ],
                    }
                ],
                "deterministic_checks": [
                    {
                        "id": "fixture-check",
                        "status": "pass",
                        "evidence": evaluated_relative,
                    }
                ],
                "overall_score": 100,
                "verdict": "pass",
                "blocking_findings": [],
                "limitations": ["Synthetic unit-test evaluator fixture."],
                "iteration": {"current": 1, "maximum": 1},
            }
            if evidence_id != "independent-architecture-evaluation":
                result_document["candidate_sha256"] = CANDIDATE_SHA
        if producer_kind == "ci":
            workflow_path = policy["external_provenance"]["ci"][
                "evidence_workflows"
            ][evidence_id]
            result_document["provenance"] = {
                "provider": "github-actions",
                "repository": "Sundried-calomel/memory-wuxian",
                "workflow_path": workflow_path,
                "head_sha": source_commit,
                "run_id": 43 if workflow_path.endswith("release.yml") else 42,
                "run_attempt": 1,
                "conclusion": "success",
            }
        result.write_text(
            json.dumps(result_document, indent=2) + "\n",
            encoding="utf-8",
        )
        subject_role = policy["subject_bindings"].get(evidence_id)
        subjects: list[dict[str, object]] = []
        if subject_role:
            candidate = root / "dist" / "MemoryWuxian-test-Setup.exe"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(CANDIDATE_BYTES)
            subjects.append(
                {
                    "path": candidate.relative_to(root).as_posix(),
                    "sha256": sha256(candidate),
                    "role": subject_role,
                }
            )
        document = {
            "schema_version": 1,
            "workflow_id": state["workflow_id"],
            "epoch_id": state["epoch_id"],
            "step_id": step_id,
            "attempt": attempt,
            "evidence_id": evidence_id,
            "authorization_receipt_sha256": (
                authorization["sha256"] if authorization else ""
            ),
            "producer": {
                "kind": producer_kind,
                "name": profile["name"],
                "version": profile["version"],
            },
            "invocation": {
                "argv": ["fixture", evidence_id],
                "cwd": ".",
                "runner": profile["runner"],
                "exit_code": 0,
                "source_commit": source_commit,
                "source_snapshot_sha256": source_sha,
            },
            "artifacts": (
                []
                if invalid_artifacts_for == evidence_id
                else [
                    {
                        "path": result.relative_to(root).as_posix(),
                        "sha256": sha256(result),
                        "role": requirement["artifact_role"],
                    }
                ]
            ),
            "subjects": subjects,
        }
        manifest = evidence_dir / f"{evidence_id}.json"
        manifest.write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        arguments.extend(
            [
                "--evidence-manifest",
                manifest.relative_to(root).as_posix(),
            ]
        )
    return arguments


def s01_ids() -> list[str]:
    return [
        "defect-preflight",
        "workflow-governance-check",
        "capability-admission-receipt",
        "state-machine-tests",
        "project-hook-test",
        "failed-candidate-evidence-freeze",
    ]


def required_ids(root: Path, step_id: str) -> list[str]:
    contract = json.loads(
        (root / "docs/install-transaction/contract.json").read_text(
            encoding="utf-8"
        )
    )
    return next(
        step["required_evidence"]
        for step in contract["steps"]
        if step["id"] == step_id
    )


def complete_and_advance(root: Path, step_id: str) -> None:
    workflow(
        root,
        "verify",
        step_id,
        *write_evidence(root, required_ids(root, step_id)),
    )
    workflow(root, "complete", step_id)
    workflow(root, "next")


class InstallTransactionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = make_project(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_contract_builds_and_hashes_candidate_before_platform_rehearsal(
        self,
    ) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in contract["steps"]}
        self.assertIn("build one immutable", steps["S09"]["title"])
        self.assertIn("frozen candidate", steps["S10"]["title"])
        self.assertEqual(
            contract["authorization_boundaries"],
            {
                "S14": "target-windows-uac-install",
                "S15": "github-push-merge-release-and-official-asset-reinstall",
            },
        )
        self.assertEqual(
            contract["independent_evaluation_steps"],
            ["S03", "S13", "S15"],
        )
        self.assertEqual(
            contract["evidence_trust_model"],
            {
                "local_process_records": "non-adversarial-process-audit",
                "github_actions_records": "workflow-attested",
                "independent_evaluator_records": "separate-process-attested",
            },
        )
        for step_id in [f"S{number:02d}" for number in range(1, 14)]:
            self.assertIsNone(steps[step_id]["authorization_scope"])
        for relative in contract["control_plane_files"]:
            self.assertTrue(
                any(
                    (
                        relative == pattern[:-3]
                        or relative.startswith(pattern[:-3] + "/")
                    )
                    if pattern.endswith("/**")
                    else fnmatch.fnmatchcase(relative, pattern)
                    for pattern in contract["protected_paths"]
                ),
                relative,
            )
        self.assertTrue(
            all(
                any(
                    (
                        relative == pattern[:-3]
                        or relative.startswith(pattern[:-3] + "/")
                    )
                    if pattern.endswith("/**")
                    else fnmatch.fnmatchcase(relative, pattern)
                    for pattern in steps["S01"]["allowed_paths"]
                )
                for relative in contract["control_plane_files"]
            )
        )
        self.assertIn("docs/promotion-reviews/**", steps["S01"]["allowed_paths"])

    def test_contract_rejects_an_overclaimed_evidence_trust_model(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        contract["evidence_trust_model"]["independent_evaluator_records"] = (
            "cryptographically-signed"
        )
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rejected = workflow(self.root, "status", expect=2)
        self.assertIn("evidence-trust-model-invalid", rejected.stderr)

    def test_init_status_and_project_scoped_path_gate(self) -> None:
        initialized = json.loads(workflow(self.root, "init").stdout)
        self.assertEqual(initialized["epoch_id"], "E001")
        self.assertEqual(initialized["current_step"], "S01")
        self.assertEqual(status(self.root)["delta_paths"], [])
        workflow(
            self.root,
            "hook",
            "pre-edit",
            "--path",
            "scripts/install_transaction_workflow.py",
        )
        workflow(
            self.root,
            "hook",
            "pre-edit",
            "--path",
            "notes/unrelated.txt",
        )
        denied = workflow(
            self.root,
            "hook",
            "pre-edit",
            "--path",
            "scripts/install_codex_autosync_windows.py",
            expect=2,
        )
        self.assertIn("protected-path-denied", denied.stderr)

    def test_dirty_init_requires_explicit_hash_bound_manifest(self) -> None:
        target = self.root / "docs" / "install-transaction" / "prepared.md"
        target.write_text("prepared\n", encoding="utf-8")
        denied = workflow(self.root, "init", expect=2)
        self.assertIn("baseline-manifest-required", denied.stderr)
        path, digest = capture_baseline(self.root)
        workflow(
            self.root,
            "init",
            "--baseline-manifest",
            path,
            "--baseline-manifest-sha256",
            digest,
        )
        self.assertEqual(status(self.root)["delta_paths"], [])

    def test_receipt_is_bound_to_evidence_identity_and_unchanged_bytes(
        self,
    ) -> None:
        workflow(self.root, "init")
        target = self.root / "docs" / "install-transaction" / "note.md"
        target.write_text("verified\n", encoding="utf-8")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(self.root, s01_ids()),
        )
        target.write_text("drifted\n", encoding="utf-8")
        failed = workflow(self.root, "complete", "S01", expect=2)
        self.assertIn("post-verification-drift", failed.stderr)
        state = json.loads(
            (
                self.root
                / "docs/install-transaction/runtime-state.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["steps"]["S01"]["attempt"], 1)
        self.assertEqual(state["steps"]["S01"]["superseded_receipts"], [])

    def test_verification_failures_automatically_stop_patch_loops(self) -> None:
        workflow(self.root, "init")
        first = workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        self.assertIn("one integrated remediation remains", first.stderr)
        second = workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        self.assertIn("needs replan", second.stderr)
        state = json.loads(
            (
                self.root
                / "docs/install-transaction/runtime-state.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "needs_replan")
        self.assertEqual(state["steps"]["S01"]["status"], "blocked")
        self.assertEqual(len(state["steps"]["S01"]["failed_attempts"]), 2)

    def test_completed_step_promotes_only_verified_snapshot(self) -> None:
        workflow(self.root, "init")
        target = self.root / "docs" / "install-transaction" / "s01.md"
        target.write_text("accepted\n", encoding="utf-8")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(self.root, s01_ids()),
        )
        workflow(self.root, "complete", "S01")
        workflow(self.root, "next")
        current = status(self.root)
        self.assertEqual(current["current_step"], "S02")
        self.assertEqual(current["delta_paths"], [])

    def test_commit_after_clean_baseline_remains_visible_as_real_drift(
        self,
    ) -> None:
        workflow(self.root, "init")
        target = self.root / "docs" / "install-transaction" / "later.md"
        target.write_text("later\n", encoding="utf-8")
        run(["git", "add", "docs/install-transaction/later.md"], self.root)
        run(["git", "commit", "-qm", "add later bytes"], self.root)
        self.assertEqual(
            status(self.root)["delta_paths"],
            ["docs/install-transaction/later.md"],
        )

    def test_committing_explicit_overlay_does_not_create_false_drift(
        self,
    ) -> None:
        target = (
            self.root
            / "docs"
            / "install-transaction"
            / "prepared-unicode-路径.md"
        )
        target.write_text("prepared\n", encoding="utf-8")
        path, digest = capture_baseline(self.root)
        workflow(
            self.root,
            "init",
            "--baseline-manifest",
            path,
            "--baseline-manifest-sha256",
            digest,
        )
        run(
            ["git", "add", "docs/install-transaction/prepared-unicode-路径.md"],
            self.root,
        )
        run(["git", "commit", "-qm", "persist prepared bytes"], self.root)
        self.assertEqual(status(self.root)["delta_paths"], [])

    def test_replan_never_promotes_failed_worktree_to_baseline(self) -> None:
        workflow(self.root, "init")
        workflow(self.root, "verify", "S01", expect=2)
        workflow(self.root, "verify", "S01", expect=2)
        contract_path = (
            self.root / "docs" / "install-transaction" / "contract.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["steps"][0]["title"] += " corrected"
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        workflow(
            self.root,
            "replan",
            "--resume-step",
            "S01",
            "--reason",
            "correct the earliest affected definition",
        )
        current = status(self.root)
        self.assertEqual(current["epoch_id"], "E002")
        self.assertIn(
            "docs/install-transaction/contract.json",
            current["delta_paths"],
        )
        help_text = workflow(self.root, "replan", "--help").stdout
        self.assertNotIn("--approval", help_text)

    def test_contract_drift_can_replan_without_manufactured_failures(
        self,
    ) -> None:
        workflow(self.root, "init")
        contract_path = (
            self.root / "docs" / "install-transaction" / "contract.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["steps"][4]["title"] += " corrected"
        contract_path.write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        result = workflow(
            self.root,
            "replan",
            "--resume-step",
            "S01",
            "--reason",
            "contract correction while S01 is active",
        )
        self.assertEqual(json.loads(result.stdout)["epoch_id"], "E002")
        self.assertIn(
            "docs/install-transaction/contract.json",
            status(self.root)["delta_paths"],
        )

    def test_self_certifying_evidence_without_artifact_is_rejected(
        self,
    ) -> None:
        workflow(self.root, "init")
        failed = workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                invalid_artifacts_for="state-machine-tests",
            ),
            expect=2,
        )
        self.assertIn("evidence-schema-invalid", failed.stderr)

    def test_producer_cannot_override_verifier_expected_value(self) -> None:
        workflow(self.root, "init")
        arguments = write_evidence(self.root, s01_ids())
        result_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests-result.json"
        )
        result_path.write_text('{"failures": 99}\n', encoding="utf-8")
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = sha256(result_path)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        failed = workflow(
            self.root, "verify", "S01", *arguments, expect=2
        )
        self.assertIn("verifier-policy-check-failed", failed.stderr)

    def test_receipt_content_drift_is_detected(self) -> None:
        workflow(self.root, "init")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(self.root, s01_ids()),
        )
        state_path = (
            self.root / "docs/install-transaction/runtime-state.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        receipt_path = self.root / state["steps"]["S01"]["verification"]["path"]
        receipt_path.write_text(
            receipt_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("receipt-drift", failed.stderr)

    def test_evidence_artifact_drift_is_detected_after_verification(self) -> None:
        workflow(self.root, "init")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(self.root, s01_ids()),
        )
        artifact = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests-result.json"
        )
        artifact.write_text('{"failures":0,"tampered":true}\n', encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("evidence-artifact-drift", failed.stderr)

    def test_failure_receipt_drift_is_detected(self) -> None:
        workflow(self.root, "init")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        failure_path = self.root / state["steps"]["S01"]["failed_attempts"][0]["path"]
        failure_path.write_bytes(failure_path.read_bytes() + b" ")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("receipt-drift", failed.stderr)

    def test_declared_producer_and_schema_types_are_enforced(self) -> None:
        workflow(self.root, "init")
        arguments = write_evidence(self.root, s01_ids())
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["producer"]["name"] = "self-declared-test"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = workflow(self.root, "verify", "S01", *arguments, expect=2)
        self.assertIn("evidence-producer-invalid", failed.stderr)
        state = json.loads(
            (self.root / "docs/install-transaction/runtime-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["steps"]["S01"]["remediation_cycles"], 0)

        arguments = write_evidence(self.root, s01_ids())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["invocation"]["exit_code"] = False
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = workflow(self.root, "verify", "S01", *arguments, expect=2)
        self.assertIn("evidence-schema-invalid", failed.stderr)

    def test_expected_integer_rejects_boolean_observation(self) -> None:
        workflow(self.root, "init")
        arguments = write_evidence(self.root, s01_ids())
        result_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests-result.json"
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["failures"] = False
        result_path.write_text(json.dumps(result), encoding="utf-8")
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = sha256(result_path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = workflow(self.root, "verify", "S01", *arguments, expect=2)
        self.assertIn("verifier-policy-check-failed", failed.stderr)

    def test_evidence_reads_are_bounded_before_json_parsing(self) -> None:
        workflow(self.root, "init")
        arguments = write_evidence(self.root, s01_ids())
        contract = json.loads(
            (self.root / "docs/install-transaction/contract.json").read_text(
                encoding="utf-8"
            )
        )
        result_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests-result.json"
        )
        result_path.write_bytes(
            b'{"failures":0,"padding":"'
            + b"x" * contract["evidence_policy"]["maximum_artifact_bytes"]
            + b'"}'
        )
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = sha256(result_path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        oversized_artifact = workflow(
            self.root, "verify", "S01", *arguments, expect=2
        )
        self.assertIn("bounded-read-limit", oversized_artifact.stderr)

        arguments = write_evidence(self.root, s01_ids())
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        raw = manifest_path.read_bytes()
        limit = contract["evidence_policy"]["maximum_manifest_bytes"]
        manifest_path.write_bytes(raw + b" " * (limit - len(raw) + 1))
        oversized_manifest = workflow(
            self.root, "verify", "S01", *arguments, expect=2
        )
        self.assertIn("bounded-read-limit", oversized_manifest.stderr)

    def test_evidence_json_rejects_duplicate_keys_and_nonfinite_values(self) -> None:
        workflow(self.root, "init")
        result_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests-result.json"
        )
        manifest_path = (
            self.root / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        invalid_documents = {
            "duplicate-key": b'{"failures":0,"failures":0,"tests_run":22}',
            "nonfinite": b'{"failures":0,"tests_run":NaN}',
        }
        for label, raw in invalid_documents.items():
            with self.subTest(label=label):
                arguments = write_evidence(self.root, s01_ids())
                result_path.write_bytes(raw)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["artifacts"][0]["sha256"] = sha256(result_path)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                failed = workflow(
                    self.root, "verify", "S01", *arguments, expect=2
                )
                self.assertIn("json-read-failed", failed.stderr)
                state = json.loads(
                    (
                        self.root / "docs/install-transaction/runtime-state.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(state["steps"]["S01"]["attempt"], 1)
                self.assertEqual(state["steps"]["S01"]["remediation_cycles"], 0)

    def test_malformed_step_state_fails_closed_without_traceback(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["steps"]["S01"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("state-step-invalid", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_orphan_failure_receipt_is_replayed_before_mutation(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        pre_failure_state = state_path.read_bytes()
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        state_path.write_bytes(pre_failure_state)
        blocked_read = workflow(self.root, "status", expect=2)
        self.assertIn("failure-recovery-required", blocked_read.stderr)
        recovered = workflow(self.root, "next", expect=2)
        self.assertIn("current-step-incomplete", recovered.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        entry = state["steps"]["S01"]
        self.assertEqual(entry["attempt"], 2)
        self.assertEqual(entry["remediation_cycles"], 1)
        self.assertEqual(len(entry["failed_attempts"]), 1)

    def test_renamed_orphan_failure_receipt_is_rejected_without_mutation(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        pre_failure_state = state_path.read_bytes()
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        state_path.write_bytes(pre_failure_state)
        receipt = next(
            (
                self.root / "docs/install-transaction/receipts/E001/S01"
            ).glob("attempt-01-verification-failure-*.json")
        )
        renamed = receipt.with_name(
            receipt.name[:-21] + "0" * 16 + ".json"
        )
        receipt.rename(renamed)
        failed = workflow(self.root, "next", expect=2)
        self.assertIn("failure-receipt-invalid", failed.stderr)
        self.assertEqual(state_path.read_bytes(), pre_failure_state)

    def test_orphan_failure_replay_uses_frozen_step_definition_during_drift(
        self,
    ) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        pre_failure_state = state_path.read_bytes()
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(
                self.root,
                s01_ids(),
                failing_result_for="state-machine-tests",
            ),
            expect=2,
        )
        state_path.write_bytes(pre_failure_state)
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["steps"][4]["title"] += " clarified"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        failed = workflow(self.root, "next", expect=2)
        self.assertIn("contract-drift", failed.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["S01"]["attempt"], 2)
        self.assertEqual(state["steps"]["S01"]["remediation_cycles"], 1)

    def test_verification_receipt_is_attempt_scoped(self) -> None:
        workflow(self.root, "init")
        workflow(
            self.root,
            "verify",
            "S01",
            *write_evidence(self.root, s01_ids()),
        )
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["steps"]["S01"]["attempt"] = 2
        state_path.write_text(json.dumps(state), encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("receipt-identity-invalid", failed.stderr)

    def test_top_level_contract_drift_replans_from_s01(self) -> None:
        workflow(self.root, "init")
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["title"] += " corrected"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        result = workflow(
            self.root,
            "replan",
            "--resume-step",
            "S01",
            "--reason",
            "top-level contract correction",
        )
        self.assertEqual(json.loads(result.stdout)["earliest_affected_step"], "S01")

    def test_step_definition_drift_can_still_replan(self) -> None:
        workflow(self.root, "init")
        for number in range(1, 4):
            complete_and_advance(self.root, f"S{number:02d}")
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["steps"][2]["title"] += " corrected"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        result = workflow(
            self.root,
            "replan",
            "--resume-step",
            "S01",
            "--reason",
            "step contract correction",
        )
        self.assertEqual(json.loads(result.stdout)["earliest_affected_step"], "S01")

    def test_later_replan_preserves_s01_binding_to_its_predecessor(self) -> None:
        workflow(self.root, "init")
        complete_and_advance(self.root, "S01")
        change = self.root / "docs/install-transaction/s02-observation.md"
        change.write_text("observed\n", encoding="utf-8")
        complete_and_advance(self.root, "S02")
        complete_and_advance(self.root, "S03")

        for expected_code in (
            "verifier-policy-check-failed",
            "verifier-policy-check-failed",
        ):
            arguments = write_evidence(self.root, required_ids(self.root, "S04"))
            result_path = (
                self.root
                / "docs/install-transaction/evidence/no-product-write-proof-result.json"
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["product_write_count"] = 1
            result_path.write_text(json.dumps(result), encoding="utf-8")
            manifest_path = (
                self.root
                / "docs/install-transaction/evidence/no-product-write-proof.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][0]["sha256"] = sha256(result_path)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            failed = workflow(
                self.root, "verify", "S04", *arguments, expect=2
            )
            self.assertIn(expected_code, failed.stderr)

        replanned = json.loads(
            workflow(
                self.root,
                "replan",
                "--resume-step",
                "S04",
                "--reason",
                "preserve verified S01-S03 after bounded S04 failure",
            ).stdout
        )
        self.assertEqual(replanned["earliest_affected_step"], "S04")
        current = status(self.root)
        self.assertEqual(current["epoch_id"], "E002")
        self.assertEqual(current["current_step"], "S04")
        state = json.loads(
            (
                self.root / "docs/install-transaction/runtime-state.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(state["steps"]["S01"]["status"], "completed")

    def test_replan_does_not_overwrite_preexisting_record(self) -> None:
        workflow(self.root, "init")
        sentinel = self.root / "docs/install-transaction/receipts/E002/replan-from-E001.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("sentinel", encoding="utf-8")
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["title"] += " corrected"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        workflow(
            self.root,
            "replan",
            "--resume-step",
            "S01",
            "--reason",
            "preserve immutable evidence",
        )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "sentinel")
        records = list(sentinel.parent.glob("replan-from-E001-*.json"))
        self.assertEqual(len(records), 1)

    def test_s09_freezes_candidate_and_s10_rejects_other_bytes(self) -> None:
        workflow(self.root, "init")
        for number in range(1, 9):
            complete_and_advance(self.root, f"S{number:02d}")
        uncommitted = self.root / "docs/install-transaction/s09-overlay.md"
        uncommitted.write_text("not committed\n", encoding="utf-8")
        denied = workflow(self.root, "verify", "S09", expect=2)
        self.assertIn("candidate-source-uncommitted", denied.stderr)
        uncommitted.unlink()

        s09_arguments = write_evidence(
            self.root, required_ids(self.root, "S09")
        )
        provenance_path = (
            self.root
            / "docs/install-transaction/evidence/candidate-source-commit-result.json"
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["provenance"]["run_id"] = 99
        provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
        provenance_manifest_path = (
            self.root
            / "docs/install-transaction/evidence/candidate-source-commit.json"
        )
        provenance_manifest = json.loads(
            provenance_manifest_path.read_text(encoding="utf-8")
        )
        provenance_manifest["artifacts"][0]["sha256"] = sha256(
            provenance_path
        )
        provenance_manifest_path.write_text(
            json.dumps(provenance_manifest), encoding="utf-8"
        )
        failed_provenance = workflow(
            self.root, "verify", "S09", *s09_arguments, expect=2
        )
        self.assertIn("evidence-provenance-failed", failed_provenance.stderr)

        complete_and_advance(self.root, "S09")
        state = json.loads(
            (self.root / "docs/install-transaction/runtime-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["current_step"], "S10")
        self.assertEqual(
            state["freezes"]["candidate"]["artifact_sha256"], CANDIDATE_SHA
        )
        receipt_path = self.root / state["steps"]["S09"]["verification"]["path"]
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for proof in receipt["origin_proofs"].values():
            self.assertRegex(
                proof["result_attestation_response_sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertRegex(proof["attested_result_sha256"], r"^[0-9a-f]{64}$")
        candidate_path = self.root / "dist/MemoryWuxian-test-Setup.exe"
        candidate_path.write_bytes(b"tampered installer\n")
        tampered = workflow(self.root, "status", expect=2)
        self.assertIn("evidence-subject-drift", tampered.stderr)
        candidate_path.write_bytes(CANDIDATE_BYTES)
        arguments = write_evidence(self.root, required_ids(self.root, "S10"))
        result_path = (
            self.root
            / "docs/install-transaction/evidence/packaged-chain-rehearsal-result.json"
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["candidate_sha256"] = "c" * 64
        result_path.write_text(json.dumps(result), encoding="utf-8")
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/packaged-chain-rehearsal.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = sha256(result_path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = workflow(self.root, "verify", "S10", *arguments, expect=2)
        self.assertIn("evidence-freeze-binding-invalid", failed.stderr)
        arguments = write_evidence(self.root, required_ids(self.root, "S10"))
        rehearsal_path = (
            self.root
            / "docs/install-transaction/evidence/packaged-chain-rehearsal-result.json"
        )
        rehearsal = json.loads(rehearsal_path.read_text(encoding="utf-8"))
        del rehearsal["route_id"]
        rehearsal_path.write_text(json.dumps(rehearsal), encoding="utf-8")
        rehearsal_manifest_path = (
            self.root
            / "docs/install-transaction/evidence/packaged-chain-rehearsal.json"
        )
        rehearsal_manifest = json.loads(
            rehearsal_manifest_path.read_text(encoding="utf-8")
        )
        rehearsal_manifest["artifacts"][0]["sha256"] = sha256(rehearsal_path)
        rehearsal_manifest_path.write_text(
            json.dumps(rehearsal_manifest), encoding="utf-8"
        )
        missing_route = workflow(
            self.root, "verify", "S10", *arguments, expect=2
        )
        self.assertIn("verifier-observation-missing", missing_route.stderr)

    def test_attestation_output_must_name_the_verified_subject_digest(self) -> None:
        from scripts import install_transaction_workflow as controller

        artifact = self.root / "candidate.bin"
        artifact.write_bytes(CANDIDATE_BYTES)
        wrong = [
            {
                "attestation": {},
                "verificationResult": {
                    "statement": {
                        "subject": [{"digest": {"sha256": "0" * 64}}]
                    }
                },
            }
        ]
        completed = subprocess.CompletedProcess(
            ["gh", "attestation", "verify"],
            0,
            json.dumps(wrong).encode("utf-8"),
            b"",
        )
        controller._ATTESTATION_CACHE.clear()
        with mock.patch.object(controller.subprocess, "run", return_value=completed):
            with self.assertRaises(controller.GateFailure) as caught:
                controller.verify_github_attestation(
                    "owner/repository",
                    ".github/workflows/test.yml",
                    "a" * 40,
                    artifact,
                    sha256(artifact),
                )
        self.assertEqual(
            caught.exception.code, "artifact-attestation-subject-mismatch"
        )

    def test_ci_evidence_in_one_group_cannot_mix_run_attempts(self) -> None:
        from scripts import install_transaction_workflow as controller

        policy = json.loads(VERIFIER_POLICY.read_text(encoding="utf-8"))
        evidence_ids = policy["external_provenance"]["ci"][
            "provenance_groups"
        ]["candidate-build"]
        proofs: dict[str, dict[str, object]] = {}
        for evidence_id in evidence_ids:
            proofs[evidence_id] = {
                "repository": "Sundried-calomel/memory-wuxian",
                "workflow_path": ".github/workflows/test.yml",
                "head_sha": "a" * 40,
                "run_id": 42,
                "run_attempt": 1,
            }
        proofs[evidence_ids[-1]]["run_attempt"] = 2
        with self.assertRaises(controller.WorkflowError) as caught:
            controller.validate_external_proof_bindings(
                policy,
                {"freezes": {}},
                {},
                proofs,
                evidence_ids,
            )
        self.assertEqual(caught.exception.code, "ci-provenance-group-mismatch")

    def test_event_chain_rejects_an_unlinked_second_event(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        event = {
            "schema_version": 2,
            "workflow_id": state["workflow_id"],
            "epoch_id": state["epoch_id"],
            "sequence": 2,
            "timestamp": "2026-09-05T00:00:00+00:00",
            "event_type": "forged-event",
            "previous_event_sha256": None,
            "previous_event_ref": None,
            "payload": {},
        }
        event["event_sha256"] = json_sha256(event)
        event_bytes = json.dumps(
            event,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        event_digest = hashlib.sha256(event_bytes).hexdigest()
        event_path = (
            self.root
            / "docs/install-transaction/events/E001"
            / f"000002-forged-event-{event_digest[:16]}.json"
        )
        event_path.write_bytes(event_bytes)
        state["event_sequence"] = 2
        state["last_event_sha256"] = event["event_sha256"]
        state["last_event_ref"] = {
            "path": event_path.relative_to(self.root).as_posix(),
            "sha256": event_digest,
            "epoch_id": "E001",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("event-chain-invalid", failed.stderr)

    def test_status_and_prepare_migration_work_before_admission(self) -> None:
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state_path.write_text(
            json.dumps({"schema_version": 2, "workflow_id": "legacy"}),
            encoding="utf-8",
        )
        observed = json.loads(workflow(self.root, "status").stdout)
        self.assertTrue(observed["migration_required"])
        prepared = json.loads(workflow(self.root, "prepare-migration").stdout)
        self.assertEqual(prepared["status"], "prepared")
        self.assertTrue((self.root / prepared["path"]).is_file())

    def test_migration_rejects_unknown_predecessor_schema(self) -> None:
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workflow_id": "memory-wuxian-v2.20.0-unified-installer",
                }
            ),
            encoding="utf-8",
        )
        baseline, digest = capture_baseline(
            self.root, legacy_state_sha256=sha256(state_path)
        )
        failed = workflow(
            self.root,
            "migrate",
            "--baseline-manifest",
            baseline,
            "--baseline-manifest-sha256",
            digest,
            "--reason",
            "unsupported predecessor",
            expect=2,
        )
        self.assertIn("legacy-schema-invalid", failed.stderr)

    def test_migration_rejects_other_workflow_identity(self) -> None:
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state_path.write_text(
            json.dumps({"schema_version": 2, "workflow_id": "other-workflow"}),
            encoding="utf-8",
        )
        baseline, digest = capture_baseline(
            self.root, legacy_state_sha256=sha256(state_path)
        )
        failed = workflow(
            self.root,
            "migrate",
            "--baseline-manifest",
            baseline,
            "--baseline-manifest-sha256",
            digest,
            "--reason",
            "wrong predecessor",
            expect=2,
        )
        self.assertIn("legacy-identity-invalid", failed.stderr)

    def test_legacy_migration_archives_state_and_excludes_generated_paths(
        self,
    ) -> None:
        legacy_path = (
            self.root / "docs/install-transaction/runtime-state.json"
        )
        legacy_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "workflow_id": "memory-wuxian-v2.20.0-unified-installer",
                    "status": "active",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        excluded = self.root / "docs/install-transaction/evidence/ignored.json"
        excluded.parent.mkdir(parents=True, exist_ok=True)
        excluded.write_text(
            "generated\n", encoding="utf-8"
        )
        legacy_sha = sha256(legacy_path)
        baseline, digest = capture_baseline(
            self.root, legacy_state_sha256=legacy_sha
        )
        workflow(
            self.root,
            "migrate",
            "--baseline-manifest",
            baseline,
            "--baseline-manifest-sha256",
            digest,
            "--reason",
            "replace legacy control plane",
            env_overrides={"MW_TEST_FORBID_STATE_READ_BYTES": "1"},
        )
        state = json.loads(legacy_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 3)
        self.assertEqual(state["epoch_baseline"]["overlay"], {})
        archived = self.root / state["legacy_state"]["path"]
        self.assertTrue(archived.is_file())
        self.assertEqual(sha256(archived), legacy_sha)

    def test_package_import_and_admission_status(self) -> None:
        (self.root / "platform_atomic.py").write_text(
            "raise RuntimeError('shadow module loaded')\n", encoding="utf-8"
        )
        imported = run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "import scripts.install_transaction_workflow as workflow; "
                    "print(workflow.atomic_replace_bytes.__module__)"
                ),
            ],
            self.root,
        )
        self.assertEqual(imported.stdout.strip(), "scripts.platform_atomic")
        admitted = json.loads(workflow(self.root, "admission-status").stdout)
        self.assertEqual(admitted["status"], "allowed")
        review_path = (
            self.root
            / "docs/capability-admission/v2.20.0-installer-workflow"
            / "semantic-review.json"
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["decision"] = "block"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        denied = workflow(self.root, "admission-status", expect=2)
        self.assertIn("admission-review-invalid", denied.stderr)

    def test_admission_rejects_drifted_independent_report(self) -> None:
        admitted = json.loads(workflow(self.root, "admission-status").stdout)
        self.assertEqual(admitted["status"], "allowed")
        report_path = (
            self.root / "docs/promotion-reviews/test-admission-evaluation.json"
        )
        report_path.write_bytes(report_path.read_bytes() + b" ")
        denied = workflow(self.root, "admission-status", expect=2)
        self.assertIn("admission-review-evidence-invalid", denied.stderr)

    def test_completed_step_without_receipt_is_rejected(self) -> None:
        workflow(self.root, "init")
        state_path = self.root / "docs/install-transaction/runtime-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["steps"]["S01"]["status"] = "completed"
        state["steps"]["S01"]["completed_at"] = "test"
        state["current_step"] = None
        state_path.write_text(json.dumps(state), encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("state-verification-missing", failed.stderr)

    def test_command_exit_failure_does_not_consume_remediation(self) -> None:
        workflow(self.root, "init")
        arguments = write_evidence(self.root, s01_ids())
        manifest_path = (
            self.root
            / "docs/install-transaction/evidence/state-machine-tests.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["invocation"]["exit_code"] = 3
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        failed = workflow(self.root, "verify", "S01", *arguments, expect=2)
        self.assertIn("evidence-command-failed", failed.stderr)
        state = json.loads(
            (self.root / "docs/install-transaction/runtime-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["steps"]["S01"]["remediation_cycles"], 0)
        self.assertEqual(state["steps"]["S01"]["attempt"], 1)

    def test_schema_drift_requires_replan_and_unknown_keywords_fail_closed(
        self,
    ) -> None:
        workflow(self.root, "init")
        schema_path = (
            self.root
            / "docs/install-transaction/evidence-manifest-schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        schema["title"] += " corrected"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        drifted = workflow(self.root, "status", expect=2)
        self.assertTrue(
            "evidence-schema-drift" in drifted.stderr
            or "control-plane-drift" in drifted.stderr
        )
        replanned = json.loads(
            workflow(
                self.root,
                "replan",
                "--resume-step",
                "S01",
                "--reason",
                "schema correction",
                env_overrides={"MW_TEST_FORBID_STATE_READ_BYTES": "1"},
            ).stdout
        )
        self.assertEqual(replanned["earliest_affected_step"], "S01")
        schema["maxProperties"] = 20
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        failed = workflow(self.root, "status", expect=2)
        self.assertIn("evidence-schema-definition-invalid", failed.stderr)

    def test_next_refuses_a_needs_replan_terminal_state(self) -> None:
        from scripts import install_transaction_workflow as controller

        state = {
            "status": "needs_replan",
            "current_step": None,
            "steps": {"S15": {"status": "blocked"}},
        }
        with mock.patch.object(controller, "load_contract", return_value={}), mock.patch.object(
            controller, "load_state", return_value=state
        ):
            with self.assertRaises(controller.WorkflowError) as caught:
                controller.cmd_next(object())
        self.assertEqual(caught.exception.code, "workflow-not-active")

    def test_full_lifecycle_through_authorized_s15(self) -> None:
        workflow(self.root, "init")
        for number in range(1, 13):
            complete_and_advance(self.root, f"S{number:02d}")
        s13_arguments = write_evidence(
            self.root, required_ids(self.root, "S13")
        )
        evaluation_path = (
            self.root
            / "docs/install-transaction/evidence/independent-candidate-evaluation-result.json"
        )
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        evaluation["contract"]["source"] = "docs/other-contract.json"
        evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
        evaluation_manifest_path = (
            self.root
            / "docs/install-transaction/evidence/independent-candidate-evaluation.json"
        )
        evaluation_manifest = json.loads(
            evaluation_manifest_path.read_text(encoding="utf-8")
        )
        evaluation_manifest["artifacts"][0]["sha256"] = sha256(
            evaluation_path
        )
        evaluation_manifest_path.write_text(
            json.dumps(evaluation_manifest), encoding="utf-8"
        )
        wrong_contract = workflow(
            self.root, "verify", "S13", *s13_arguments, expect=2
        )
        self.assertIn("evaluator-report-invalid", wrong_contract.stderr)

        s13_arguments = write_evidence(
            self.root, required_ids(self.root, "S13")
        )
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        unrelated = self.root / "docs/install-transaction/contract.json"
        evaluation["artifact"]["paths"] = [
            unrelated.relative_to(self.root).as_posix()
        ]
        evaluation["artifact"]["sha256"] = [sha256(unrelated)]
        evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
        evaluation_manifest = json.loads(
            evaluation_manifest_path.read_text(encoding="utf-8")
        )
        evaluation_manifest["artifacts"][0]["sha256"] = sha256(
            evaluation_path
        )
        evaluation_manifest_path.write_text(
            json.dumps(evaluation_manifest), encoding="utf-8"
        )
        unbound_evaluation = workflow(
            self.root, "verify", "S13", *s13_arguments, expect=2
        )
        self.assertIn(
            "evaluator-artifact-binding-invalid", unbound_evaluation.stderr
        )
        complete_and_advance(self.root, "S13")
        self.assertEqual(status(self.root)["current_step"], "S14")
        unauthorised = workflow(
            self.root,
            "verify",
            "S14",
            *write_evidence(self.root, required_ids(self.root, "S14")),
            expect=2,
        )
        self.assertIn("authorization-required", unauthorised.stderr)
        workflow(
            self.root,
            "authorize",
            "S14",
            "--scope",
            "target-windows-uac-install",
            "--authorization-id",
            "test-user-authorized-s14",
        )
        s14_arguments = write_evidence(
            self.root, required_ids(self.root, "S14")
        )
        download_result = (
            self.root
            / "docs/install-transaction/evidence/exact-ci-artifact-download-result.json"
        )
        download = json.loads(download_result.read_text(encoding="utf-8"))
        download["byte_length"] += 1
        download_result.write_text(json.dumps(download), encoding="utf-8")
        download_manifest_path = (
            self.root
            / "docs/install-transaction/evidence/exact-ci-artifact-download.json"
        )
        download_manifest = json.loads(
            download_manifest_path.read_text(encoding="utf-8")
        )
        download_manifest["artifacts"][0]["sha256"] = sha256(download_result)
        download_manifest_path.write_text(
            json.dumps(download_manifest), encoding="utf-8"
        )
        wrong_size = workflow(
            self.root, "verify", "S14", *s14_arguments, expect=2
        )
        self.assertIn("evidence-subject-size-invalid", wrong_size.stderr)
        s14_arguments = write_evidence(
            self.root, required_ids(self.root, "S14")
        )
        install_result = (
            self.root
            / "docs/install-transaction/evidence/real-target-upgrade-receipt-result.json"
        )
        install = json.loads(install_result.read_text(encoding="utf-8"))
        install["status"] = "failed"
        install_result.write_text(json.dumps(install), encoding="utf-8")
        install_manifest_path = (
            self.root
            / "docs/install-transaction/evidence/real-target-upgrade-receipt.json"
        )
        install_manifest = json.loads(
            install_manifest_path.read_text(encoding="utf-8")
        )
        install_manifest["artifacts"][0]["sha256"] = sha256(install_result)
        install_manifest_path.write_text(
            json.dumps(install_manifest), encoding="utf-8"
        )
        failed_install = workflow(
            self.root, "verify", "S14", *s14_arguments, expect=2
        )
        self.assertIn("verifier-policy-check-failed", failed_install.stderr)
        failed_state = json.loads(
            (
                self.root / "docs/install-transaction/runtime-state.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(failed_state["steps"]["S14"]["attempt"], 2)
        self.assertNotIn("authorization", failed_state["steps"]["S14"])
        workflow(
            self.root,
            "authorize",
            "S14",
            "--scope",
            "target-windows-uac-install",
            "--authorization-id",
            "test-user-reauthorized-s14-attempt-2",
        )
        complete_and_advance(self.root, "S14")
        workflow(
            self.root,
            "authorize",
            "S15",
            "--scope",
            "github-push-merge-release-and-official-asset-reinstall",
            "--authorization-id",
            "test-user-authorized-s15",
        )
        workflow(
            self.root,
            "verify",
            "S15",
            *write_evidence(self.root, required_ids(self.root, "S15")),
        )
        workflow(self.root, "complete", "S15")
        completed = json.loads(workflow(self.root, "next").stdout)
        self.assertEqual(completed["status"], "completed")
        final_state = json.loads(
            (self.root / "docs/install-transaction/runtime-state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(final_state["status"], "completed")
        self.assertEqual(
            final_state["freezes"]["release"]["artifact_sha256"],
            final_state["freezes"]["candidate"]["artifact_sha256"],
        )
        contract_path = self.root / "docs/install-transaction/contract.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["title"] += " next release"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        replanned = json.loads(
            workflow(
                self.root,
                "replan",
                "--resume-step",
                "S01",
                "--reason",
                "completed workflow control-plane change",
            ).stdout
        )
        self.assertEqual(replanned["earliest_affected_step"], "S01")


if __name__ == "__main__":
    unittest.main()
