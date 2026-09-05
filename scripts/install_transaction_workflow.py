#!/usr/bin/env python3
"""Evidence-bound controller for the project-local installer workflow."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

if __package__:
    from scripts.platform_atomic import atomic_replace_bytes
    from scripts.platform_lock import exclusive_lock
    from scripts.platform_transaction import (
        atomic_write_canonical_json,
        canonical_json_bytes,
        decode_strict_json,
    )
else:  # pragma: no cover - exercised by CLI subprocess tests
    from platform_atomic import atomic_replace_bytes
    from platform_lock import exclusive_lock
    from platform_transaction import (
        atomic_write_canonical_json,
        canonical_json_bytes,
        decode_strict_json,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs" / "install-transaction" / "contract.json"
DEFAULT_STATE = ROOT / "docs" / "install-transaction" / "runtime-state.json"
RECEIPTS = ROOT / "docs" / "install-transaction" / "receipts"
EVENTS = ROOT / "docs" / "install-transaction" / "events"
LEGACY = ROOT / "docs" / "install-transaction" / "legacy"
WORKFLOW_LOCK = ROOT / "docs" / "install-transaction" / "workflow.lock"
STATE_SCHEMA_VERSION = 3
CONTRACT_SCHEMA_VERSION = 3
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EPOCH_PATTERN = re.compile(r"^E[0-9]{3,}$")
MAX_CONTROL_JSON_BYTES = 4 * 1024 * 1024
_VERIFIED_COMMITS: set[str] = set()
_CI_RUN_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_ATTESTATION_CACHE: dict[tuple[str, str, str, str], str] = {}
_CONTRACT_SOURCE_HASHES: dict[str, str] = {}


class WorkflowError(RuntimeError):
    def __init__(self, message: str, *, code: str = "workflow-invalid") -> None:
        super().__init__(message)
        self.code = code


class GateFailure(WorkflowError):
    """A validated negative quality result that consumes remediation budget."""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bounded_file(
    path: Path,
    maximum_bytes: int,
    *,
    capture: bool = False,
) -> tuple[str, int, bytes | None]:
    """Hash at most maximum_bytes and optionally return those exact bytes."""

    digest = hashlib.sha256()
    total = 0
    chunks: list[bytes] | None = [] if capture else None
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(min(1024 * 1024, maximum_bytes - total + 1))
                if not block:
                    break
                total += len(block)
                if total > maximum_bytes:
                    raise WorkflowError(
                        f"File exceeds bounded-read limit: {path}",
                        code="bounded-read-limit",
                    )
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)
    except OSError as exc:
        raise WorkflowError(
            f"Unable to read file {path}: {exc}", code="bounded-read-failed"
        ) from exc
    return digest.hexdigest(), total, b"".join(chunks) if chunks is not None else None


def json_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def read_json_with_sha(
    path: Path, maximum_bytes: int = MAX_CONTROL_JSON_BYTES
) -> tuple[dict[str, Any], str]:
    value, digest, _ = read_json_capture(path, maximum_bytes)
    return value, digest


def read_json_capture(
    path: Path, maximum_bytes: int = MAX_CONTROL_JSON_BYTES
) -> tuple[dict[str, Any], str, bytes]:
    digest, _, raw = bounded_file(path, maximum_bytes, capture=True)
    assert raw is not None
    value = json_object_from_bytes(raw, path)
    return value, digest, raw


def read_json(path: Path) -> dict[str, Any]:
    value, _ = read_json_with_sha(path)
    return value


def contract_source_sha256(contract: dict[str, Any]) -> str:
    identity = json_sha256(contract)
    digest = _CONTRACT_SOURCE_HASHES.get(identity)
    if digest is not None:
        return digest
    observed, digest = read_json_with_sha(DEFAULT_CONTRACT)
    if not exact_json_equal(observed, contract):
        raise WorkflowError(
            "Contract bytes changed after loading", code="contract-read-drift"
        )
    _CONTRACT_SOURCE_HASHES[identity] = digest
    return digest


def json_object_from_bytes(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        value = decode_strict_json(raw)
    except ValueError as exc:
        raise WorkflowError(
            f"Unable to read JSON {path}: {exc}", code="json-read-failed"
        ) from exc
    if not isinstance(value, dict):
        raise WorkflowError(
            f"JSON root must be an object: {path}", code="json-shape-invalid"
        )
    return value


def exact_json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            exact_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            exact_json_equal(a, b) for a, b in zip(left, right)
        )
    return bool(left == right)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_canonical_json(path, value)


def git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise WorkflowError(
            message or f"git {' '.join(args)} failed", code="git-failed"
        )
    return result.stdout


def normalize(path: str) -> str:
    candidate = path.replace("\\", "/")
    if candidate.startswith("./"):
        candidate = candidate[2:]
    resolved = (ROOT / candidate).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise WorkflowError(
            f"Path escapes project root: {path}", code="path-outside-project"
        ) from exc


def matches(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if path == prefix or path.startswith(prefix + "/"):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def is_volatile(contract: dict[str, Any], path: str) -> bool:
    return matches(path, contract["snapshot_policy"]["excluded_patterns"])


def git_paths(*args: str) -> list[str]:
    return [
        normalize(item.decode("utf-8", errors="surrogateescape"))
        for item in git(*args).split(b"\0")
        if item
    ]


def changed_paths(contract: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    names.update(git_paths("diff", "--name-only", "-z", "HEAD"))
    names.update(git_paths("ls-files", "--others", "--exclude-standard", "-z"))
    return sorted(
        path
        for path in names
        if matches(path, contract["protected_paths"])
        and not is_volatile(contract, path)
    )


def worktree_map(contract: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in changed_paths(contract):
        path = ROOT / relative
        result[relative] = sha256_file(path) if path.is_file() else "<deleted>"
    return result


def current_snapshot(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "commit": git("rev-parse", "HEAD").decode().strip(),
        "overlay": worktree_map(contract),
    }


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    return json_sha256(snapshot)


def committed_file_hashes(
    commit: str, relatives: list[str]
) -> dict[str, str]:
    if not relatives:
        return {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    request = b"".join(
        f"{commit}:{relative}\n".encode("utf-8") for relative in relatives
    )
    output, error = process.communicate(request)
    if process.returncode:
        raise WorkflowError(
            error.decode("utf-8", errors="replace").strip()
            or "git cat-file --batch failed",
            code="baseline-read-failed",
        )
    offset = 0
    result: dict[str, str] = {}
    for relative in relatives:
        line_end = output.find(b"\n", offset)
        if line_end < 0:
            raise WorkflowError(
                "Malformed git cat-file response", code="baseline-read-failed"
            )
        header = output[offset:line_end]
        offset = line_end + 1
        if header.endswith(b" missing"):
            result[relative] = "<deleted>"
            continue
        fields = header.rsplit(b" ", 2)
        if len(fields) != 3 or fields[1] != b"blob":
            raise WorkflowError(
                f"Baseline path is not a blob: {relative}",
                code="baseline-read-failed",
            )
        size = int(fields[2])
        content = output[offset : offset + size]
        offset += size
        if output[offset : offset + 1] != b"\n":
            raise WorkflowError(
                "Malformed git cat-file payload", code="baseline-read-failed"
            )
        offset += 1
        result[relative] = sha256_bytes(content)
    return result


def delta_from_snapshot(
    contract: dict[str, Any],
    baseline: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    current = current or current_snapshot(contract)
    paths = set(baseline.get("overlay", {})) | set(current["overlay"])
    if baseline["commit"] != current["commit"]:
        paths.update(
            path
            for path in git_paths(
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                baseline["commit"],
                current["commit"],
            )
            if matches(path, contract["protected_paths"])
            and not is_volatile(contract, path)
        )
    result: dict[str, str] = {}
    ordered = sorted(
        path for path in paths if not is_volatile(contract, path)
    )
    before_committed = committed_file_hashes(
        str(baseline["commit"]),
        [path for path in ordered if path not in baseline.get("overlay", {})],
    )
    after_committed = committed_file_hashes(
        str(current["commit"]),
        [
            path
            for path in ordered
            if path not in current.get("overlay", {})
            and path not in baseline.get("overlay", {})
        ],
    )
    for relative in ordered:
        before = (
            baseline["overlay"][relative]
            if relative in baseline.get("overlay", {})
            else before_committed[relative]
        )
        after = (
            current["overlay"][relative]
            if relative in current.get("overlay", {})
            else (
                sha256_file(ROOT / relative)
                if relative in baseline.get("overlay", {})
                and (ROOT / relative).is_file()
                else (
                    "<deleted>"
                    if relative in baseline.get("overlay", {})
                    else after_committed[relative]
                )
            )
        )
        if before != after:
            result[relative] = after
    return result, current


def step_sha256(step: dict[str, Any]) -> str:
    return json_sha256(step)


def contract_core_sha256(contract: dict[str, Any]) -> str:
    return json_sha256(
        {key: value for key, value in contract.items() if key != "steps"}
    )


def evidence_definition_hashes(
    contract: dict[str, Any],
    policy: dict[str, Any],
    evidence_schema_sha256: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for step in contract["steps"]:
        for evidence_id in step["required_evidence"]:
            requirement = policy["requirements"][evidence_id]
            kind = requirement["producer_kinds"][0]
            result[evidence_id] = json_sha256(
                {
                    "requirement": requirement,
                    "producer": policy["producer_profiles"][kind],
                    "captures": policy["captures"].get(evidence_id, {}),
                    "artifact_hash_captures": policy[
                        "artifact_hash_captures"
                    ].get(evidence_id, {}),
                    "freeze_bindings": policy["freeze_bindings"].get(
                        evidence_id, {}
                    ),
                    "subject_binding": policy["subject_bindings"].get(
                        evidence_id
                    ),
                    "subject_size_binding": policy[
                        "subject_size_bindings"
                    ].get(evidence_id),
                    "state_bindings": policy["state_bindings"].get(
                        evidence_id, {}
                    ),
                    "exact_values": policy["exact_values"].get(
                        evidence_id, {}
                    ),
                    "external_provenance": {
                        "profile": policy["external_provenance"].get(kind),
                        "workflow": policy["external_provenance"]
                        .get("ci", {})
                        .get("evidence_workflows", {})
                        .get(evidence_id),
                        "attested_subject": policy["external_provenance"]
                        .get("ci", {})
                        .get("attested_subjects", {})
                        .get(evidence_id),
                    },
                    "minimums": policy["minimums"].get(evidence_id, {}),
                    "evidence_schema_sha256": evidence_schema_sha256,
                }
            )
    return result


def load_contract() -> dict[str, Any]:
    contract, contract_sha = read_json_with_sha(DEFAULT_CONTRACT)
    _CONTRACT_SOURCE_HASHES[json_sha256(contract)] = contract_sha
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise WorkflowError(
            "Unsupported installer contract schema", code="contract-schema-invalid"
        )
    required_top_level = {
        "schema_version",
        "workflow_id",
        "project_id",
        "admission_directory",
        "control_plane_files",
        "title",
        "maximum_integrated_remediation_cycles",
        "snapshot_policy",
        "evidence_policy",
        "evidence_trust_model",
        "independent_evaluation_steps",
        "authorization_boundaries",
        "authorization_actions",
        "protected_paths",
        "freeze_points",
        "steps",
    }
    if set(contract) != required_top_level:
        raise WorkflowError(
            "Installer contract is not a closed definition",
            code="contract-shape-invalid",
        )
    ids = [step.get("id") for step in contract.get("steps", [])]
    if ids != [f"S{i:02d}" for i in range(1, 16)]:
        raise WorkflowError(
            "Contract must contain ordered S01-S15 exactly",
            code="contract-order-invalid",
        )
    if not exact_json_equal(
        contract.get("maximum_integrated_remediation_cycles"), 1
    ):
        raise WorkflowError(
            "Exactly one integrated remediation cycle is required",
            code="remediation-contract-invalid",
        )
    required = {
        "id",
        "title",
        "prerequisites",
        "allowed_paths",
        "required_evidence",
        "authorization_scope",
    }
    seen_evidence: set[str] = set()
    for index, step in enumerate(contract["steps"]):
        if set(step) != required:
            raise WorkflowError(
                f"Step {step.get('id')} is not a closed definition",
                code="contract-step-invalid",
            )
        expected_prerequisites = [] if index == 0 else [ids[index - 1]]
        if step["prerequisites"] != expected_prerequisites:
            raise WorkflowError(
                f"Step {step['id']} must have exactly the preceding step as prerequisite",
                code="contract-prerequisite-invalid",
            )
        for field in ("allowed_paths", "required_evidence"):
            values = step[field]
            if (
                not isinstance(values, list)
                or not values
                or not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values))
            ):
                raise WorkflowError(
                    f"Step {step['id']} has invalid {field}",
                    code="contract-step-invalid",
                )
        duplicates = seen_evidence.intersection(step["required_evidence"])
        if duplicates:
            raise WorkflowError(
                f"Evidence ids are reused across steps: {sorted(duplicates)}",
                code="contract-evidence-invalid",
            )
        seen_evidence.update(step["required_evidence"])
    declared = {
        step["id"]: step["authorization_scope"]
        for step in contract["steps"]
        if step["authorization_scope"]
    }
    boundaries = contract.get("authorization_boundaries", {})
    if declared != boundaries or set(boundaries) != {"S14", "S15"}:
        raise WorkflowError(
            "Only S14 and S15 may require user authorization",
            code="authorization-contract-invalid",
        )
    if set(contract["authorization_actions"]) != set(boundaries) or any(
        not isinstance(actions, list)
        or not actions
        or not all(isinstance(action, str) and action for action in actions)
        or len(actions) != len(set(actions))
        for actions in contract["authorization_actions"].values()
    ):
        raise WorkflowError(
            "Authorization actions must be closed for S14 and S15",
            code="authorization-actions-invalid",
        )
    if contract.get("independent_evaluation_steps") != ["S03", "S13", "S15"]:
        raise WorkflowError(
            "Independent evaluation gates must be S03, S13, and S15",
            code="evaluation-contract-invalid",
        )
    snapshot_policy = contract["snapshot_policy"]
    if snapshot_policy != {
        "excluded_patterns": snapshot_policy.get("excluded_patterns"),
        "initial_dirty_tree_requires_manifest": True,
        "replan_baseline": "last-completed-verification",
        "replan_may_promote_current_worktree": False,
    } or not isinstance(snapshot_policy["excluded_patterns"], list):
        raise WorkflowError(
            "Snapshot policy is invalid", code="snapshot-policy-invalid"
        )
    evidence_policy = contract["evidence_policy"]
    expected_evidence_fields = {
        "schema_path",
        "verifier_policy_path",
        "directory",
        "artifact_roots",
        "subject_roots",
        "maximum_manifest_bytes",
        "maximum_artifacts_per_manifest",
        "maximum_artifact_bytes",
        "maximum_total_artifact_bytes",
        "maximum_subjects_per_manifest",
        "maximum_subject_bytes",
    }
    if set(evidence_policy) != expected_evidence_fields:
        raise WorkflowError(
            "Evidence policy is not closed", code="evidence-policy-invalid"
        )
    if contract["evidence_trust_model"] != {
        "local_process_records": "non-adversarial-process-audit",
        "github_actions_records": "workflow-attested",
        "independent_evaluator_records": "separate-process-attested",
    }:
        raise WorkflowError(
            "Evidence trust model is invalid", code="evidence-trust-model-invalid"
        )
    for field in ("artifact_roots", "subject_roots"):
        roots = evidence_policy[field]
        if (
            not isinstance(roots, list)
            or not roots
            or len(roots) != len(set(roots))
            or not all(
                isinstance(root, str) and root and normalize(root) == root
                for root in roots
            )
        ):
            raise WorkflowError(
                f"Evidence policy {field} is invalid",
                code="evidence-policy-invalid",
            )
    for field in (
        "maximum_manifest_bytes",
        "maximum_artifacts_per_manifest",
        "maximum_artifact_bytes",
        "maximum_total_artifact_bytes",
        "maximum_subjects_per_manifest",
        "maximum_subject_bytes",
    ):
        value = evidence_policy[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise WorkflowError(
                f"Evidence policy {field} is invalid",
                code="evidence-policy-invalid",
            )
    freezes = contract["freeze_points"]
    if set(freezes) != {"architecture", "candidate", "release"}:
        raise WorkflowError(
            "Freeze points are invalid", code="freeze-contract-invalid"
        )
    expected_freezes = {
        "architecture": {
            "step": "S03",
            "capture": "architecture_sha256",
        },
        "candidate": {"step": "S09", "capture": "candidate_sha256"},
        "release": {
            "step": "S15",
            "capture": "release_sha256",
            "must_equal": "candidate",
        },
    }
    if freezes != expected_freezes:
        raise WorkflowError(
            "Freeze points do not match the S03/S09/S15 identity contract",
            code="freeze-contract-invalid",
        )
    for field in ("control_plane_files", "protected_paths"):
        values = contract[field]
        if (
            not isinstance(values, list)
            or not values
            or len(values) != len(set(values))
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise WorkflowError(
                f"Contract {field} is invalid", code="contract-path-invalid"
            )
    missing_control_files = [
        path
        for path in contract["control_plane_files"]
        if normalize(path) != path or not (ROOT / path).is_file()
    ]
    if missing_control_files:
        raise WorkflowError(
            "Control-plane files are missing or unsafe: "
            + ", ".join(missing_control_files),
            code="control-plane-file-invalid",
        )
    required_control_files = {
        evidence_policy["schema_path"],
        evidence_policy["verifier_policy_path"],
        "scripts/install_transaction_workflow.py",
    }
    if not required_control_files.issubset(contract["control_plane_files"]):
        raise WorkflowError(
            "Executed schema, policy, and controller must be control-plane files",
            code="control-plane-file-invalid",
        )
    unprotected_control_files = [
        path
        for path in contract["control_plane_files"]
        if not matches(path, contract["protected_paths"])
    ]
    if unprotected_control_files:
        raise WorkflowError(
            "Control-plane files are outside protected scope: "
            + ", ".join(unprotected_control_files),
            code="control-plane-scope-invalid",
        )
    volatile_control_files = [
        path
        for path in contract["control_plane_files"]
        if is_volatile(contract, path)
    ]
    if volatile_control_files:
        raise WorkflowError(
            "Control-plane files cannot be excluded from snapshots: "
            + ", ".join(volatile_control_files),
            code="control-plane-scope-invalid",
        )
    evidence_directory = normalize(evidence_policy["directory"])
    if evidence_directory != evidence_policy["directory"] or not all(
        root == evidence_directory
        or root.startswith(evidence_directory + "/")
        for root in evidence_policy["artifact_roots"]
    ):
        raise WorkflowError(
            "Evidence artifact roots must remain inside the evidence directory",
            code="evidence-policy-invalid",
        )
    if not matches(
        evidence_directory + "/placeholder",
        snapshot_policy["excluded_patterns"],
    ):
        raise WorkflowError(
            "Evidence directory must be excluded from source snapshots",
            code="evidence-policy-invalid",
        )
    for subject_root in evidence_policy["subject_roots"]:
        if not matches(
            subject_root + "/placeholder",
            snapshot_policy["excluded_patterns"],
        ):
            raise WorkflowError(
                f"Evidence subject root must be excluded from source snapshots: {subject_root}",
                code="evidence-policy-invalid",
            )
    return contract


def load_verifier_policy(
    contract: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    path = ROOT / normalize(
        str(contract["evidence_policy"]["verifier_policy_path"])
    )
    policy, policy_sha = read_json_with_sha(path)
    if set(policy) != {
        "schema_version",
        "policy_id",
        "producer_profiles",
        "external_provenance",
        "captures",
        "artifact_hash_captures",
        "freeze_bindings",
        "subject_bindings",
        "subject_size_bindings",
        "state_bindings",
        "exact_values",
        "minimums",
        "requirements",
    } or not exact_json_equal(policy.get("schema_version"), 1):
        raise WorkflowError(
            "Verifier policy has an invalid schema",
            code="verifier-policy-invalid",
        )
    profiles = policy.get("producer_profiles")
    if not isinstance(profiles, dict) or set(profiles) != {
        "command", "test", "ci", "inspection", "evaluator"
    }:
        raise WorkflowError(
            "Verifier producer profiles are invalid",
            code="verifier-policy-invalid",
        )
    for kind, profile in profiles.items():
        if (
            not isinstance(profile, dict)
            or set(profile) != {"name", "version", "runner"}
            or not all(isinstance(value, str) and value for value in profile.values())
        ):
            raise WorkflowError(
                f"Verifier producer profile is invalid: {kind}",
                code="verifier-policy-invalid",
            )
    expected_ids = {
        evidence_id
        for step in contract["steps"]
        for evidence_id in step["required_evidence"]
    }
    if set(policy["requirements"]) != expected_ids:
        missing = sorted(expected_ids - set(policy["requirements"]))
        extra = sorted(set(policy["requirements"]) - expected_ids)
        raise WorkflowError(
            f"Verifier policy coverage mismatch; missing={missing}, extra={extra}",
            code="verifier-policy-coverage-invalid",
        )
    required = {
        "producer_kinds",
        "artifact_role",
        "json_pointer",
        "expected",
    }
    for evidence_id, requirement in policy["requirements"].items():
        if (
            not isinstance(requirement, dict)
            or set(requirement) != required
            or not isinstance(requirement["producer_kinds"], list)
            or len(requirement["producer_kinds"]) != 1
            or requirement["producer_kinds"][0] not in profiles
        ):
            raise WorkflowError(
                f"Verifier policy requirement is invalid: {evidence_id}",
                code="verifier-policy-requirement-invalid",
            )
    for section in ("captures", "freeze_bindings"):
        mapping = policy[section]
        if not isinstance(mapping, dict) or not set(mapping).issubset(expected_ids):
            raise WorkflowError(
                f"Verifier {section} are invalid",
                code="verifier-policy-invalid",
            )
        for evidence_id, values in mapping.items():
            if (
                not isinstance(values, dict)
                or not values
                or not all(
                    isinstance(name, str)
                    and name
                    and isinstance(pointer, str)
                    and pointer.startswith("/")
                    for name, pointer in values.items()
                )
            ):
                raise WorkflowError(
                    f"Verifier {section} entry is invalid: {evidence_id}",
                    code="verifier-policy-invalid",
                )
    artifact_captures = policy["artifact_hash_captures"]
    if (
        not isinstance(artifact_captures, dict)
        or not set(artifact_captures).issubset(expected_ids)
    ):
        raise WorkflowError(
            "Verifier artifact captures are invalid",
            code="verifier-policy-invalid",
        )
    for evidence_id, values in artifact_captures.items():
        if (
            not isinstance(values, dict)
            or not values
            or not all(
                isinstance(name, str)
                and name
                and isinstance(role, str)
                and role
                for name, role in values.items()
            )
        ):
            raise WorkflowError(
                f"Verifier artifact capture is invalid: {evidence_id}",
                code="verifier-policy-invalid",
            )
    subject_bindings = policy["subject_bindings"]
    if (
        not isinstance(subject_bindings, dict)
        or not set(subject_bindings).issubset(expected_ids)
        or not all(
            isinstance(role, str) and role
            for role in subject_bindings.values()
        )
    ):
        raise WorkflowError(
            "Verifier subject bindings are invalid",
            code="verifier-policy-invalid",
        )
    subject_size_bindings = policy["subject_size_bindings"]
    if (
        not isinstance(subject_size_bindings, dict)
        or not set(subject_size_bindings).issubset(subject_bindings)
    ):
        raise WorkflowError(
            "Verifier subject-size bindings are invalid",
            code="verifier-policy-invalid",
        )
    for evidence_id, binding_spec in subject_size_bindings.items():
        if (
            not isinstance(binding_spec, dict)
            or set(binding_spec) != {"role", "json_pointer"}
            or binding_spec["role"] != subject_bindings[evidence_id]
            or not isinstance(binding_spec["json_pointer"], str)
            or not binding_spec["json_pointer"].startswith("/")
        ):
            raise WorkflowError(
                f"Verifier subject-size binding is invalid: {evidence_id}",
                code="verifier-policy-invalid",
            )
    for section in ("state_bindings", "exact_values"):
        mapping = policy[section]
        if not isinstance(mapping, dict) or not set(mapping).issubset(expected_ids):
            raise WorkflowError(
                f"Verifier {section} are invalid",
                code="verifier-policy-invalid",
            )
        for evidence_id, values in mapping.items():
            if (
                not isinstance(values, dict)
                or not values
                or not all(
                    isinstance(pointer, str)
                    and pointer.startswith("/")
                    and (
                        section == "exact_values"
                        or isinstance(expected, str)
                        and expected.startswith("/")
                    )
                    for pointer, expected in values.items()
                )
            ):
                raise WorkflowError(
                    f"Verifier {section} entry is invalid: {evidence_id}",
                    code="verifier-policy-invalid",
                )
    minimums = policy["minimums"]
    if not isinstance(minimums, dict) or not set(minimums).issubset(expected_ids):
        raise WorkflowError(
            "Verifier minimums are invalid", code="verifier-policy-invalid"
        )
    for evidence_id, checks in minimums.items():
        if (
            not isinstance(checks, dict)
            or not checks
            or not all(
                isinstance(pointer, str)
                and pointer.startswith("/")
                and isinstance(threshold, int)
                and not isinstance(threshold, bool)
                and threshold >= 1
                for pointer, threshold in checks.items()
            )
        ):
            raise WorkflowError(
                f"Verifier minimums entry is invalid: {evidence_id}",
                code="verifier-policy-invalid",
            )
    freeze_names = set(contract["freeze_points"])
    referenced_freezes = {
        name
        for values in policy["freeze_bindings"].values()
        for name in values
    }
    if not referenced_freezes.issubset(freeze_names):
        raise WorkflowError(
            "Verifier binding references an unknown freeze",
            code="verifier-policy-invalid",
        )
    provenance = policy["external_provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"evaluator", "ci"}:
        raise WorkflowError(
            "Verifier external provenance is invalid",
            code="verifier-policy-invalid",
        )
    evaluator = provenance["evaluator"]
    if (
        not isinstance(evaluator, dict)
        or set(evaluator)
        != {
            "schema_version",
            "minimum_pass_threshold",
            "contract_source",
            "artifact_bindings",
        }
        or evaluator["schema_version"] != "1.0"
        or evaluator["contract_source"]
        != "docs/install-transaction/contract.json"
        or not isinstance(evaluator["minimum_pass_threshold"], int)
        or isinstance(evaluator["minimum_pass_threshold"], bool)
        or not 0 <= evaluator["minimum_pass_threshold"] <= 100
    ):
        raise WorkflowError(
            "Verifier evaluator provenance is invalid",
            code="verifier-policy-invalid",
        )
    evaluator_ids = {
        evidence_id
        for evidence_id, requirement in policy["requirements"].items()
        if requirement["producer_kinds"] == ["evaluator"]
    }
    artifact_bindings = evaluator["artifact_bindings"]
    if (
        not isinstance(artifact_bindings, dict)
        or set(artifact_bindings) != evaluator_ids
    ):
        raise WorkflowError(
            "Verifier evaluator artifact bindings are invalid",
            code="verifier-policy-invalid",
        )
    for evidence_id, binding in artifact_bindings.items():
        if (
            not isinstance(binding, dict)
            or len(binding) != 1
            or set(binding) not in ({"capture"}, {"freeze"})
            or not isinstance(next(iter(binding.values())), str)
            or not next(iter(binding.values()))
        ):
            raise WorkflowError(
                f"Verifier evaluator artifact binding is invalid: {evidence_id}",
                code="verifier-policy-invalid",
            )
        if "freeze" in binding and binding["freeze"] not in contract["freeze_points"]:
            raise WorkflowError(
                f"Verifier evaluator binding references an unknown freeze: {evidence_id}",
                code="verifier-policy-invalid",
            )
    ci = provenance["ci"]
    ci_ids = {
        evidence_id
        for evidence_id, requirement in policy["requirements"].items()
        if requirement["producer_kinds"] == ["ci"]
    }
    if (
        not isinstance(ci, dict)
        or set(ci)
         != {
             "repository",
             "evidence_workflows",
             "attest_result_artifacts",
             "attested_subjects",
             "provenance_groups",
         }
        or not isinstance(ci["repository"], str)
        or not ci["repository"]
        or not isinstance(ci["evidence_workflows"], dict)
        or set(ci["evidence_workflows"]) != ci_ids
        or not all(
            isinstance(path, str) and path.startswith(".github/workflows/")
            for path in ci["evidence_workflows"].values()
        )
         or not isinstance(ci["attested_subjects"], dict)
         or not set(ci["attested_subjects"]).issubset(ci_ids)
         or not isinstance(ci["provenance_groups"], dict)
         or ci["attest_result_artifacts"] is not True
    ):
        raise WorkflowError(
            "Verifier CI provenance is invalid",
            code="verifier-policy-invalid",
        )
    for evidence_id, attestation in ci["attested_subjects"].items():
        if (
            not isinstance(attestation, dict)
            or set(attestation) != {"role", "signer_workflow"}
            or attestation["role"] != subject_bindings.get(evidence_id)
            or not isinstance(attestation["signer_workflow"], str)
            or not attestation["signer_workflow"].startswith(
                ".github/workflows/"
            )
        ):
            raise WorkflowError(
                f"Verifier CI attestation is invalid: {evidence_id}",
                code="verifier-policy-invalid",
             )
    grouped_ids: set[str] = set()
    evidence_steps = {
        evidence_id: step["id"]
        for step in contract["steps"]
        for evidence_id in step["required_evidence"]
    }
    for group_id, members in ci["provenance_groups"].items():
        if (
            not isinstance(group_id, str)
            or not group_id
            or not isinstance(members, list)
            or len(members) < 2
            or not all(isinstance(item, str) and item in ci_ids for item in members)
            or len(members) != len(set(members))
            or grouped_ids.intersection(members)
            or len({evidence_steps[item] for item in members}) != 1
        ):
            raise WorkflowError(
                f"Verifier CI provenance group is invalid: {group_id}",
                code="verifier-policy-invalid",
            )
        grouped_ids.update(members)
    return policy, policy_sha


def capability_artifact_sha256(contract: dict[str, Any]) -> str:
    identity = {
        relative: sha256_file(ROOT / normalize(relative))
        for relative in contract["control_plane_files"]
    }
    return json_sha256(identity)


def validate_admission_evaluation(
    contract: dict[str, Any],
    manifest: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    evidence = review.get("evidence")
    if (
        not isinstance(evidence, list)
        or len(evidence) != 1
        or not isinstance(evidence[0], str)
    ):
        raise WorkflowError(
            "Capability review must bind one independent evaluation report",
            code="admission-review-evidence-invalid",
        )
    try:
        evidence_ref = decode_strict_json(evidence[0].encode("utf-8"))
    except ValueError as exc:
        raise WorkflowError(
            "Capability review evidence reference is invalid",
            code="admission-review-evidence-invalid",
        ) from exc
    if (
        not isinstance(evidence_ref, dict)
        or set(evidence_ref) != {"path", "role", "sha256"}
        or evidence_ref.get("role") != "independent-evaluation"
        or evidence[0]
        != canonical_json_bytes(evidence_ref).decode("utf-8", errors="strict")
    ):
        raise WorkflowError(
            "Capability review evidence reference is not canonical",
            code="admission-review-evidence-invalid",
        )
    relative = normalize(str(evidence_ref["path"]))
    if not matches(relative, ["docs/promotion-reviews/**"]):
        raise WorkflowError(
            "Capability review evidence is outside promotion reviews",
            code="admission-review-evidence-invalid",
        )
    path = ROOT / relative
    report_sha, _, report_raw = bounded_file(
        path,
        int(contract["evidence_policy"]["maximum_artifact_bytes"]),
        capture=True,
    )
    if report_sha != evidence_ref.get("sha256"):
        raise WorkflowError(
            "Capability review evidence hash drift",
            code="admission-review-evidence-invalid",
        )
    assert report_raw is not None
    report = json_object_from_bytes(report_raw, path)
    policy, _ = load_verifier_policy(contract)
    proof = validate_evaluator_report(
        contract,
        report,
        report_sha,
        int(
            policy["external_provenance"]["evaluator"][
                "minimum_pass_threshold"
            ]
        ),
    )
    expected_artifacts = sorted(
        [
            {
                "path": relative_path,
                "sha256": sha256_file(ROOT / relative_path),
            }
            for relative_path in contract["control_plane_files"]
        ],
        key=lambda item: item["path"],
    )
    observed_artifacts = sorted(
        [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in proof["artifacts"]
        ],
        key=lambda item: item["path"],
    )
    if (
        observed_artifacts != expected_artifacts
        or proof["producer_identity"] != manifest.get("producer_identity")
        or proof["evaluator_identity"] != review.get("reviewer_identity")
    ):
        raise WorkflowError(
            "Capability review does not cover the exact control plane",
            code="admission-review-evidence-invalid",
        )
    return proof


def check_admission(contract: dict[str, Any], action_id: str) -> None:
    admission = ROOT / normalize(contract["admission_directory"])
    manifest_path = admission / "manifest.json"
    registry_path = admission / "registry.json"
    binding_path = admission / "binding.json"
    review_path = admission / "semantic-review.json"
    receipt_path = admission / "receipt.json"
    for path in (
        manifest_path,
        registry_path,
        binding_path,
        review_path,
        receipt_path,
    ):
        if not path.is_file():
            raise WorkflowError(
                f"Capability admission file is missing: {path.relative_to(ROOT)}",
                code="admission-missing",
            )
    manifest, manifest_sha = read_json_with_sha(manifest_path)
    registry, registry_sha = read_json_with_sha(registry_path)
    binding, binding_sha = read_json_with_sha(binding_path)
    review, review_sha = read_json_with_sha(review_path)
    receipt, _ = read_json_with_sha(receipt_path)
    if manifest.get("artifact_sha256") != capability_artifact_sha256(contract):
        raise WorkflowError(
            "Capability artifact hash drift", code="admission-artifact-drift"
        )
    if set(registry) != {"schema_version", "registry_id", "capabilities"} or (
        not exact_json_equal(registry.get("schema_version"), 1)
        or not exact_json_equal(
            registry.get("capabilities"), ["manifest.json"]
        )
    ):
        raise WorkflowError(
            "Capability registry is not closed", code="admission-registry-open"
        )
    bindings = binding.get("capabilities")
    if (
        not exact_json_equal(binding.get("schema_version"), 1)
        or binding.get("project_id") != contract["project_id"]
        or binding.get("registry_sha256") != registry_sha
        or not isinstance(bindings, list)
        or len(bindings) != 1
        or not isinstance(bindings[0], dict)
    ):
        raise WorkflowError(
            "Capability binding is invalid", code="admission-binding-drift"
        )
    candidate = bindings[0]
    manifest_actions = manifest.get("actions")
    manifest_resources = manifest.get("resources")
    manifest_permissions = manifest.get("permissions")
    if (
        not isinstance(manifest_actions, list)
        or not isinstance(manifest_resources, list)
        or not isinstance(manifest_permissions, list)
        or not all(isinstance(permission, str) for permission in manifest_permissions)
    ):
        raise WorkflowError(
            "Capability manifest collections are invalid",
            code="admission-manifest-invalid",
        )
    action_map = {
        action.get("action_id"): action
        for action in manifest_actions
        if isinstance(action, dict) and action.get("action_id")
    }
    resource_ids = {
        resource.get("resource_id")
        for resource in manifest_resources
        if isinstance(resource, dict)
    }
    allowed_actions = candidate.get("allowed_actions")
    allowed_permissions = candidate.get("allowed_permissions")
    allowed_resources = candidate.get("allowed_resources")
    if (
        candidate.get("capability_id") != manifest.get("capability_id")
        or candidate.get("revision") != manifest.get("revision")
        or candidate.get("manifest_sha256") != manifest_sha
        or not isinstance(allowed_actions, list)
        or not allowed_actions
        or not isinstance(allowed_permissions, list)
        or not isinstance(allowed_resources, list)
        or not all(isinstance(value, str) for value in allowed_actions)
        or not all(isinstance(value, str) for value in allowed_permissions)
        or not all(isinstance(value, str) for value in allowed_resources)
        or len(allowed_actions) != len(set(allowed_actions))
        or not set(allowed_actions).issubset(action_map)
        or not set(allowed_permissions).issubset(set(manifest_permissions))
        or not set(allowed_resources).issubset(resource_ids)
    ):
        raise WorkflowError(
            "Capability binding does not match the manifest",
            code="admission-binding-drift",
        )
    expected_review = {
        "candidate_id": manifest.get("capability_id"),
        "candidate_revision": manifest.get("revision"),
        "candidate_manifest_sha256": manifest_sha,
        "registry_sha256": registry_sha,
        "owner_id": manifest.get("owner_id"),
    }
    if (
        not exact_json_equal(review.get("schema_version"), 1)
        or review.get("decision") != "allow"
        or review.get("reviewer_identity") == manifest.get("producer_identity")
        or any(
            not exact_json_equal(review.get(field), value)
            for field, value in expected_review.items()
        )
    ):
        raise WorkflowError(
            "Capability semantic review is invalid", code="admission-review-invalid"
        )
    validate_admission_evaluation(contract, manifest, review)
    gated_actions = sorted(
        action_id
        for action_id in allowed_actions
        if action_map[action_id].get("risk") == "authorization-gated"
    )
    expected_receipt = {
        "schema_version": 1,
        "receipt_id": "",
        "candidate_id": manifest.get("capability_id"),
        "candidate_revision": manifest.get("revision"),
        "owner_id": manifest.get("owner_id"),
        "project_id": contract["project_id"],
        "manifest_sha256": manifest_sha,
        "registry_sha256": registry_sha,
        "semantic_review_sha256": review_sha,
        "binding_sha256": binding_sha,
        "allowed_actions": sorted(allowed_actions),
        "authorization_gated_actions": gated_actions,
        "deterministic_check_version": 1,
        "status": "allowed",
    }
    receipt_payload = dict(expected_receipt)
    receipt_payload.pop("receipt_id")
    expected_receipt["receipt_id"] = f"car:{json_sha256(receipt_payload)}"
    if not exact_json_equal(receipt, expected_receipt):
        raise WorkflowError(
            "Capability receipt does not match current admissible state",
            code="admission-hash-drift",
        )
    if action_id not in expected_receipt["allowed_actions"]:
        raise WorkflowError(
            f"Capability action is not admitted: {action_id}",
            code="admission-action-denied",
        )


def cmd_admission_status(_args: argparse.Namespace) -> None:
    contract = load_contract()
    check_admission(contract, "advance-workflow-step")
    print(
        json.dumps(
            {
                "status": "allowed",
                "project_id": contract["project_id"],
                "artifact_sha256": capability_artifact_sha256(contract),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def write_immutable_json(
    directory: Path,
    prefix: str,
    document: dict[str, Any],
) -> Path:
    digest = json_sha256(document)
    path = directory / f"{prefix}-{digest[:16]}.json"
    if path.exists():
        if sha256_file(path) != sha256_bytes(canonical_json_bytes(document)):
            raise WorkflowError(
                f"Immutable record collision: {path.relative_to(ROOT)}",
                code="immutable-record-collision",
            )
        return path
    atomic_json(path, document)
    return path


def receipt_ref(path: Path, epoch_id: str) -> dict[str, str]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "epoch_id": epoch_id,
    }


def validate_file_ref(
    ref: Any,
    label: str,
    *,
    expected_epoch: str | None = None,
) -> Path:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "epoch_id"}:
        raise WorkflowError(f"{label} reference is invalid", code="receipt-ref-invalid")
    if expected_epoch is not None and not exact_json_equal(
        ref.get("epoch_id"), expected_epoch
    ):
        raise WorkflowError(f"{label} epoch is invalid", code="receipt-ref-invalid")
    path = ROOT / normalize(str(ref["path"]))
    if not path.is_file() or sha256_file(path) != ref["sha256"]:
        raise WorkflowError(f"{label} drift", code="receipt-drift")
    return path


def read_referenced_json(
    ref: Any,
    label: str,
    *,
    expected_epoch: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "epoch_id"}:
        raise WorkflowError(f"{label} reference is invalid", code="receipt-ref-invalid")
    if expected_epoch is not None and not exact_json_equal(
        ref.get("epoch_id"), expected_epoch
    ):
        raise WorkflowError(f"{label} epoch is invalid", code="receipt-ref-invalid")
    path = ROOT / normalize(str(ref["path"]))
    digest, _, raw = bounded_file(path, MAX_CONTROL_JSON_BYTES, capture=True)
    if digest != ref.get("sha256"):
        raise WorkflowError(f"{label} drift", code="receipt-drift")
    assert raw is not None
    return path, json_object_from_bytes(raw, path)


def append_event(
    state: dict[str, Any], event_type: str, payload: dict[str, Any]
) -> None:
    sequence = int(state.get("event_sequence", 0)) + 1
    event = {
        "schema_version": 2,
        "workflow_id": state["workflow_id"],
        "epoch_id": state["epoch_id"],
        "sequence": sequence,
        "timestamp": now(),
        "event_type": event_type,
        "previous_event_sha256": state.get("last_event_sha256"),
        "previous_event_ref": state.get("last_event_ref"),
        "payload": payload,
    }
    event["event_sha256"] = json_sha256(event)
    path = write_immutable_json(
        EVENTS / state["epoch_id"], f"{sequence:06d}-{event_type}", event
    )
    state["event_sequence"] = sequence
    state["last_event_sha256"] = event["event_sha256"]
    state["last_event_ref"] = receipt_ref(path, state["epoch_id"])


def validate_event_chain(state: dict[str, Any]) -> None:
    sequence = state.get("event_sequence")
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 0
        or sequence > 100000
    ):
        raise WorkflowError("Event sequence is invalid", code="event-chain-invalid")
    current_ref = state.get("last_event_ref")
    expected_event_sha = state.get("last_event_sha256")
    if sequence == 0:
        if current_ref is not None or expected_event_sha is not None:
            raise WorkflowError(
                "Empty event chain has a tip", code="event-chain-invalid"
            )
        return
    if HASH_PATTERN.fullmatch(str(expected_event_sha or "")) is None:
        raise WorkflowError("Event tip hash is invalid", code="event-chain-invalid")
    seen_paths: set[str] = set()
    expected_fields = {
        "schema_version",
        "workflow_id",
        "epoch_id",
        "sequence",
        "timestamp",
        "event_type",
        "previous_event_sha256",
        "previous_event_ref",
        "payload",
        "event_sha256",
    }
    while sequence:
        path, event = read_referenced_json(
            current_ref,
            f"Event {sequence}",
            expected_epoch=state["epoch_id"],
        )
        relative = path.relative_to(ROOT).as_posix()
        if relative in seen_paths or not matches(
            relative, [f"docs/install-transaction/events/{state['epoch_id']}/**"]
        ):
            raise WorkflowError(
                "Event chain path is invalid", code="event-chain-invalid"
            )
        seen_paths.add(relative)
        logical_event = dict(event)
        stored_event_sha = logical_event.pop("event_sha256", None)
        event_type = event.get("event_type")
        expected_name = (
            f"{sequence:06d}-{event_type}-{json_sha256(event)[:16]}.json"
            if isinstance(event_type, str)
            else ""
        )
        if (
            set(event) != expected_fields
            or not exact_json_equal(event.get("schema_version"), 2)
            or event.get("workflow_id") != state.get("workflow_id")
            or event.get("epoch_id") != state.get("epoch_id")
            or not exact_json_equal(event.get("sequence"), sequence)
            or stored_event_sha != json_sha256(logical_event)
            or stored_event_sha != expected_event_sha
            or not isinstance(event.get("timestamp"), str)
            or not event["timestamp"]
            or not isinstance(event_type, str)
            or re.fullmatch(r"[a-z0-9-]+", event_type) is None
            or not isinstance(event.get("payload"), dict)
            or path.name != expected_name
        ):
            raise WorkflowError(
                "Event chain record is invalid", code="event-chain-invalid"
            )
        previous_ref = event.get("previous_event_ref")
        previous_sha = event.get("previous_event_sha256")
        if sequence == 1:
            if previous_ref is not None or previous_sha is not None:
                raise WorkflowError(
                    "First event has a predecessor", code="event-chain-invalid"
                )
        elif (
            not isinstance(previous_ref, dict)
            or HASH_PATTERN.fullmatch(str(previous_sha or "")) is None
        ):
            raise WorkflowError(
                "Event predecessor is invalid", code="event-chain-invalid"
            )
        current_ref = previous_ref
        expected_event_sha = previous_sha
        sequence -= 1


def persist_state(
    state: dict[str, Any], event_type: str, payload: dict[str, Any]
) -> None:
    state["updated_at"] = now()
    append_event(state, event_type, payload)
    atomic_json(DEFAULT_STATE, state)


def validate_snapshot(
    contract: dict[str, Any],
    snapshot: Any,
    label: str,
    *,
    enforce_scope: bool = True,
) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or set(snapshot) != {"commit", "overlay"}:
        raise WorkflowError(
            f"{label} is not a closed snapshot", code="snapshot-invalid"
        )
    commit = snapshot.get("commit")
    overlay = snapshot.get("overlay")
    if not isinstance(commit, str) or not isinstance(overlay, dict):
        raise WorkflowError(f"{label} has invalid fields", code="snapshot-invalid")
    if commit not in _VERIFIED_COMMITS:
        git("rev-parse", "--verify", f"{commit}^{{commit}}")
        _VERIFIED_COMMITS.add(commit)
    for relative, digest in overlay.items():
        normalized = normalize(relative)
        if (
            normalized != relative
            or (
                enforce_scope
                and (
                    is_volatile(contract, relative)
                    or not matches(relative, contract["protected_paths"])
                )
            )
        ):
            raise WorkflowError(
                f"{label} contains forbidden path: {relative}",
                code="snapshot-path-invalid",
            )
        if digest != "<deleted>" and (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise WorkflowError(
                f"{label} contains invalid digest: {relative}",
                code="snapshot-digest-invalid",
            )
    return snapshot


def snapshot_for_current_scope(
    contract: dict[str, Any], snapshot: Any, label: str
) -> dict[str, Any]:
    validated = validate_snapshot(
        contract, snapshot, label, enforce_scope=False
    )
    scoped = {
        "commit": validated["commit"],
        "overlay": {
            relative: digest
            for relative, digest in validated["overlay"].items()
            if matches(relative, contract["protected_paths"])
            and not is_volatile(contract, relative)
        },
    }
    return validate_snapshot(contract, scoped, f"{label} current scope")


def validate_verification_ref(
    contract: dict[str, Any],
    state: dict[str, Any],
    step_id: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    ref = entry.get("verification")
    path, receipt = read_referenced_json(
        ref, f"Verification receipt for {step_id}"
    )
    step = next(item for item in contract["steps"] if item["id"] == step_id)
    expected = {
        "workflow_id": state["workflow_id"],
        "epoch_id": ref["epoch_id"],
        "step_id": step_id,
        "attempt": int(entry["attempt"]),
        "contract_sha256": entry.get(
            "contract_sha256", state["contract_sha256"]
        ),
        "step_definition_sha256": step_sha256(step),
        "verifier_policy_sha256": entry.get(
            "verifier_policy_sha256", state["verifier_policy_sha256"]
        ),
        "evidence_schema_sha256": entry.get(
            "evidence_schema_sha256", state["evidence_schema_sha256"]
        ),
        "control_plane_sha256": entry.get(
            "control_plane_sha256", state["control_plane_sha256"]
        ),
        "predecessor_snapshot_sha256": entry.get(
            "predecessor_snapshot_sha256"
        ),
        "status": "passed",
    }
    for field, value in expected.items():
        if not exact_json_equal(receipt.get(field), value):
            raise WorkflowError(
                f"Verification receipt identity mismatch for {step_id}: {field}",
                code="receipt-identity-invalid",
            )
    snapshot = validate_snapshot(
        contract,
        receipt.get("verified_snapshot"),
        f"{step_id} verified snapshot",
    )
    if receipt.get("verified_snapshot_sha256") != snapshot_sha256(snapshot):
        raise WorkflowError(
            f"Verification snapshot hash mismatch for {step_id}",
            code="receipt-snapshot-invalid",
        )
    manifests = receipt.get("evidence_manifests")
    captures = receipt.get("captures")
    origin_proofs = receipt.get("origin_proofs")
    if (
        not isinstance(manifests, dict)
        or set(manifests) != set(step["required_evidence"])
        or not isinstance(captures, dict)
        or not isinstance(origin_proofs, dict)
    ):
        raise WorkflowError(
            f"Verification evidence set is invalid for {step_id}",
            code="receipt-evidence-invalid",
        )
    policy, _ = load_verifier_policy(contract)
    observed_captures: dict[str, str] = {}
    observed_origins: dict[str, dict[str, Any]] = {}
    if step_id == "S09" and snapshot["overlay"]:
        raise WorkflowError(
            "S09 verification snapshot is not a committed source tree",
            code="candidate-source-uncommitted",
        )
    for evidence_id in step["required_evidence"]:
        manifest_ref = manifests[evidence_id]
        manifest_path = validate_file_ref(
            manifest_ref,
            f"Evidence manifest {evidence_id}",
            expected_epoch=ref["epoch_id"],
        )
        validated_id, validated_ref, bindings, origin_proof = validate_evidence_manifest(
            contract,
            state,
            step,
            manifest_path.relative_to(ROOT).as_posix(),
            receipt["verified_snapshot_sha256"],
            str(snapshot["commit"]),
            attempt=int(receipt["attempt"]),
            epoch_id=str(ref["epoch_id"]),
            policy=policy,
            stored_origin_proof=origin_proofs.get(evidence_id),
        )
        if validated_id != evidence_id or validated_ref != manifest_ref:
            raise WorkflowError(
                f"Evidence manifest identity drift for {evidence_id}",
                code="receipt-evidence-invalid",
            )
        observed_captures.update(bindings)
        if origin_proof is not None:
            observed_origins[evidence_id] = origin_proof
    if observed_captures != captures:
        raise WorkflowError(
            f"Verification captures drift for {step_id}",
            code="receipt-evidence-invalid",
        )
    validate_external_proof_bindings(
        policy,
        state,
        observed_captures,
        observed_origins,
        step["required_evidence"],
    )
    if not exact_json_equal(observed_origins, origin_proofs):
        raise WorkflowError(
            f"Verification origin proofs drift for {step_id}",
            code="receipt-evidence-invalid",
        )
    return receipt


def _validate_state_document(
    contract: dict[str, Any],
    state: dict[str, Any],
    *,
    allow_definition_drift: bool = False,
    allow_orphan_failures: bool = False,
) -> dict[str, Any]:
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise WorkflowError(
            "Legacy workflow state requires migrate",
            code="state-migration-required",
        )
    if state.get("workflow_id") != contract.get("workflow_id"):
        raise WorkflowError(
            "Workflow identity mismatch", code="state-identity-invalid"
        )
    if not isinstance(state.get("epoch_id"), str) or EPOCH_PATTERN.fullmatch(
        state["epoch_id"]
    ) is None:
        raise WorkflowError("Workflow epoch is invalid", code="state-epoch-invalid")
    if (
        not allow_definition_drift
        and state.get("contract_sha256") != contract_source_sha256(contract)
    ):
        raise WorkflowError(
            "Contract drift requires a bounded replan", code="contract-drift"
        )
    if (
        not allow_definition_drift
        and state.get("contract_core_sha256") != contract_core_sha256(contract)
    ):
        raise WorkflowError(
            "Contract core drift requires a bounded replan",
            code="contract-drift",
        )
    definitions = {
        step["id"]: step_sha256(step) for step in contract["steps"]
    }
    if not allow_definition_drift and state.get("step_definitions") != definitions:
        raise WorkflowError(
            "Step-definition drift requires a bounded replan",
            code="step-definition-drift",
        )
    policy, policy_sha = load_verifier_policy(contract)
    _, evidence_schema_sha = load_evidence_schema(contract)
    control_plane_sha = capability_artifact_sha256(contract)
    if not allow_definition_drift and state.get("verifier_policy_sha256") != policy_sha:
        raise WorkflowError(
            "Verifier-policy drift requires a bounded replan",
            code="verifier-policy-drift",
        )
    if (
        not allow_definition_drift
        and state.get("evidence_schema_sha256") != evidence_schema_sha
    ):
        raise WorkflowError(
            "Evidence-schema drift requires a bounded replan",
            code="evidence-schema-drift",
        )
    if (
        not allow_definition_drift
        and state.get("control_plane_sha256") != control_plane_sha
    ):
        raise WorkflowError(
            "Control-plane drift requires a bounded replan",
            code="control-plane-drift",
        )
    if not allow_definition_drift and state.get(
        "evidence_definitions"
    ) != evidence_definition_hashes(
        contract, policy, evidence_schema_sha
    ):
        raise WorkflowError(
            "Evidence-definition drift requires a bounded replan",
            code="evidence-definition-drift",
        )
    epoch_baseline = validate_snapshot(
        contract,
        state.get("epoch_baseline"),
        "epoch baseline",
        enforce_scope=not allow_definition_drift,
    )
    last_completed = validate_snapshot(
        contract,
        state.get("last_completed_snapshot"),
        "last completed snapshot",
        enforce_scope=not allow_definition_drift,
    )
    if state.get("epoch_baseline_sha256") != snapshot_sha256(epoch_baseline):
        raise WorkflowError(
            "Epoch baseline hash drift", code="state-derived-hash-invalid"
        )
    if state.get("last_completed_snapshot_sha256") != snapshot_sha256(last_completed):
        raise WorkflowError(
            "Last completed snapshot hash drift",
            code="state-derived-hash-invalid",
        )
    validate_event_chain(state)
    previous_epoch = state.get("previous_epoch")
    if previous_epoch:
        previous_path = ROOT / normalize(previous_epoch["state_path"])
        if (
            not previous_path.is_file()
            or sha256_file(previous_path) != previous_epoch["state_sha256"]
        ):
            raise WorkflowError(
                "Previous epoch archive drift", code="receipt-drift"
            )
    if state.get("replan_receipt"):
        validate_file_ref(
            state["replan_receipt"],
            "Replan receipt",
            expected_epoch=state["epoch_id"],
        )
    for field in ("baseline_manifest", "legacy_state"):
        ref = state.get(field)
        if ref:
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
                raise WorkflowError(
                    f"{field} reference is invalid", code="receipt-ref-invalid"
                )
            referenced = ROOT / normalize(ref["path"])
            if not referenced.is_file() or sha256_file(referenced) != ref["sha256"]:
                raise WorkflowError(f"{field} drift", code="receipt-drift")
    expected_ids = [step["id"] for step in contract["steps"]]
    steps = state.get("steps")
    if (
        not isinstance(steps, dict)
        or list(steps) != expected_ids
        or any(not isinstance(entry, dict) for entry in steps.values())
    ):
        raise WorkflowError(
            "State step set or order is invalid", code="state-step-invalid"
        )
    pending_failures = orphan_failure_receipts(state)
    if pending_failures and not allow_orphan_failures:
        raise WorkflowError(
            "A durable failure receipt requires replay before the workflow can continue",
            code="failure-recovery-required",
        )
    workflow_status = state.get("status")
    if workflow_status not in {"active", "needs_replan", "completed"}:
        raise WorkflowError(
            "Workflow status is invalid", code="state-status-invalid"
        )
    invalid_statuses = {
        step_id: entry.get("status")
        for step_id, entry in state["steps"].items()
        if entry.get("status")
        not in {"pending", "in_progress", "completed", "blocked"}
    }
    if invalid_statuses:
        raise WorkflowError(
            f"Step statuses are invalid: {invalid_statuses}",
            code="state-step-invalid",
        )
    active = [
        key
        for key, value in state["steps"].items()
        if value.get("status") == "in_progress"
    ]
    if len(active) > 1 or state.get("current_step") != (
        active[0] if active else None
    ):
        raise WorkflowError(
            "State invariant failed: exactly zero or one current step",
            code="state-active-invalid",
        )
    if workflow_status == "completed" and any(
        entry["status"] != "completed" for entry in state["steps"].values()
    ):
        raise WorkflowError(
            "Completed workflow contains an incomplete step",
            code="state-terminal-invalid",
        )
    if workflow_status == "needs_replan" and (
        active
        or not any(
            entry["status"] == "blocked"
            for entry in state["steps"].values()
        )
    ):
        raise WorkflowError(
            "Replan state does not contain one blocked transition",
            code="state-terminal-invalid",
        )
    for step_id, entry in state["steps"].items():
        step = next(item for item in contract["steps"] if item["id"] == step_id)
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("attempt"), int)
            or isinstance(entry.get("attempt"), bool)
            or entry["attempt"] < 1
            or not isinstance(entry.get("remediation_cycles"), int)
            or isinstance(entry.get("remediation_cycles"), bool)
            or entry["remediation_cycles"] < 0
        ):
            raise WorkflowError(
                f"State attempt accounting is invalid: {step_id}",
                code="state-attempt-invalid",
            )
        if entry["status"] != "pending":
            incomplete = [
                prerequisite
                for prerequisite in step["prerequisites"]
                if state["steps"][prerequisite]["status"] != "completed"
            ]
            if incomplete:
                raise WorkflowError(
                    f"Step {step_id} bypasses prerequisites: {incomplete}",
                    code="state-prerequisite-invalid",
                )
        if entry["status"] == "completed" and not entry.get("verification"):
            raise WorkflowError(
                f"Completed step lacks verification: {step_id}",
                code="state-verification-missing",
            )
        for collection in ("failed_attempts", "superseded_receipts"):
            refs = entry.get(collection, [])
            if not isinstance(refs, list):
                raise WorkflowError(
                    f"State receipt list is invalid: {step_id}.{collection}",
                    code="state-step-invalid",
                )
            for ref in refs:
                validate_file_ref(ref, f"{step_id} {collection} receipt")
        if entry.get("authorization"):
            _, authorization = read_referenced_json(
                entry["authorization"], f"{step_id} authorization receipt"
            )
            if (
                not exact_json_equal(
                    authorization.get("workflow_id"), state["workflow_id"]
                )
                or not exact_json_equal(
                    authorization.get("step_id"), step_id
                )
                or not exact_json_equal(
                    authorization.get("attempt"), entry["attempt"]
                )
            ):
                raise WorkflowError(
                    f"Authorization identity is invalid: {step_id}",
                    code="authorization-invalid",
                )
        if entry.get("verification") and not allow_definition_drift:
            validate_verification_ref(contract, state, step_id, entry)
    for name, freeze in state.get("freezes", {}).items():
        if freeze and (
            not isinstance(freeze, dict)
            or HASH_PATTERN.fullmatch(str(freeze.get("artifact_sha256", "")))
            is None
        ):
            raise WorkflowError(
                f"Freeze state is malformed: {name}",
                code="freeze-state-invalid",
            )
    if allow_definition_drift:
        return state
    for name, spec in contract["freeze_points"].items():
        freeze = state.get("freezes", {}).get(name)
        freeze_step_completed = (
            state["steps"][spec["step"]].get("status") == "completed"
        )
        if freeze_step_completed != bool(freeze):
            raise WorkflowError(
                f"Mandatory freeze presence is invalid: {name}",
                code="freeze-state-invalid",
            )
        if not freeze:
            continue
        entry = state["steps"][spec["step"]]
        verification = entry.get("verification")
        if (
            entry.get("status") != "completed"
            or not isinstance(freeze, dict)
            or freeze.get("step") != spec["step"]
            or HASH_PATTERN.fullmatch(str(freeze.get("artifact_sha256", ""))) is None
            or not verification
            or freeze.get("verification_receipt_sha256") != verification["sha256"]
        ):
            raise WorkflowError(
                f"Freeze state is invalid: {name}",
                code="freeze-state-invalid",
            )
        if not allow_definition_drift:
            verification_receipt = validate_verification_ref(
                contract, state, spec["step"], entry
            )
            if (
                verification_receipt["captures"].get(spec["capture"])
                != freeze["artifact_sha256"]
                or verification_receipt["verified_snapshot_sha256"]
                != freeze.get("source_snapshot_sha256")
            ):
                raise WorkflowError(
                    f"Freeze receipt binding drift: {name}",
                    code="freeze-state-invalid",
                )
    release = state.get("freezes", {}).get("release")
    candidate = state.get("freezes", {}).get("candidate")
    if release and (
        not candidate
        or release["artifact_sha256"] != candidate["artifact_sha256"]
    ):
        raise WorkflowError(
            "Release freeze differs from candidate freeze",
            code="freeze-identity-mismatch",
        )
    return state


def capture_state() -> tuple[dict[str, Any], str, bytes]:
    if not DEFAULT_STATE.is_file():
        raise WorkflowError(
            "Workflow state is missing; run init", code="state-missing"
        )
    return read_json_capture(DEFAULT_STATE)


def load_state(
    contract: dict[str, Any],
    *,
    allow_definition_drift: bool = False,
    allow_orphan_failures: bool = False,
) -> dict[str, Any]:
    state, _, _ = capture_state()
    return _validate_state_document(
        contract,
        state,
        allow_definition_drift=allow_definition_drift,
        allow_orphan_failures=allow_orphan_failures,
    )


def current_definition(
    contract: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    step_id = state.get("current_step")
    if not step_id:
        raise WorkflowError(
            "No step is in progress; run next or replan",
            code="step-not-active",
        )
    return next(step for step in contract["steps"] if step["id"] == step_id)


def enforce_paths(
    contract: dict[str, Any], state: dict[str, Any], paths: list[str]
) -> None:
    step = current_definition(contract, state)
    denied = [
        path
        for path in paths
        if matches(path, contract["protected_paths"])
        and not matches(path, step["allowed_paths"])
    ]
    if denied:
        raise WorkflowError(
            f"{step['id']} does not allow protected paths: {', '.join(denied)}",
            code="protected-path-denied",
        )


def baseline_from_manifest(
    contract: dict[str, Any],
    path_text: str,
    expected_sha256: str | None,
    legacy_sha256: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not expected_sha256:
        raise WorkflowError(
            "Baseline manifest SHA-256 is required",
            code="baseline-manifest-hash-required",
    )
    relative = normalize(path_text)
    path = ROOT / relative
    actual_sha, _, raw = bounded_file(
        path,
        int(contract["evidence_policy"]["maximum_manifest_bytes"]),
        capture=True,
    )
    if actual_sha != expected_sha256:
        raise WorkflowError(
            "Baseline manifest is missing or hash-mismatched",
            code="baseline-manifest-invalid",
        )
    assert raw is not None
    document = json_object_from_bytes(raw, path)
    head = git("rev-parse", "HEAD").decode().strip()
    if not exact_json_equal(
        document.get("schema_version"), 1
    ) or not exact_json_equal(document.get("git_head"), head):
        raise WorkflowError(
            "Baseline manifest commit is not the current HEAD",
            code="baseline-manifest-commit-invalid",
        )
    if (
        legacy_sha256 is not None
        and document.get("legacy_state_sha256") != legacy_sha256
    ):
        raise WorkflowError(
            "Baseline manifest does not bind the legacy state",
            code="baseline-manifest-state-invalid",
        )
    snapshot = validate_snapshot(
        contract,
        {"commit": document["git_head"], "overlay": document.get("overlay")},
        "explicit baseline manifest",
    )
    return snapshot, {"path": relative, "sha256": expected_sha256}


def new_state(
    contract: dict[str, Any],
    baseline: dict[str, Any],
    *,
    epoch_id: str,
    baseline_ref: dict[str, str] | None = None,
    legacy_ref: dict[str, str] | None = None,
) -> dict[str, Any]:
    baseline_hash = snapshot_sha256(baseline)
    policy, verifier_policy_sha = load_verifier_policy(contract)
    _, evidence_schema_sha = load_evidence_schema(contract)
    control_plane_sha = capability_artifact_sha256(contract)
    contract_sha = contract_source_sha256(contract)
    steps: dict[str, Any] = {}
    for step in contract["steps"]:
        steps[step["id"]] = {
            "status": "pending",
            "attempt": 1,
            "contract_sha256": contract_sha,
            "verifier_policy_sha256": verifier_policy_sha,
            "evidence_schema_sha256": evidence_schema_sha,
            "control_plane_sha256": control_plane_sha,
            "remediation_cycles": 0,
            "predecessor_snapshot_sha256": (
                baseline_hash if step["id"] == "S01" else None
            ),
            "failed_attempts": [],
            "superseded_receipts": [],
        }
    steps["S01"]["status"] = "in_progress"
    created = now()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_id": contract["workflow_id"],
        "contract_path": DEFAULT_CONTRACT.relative_to(ROOT).as_posix(),
        "contract_sha256": contract_sha,
        "contract_core_sha256": contract_core_sha256(contract),
        "step_definitions": {
            step["id"]: step_sha256(step) for step in contract["steps"]
        },
        "verifier_policy_sha256": verifier_policy_sha,
        "evidence_schema_sha256": evidence_schema_sha,
        "control_plane_sha256": control_plane_sha,
        "evidence_definitions": evidence_definition_hashes(
            contract, policy, evidence_schema_sha
        ),
        "epoch_id": epoch_id,
        "status": "active",
        "current_step": "S01",
        "created_at": created,
        "updated_at": created,
        "epoch_baseline": baseline,
        "epoch_baseline_sha256": baseline_hash,
        "last_completed_snapshot": baseline,
        "last_completed_snapshot_sha256": baseline_hash,
        "baseline_manifest": baseline_ref,
        "legacy_state": legacy_ref,
        "previous_epoch": None,
        "freezes": {
            name: None for name in contract["freeze_points"]
        },
        "steps": steps,
        "event_sequence": 0,
        "last_event_sha256": None,
        "last_event_ref": None,
    }


def cmd_init(args: argparse.Namespace) -> None:
    if DEFAULT_STATE.exists():
        raise WorkflowError("Workflow state already exists", code="state-exists")
    contract = load_contract()
    if changed_paths(contract) and not args.baseline_manifest:
        raise WorkflowError(
            "Dirty initialization requires an explicit baseline manifest",
            code="baseline-manifest-required",
        )
    if args.baseline_manifest:
        baseline, ref = baseline_from_manifest(
            contract,
            args.baseline_manifest,
            args.baseline_manifest_sha256,
            None,
        )
    else:
        baseline, ref = current_snapshot(contract), None
    state = new_state(
        contract, baseline, epoch_id="E001", baseline_ref=ref
    )
    persist_state(
        state,
        "epoch-started",
        {
            "reason": "initialization",
            "baseline_sha256": snapshot_sha256(baseline),
        },
    )
    print(
        json.dumps(
            {
                "status": "initialized",
                "epoch_id": state["epoch_id"],
                "current_step": "S01",
            }
        )
    )


def cmd_migrate(args: argparse.Namespace) -> None:
    contract = load_contract()
    if not DEFAULT_STATE.is_file():
        raise WorkflowError(
            "Legacy workflow state is missing", code="state-missing"
        )
    legacy_document, legacy_sha, legacy_bytes = read_json_capture(DEFAULT_STATE)
    if legacy_document.get("schema_version") == STATE_SCHEMA_VERSION:
        raise WorkflowError(
            "Workflow state is already current", code="state-already-current"
        )
    if legacy_document.get("schema_version") != 2:
        raise WorkflowError(
            "Only the supported schema-2 predecessor may be migrated",
            code="legacy-schema-invalid",
        )
    if legacy_document.get("workflow_id") != contract["workflow_id"]:
        raise WorkflowError(
            "Legacy workflow identity does not match this controller",
            code="legacy-identity-invalid",
        )
    baseline, baseline_ref = baseline_from_manifest(
        contract,
        args.baseline_manifest, args.baseline_manifest_sha256, legacy_sha
    )
    LEGACY.mkdir(parents=True, exist_ok=True)
    legacy_path = LEGACY / f"runtime-state-{legacy_sha[:16]}.json"
    if legacy_path.exists():
        _, _, archived_bytes = bounded_file(
            legacy_path, MAX_CONTROL_JSON_BYTES, capture=True
        )
        if archived_bytes != legacy_bytes:
            raise WorkflowError(
                "Legacy state archive collision", code="legacy-archive-collision"
            )
    if not legacy_path.exists():
        atomic_replace_bytes(legacy_path, legacy_bytes)
    legacy_ref = {
        "path": legacy_path.relative_to(ROOT).as_posix(),
        "sha256": legacy_sha,
    }
    state = new_state(
        contract,
        baseline,
        epoch_id="E001",
        baseline_ref=baseline_ref,
        legacy_ref=legacy_ref,
    )
    state["migration_reason"] = args.reason
    persist_state(
        state,
        "legacy-state-migrated",
        {"reason": args.reason, "legacy_state_sha256": legacy_sha},
    )
    print(
        json.dumps(
            {
                "status": "migrated",
                "epoch_id": "E001",
                "current_step": "S01",
                "legacy_state_sha256": legacy_sha,
            }
        )
    )


def cmd_prepare_migration(_args: argparse.Namespace) -> None:
    contract = load_contract()
    legacy_sha = sha256_file(DEFAULT_STATE) if DEFAULT_STATE.is_file() else None
    document = {
        "schema_version": 1,
        "purpose": "installer-workflow-migration-baseline",
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "legacy_state_sha256": legacy_sha,
        "overlay": current_snapshot(contract)["overlay"],
    }
    path = write_immutable_json(
        ROOT / contract["evidence_policy"]["directory"],
        "prepared-migration-baseline",
        document,
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256_file(path),
                "legacy_state_sha256": legacy_sha,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_status(_args: argparse.Namespace) -> None:
    contract = load_contract()
    if DEFAULT_STATE.is_file():
        raw_state = read_json(DEFAULT_STATE)
        if raw_state.get("schema_version") != STATE_SCHEMA_VERSION:
            print(
                json.dumps(
                    {
                        "status": "legacy",
                        "schema_version": raw_state.get("schema_version"),
                        "migration_required": True,
                        "legacy_state_sha256": sha256_file(DEFAULT_STATE),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
    state = load_state(contract)
    delta, current_snapshot_value = delta_from_snapshot(
        contract, state["last_completed_snapshot"]
    )
    current = state.get("current_step")
    scope = next(
        (
            step["authorization_scope"]
            for step in contract["steps"]
            if step["id"] == current
        ),
        None,
    )
    output = {
        "status": state["status"],
        "epoch_id": state["epoch_id"],
        "current_step": current,
        "current_attempt": (
            state["steps"][current]["attempt"] if current else None
        ),
        "contract_sha256": state["contract_sha256"],
        "predecessor_snapshot_sha256": state[
            "last_completed_snapshot_sha256"
        ],
        "current_worktree_sha256": snapshot_sha256(current_snapshot_value),
        "delta_paths": sorted(delta),
        "authorization_scope": scope,
        "freezes": state["freezes"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_hook(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract, allow_definition_drift=True)
    if state["status"] != "active":
        raise WorkflowError(
            f"Workflow is {state['status']}", code="workflow-not-active"
        )
    if args.phase == "pre-edit":
        if not args.paths:
            raise WorkflowError(
                "pre-edit requires at least one --path", code="path-required"
            )
        paths = [normalize(path) for path in args.paths]
    else:
        paths = list(
            delta_from_snapshot(contract, state["last_completed_snapshot"])[0]
        )
    enforce_paths(contract, state, paths)
    print(
        json.dumps(
            {"status": "passed", "phase": args.phase, "paths": paths},
            ensure_ascii=False,
        )
    )


def json_pointer_value(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise WorkflowError(
            f"Invalid verifier JSON pointer: {pointer}",
            code="verifier-pointer-invalid",
        )
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                raise WorkflowError(
                    f"Verifier JSON pointer is absent: {pointer}",
                    code="verifier-observation-missing",
                )
            current = current[index]
        else:
            raise WorkflowError(
                f"Verifier JSON pointer is absent: {pointer}",
                code="verifier-observation-missing",
            )
    return current


def validate_evaluator_report(
    contract: dict[str, Any],
    report: dict[str, Any],
    report_sha256: str,
    minimum_threshold: int,
) -> dict[str, Any]:
    artifact = report.get("artifact")
    evaluator = report.get("evaluator")
    evaluation_contract = report.get("contract")
    dimensions = report.get("dimensions")
    checks = report.get("deterministic_checks")
    blockers = report.get("blocking_findings")
    limitations = report.get("limitations")
    iteration = report.get("iteration")
    if (
        report.get("schema_version") != "1.0"
        or not isinstance(report.get("evaluation_id"), str)
        or not report["evaluation_id"]
        or not isinstance(artifact, dict)
        or not isinstance(evaluator, dict)
        or not isinstance(evaluation_contract, dict)
        or not isinstance(dimensions, list)
        or not dimensions
        or not isinstance(checks, list)
        or not checks
        or not isinstance(blockers, list)
        or not isinstance(limitations, list)
        or not isinstance(iteration, dict)
    ):
        raise WorkflowError(
            "Independent evaluation report shape is invalid",
            code="evaluator-report-invalid",
        )
    paths = artifact.get("paths")
    hashes = artifact.get("sha256")
    producer_identity = artifact.get("producer_identity")
    evaluator_identity = evaluator.get("identity")
    if (
        not isinstance(paths, list)
        or not paths
        or not isinstance(hashes, list)
        or len(paths) != len(hashes)
        or not all(isinstance(path, str) for path in paths)
        or not all(isinstance(digest, str) for digest in hashes)
        or len(paths) > int(
            contract["evidence_policy"]["maximum_artifacts_per_manifest"]
        )
        or len(paths) != len(set(paths))
        or not isinstance(producer_identity, str)
        or not producer_identity
        or not isinstance(evaluator_identity, str)
        or not evaluator_identity
        or evaluator.get("independent") is not True
        or evaluator_identity == producer_identity
    ):
        raise WorkflowError(
            "Independent evaluation identity is invalid",
            code="evaluator-independence-invalid",
        )
    verified_artifacts: list[dict[str, Any]] = []
    allowed_roots = [
        *contract["evidence_policy"]["artifact_roots"],
        *contract["evidence_policy"]["subject_roots"],
    ]
    for raw_path, expected_sha in zip(paths, hashes):
        if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
            raise WorkflowError(
                "Independent evaluation artifact identity is invalid",
                code="evaluator-artifact-invalid",
            )
        relative = normalize(raw_path)
        if not (
            matches(relative, contract["protected_paths"])
            or any(
                matches(relative, [normalize(root) + "/**"])
                for root in allowed_roots
            )
        ):
            raise WorkflowError(
                f"Independent evaluation artifact is outside governed scope: {relative}",
                code="evaluator-artifact-invalid",
            )
        path = ROOT / relative
        is_subject = any(
            matches(relative, [normalize(root) + "/**"])
            for root in contract["evidence_policy"]["subject_roots"]
        )
        maximum_bytes = int(
            contract["evidence_policy"][
                "maximum_subject_bytes" if is_subject else "maximum_artifact_bytes"
            ]
        )
        actual_sha, byte_length, _ = bounded_file(
            path,
            maximum_bytes,
        )
        if expected_sha != actual_sha:
            raise WorkflowError(
                f"Independent evaluation artifact drift: {relative}",
                code="evaluator-artifact-drift",
            )
        verified_artifacts.append(
            {"path": relative, "sha256": actual_sha, "byte_length": byte_length}
        )
    threshold = evaluation_contract.get("pass_threshold")
    overall = report.get("overall_score")
    if (
        not exact_json_equal(
            evaluation_contract.get("source"),
            DEFAULT_CONTRACT.relative_to(ROOT).as_posix(),
        )
        or not isinstance(threshold, (int, float))
        or isinstance(threshold, bool)
        or threshold < minimum_threshold
        or threshold > 100
        or not isinstance(overall, (int, float))
        or isinstance(overall, bool)
        or not 0 <= overall <= 100
    ):
        raise WorkflowError(
            "Independent evaluation scoring contract is invalid",
            code="evaluator-report-invalid",
        )
    weight_sum = 0.0
    weighted_score = 0.0
    non_llm_evidence = False
    dimension_ids: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise WorkflowError(
                "Independent evaluation dimension is invalid",
                code="evaluator-report-invalid",
            )
        dimension_id = dimension.get("id")
        weight = dimension.get("weight")
        score = dimension.get("score")
        evidence = dimension.get("evidence")
        if (
            not isinstance(dimension_id, str)
            or not dimension_id
            or dimension_id in dimension_ids
            or not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or weight <= 0
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= score <= 100
            or not isinstance(dimension.get("mandatory"), bool)
            or not isinstance(evidence, list)
            or not evidence
        ):
            raise WorkflowError(
                "Independent evaluation dimension is invalid",
                code="evaluator-report-invalid",
            )
        dimension_ids.add(dimension_id)
        weight_sum += float(weight)
        weighted_score += float(weight) * float(score)
        for item in evidence:
            if (
                not isinstance(item, dict)
                or not all(
                    isinstance(item.get(field), str) and item[field]
                    for field in ("claim", "source", "method")
                )
            ):
                raise WorkflowError(
                    "Independent evaluation evidence is invalid",
                    code="evaluator-report-invalid",
                )
            non_llm_evidence = non_llm_evidence or item["method"] != "llm_judgment"
    if abs(weight_sum - 1.0) > 0.000001 or abs(weighted_score - float(overall)) > 0.01:
        raise WorkflowError(
            "Independent evaluation score is inconsistent",
            code="evaluator-report-invalid",
        )
    if any(
        not isinstance(check, dict)
        or not isinstance(check.get("id"), str)
        or not check["id"]
        or check.get("status") != "pass"
        or not isinstance(check.get("evidence"), str)
        or not check["evidence"]
        for check in checks
    ):
        raise WorkflowError(
            "Independent evaluation deterministic checks did not all pass",
            code="evaluator-check-failed",
        )
    current_iteration = iteration.get("current")
    maximum_iteration = iteration.get("maximum")
    if (
        not isinstance(current_iteration, int)
        or isinstance(current_iteration, bool)
        or current_iteration < 1
        or not isinstance(maximum_iteration, int)
        or isinstance(maximum_iteration, bool)
        or maximum_iteration < current_iteration
        or report.get("verdict") != "pass"
        or blockers
        or float(overall) < float(threshold)
        or not non_llm_evidence
    ):
        raise GateFailure(
            "Independent evaluation does not satisfy the pass contract",
            code="evaluator-verdict-failed",
        )
    return {
        "kind": "validated-independent-evaluation",
        "report_sha256": report_sha256,
        "evaluation_id": report["evaluation_id"],
        "producer_identity": producer_identity,
        "evaluator_identity": evaluator_identity,
        "artifacts": verified_artifacts,
    }


def verify_github_attestation(
    repository: str,
    workflow_path: str,
    source_commit: str,
    path: Path,
    artifact_sha256: str,
) -> str:
    signer = f"{repository}/{workflow_path}"
    cache_key = (artifact_sha256, repository, signer, source_commit)
    cached = _ATTESTATION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [
                "gh",
                "attestation",
                "verify",
                str(path),
                "--repo",
                repository,
                "--signer-workflow",
                signer,
                "--source-digest",
                source_commit,
                "--digest-alg",
                "sha256",
                "--format",
                "json",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError(
            f"GitHub artifact attestation lookup failed: {exc}",
            code="external-provenance-unavailable",
        ) from exc
    if result.returncode:
        raise GateFailure(
            result.stderr.decode("utf-8", errors="replace")[:512]
            or "GitHub artifact attestation verification failed",
            code="artifact-attestation-failed",
        )
    if len(result.stdout) > MAX_CONTROL_JSON_BYTES:
        raise WorkflowError(
            "GitHub artifact attestation output exceeds the response limit",
            code="external-provenance-invalid",
        )
    try:
        document = decode_strict_json(result.stdout)
    except ValueError as exc:
        raise WorkflowError(
            "GitHub artifact attestation output is invalid",
            code="external-provenance-invalid",
        ) from exc
    if not isinstance(document, list) or not document:
        raise GateFailure(
            "GitHub artifact attestation is empty",
            code="artifact-attestation-failed",
        )
    verified_digests: set[str] = set()
    for item in document:
        if not isinstance(item, dict):
            raise WorkflowError(
                "GitHub artifact attestation entry is invalid",
                code="external-provenance-invalid",
            )
        verification = item.get("verificationResult")
        statement = (
            verification.get("statement") if isinstance(verification, dict) else None
        )
        subjects = statement.get("subject") if isinstance(statement, dict) else None
        if not isinstance(subjects, list):
            raise WorkflowError(
                "GitHub artifact attestation subject is invalid",
                code="external-provenance-invalid",
            )
        for subject in subjects:
            digest = subject.get("digest") if isinstance(subject, dict) else None
            sha256 = digest.get("sha256") if isinstance(digest, dict) else None
            if isinstance(sha256, str) and HASH_PATTERN.fullmatch(sha256):
                verified_digests.add(sha256)
    if artifact_sha256 not in verified_digests:
        raise GateFailure(
            "GitHub artifact attestation does not bind the expected SHA-256",
            code="artifact-attestation-subject-mismatch",
        )
    if sha256_file(path) != artifact_sha256:
        raise WorkflowError(
            "Artifact bytes changed during attestation verification",
            code="artifact-attestation-race",
        )
    response_sha256 = json_sha256(document)
    _ATTESTATION_CACHE[cache_key] = response_sha256
    return response_sha256


def validate_ci_provenance(
    policy: dict[str, Any],
    evidence_id: str,
    observation: dict[str, Any],
    source_commit: str,
    artifacts_by_role: dict[str, dict[str, Any]],
    required_artifact_role: str,
    subjects_by_role: dict[str, dict[str, Any]],
    *,
    authenticate: bool,
    stored_proof: dict[str, Any] | None,
) -> dict[str, Any]:
    ci_policy = policy["external_provenance"]["ci"]
    provenance = observation.get("provenance")
    expected_workflow = ci_policy["evidence_workflows"][evidence_id]
    expected_fields = {
        "provider",
        "repository",
        "workflow_path",
        "head_sha",
        "run_id",
        "run_attempt",
        "conclusion",
    }
    if (
        not isinstance(provenance, dict)
        or set(provenance) != expected_fields
        or provenance.get("provider") != "github-actions"
        or provenance.get("repository") != ci_policy["repository"]
        or provenance.get("workflow_path") != expected_workflow
        or provenance.get("head_sha") != source_commit
        or not isinstance(provenance.get("run_id"), int)
        or isinstance(provenance.get("run_id"), bool)
        or provenance["run_id"] < 1
        or not isinstance(provenance.get("run_attempt"), int)
        or isinstance(provenance.get("run_attempt"), bool)
        or provenance["run_attempt"] < 1
        or provenance.get("conclusion") != "success"
    ):
        raise WorkflowError(
            f"GitHub Actions provenance is invalid: {evidence_id}",
            code="evidence-provenance-invalid",
        )
    proof: dict[str, Any] = {
        "kind": "github-actions-live-verification",
        "repository": provenance["repository"],
        "workflow_path": expected_workflow,
        "head_sha": source_commit,
        "run_id": provenance["run_id"],
        "run_attempt": provenance["run_attempt"],
        "conclusion": "success",
        "run_response_sha256": "",
        "result_attestation_response_sha256": None,
        "attested_result_sha256": None,
        "attestation_response_sha256": None,
        "attested_subject_sha256": None,
    }
    if authenticate:
        cache_key = (provenance["repository"], provenance["run_id"])
        run_document = _CI_RUN_CACHE.get(cache_key)
        if run_document is None:
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "api",
                        f"repos/{provenance['repository']}/actions/runs/{provenance['run_id']}",
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=60,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WorkflowError(
                    f"GitHub Actions provenance lookup failed: {exc}",
                    code="external-provenance-unavailable",
                ) from exc
            if result.returncode:
                raise WorkflowError(
                    result.stderr.decode("utf-8", errors="replace")[:512]
                    or "GitHub Actions provenance lookup failed",
                    code="external-provenance-unavailable",
                )
            if len(result.stdout) > MAX_CONTROL_JSON_BYTES:
                raise WorkflowError(
                    "GitHub Actions provenance output exceeds the response limit",
                    code="external-provenance-invalid",
                )
            run_document = json_object_from_bytes(result.stdout, Path("gh-api-run"))
            _CI_RUN_CACHE[cache_key] = run_document
        expected_run = {
            "id": provenance["run_id"],
            "run_attempt": provenance["run_attempt"],
            "head_sha": source_commit,
            "path": expected_workflow,
            "status": "completed",
            "conclusion": "success",
        }
        if any(
            not exact_json_equal(run_document.get(field), expected)
            for field, expected in expected_run.items()
        ):
            raise GateFailure(
                f"GitHub Actions run does not satisfy provenance: {evidence_id}",
                code="evidence-provenance-failed",
            )
        proof["run_response_sha256"] = json_sha256(run_document)
        if ci_policy["attest_result_artifacts"]:
            result_artifact = artifacts_by_role.get(required_artifact_role)
            if result_artifact is None:
                raise WorkflowError(
                    f"Attested CI result is missing: {evidence_id}",
                    code="evidence-artifact-missing",
                )
            proof["result_attestation_response_sha256"] = (
                verify_github_attestation(
                    provenance["repository"],
                    expected_workflow,
                    source_commit,
                    result_artifact["path"],
                    result_artifact["sha256"],
                )
            )
            proof["attested_result_sha256"] = result_artifact["sha256"]
        attestation = ci_policy["attested_subjects"].get(evidence_id)
        if attestation:
            subject = subjects_by_role.get(attestation["role"])
            if subject is None:
                raise WorkflowError(
                    f"Attested subject is missing: {evidence_id}",
                    code="evidence-subject-invalid",
                )
            proof["attestation_response_sha256"] = verify_github_attestation(
                provenance["repository"],
                attestation["signer_workflow"],
                source_commit,
                ROOT / subject["path"],
                subject["sha256"],
            )
            proof["attested_subject_sha256"] = subject["sha256"]
    if not authenticate:
        if not isinstance(stored_proof, dict):
            raise WorkflowError(
                f"Stored external provenance is invalid: {evidence_id}",
                code="external-provenance-drift",
            )
        static_fields = {
            key: value
            for key, value in proof.items()
            if key not in {
                "run_response_sha256",
                "result_attestation_response_sha256",
                "attested_result_sha256",
                "attestation_response_sha256",
                "attested_subject_sha256",
            }
        }
        if any(
            not exact_json_equal(stored_proof.get(key), value)
            for key, value in static_fields.items()
        ) or HASH_PATTERN.fullmatch(
            str(stored_proof.get("run_response_sha256", ""))
        ) is None:
            raise WorkflowError(
                f"Stored external provenance is invalid: {evidence_id}",
                code="external-provenance-drift",
            )
        result_artifact = artifacts_by_role.get(required_artifact_role)
        if (
            ci_policy["attest_result_artifacts"]
            and (
                result_artifact is None
                or stored_proof.get("attested_result_sha256")
                != result_artifact["sha256"]
                or HASH_PATTERN.fullmatch(
                    str(
                        stored_proof.get(
                            "result_attestation_response_sha256", ""
                        )
                    )
                )
                is None
            )
        ):
            raise WorkflowError(
                f"Stored CI result attestation is invalid: {evidence_id}",
                code="external-provenance-drift",
            )
        attestation = ci_policy["attested_subjects"].get(evidence_id)
        if attestation:
            subject = subjects_by_role.get(attestation["role"])
            if (
                subject is None
                or stored_proof.get("attested_subject_sha256")
                != subject["sha256"]
                or HASH_PATTERN.fullmatch(
                    str(stored_proof.get("attestation_response_sha256", ""))
                )
                is None
            ):
                raise WorkflowError(
                    f"Stored artifact attestation is invalid: {evidence_id}",
                    code="external-provenance-drift",
                )
        elif (
            stored_proof.get("attestation_response_sha256") is not None
            or stored_proof.get("attested_subject_sha256") is not None
        ):
            raise WorkflowError(
                f"Unexpected stored artifact attestation: {evidence_id}",
                code="external-provenance-drift",
            )
        proof = stored_proof
    return proof


def validate_external_proof_bindings(
    policy: dict[str, Any],
    state: dict[str, Any],
    captures: dict[str, str],
    origin_proofs: dict[str, dict[str, Any]],
    expected_evidence_ids: list[str],
) -> None:
    bindings = policy["external_provenance"]["evaluator"][
        "artifact_bindings"
    ]
    for evidence_id, binding in bindings.items():
        if evidence_id not in expected_evidence_ids:
            continue
        proof = origin_proofs.get(evidence_id)
        if not isinstance(proof, dict):
            raise WorkflowError(
                f"Independent evaluation provenance is missing: {evidence_id}",
                code="evaluator-artifact-binding-invalid",
            )
        if "capture" in binding:
            expected_sha = captures.get(binding["capture"])
        else:
            freeze = state.get("freezes", {}).get(binding["freeze"])
            expected_sha = (
                freeze.get("artifact_sha256")
                if isinstance(freeze, dict)
                else None
            )
        artifacts = proof.get("artifacts")
        if (
            not isinstance(expected_sha, str)
            or HASH_PATTERN.fullmatch(expected_sha) is None
            or not isinstance(artifacts, list)
            or not any(
                isinstance(artifact, dict)
                and exact_json_equal(artifact.get("sha256"), expected_sha)
                for artifact in artifacts
            )
        ):
            raise WorkflowError(
                f"Independent evaluation did not inspect the bound artifact: {evidence_id}",
                code="evaluator-artifact-binding-invalid",
            )
    expected = set(expected_evidence_ids)
    identity_fields = (
        "repository",
        "workflow_path",
        "head_sha",
        "run_id",
        "run_attempt",
    )
    for group_id, members in policy["external_provenance"]["ci"][
        "provenance_groups"
    ].items():
        selected = expected.intersection(members)
        if not selected:
            continue
        if selected != set(members):
            raise WorkflowError(
                f"CI provenance group is incomplete: {group_id}",
                code="ci-provenance-group-incomplete",
            )
        group_proofs = [origin_proofs.get(evidence_id) for evidence_id in members]
        if not all(isinstance(proof, dict) for proof in group_proofs):
            raise WorkflowError(
                f"CI provenance group proof is missing: {group_id}",
                code="ci-provenance-group-missing",
            )
        first = group_proofs[0]
        assert isinstance(first, dict)
        identity = tuple(first.get(field) for field in identity_fields)
        if any(
            tuple(proof.get(field) for field in identity_fields) != identity
            for proof in group_proofs[1:]
            if isinstance(proof, dict)
        ):
            raise WorkflowError(
                f"CI evidence mixes workflow attempts: {group_id}",
                code="ci-provenance-group-mismatch",
            )


def validate_schema_definition(
    schema: Any, location: str = "$"
) -> None:
    if not isinstance(schema, dict):
        raise WorkflowError(
            f"Evidence schema node is not an object at {location}",
            code="evidence-schema-definition-invalid",
        )
    supported = {
        "$schema",
        "$id",
        "title",
        "type",
        "additionalProperties",
        "required",
        "properties",
        "const",
        "enum",
        "minLength",
        "pattern",
        "minimum",
        "minItems",
        "maxItems",
        "items",
    }
    unknown = set(schema) - supported
    if unknown:
        raise WorkflowError(
            f"Unsupported evidence schema keywords at {location}: {sorted(unknown)}",
            code="evidence-schema-definition-invalid",
        )
    expected_type = schema.get("type")
    if expected_type is not None and expected_type not in {
        "object",
        "array",
        "string",
        "integer",
    }:
        raise WorkflowError(
            f"Unsupported evidence schema type at {location}: {expected_type}",
            code="evidence-schema-definition-invalid",
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise WorkflowError(
            f"Evidence schema properties are invalid at {location}",
            code="evidence-schema-definition-invalid",
        )
    for key, child in properties.items():
        validate_schema_definition(child, f"{location}.properties.{key}")
    if "items" in schema:
        validate_schema_definition(schema["items"], f"{location}.items")


def validate_json_schema(value: Any, schema: dict[str, Any], location: str = "$") -> None:
    expected_type = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }.get(expected_type, True)
    if not type_ok:
        raise WorkflowError(
            f"Evidence schema type mismatch at {location}",
            code="evidence-schema-invalid",
        )
    if "const" in schema and not exact_json_equal(value, schema["const"]):
        raise WorkflowError(
            f"Evidence schema constant mismatch at {location}",
            code="evidence-schema-invalid",
        )
    if "enum" in schema and not any(
        exact_json_equal(value, candidate) for candidate in schema["enum"]
    ):
        raise WorkflowError(
            f"Evidence schema enum mismatch at {location}",
            code="evidence-schema-invalid",
        )
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise WorkflowError(
                f"Evidence schema string is too short at {location}",
                code="evidence-schema-invalid",
            )
        if schema.get("pattern") and re.fullmatch(schema["pattern"], value) is None:
            raise WorkflowError(
                f"Evidence schema pattern mismatch at {location}",
                code="evidence-schema-invalid",
            )
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < int(schema["minimum"]):
            raise WorkflowError(
                f"Evidence schema integer is too small at {location}",
                code="evidence-schema-invalid",
            )
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise WorkflowError(
                f"Evidence schema array is too short at {location}",
                code="evidence-schema-invalid",
            )
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise WorkflowError(
                f"Evidence schema array is too long at {location}",
                code="evidence-schema-invalid",
            )
        if "items" in schema:
            for index, item in enumerate(value):
                validate_json_schema(item, schema["items"], f"{location}[{index}]")
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        properties = schema.get("properties", {})
        missing = required - set(value)
        if missing:
            raise WorkflowError(
                f"Evidence schema missing fields at {location}: {sorted(missing)}",
                code="evidence-schema-invalid",
            )
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise WorkflowError(
                f"Evidence schema has extra fields at {location}",
                code="evidence-schema-invalid",
            )
        for key, child in value.items():
            if key in properties:
                validate_json_schema(child, properties[key], f"{location}.{key}")


def load_evidence_schema(contract: dict[str, Any]) -> tuple[dict[str, Any], str]:
    path = ROOT / normalize(contract["evidence_policy"]["schema_path"])
    schema, schema_sha = read_json_with_sha(path)
    validate_schema_definition(schema)
    return schema, schema_sha


def validate_evidence_manifest(
    contract: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    path_text: str,
    source_snapshot_sha: str,
    source_commit: str,
    *,
    attempt: int,
    epoch_id: str | None = None,
    policy: dict[str, Any] | None = None,
    authenticate_external: bool = False,
    stored_origin_proof: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, Any] | None]:
    relative = normalize(path_text)
    evidence_root = contract["evidence_policy"]["directory"]
    if not matches(relative, [evidence_root + "/**"]):
        raise WorkflowError(
            f"Evidence manifest is outside {evidence_root}: {relative}",
            code="evidence-path-invalid",
    )
    path = ROOT / relative
    limits = contract["evidence_policy"]
    manifest_sha, _, manifest_raw = bounded_file(
        path, int(limits["maximum_manifest_bytes"]), capture=True
    )
    assert manifest_raw is not None
    document = json_object_from_bytes(manifest_raw, path)
    schema, _ = load_evidence_schema(contract)
    validate_json_schema(document, schema)
    evidence_id = document.get("evidence_id")
    if evidence_id not in step["required_evidence"]:
        raise WorkflowError(
            f"Unexpected evidence id for {step['id']}: {evidence_id}",
            code="evidence-id-invalid",
        )
    policy = policy or load_verifier_policy(contract)[0]
    requirement = policy["requirements"][evidence_id]
    evidence_epoch = epoch_id or state["epoch_id"]
    for field, expected in {
        "workflow_id": state["workflow_id"],
        "epoch_id": evidence_epoch,
        "step_id": step["id"],
        "attempt": attempt,
    }.items():
        if document.get(field) != expected:
            raise WorkflowError(
                f"Evidence identity mismatch for {evidence_id}: {field}",
                code="evidence-identity-invalid",
            )
    authorization_ref = state["steps"][step["id"]].get("authorization")
    expected_authorization_sha = (
        str(authorization_ref["sha256"])
        if authorization_ref is not None
        else ""
    )
    if document.get("authorization_receipt_sha256") != expected_authorization_sha:
        raise WorkflowError(
            f"Evidence authorization binding is invalid: {evidence_id}",
            code="evidence-authorization-invalid",
        )
    producer = document.get("producer")
    producer_kind = requirement["producer_kinds"][0]
    profile = policy["producer_profiles"][producer_kind]
    if (
        producer
        != {
            "kind": producer_kind,
            "name": profile["name"],
            "version": profile["version"],
        }
    ):
        raise WorkflowError(
            f"Evidence producer is invalid: {evidence_id}",
            code="evidence-producer-invalid",
        )
    if producer["kind"] == "evaluator" and not evidence_id.startswith("independent-"):
        raise WorkflowError(
            f"Evaluator identity is invalid for evidence: {evidence_id}",
            code="evaluator-independence-invalid",
        )
    if evidence_id.startswith("independent-") and producer["kind"] != "evaluator":
        raise WorkflowError(
            f"Independent evidence lacks an independent evaluator: {evidence_id}",
            code="evaluator-independence-invalid",
        )
    invocation = document.get("invocation")
    invocation_fields = {
        "argv",
        "cwd",
        "runner",
        "exit_code",
        "source_commit",
        "source_snapshot_sha256",
    }
    if (
        not isinstance(invocation, dict)
        or set(invocation) != invocation_fields
        or not isinstance(invocation.get("argv"), list)
        or not invocation["argv"]
        or not all(isinstance(item, str) for item in invocation["argv"])
        or not invocation.get("cwd")
        or invocation.get("runner") != profile["runner"]
        or not isinstance(invocation.get("exit_code"), int)
        or isinstance(invocation.get("exit_code"), bool)
    ):
        raise WorkflowError(
            f"Evidence invocation is invalid: {evidence_id}",
            code="evidence-invocation-invalid",
        )
    if invocation["exit_code"] != 0:
        raise WorkflowError(
            f"Evidence command failed: {evidence_id}",
            code="evidence-command-failed",
        )
    if invocation["source_snapshot_sha256"] != source_snapshot_sha:
        raise WorkflowError(
            f"Evidence was produced for different source bytes: {evidence_id}",
            code="evidence-source-drift",
        )
    if invocation["source_commit"] != source_commit:
        raise WorkflowError(
            f"Evidence was produced for a different source commit: {evidence_id}",
            code="evidence-source-drift",
        )
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise WorkflowError(
            f"Evidence has no independently hashable artifact: {evidence_id}",
            code="evidence-artifact-missing",
        )
    if len(artifacts) > int(limits["maximum_artifacts_per_manifest"]):
        raise WorkflowError(
            f"Evidence has too many artifacts: {evidence_id}",
            code="evidence-artifact-limit",
        )
    artifacts_by_role: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for artifact in artifacts:
        artifact_relative = normalize(str(artifact["path"]))
        if not any(
            matches(artifact_relative, [normalize(root) + "/**"])
            for root in limits["artifact_roots"]
        ):
            raise WorkflowError(
                f"Evidence artifact is outside approved roots: {artifact_relative}",
                code="evidence-artifact-path-invalid",
            )
        artifact_path = ROOT / artifact_relative
        actual_sha, size, raw = bounded_file(
            artifact_path,
            int(limits["maximum_artifact_bytes"]),
            capture=True,
        )
        if actual_sha != artifact["sha256"]:
            raise WorkflowError(
                f"Evidence artifact is missing or drifted: {artifact['path']}",
                code="evidence-artifact-drift",
            )
        total_bytes += size
        role = artifact.get("role")
        if not isinstance(role, str) or not role or role in artifacts_by_role:
            raise WorkflowError(
                f"Evidence artifact role is invalid or duplicated: {evidence_id}",
                code="evidence-artifact-role-invalid",
            )
        artifacts_by_role[role] = {
            "path": artifact_path,
            "sha256": actual_sha,
            "byte_length": size,
            "raw": raw,
        }
    if total_bytes > int(limits["maximum_total_artifact_bytes"]):
        raise WorkflowError(
            f"Evidence artifacts exceed total size limit: {evidence_id}",
            code="evidence-artifact-limit",
        )
    subjects = document.get("subjects")
    if not isinstance(subjects, list) or len(subjects) > int(
        limits["maximum_subjects_per_manifest"]
    ):
        raise WorkflowError(
            f"Evidence subject set is invalid: {evidence_id}",
            code="evidence-subject-invalid",
        )
    subjects_by_role: dict[str, dict[str, Any]] = {}
    for subject in subjects:
        subject_relative = normalize(str(subject["path"]))
        if not any(
            matches(subject_relative, [normalize(root) + "/**"])
            for root in limits["subject_roots"]
        ):
            raise WorkflowError(
                f"Evidence subject is outside approved roots: {subject_relative}",
                code="evidence-subject-path-invalid",
            )
        subject_path = ROOT / subject_relative
        subject_sha, subject_size, _ = bounded_file(
            subject_path, int(limits["maximum_subject_bytes"])
        )
        if subject_sha != subject["sha256"]:
            raise WorkflowError(
                f"Evidence subject is missing, drifted, or too large: {subject_relative}",
                code="evidence-subject-drift",
            )
        role = subject.get("role")
        if not isinstance(role, str) or not role or role in subjects_by_role:
            raise WorkflowError(
                f"Evidence subject role is invalid or duplicated: {evidence_id}",
                code="evidence-subject-role-invalid",
            )
        subjects_by_role[role] = {
            "path": subject_relative,
            "sha256": subject_sha,
            "byte_length": subject_size,
        }
    required_role = requirement["artifact_role"]
    if required_role not in artifacts_by_role:
        raise WorkflowError(
            f"Evidence lacks policy-required artifact role: {required_role}",
            code="evidence-required-artifact-missing",
        )
    required_artifact = artifacts_by_role[required_role]
    observation_raw = required_artifact["raw"]
    assert isinstance(observation_raw, bytes)
    observation = json_object_from_bytes(
        observation_raw, required_artifact["path"]
    )
    if evidence_id == "capability-admission-receipt":
        check_admission(contract, "advance-workflow-step")
        admission_receipt = (
            ROOT
            / normalize(contract["admission_directory"])
            / "receipt.json"
        )
        expected_admission = {
            "project_id": contract["project_id"],
            "artifact_sha256": capability_artifact_sha256(contract),
            "receipt_sha256": sha256_file(admission_receipt),
        }
        if any(
            observation.get(field) != expected
            for field, expected in expected_admission.items()
        ):
            raise WorkflowError(
                "Capability admission evidence does not bind the admitted bytes",
                code="admission-evidence-invalid",
            )
    origin_proof: dict[str, Any] | None = None
    if producer_kind == "evaluator":
        origin_proof = validate_evaluator_report(
            contract,
            observation,
            str(required_artifact["sha256"]),
            int(
                policy["external_provenance"]["evaluator"][
                    "minimum_pass_threshold"
                ]
            ),
        )
        if stored_origin_proof is not None and not exact_json_equal(
            stored_origin_proof, origin_proof
        ):
            raise WorkflowError(
                f"Stored evaluator provenance is invalid: {evidence_id}",
                code="external-provenance-drift",
            )
    elif producer_kind == "ci":
        origin_proof = validate_ci_provenance(
            policy,
            str(evidence_id),
            observation,
            source_commit,
            artifacts_by_role,
            required_role,
            subjects_by_role,
            authenticate=authenticate_external,
            stored_proof=stored_origin_proof,
        )
    elif stored_origin_proof is not None:
        raise WorkflowError(
            f"Unexpected external provenance: {evidence_id}",
            code="external-provenance-drift",
        )
    observed = json_pointer_value(observation, requirement["json_pointer"])
    if not exact_json_equal(observed, requirement["expected"]):
        raise GateFailure(
            f"Verifier policy check failed: {evidence_id}",
            code="verifier-policy-check-failed",
        )
    for pointer, expected in policy["exact_values"].get(evidence_id, {}).items():
        if not exact_json_equal(json_pointer_value(observation, pointer), expected):
            raise GateFailure(
                f"Verifier exact-value check failed: {evidence_id} {pointer}",
                code="verifier-policy-check-failed",
            )
    for pointer, threshold in policy["minimums"].get(evidence_id, {}).items():
        minimum_observed = json_pointer_value(observation, pointer)
        if (
            not isinstance(minimum_observed, int)
            or isinstance(minimum_observed, bool)
            or minimum_observed < threshold
        ):
            raise GateFailure(
                f"Verifier minimum check failed: {evidence_id} {pointer}",
                code="verifier-policy-check-failed",
            )
    for observation_pointer, state_pointer in policy["state_bindings"].get(
        evidence_id, {}
    ).items():
        observed_state_value = json_pointer_value(observation, observation_pointer)
        expected_state_value = json_pointer_value(state, state_pointer)
        if not exact_json_equal(observed_state_value, expected_state_value):
            raise WorkflowError(
                f"Evidence does not match workflow state: {evidence_id} {observation_pointer}",
                code="evidence-state-binding-invalid",
            )
    bindings: dict[str, str] = {}
    for freeze_name, pointer in policy["freeze_bindings"].get(evidence_id, {}).items():
        value = json_pointer_value(observation, pointer)
        freeze = state["freezes"].get(freeze_name)
        if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
            raise WorkflowError(
                f"Evidence freeze binding is not a SHA-256: {evidence_id}",
                code="evidence-freeze-binding-invalid",
            )
        if not freeze or freeze.get("artifact_sha256") != value:
            raise WorkflowError(
                f"Evidence does not match frozen {freeze_name}: {evidence_id}",
                code="evidence-freeze-binding-invalid",
            )
        bindings[freeze_name] = value
    for capture_name, pointer in policy["captures"].get(evidence_id, {}).items():
        value = json_pointer_value(observation, pointer)
        if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
            raise WorkflowError(
                f"Evidence capture is not a SHA-256: {evidence_id}",
                code="evidence-capture-invalid",
            )
        bindings[capture_name] = value
    for capture_name, artifact_role in policy["artifact_hash_captures"].get(
        evidence_id, {}
    ).items():
        artifact_capture = artifacts_by_role.get(artifact_role)
        if artifact_capture is None:
            raise WorkflowError(
                f"Evidence lacks captured artifact role: {artifact_role}",
                code="evidence-required-artifact-missing",
            )
        bindings[capture_name] = str(artifact_capture["sha256"])
    subject_role = policy["subject_bindings"].get(evidence_id)
    if subject_role:
        subject = subjects_by_role.get(subject_role)
        if subject is None or not bindings or any(
            value != subject["sha256"] for value in bindings.values()
        ):
            raise WorkflowError(
                f"Evidence is not bound to subject bytes: {evidence_id}",
                code="evidence-subject-binding-invalid",
            )
    elif subjects:
        raise WorkflowError(
            f"Evidence declares an unrequested subject: {evidence_id}",
            code="evidence-subject-unexpected",
        )
    size_binding = policy["subject_size_bindings"].get(evidence_id)
    if size_binding:
        subject = subjects_by_role[size_binding["role"]]
        observed_size = json_pointer_value(
            observation, size_binding["json_pointer"]
        )
        if not exact_json_equal(observed_size, subject["byte_length"]):
            raise WorkflowError(
                f"Evidence byte length does not match subject: {evidence_id}",
                code="evidence-subject-size-invalid",
            )
    return str(evidence_id), {
        "path": relative,
        "sha256": manifest_sha,
        "epoch_id": evidence_epoch,
    }, bindings, origin_proof


def orphan_failure_receipts(
    state: dict[str, Any],
) -> list[tuple[Path, dict[str, Any]]]:
    steps = state.get("steps")
    if not isinstance(steps, dict) or any(
        not isinstance(entry, dict) for entry in steps.values()
    ):
        raise WorkflowError(
            "State step set is invalid during failure recovery",
            code="state-step-invalid",
        )
    referenced = {
        normalize(str(ref["path"]))
        for entry in steps.values()
        for ref in entry.get("failed_attempts", [])
        if isinstance(ref, dict) and "path" in ref
    }
    epoch_id = state.get("epoch_id")
    if not isinstance(epoch_id, str) or EPOCH_PATTERN.fullmatch(epoch_id) is None:
        raise WorkflowError(
            "State epoch is invalid during failure recovery",
            code="state-epoch-invalid",
        )
    definitions = state.get("step_definitions")
    if not isinstance(definitions, dict):
        raise WorkflowError(
            "State step definitions are invalid during failure recovery",
            code="state-step-invalid",
        )
    directory = RECEIPTS / epoch_id
    if not directory.is_dir():
        return []
    result: list[tuple[Path, dict[str, Any]]] = []
    expected_fields = {
        "schema_version",
        "workflow_id",
        "epoch_id",
        "step_id",
        "attempt",
        "phase",
        "status",
        "failed_at",
        "step_definition_sha256",
        "predecessor_snapshot_sha256",
        "observed_snapshot_sha256",
        "error",
    }
    for path in sorted(directory.glob("S*/attempt-*-failure-*.json")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in referenced:
            continue
        _, _, receipt_raw = bounded_file(
            path, MAX_CONTROL_JSON_BYTES, capture=True
        )
        assert receipt_raw is not None
        receipt = json_object_from_bytes(receipt_raw, path)
        step_id = receipt.get("step_id")
        entry = (
            state.get("steps", {}).get(step_id)
            if isinstance(step_id, str)
            else None
        )
        error = receipt.get("error")
        attempt = receipt.get("attempt")
        expected_name = (
            f"attempt-{attempt:02d}-verification-failure-{json_sha256(receipt)[:16]}.json"
            if isinstance(attempt, int) and not isinstance(attempt, bool)
            else ""
        )
        if (
            set(receipt) != expected_fields
            or receipt_raw != canonical_json_bytes(receipt)
            or not exact_json_equal(receipt.get("schema_version"), 1)
            or receipt.get("workflow_id") != state.get("workflow_id")
            or receipt.get("epoch_id") != epoch_id
            or not isinstance(entry, dict)
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
            or receipt.get("phase") != "verification"
            or receipt.get("status") != "failed"
            or not isinstance(step_id, str)
            or receipt.get("step_definition_sha256") != definitions.get(step_id)
            or receipt.get("predecessor_snapshot_sha256")
            != entry.get("predecessor_snapshot_sha256")
            or not isinstance(receipt.get("failed_at"), str)
            or not receipt["failed_at"]
            or path.parent.name != step_id
            or path.name != expected_name
            or HASH_PATTERN.fullmatch(
                str(receipt.get("observed_snapshot_sha256", ""))
            )
            is None
            or not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or not all(isinstance(error.get(field), str) and error[field] for field in error)
        ):
            raise WorkflowError(
                f"Orphan failure receipt is invalid: {relative}",
                code="failure-receipt-invalid",
            )
        result.append((path, receipt))
    return result


def apply_failure_receipt(
    contract: dict[str, Any],
    state: dict[str, Any],
    path: Path,
    receipt: dict[str, Any],
) -> None:
    step_id = str(receipt["step_id"])
    entry = state["steps"][step_id]
    attempt = int(receipt["attempt"])
    if (
        state.get("status") != "active"
        or state.get("current_step") != step_id
        or entry.get("status") != "in_progress"
        or int(entry.get("attempt", 0)) != attempt
    ):
        raise WorkflowError(
            f"Orphan failure receipt cannot be replayed: {path.relative_to(ROOT)}",
            code="failure-replay-conflict",
        )
    entry["failed_attempts"].append(receipt_ref(path, state["epoch_id"]))
    for field in ("verification", "authorization"):
        if entry.get(field):
            entry["superseded_receipts"].append(entry.pop(field))
    entry["remediation_cycles"] = int(entry.get("remediation_cycles", 0)) + 1
    entry["last_failure_code"] = receipt["error"]["code"]
    if entry["remediation_cycles"] > int(
        contract["maximum_integrated_remediation_cycles"]
    ):
        entry["status"] = "blocked"
        state["status"] = "needs_replan"
        state["current_step"] = None
    else:
        entry["attempt"] = attempt + 1
    persist_state(
        state,
        "step-failed",
        {
            "step_id": step_id,
            "attempt": attempt,
            "phase": receipt["phase"],
            "error_code": receipt["error"]["code"],
            "failure_receipt_sha256": sha256_file(path),
            "workflow_status": state["status"],
        },
    )


def recover_orphan_failures() -> None:
    if not DEFAULT_STATE.is_file():
        return
    raw_state = read_json(DEFAULT_STATE)
    if raw_state.get("schema_version") != STATE_SCHEMA_VERSION:
        return
    contract = load_contract()
    if raw_state.get("workflow_id") != contract["workflow_id"]:
        return
    state = load_state(
        contract,
        allow_definition_drift=True,
        allow_orphan_failures=True,
    )
    while True:
        orphans = orphan_failure_receipts(state)
        if not orphans:
            return
        path, receipt = orphans[0]
        apply_failure_receipt(contract, state, path, receipt)


def record_failure(
    contract: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    phase: str,
    error: WorkflowError,
    observed_snapshot: dict[str, Any] | None = None,
) -> None:
    entry = state["steps"][step["id"]]
    attempt = int(entry["attempt"])
    snapshot = observed_snapshot or current_snapshot(contract)
    receipt = {
        "schema_version": 1,
        "workflow_id": state["workflow_id"],
        "epoch_id": state["epoch_id"],
        "step_id": step["id"],
        "attempt": attempt,
        "phase": phase,
        "status": "failed",
        "failed_at": now(),
        "step_definition_sha256": step_sha256(step),
        "predecessor_snapshot_sha256": entry[
            "predecessor_snapshot_sha256"
        ],
        "observed_snapshot_sha256": snapshot_sha256(snapshot),
        "error": {"code": error.code, "message": str(error)[:512]},
    }
    path = write_immutable_json(
        RECEIPTS / state["epoch_id"] / step["id"],
        f"attempt-{attempt:02d}-{phase}-failure",
        receipt,
    )
    apply_failure_receipt(contract, state, path, receipt)


def validate_step_authorization(
    contract: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    source_snapshot_sha256: str,
) -> None:
    if not step["authorization_scope"]:
        return
    entry = state["steps"][step["id"]]
    if not entry.get("authorization"):
        raise WorkflowError(
            f"Step requires authorization scope: {step['authorization_scope']}",
            code="authorization-required",
        )
    _, authorization = read_referenced_json(
        entry["authorization"],
        f"{step['id']} authorization receipt",
        expected_epoch=state["epoch_id"],
    )
    expected_authorization = {
        "workflow_id": state["workflow_id"],
        "epoch_id": state["epoch_id"],
        "step_id": step["id"],
        "attempt": int(entry["attempt"]),
        "scope": step["authorization_scope"],
        "actions": contract["authorization_actions"][step["id"]],
        "source_snapshot_sha256": source_snapshot_sha256,
        "candidate_sha256": state["freezes"]["candidate"]["artifact_sha256"],
    }
    for field, value in expected_authorization.items():
        if not exact_json_equal(authorization.get(field), value):
            raise WorkflowError(
                f"Authorization receipt drift: {field}",
                code="authorization-invalid",
            )


def cmd_verify(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    if args.step != step["id"]:
        raise WorkflowError(
            f"Current step is {step['id']}, not {args.step}",
            code="step-mismatch",
        )
    entry = state["steps"][step["id"]]
    if len(args.evidence_manifests) != len(set(args.evidence_manifests)):
        raise WorkflowError(
            "Duplicate evidence manifest arguments",
            code="evidence-duplicate",
        )
    current: dict[str, Any] | None = None
    try:
        delta, current = delta_from_snapshot(
            contract, state["last_completed_snapshot"]
        )
        enforce_paths(contract, state, list(delta))
        source_sha = snapshot_sha256(current)
        if step["id"] == "S09" and current["overlay"]:
            raise WorkflowError(
                "S09 requires all governed source bytes to be committed",
                code="candidate-source-uncommitted",
            )
        validate_step_authorization(contract, state, step, source_sha)
        manifests: dict[str, dict[str, str]] = {}
        captures: dict[str, str] = {}
        origin_proofs: dict[str, dict[str, Any]] = {}
        policy, _ = load_verifier_policy(contract)
        for item in args.evidence_manifests:
            evidence_id, ref, evidence_captures, origin_proof = validate_evidence_manifest(
                contract,
                state,
                step,
                item,
                source_sha,
                str(current["commit"]),
                attempt=int(entry["attempt"]),
                policy=policy,
                authenticate_external=True,
            )
            if evidence_id in manifests:
                raise WorkflowError(
                    f"Duplicate evidence id: {evidence_id}",
                    code="evidence-duplicate",
                )
            manifests[evidence_id] = ref
            for name, value in evidence_captures.items():
                if name in captures and captures[name] != value:
                    raise WorkflowError(
                        f"Conflicting evidence capture: {name}",
                        code="evidence-capture-conflict",
                    )
                captures[name] = value
            if origin_proof is not None:
                origin_proofs[evidence_id] = origin_proof
        missing = [
            item
            for item in step["required_evidence"]
            if item not in manifests
        ]
        if missing:
            raise WorkflowError(
                f"Missing required evidence: {', '.join(missing)}",
                code="evidence-missing",
            )
        validate_external_proof_bindings(
            policy,
            state,
            captures,
            origin_proofs,
            step["required_evidence"],
        )
    except GateFailure as exc:
        record_failure(
            contract,
            state,
            step,
            "verification",
            exc,
            observed_snapshot=current,
        )
        suffix = (
            "needs replan"
            if state["status"] == "needs_replan"
            else "one integrated remediation remains"
        )
        raise WorkflowError(f"{exc}; {suffix}", code=exc.code) from exc
    attempt = int(entry["attempt"])
    receipt = {
        "schema_version": 2,
        "workflow_id": state["workflow_id"],
        "epoch_id": state["epoch_id"],
        "step_id": step["id"],
        "attempt": attempt,
        "status": "passed",
        "verified_at": now(),
        "contract_sha256": state["contract_sha256"],
        "step_definition_sha256": step_sha256(step),
        "verifier_policy_sha256": entry["verifier_policy_sha256"],
        "evidence_schema_sha256": entry["evidence_schema_sha256"],
        "control_plane_sha256": entry["control_plane_sha256"],
        "predecessor_snapshot_sha256": entry[
            "predecessor_snapshot_sha256"
        ],
        "verified_snapshot": current,
        "verified_snapshot_sha256": source_sha,
        "delta_paths": sorted(delta),
        "evidence_manifests": manifests,
        "captures": captures,
        "origin_proofs": origin_proofs,
    }
    path = write_immutable_json(
        RECEIPTS / state["epoch_id"] / step["id"],
        f"attempt-{attempt:02d}-verification",
        receipt,
    )
    new_ref = receipt_ref(path, state["epoch_id"])
    if entry.get("verification") and entry["verification"] != new_ref:
        entry["superseded_receipts"].append(entry["verification"])
    entry["verification"] = new_ref
    persist_state(
        state,
        "step-verified",
        {
            "step_id": step["id"],
            "attempt": attempt,
            "receipt_sha256": sha256_file(path),
        },
    )
    print(
        json.dumps(
            {
                "status": "verified",
                "step": step["id"],
                "attempt": attempt,
                "receipt_sha256": sha256_file(path),
            }
        )
    )


def cmd_complete(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    if args.step != step["id"]:
        raise WorkflowError(
            f"Current step is {step['id']}, not {args.step}",
            code="step-mismatch",
        )
    entry = state["steps"][step["id"]]
    if not entry.get("verification"):
        raise WorkflowError(
            "Step has no verification receipt", code="verification-missing"
        )
    receipt = validate_verification_ref(contract, state, step["id"], entry)
    if receipt["verified_snapshot_sha256"] != snapshot_sha256(
        current_snapshot(contract)
    ):
        raise WorkflowError(
            "Working bytes changed after verification",
            code="post-verification-drift",
        )
    for name, freeze_spec in contract["freeze_points"].items():
        if freeze_spec["step"] != step["id"]:
            continue
        capture_name = freeze_spec["capture"]
        artifact_sha = receipt["captures"].get(capture_name)
        if not artifact_sha:
            raise WorkflowError(
                f"Step lacks mandatory freeze capture: {capture_name}",
                code="freeze-capture-missing",
            )
        must_equal = freeze_spec.get("must_equal")
        if must_equal:
            comparison = state["freezes"].get(must_equal)
            if not comparison or comparison["artifact_sha256"] != artifact_sha:
                raise WorkflowError(
                    f"Freeze {name} does not equal {must_equal}",
                    code="freeze-identity-mismatch",
                )
        frozen = {
            "step": step["id"],
            "created_at": now(),
            "source_snapshot_sha256": receipt["verified_snapshot_sha256"],
            "artifact_sha256": artifact_sha,
            "verification_receipt_sha256": entry["verification"]["sha256"],
        }
        existing = state["freezes"].get(name)
        if existing and existing != frozen:
            raise WorkflowError(
                f"Freeze already exists with different bytes: {name}",
                code="freeze-identity-mismatch",
            )
        state["freezes"][name] = frozen
    entry["status"] = "completed"
    entry["completed_at"] = now()
    state["current_step"] = None
    state["last_completed_snapshot"] = receipt["verified_snapshot"]
    state["last_completed_snapshot_sha256"] = receipt[
        "verified_snapshot_sha256"
    ]
    persist_state(
        state,
        "step-completed",
        {"step_id": step["id"], "attempt": entry["attempt"]},
    )
    print(json.dumps({"status": "completed", "step": step["id"]}))


def enter_step(
    contract: dict[str, Any], state: dict[str, Any], step_id: str
) -> None:
    if state["status"] != "active" or state["current_step"]:
        raise WorkflowError(
            "Workflow is not ready to enter a step",
            code="step-entry-invalid",
        )
    step = next(
        (item for item in contract["steps"] if item["id"] == step_id),
        None,
    )
    if not step or state["steps"][step_id]["status"] != "pending":
        raise WorkflowError(
            f"Step cannot be entered: {step_id}",
            code="step-entry-invalid",
        )
    incomplete = [
        item
        for item in step["prerequisites"]
        if state["steps"][item]["status"] != "completed"
    ]
    if incomplete:
        raise WorkflowError(
            f"Incomplete prerequisites: {', '.join(incomplete)}",
            code="prerequisite-incomplete",
        )
    entry = state["steps"][step_id]
    entry["status"] = "in_progress"
    entry["predecessor_snapshot_sha256"] = state[
        "last_completed_snapshot_sha256"
    ]
    state["current_step"] = step_id
    persist_state(
        state,
        "step-entered",
        {"step_id": step_id, "attempt": entry["attempt"]},
    )
    print(json.dumps({"status": "entered", "step": step_id}))


def cmd_next(_args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    if state["status"] != "active":
        raise WorkflowError(
            f"Workflow is {state['status']}; replan before advancing",
            code="workflow-not-active",
        )
    if state["current_step"]:
        raise WorkflowError(
            "Complete the current step before next",
            code="current-step-incomplete",
        )
    pending = [
        step
        for step in contract["steps"]
        if state["steps"][step["id"]]["status"] == "pending"
    ]
    if not pending:
        incomplete = [
            step_id
            for step_id, entry in state["steps"].items()
            if entry["status"] != "completed"
        ]
        if incomplete:
            raise WorkflowError(
                f"Workflow cannot complete with incomplete steps: {incomplete}",
                code="state-terminal-invalid",
            )
        state["status"] = "completed"
        persist_state(state, "workflow-completed", {})
        print(json.dumps({"status": "completed"}))
        return
    enter_step(contract, state, pending[0]["id"])


def cmd_authorize(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    if args.step != step["id"]:
        raise WorkflowError(
            f"Current step is {step['id']}, not {args.step}",
            code="step-mismatch",
        )
    scope = step["authorization_scope"]
    if not scope:
        raise WorkflowError(
            f"{step['id']} has no user-authorization boundary",
            code="authorization-not-applicable",
        )
    if args.scope != scope:
        raise WorkflowError(
            f"Authorization scope must be exactly {scope}",
            code="authorization-scope-invalid",
        )
    if not args.authorization_id.strip():
        raise WorkflowError(
            "Authorization id must be non-empty",
            code="authorization-id-invalid",
        )
    candidate = state["freezes"].get("candidate")
    if not candidate:
        raise WorkflowError(
            "Authorization requires a frozen candidate",
            code="authorization-candidate-missing",
        )
    entry = state["steps"][step["id"]]
    receipt = {
        "schema_version": 1,
        "workflow_id": state["workflow_id"],
        "epoch_id": state["epoch_id"],
        "step_id": step["id"],
        "attempt": int(entry["attempt"]),
        "scope": scope,
        "actions": contract["authorization_actions"][step["id"]],
        "authorization_id": args.authorization_id,
        "recorded_at": now(),
        "source_snapshot_sha256": snapshot_sha256(current_snapshot(contract)),
        "candidate_sha256": candidate["artifact_sha256"],
    }
    path = write_immutable_json(
        RECEIPTS / state["epoch_id"] / step["id"],
        f"attempt-{int(entry['attempt']):02d}-authorization",
        receipt,
    )
    entry["authorization"] = receipt_ref(path, state["epoch_id"])
    persist_state(
        state,
        "authorization-recorded",
        {
            "step_id": step["id"],
            "scope": scope,
            "authorization_id": args.authorization_id,
        },
    )
    print(
        json.dumps(
            {
                "status": "authorization-recorded",
                "step": step["id"],
                "scope": scope,
            }
        )
    )


def earliest_affected_step(
    contract: dict[str, Any], state: dict[str, Any]
) -> str:
    ids = [step["id"] for step in contract["steps"]]
    candidates: list[int] = []
    policy, current_policy_sha = load_verifier_policy(contract)
    _, current_schema_sha = load_evidence_schema(contract)
    current_control_plane_sha = capability_artifact_sha256(contract)
    if (
        state.get("contract_core_sha256") != contract_core_sha256(contract)
        or (
            state.get("contract_sha256") != contract_source_sha256(contract)
            and state.get("step_definitions")
            == {step["id"]: step_sha256(step) for step in contract["steps"]}
        )
    ):
        candidates.append(0)
    old_definitions = state.get("step_definitions", {})
    for index, step in enumerate(contract["steps"]):
        if old_definitions.get(step["id"]) != step_sha256(step):
            candidates.append(index)
    for index, step_id in enumerate(ids):
        if (
            state.get("steps", {})
            .get(step_id, {})
            .get("status")
            == "blocked"
        ):
            candidates.append(index)
    if (
        state.get("evidence_schema_sha256") != current_schema_sha
        or state.get("control_plane_sha256") != current_control_plane_sha
    ):
        candidates.append(0)
    current_evidence = evidence_definition_hashes(
        contract, policy, current_schema_sha
    )
    old_evidence = state.get("evidence_definitions", {})
    for evidence_id in set(current_evidence) | set(old_evidence):
        if old_evidence.get(evidence_id) == current_evidence.get(evidence_id):
            continue
        for index, step in enumerate(contract["steps"]):
            if evidence_id in step["required_evidence"]:
                candidates.append(index)
                break
        else:
            candidates.append(0)
    if (
        state.get("verifier_policy_sha256") != current_policy_sha
        and old_evidence == current_evidence
    ):
        candidates.append(0)
    if not candidates:
        raise WorkflowError(
            "No changed or blocked step requires replan",
            code="replan-not-needed",
        )
    return ids[min(candidates)]


def cmd_replan(args: argparse.Namespace) -> None:
    contract = load_contract()
    if not DEFAULT_STATE.is_file():
        raise WorkflowError("Workflow state is missing", code="state-missing")
    captured_state, old_state_sha, old_state_bytes = capture_state()
    state = _validate_state_document(
        contract, captured_state, allow_definition_drift=True
    )
    current_policy, current_policy_sha = load_verifier_policy(contract)
    _, current_schema_sha = load_evidence_schema(contract)
    current_control_plane_sha = capability_artifact_sha256(contract)
    contract_drift = (
        state.get("contract_sha256") != contract_source_sha256(contract)
        or state.get("step_definitions")
        != {
            step["id"]: step_sha256(step)
            for step in contract["steps"]
        }
        or state.get("verifier_policy_sha256") != current_policy_sha
        or state.get("evidence_schema_sha256") != current_schema_sha
        or state.get("control_plane_sha256") != current_control_plane_sha
        or state.get("evidence_definitions")
        != evidence_definition_hashes(
            contract, current_policy, current_schema_sha
        )
    )
    needs_replan = (
        state.get("status") == "needs_replan"
        and state.get("current_step") is None
    )
    if not needs_replan and not (
        state.get("status") in {"active", "completed"} and contract_drift
    ):
        raise WorkflowError(
            "Replan requires needs_replan or actual contract drift",
            code="replan-state-invalid",
        )
    ids = [step["id"] for step in contract["steps"]]
    earliest = earliest_affected_step(contract, state)
    if state.get("current_step"):
        active_index = ids.index(state["current_step"])
        if active_index < ids.index(earliest):
            earliest = state["current_step"]
    if args.resume_step not in ids:
        raise WorkflowError(
            f"Unknown resume step: {args.resume_step}",
            code="replan-step-invalid",
        )
    if ids.index(args.resume_step) > ids.index(earliest):
        raise WorkflowError(
            f"Replan cannot resume after earliest affected step {earliest}",
            code="replan-too-late",
        )
    resume_index = ids.index(args.resume_step)
    for step in contract["steps"][:resume_index]:
        old_entry = state["steps"][step["id"]]
        if (
            old_entry.get("status") != "completed"
            or state["step_definitions"].get(step["id"])
            != step_sha256(step)
        ):
            raise WorkflowError(
                f"Cannot preserve affected prerequisite: {step['id']}",
                code="replan-prefix-invalid",
            )
    preserved_receipts: dict[str, dict[str, Any]] = {}
    for step in contract["steps"][:resume_index]:
        preserved_receipts[step["id"]] = validate_verification_ref(
            contract,
            state,
            step["id"],
            state["steps"][step["id"]],
        )
    if resume_index:
        previous_id = ids[resume_index - 1]
        prior_receipt = preserved_receipts[previous_id]
        baseline = prior_receipt["verified_snapshot"]
    else:
        baseline = snapshot_for_current_scope(
            contract, state["epoch_baseline"], "prior epoch baseline"
        )
    old_epoch = state["epoch_id"]
    epoch_id = f"E{int(old_epoch[1:]) + 1:03d}"
    archived_state = (
        LEGACY / "epochs" / f"{old_epoch}-state-{old_state_sha[:16]}.json"
    )
    if archived_state.exists():
        _, _, archived_bytes = bounded_file(
            archived_state, MAX_CONTROL_JSON_BYTES, capture=True
        )
        if archived_bytes != old_state_bytes:
            raise WorkflowError(
                "Previous epoch archive collision",
                code="legacy-archive-collision",
            )
    if not archived_state.exists():
        atomic_replace_bytes(archived_state, old_state_bytes)
    new_steps: dict[str, Any] = {}
    for index, step in enumerate(contract["steps"]):
        if index < resume_index:
            entry = dict(state["steps"][step["id"]])
            entry["preserved_from_epoch"] = old_epoch
            new_steps[step["id"]] = entry
        else:
            new_steps[step["id"]] = {
                "status": (
                    "in_progress" if index == resume_index else "pending"
                ),
                "attempt": 1,
                "contract_sha256": contract_source_sha256(contract),
                "verifier_policy_sha256": current_policy_sha,
                "evidence_schema_sha256": current_schema_sha,
                "control_plane_sha256": current_control_plane_sha,
                "remediation_cycles": 0,
                "predecessor_snapshot_sha256": (
                    snapshot_sha256(baseline)
                    if index == resume_index
                    else None
                ),
                "failed_attempts": [],
                "superseded_receipts": [],
            }
    previous_epoch = {
        "epoch_id": old_epoch,
        "state_sha256": old_state_sha,
        "state_path": archived_state.relative_to(ROOT).as_posix(),
        "status": state["status"],
        "superseded_at": now(),
        "reason": args.reason,
        "earliest_affected_step": earliest,
    }
    freezes = dict(state.get("freezes", {}))
    for name, freeze_spec in contract["freeze_points"].items():
        if ids.index(freeze_spec["step"]) >= resume_index:
            freezes[name] = None
    state.update(
        {
            "contract_sha256": contract_source_sha256(contract),
            "contract_core_sha256": contract_core_sha256(contract),
            "step_definitions": {
                step["id"]: step_sha256(step)
                for step in contract["steps"]
            },
            "verifier_policy_sha256": current_policy_sha,
            "evidence_schema_sha256": current_schema_sha,
            "control_plane_sha256": current_control_plane_sha,
            "evidence_definitions": evidence_definition_hashes(
                contract, current_policy, current_schema_sha
            ),
            "epoch_id": epoch_id,
            "status": "active",
            "current_step": args.resume_step,
            "epoch_baseline": baseline,
            "epoch_baseline_sha256": snapshot_sha256(baseline),
            "last_completed_snapshot": baseline,
            "last_completed_snapshot_sha256": snapshot_sha256(baseline),
            "previous_epoch": previous_epoch,
            "freezes": freezes,
            "steps": new_steps,
            "event_sequence": 0,
            "last_event_sha256": None,
            "last_event_ref": None,
        }
    )
    record = {
        "schema_version": 2,
        "workflow_id": contract["workflow_id"],
        "old_epoch_id": old_epoch,
        "new_epoch_id": epoch_id,
        "replanned_at": now(),
        "reason": args.reason,
        "requested_resume_step": args.resume_step,
        "earliest_affected_step": earliest,
        "previous_state_sha256": old_state_sha,
        "previous_state_path": archived_state.relative_to(ROOT).as_posix(),
        "new_contract_sha256": state["contract_sha256"],
        "baseline_source": (
            "last-completed-verification"
            if resume_index
            else "prior-epoch-baseline"
        ),
        "baseline_sha256": state["epoch_baseline_sha256"],
        "current_worktree_was_promoted": False,
    }
    path = write_immutable_json(
        RECEIPTS / epoch_id, f"replan-from-{old_epoch}", record
    )
    state["replan_receipt"] = receipt_ref(path, epoch_id)
    persist_state(
        state,
        "epoch-replanned",
        {
            "reason": args.reason,
            "resume_step": args.resume_step,
            "earliest_affected_step": earliest,
        },
    )
    print(
        json.dumps(
            {
                "status": "replanned",
                "epoch_id": epoch_id,
                "resume_step": args.resume_step,
                "earliest_affected_step": earliest,
            }
        )
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--baseline-manifest")
    init.add_argument("--baseline-manifest-sha256")
    init.set_defaults(func=cmd_init)
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--baseline-manifest", required=True)
    migrate.add_argument("--baseline-manifest-sha256", required=True)
    migrate.add_argument("--reason", required=True)
    migrate.set_defaults(func=cmd_migrate)
    commands.add_parser("prepare-migration").set_defaults(
        func=cmd_prepare_migration
    )
    commands.add_parser("admission-status").set_defaults(
        func=cmd_admission_status
    )
    commands.add_parser("status").set_defaults(func=cmd_status)
    hook = commands.add_parser("hook")
    hook.add_argument("phase", choices=("pre-edit", "post-edit"))
    hook.add_argument("--path", dest="paths", action="append", default=[])
    hook.set_defaults(func=cmd_hook)
    verify = commands.add_parser("verify")
    verify.add_argument("step")
    verify.add_argument(
        "--evidence-manifest",
        dest="evidence_manifests",
        action="append",
        default=[],
    )
    verify.set_defaults(func=cmd_verify)
    complete = commands.add_parser("complete")
    complete.add_argument("step")
    complete.set_defaults(func=cmd_complete)
    commands.add_parser("next").set_defaults(func=cmd_next)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("step")
    authorize.add_argument("--scope", required=True)
    authorize.add_argument("--authorization-id", required=True)
    authorize.set_defaults(func=cmd_authorize)
    replan = commands.add_parser("replan")
    replan.add_argument("--resume-step", required=True)
    replan.add_argument("--reason", required=True)
    replan.set_defaults(func=cmd_replan)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        mutating = {
            "init",
            "migrate",
            "prepare-migration",
            "verify",
            "complete",
            "next",
            "authorize",
            "replan",
        }
        if args.command in mutating:
            with exclusive_lock(WORKFLOW_LOCK):
                if args.command not in {"init", "migrate", "prepare-migration"}:
                    recover_orphan_failures()
                args.func(args)
        else:
            args.func(args)
        return 0
    except WorkflowError as exc:
        print(f"workflow-error[{exc.code}]: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(
            f"workflow-error[platform-operation-failed]: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
