#!/usr/bin/env python3
"""Hash-bound controller for the project-local installer workflow."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs" / "install-transaction" / "contract.json"
DEFAULT_STATE = ROOT / "docs" / "install-transaction" / "runtime-state.json"
RECEIPTS = ROOT / "docs" / "install-transaction" / "receipts"
ADMISSION = ROOT / "docs" / "capability-admission" / "v2.20.0-installer-workflow"
VOLATILE_PREFIXES = (
    "docs/install-transaction/runtime-state.json",
    "docs/install-transaction/receipts/",
    "docs/install-transaction/pending/",
    "docs/install-transaction/completed/",
    "docs/install-transaction/governance-state.json",
    "docs/install-transaction/change-log.md",
)


class WorkflowError(RuntimeError):
    pass


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise WorkflowError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def normalize(path: str) -> str:
    candidate = path.replace("\\", "/")
    if candidate.startswith("./"):
        candidate = candidate[2:]
    resolved = (ROOT / candidate).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise WorkflowError(f"Path escapes project root: {path}") from exc


def is_volatile(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in VOLATILE_PREFIXES)


def changed_paths() -> list[str]:
    names: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        output = git(*args).decode("utf-8", errors="surrogateescape")
        names.update(normalize(line) for line in output.splitlines() if line.strip())
    return sorted(path for path in names if not is_volatile(path))


def worktree_map() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in changed_paths():
        path = ROOT / relative
        result[relative] = sha256_file(path) if path.is_file() else "<deleted>"
    return result


def map_hash(value: dict[str, str]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def capability_artifact_sha256() -> str:
    identity = {
        "contract_sha256": sha256_file(DEFAULT_CONTRACT),
        "controller_sha256": sha256_file(Path(__file__).resolve()),
        "reuse_map_sha256": sha256_file(
            ROOT / "docs" / "install-transaction" / "replan-reuse-map.json"
        ),
        "reuse_validator_sha256": sha256_file(
            ROOT / "scripts" / "validate_installer_reuse_map.py"
        ),
    }
    return sha256_bytes(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def matches(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**") and (path == pattern[:-3] or path.startswith(pattern[:-2])):
            return True
        if fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def load_contract() -> dict[str, Any]:
    contract = read_json(DEFAULT_CONTRACT)
    ids = [step["id"] for step in contract.get("steps", [])]
    if ids != [f"S{i:02d}" for i in range(1, 16)]:
        raise WorkflowError("Contract must contain ordered S01-S15 exactly")
    return contract


def check_admission(action_id: str) -> None:
    manifest_path = ADMISSION / "manifest.json"
    registry_path = ADMISSION / "registry.json"
    binding_path = ADMISSION / "binding.json"
    review_path = ADMISSION / "semantic-review.json"
    receipt_path = ADMISSION / "receipt.json"
    for path in (manifest_path, registry_path, binding_path, review_path, receipt_path):
        if not path.is_file():
            raise WorkflowError(f"Capability admission file is missing: {path.relative_to(ROOT)}")
    manifest = read_json(manifest_path)
    registry = read_json(registry_path)
    binding = read_json(binding_path)
    receipt = read_json(receipt_path)
    expected = {
        "registry_sha256": sha256_file(registry_path),
        "binding_sha256": sha256_file(binding_path),
        "semantic_review_sha256": sha256_file(review_path),
        "manifest_sha256": sha256_file(manifest_path),
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise WorkflowError(f"Capability admission hash drift: {field}")
    if manifest.get("artifact_sha256") != capability_artifact_sha256():
        raise WorkflowError("Capability artifact hash drift")
    if binding.get("registry_sha256") != expected["registry_sha256"]:
        raise WorkflowError("Capability binding registry hash drift")
    if registry.get("capabilities") != ["manifest.json"]:
        raise WorkflowError("Capability registry is not closed")
    if receipt.get("status") != "allowed" or receipt.get("project_id") != "memory-wuxian-v2.20.0":
        raise WorkflowError("Capability is not admitted for this project")
    if action_id not in receipt.get("allowed_actions", []):
        raise WorkflowError(f"Capability action is not admitted: {action_id}")


def load_state(contract: dict[str, Any]) -> dict[str, Any]:
    if not DEFAULT_STATE.is_file():
        raise WorkflowError("Workflow state is missing; run init")
    state = read_json(DEFAULT_STATE)
    if state.get("contract_sha256") != sha256_file(DEFAULT_CONTRACT):
        raise WorkflowError("Contract hash drift; explicit replan is required")
    if state.get("workflow_id") != contract.get("workflow_id"):
        raise WorkflowError("Workflow identity mismatch")
    active = [key for key, value in state["steps"].items() if value["status"] == "in_progress"]
    if len(active) > 1 or state.get("current_step") != (active[0] if active else None):
        raise WorkflowError("State invariant failed: exactly zero or one current step")
    for step_id, entry in state["steps"].items():
        receipt_path = entry.get("verification_receipt")
        receipt_hash = entry.get("verification_sha256")
        if receipt_path and receipt_hash:
            path = ROOT / receipt_path
            if not path.is_file() or sha256_file(path) != receipt_hash:
                raise WorkflowError(f"Receipt drift for {step_id}")
    return state


def delta_from_baseline(state: dict[str, Any]) -> dict[str, str]:
    baseline = state["baseline_worktree"]
    current = worktree_map()
    return {path: current.get(path, "<clean>") for path in sorted(set(baseline) | set(current)) if baseline.get(path) != current.get(path)}


def current_definition(contract: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    step_id = state.get("current_step")
    if not step_id:
        raise WorkflowError("No step is in progress; run next or enter")
    return next(step for step in contract["steps"] if step["id"] == step_id)


def enforce_paths(contract: dict[str, Any], state: dict[str, Any], paths: list[str]) -> None:
    step = current_definition(contract, state)
    denied = [path for path in paths if matches(path, contract["protected_paths"]) and not matches(path, step["allowed_paths"])]
    if denied:
        raise WorkflowError(f"{step['id']} does not allow protected paths: {', '.join(denied)}")


def cmd_init(_args: argparse.Namespace) -> None:
    if DEFAULT_STATE.exists():
        raise WorkflowError("Workflow state already exists")
    contract = load_contract()
    baseline = worktree_map()
    steps = {step["id"]: {"status": "pending", "remediation_cycles": 0} for step in contract["steps"]}
    steps["S01"]["status"] = "in_progress"
    state = {
        "schema_version": 1,
        "workflow_id": contract["workflow_id"],
        "contract_path": DEFAULT_CONTRACT.relative_to(ROOT).as_posix(),
        "contract_sha256": sha256_file(DEFAULT_CONTRACT),
        "status": "active",
        "current_step": "S01",
        "created_at": now(),
        "updated_at": now(),
        "baseline_worktree": baseline,
        "baseline_sha256": map_hash(baseline),
        "last_verified_commit": git("rev-parse", "HEAD").decode().strip(),
        "freezes": {name: None for name in contract["freeze_points"]},
        "steps": steps,
    }
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "initialized", "current_step": "S01", "baseline_sha256": state["baseline_sha256"]}))


def cmd_status(_args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    delta = delta_from_baseline(state)
    output = {
        "status": state["status"],
        "current_step": state["current_step"],
        "contract_sha256": state["contract_sha256"],
        "baseline_sha256": state["baseline_sha256"],
        "current_worktree_sha256": map_hash(worktree_map()),
        "delta_paths": sorted(delta),
        "freezes": state["freezes"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_hook(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    if state["status"] != "active":
        raise WorkflowError(f"Workflow is {state['status']}")
    if args.phase == "pre-edit":
        if not args.paths:
            raise WorkflowError("pre-edit requires at least one --path")
        paths = [normalize(path) for path in args.paths]
    else:
        paths = list(delta_from_baseline(state))
    enforce_paths(contract, state, paths)
    print(json.dumps({"status": "passed", "phase": args.phase, "paths": paths}, ensure_ascii=False))


def cmd_verify(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    if args.step != step["id"]:
        raise WorkflowError(f"Current step is {step['id']}, not {args.step}")
    reuse_map = ROOT / "docs" / "install-transaction" / "replan-reuse-map.json"
    if step["id"] in {"S06", "S07", "S08", "S09", "S10"}:
        if not reuse_map.is_file():
            raise WorkflowError("Installer reuse-map gate failed: reuse map is missing")
        try:
            from validate_installer_reuse_map import validate_reuse_map

            validate_reuse_map(ROOT, reuse_map, step=step["id"], state=state)
        except (OSError, ValueError, RuntimeError) as exc:
            raise WorkflowError(f"Installer reuse-map gate failed: {exc}") from exc
    delta = delta_from_baseline(state)
    enforce_paths(contract, state, list(delta))
    missing = [item for item in step["required_evidence"] if item not in args.evidence]
    if missing:
        raise WorkflowError(f"Missing required evidence: {', '.join(missing)}")
    receipt = {
        "schema_version": 1,
        "workflow_id": contract["workflow_id"],
        "step": step["id"],
        "verified_at": now(),
        "contract_sha256": state["contract_sha256"],
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "worktree_sha256": map_hash(worktree_map()),
        "delta_paths": sorted(delta),
        "evidence": sorted(args.evidence),
    }
    path = RECEIPTS / f"{step['id']}-verification.json"
    atomic_json(path, receipt)
    entry = state["steps"][step["id"]]
    entry["verification_receipt"] = path.relative_to(ROOT).as_posix()
    entry["verification_sha256"] = sha256_file(path)
    entry["verified_worktree_sha256"] = receipt["worktree_sha256"]
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "verified", "step": step["id"], "receipt_sha256": entry["verification_sha256"]}))


def cmd_complete(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    if args.step != step["id"]:
        raise WorkflowError(f"Current step is {step['id']}, not {args.step}")
    entry = state["steps"][step["id"]]
    if not entry.get("verification_receipt"):
        raise WorkflowError("Step has no verification receipt")
    if entry["verified_worktree_sha256"] != map_hash(worktree_map()):
        raise WorkflowError("Working bytes changed after verification")
    entry["status"] = "completed"
    entry["completed_at"] = now()
    state["current_step"] = None
    promoted = worktree_map()
    state["baseline_worktree"] = promoted
    state["baseline_sha256"] = map_hash(promoted)
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "completed", "step": step["id"]}))


def cmd_enter(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    if state["status"] != "active" or state["current_step"]:
        raise WorkflowError("Workflow is not ready to enter a step")
    step = next((item for item in contract["steps"] if item["id"] == args.step), None)
    if not step or state["steps"][args.step]["status"] != "pending":
        raise WorkflowError(f"Step cannot be entered: {args.step}")
    incomplete = [item for item in step["prerequisites"] if state["steps"][item]["status"] != "completed"]
    if incomplete:
        raise WorkflowError(f"Incomplete prerequisites: {', '.join(incomplete)}")
    state["steps"][args.step]["status"] = "in_progress"
    state["current_step"] = args.step
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "entered", "step": args.step}))


def cmd_next(_args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    if state["current_step"]:
        raise WorkflowError("Complete the current step before next")
    pending = [step for step in contract["steps"] if state["steps"][step["id"]]["status"] == "pending"]
    if not pending:
        state["status"] = "completed"
        state["updated_at"] = now()
        atomic_json(DEFAULT_STATE, state)
        print(json.dumps({"status": "completed"}))
        return
    args = argparse.Namespace(step=pending[0]["id"])
    cmd_enter(args)


def cmd_remediate(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    step = current_definition(contract, state)
    entry = state["steps"][step["id"]]
    entry["remediation_cycles"] += 1
    entry["remediation_reason"] = args.reason
    if entry["remediation_cycles"] > contract["maximum_integrated_remediation_cycles"]:
        state["status"] = "needs_replan"
        state["current_step"] = None
        entry["status"] = "blocked"
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    if state["status"] == "needs_replan":
        raise WorkflowError("Remediation limit exceeded; explicit replan is required")
    print(json.dumps({"status": "remediation-authorized", "step": step["id"], "cycle": entry["remediation_cycles"]}))


def cmd_replan(args: argparse.Namespace) -> None:
    contract = load_contract()
    if not DEFAULT_STATE.is_file():
        raise WorkflowError("Workflow state is missing")
    state = read_json(DEFAULT_STATE)
    if state.get("status") != "needs_replan" or state.get("current_step") is not None:
        raise WorkflowError("Replan is allowed only from needs_replan with no active step")
    step_ids = [step["id"] for step in contract["steps"]]
    if args.resume_step not in step_ids:
        raise WorkflowError(f"Unknown resume step: {args.resume_step}")
    resume_index = step_ids.index(args.resume_step)
    incomplete = [step_id for step_id in step_ids[:resume_index] if state["steps"][step_id]["status"] != "completed"]
    if incomplete:
        raise WorkflowError(f"Cannot preserve incomplete prerequisite steps: {', '.join(incomplete)}")
    previous_state_sha256 = sha256_file(DEFAULT_STATE)
    old_contract_sha256 = state.get("contract_sha256")
    baseline = worktree_map()
    for step_id in step_ids[resume_index:]:
        previous_cycles = state["steps"][step_id].get("remediation_cycles", 0)
        state["steps"][step_id] = {
            "status": "pending",
            "remediation_cycles": 0,
            "pre_replan_remediation_cycles": previous_cycles,
        }
    state["steps"][args.resume_step]["status"] = "in_progress"
    state["status"] = "active"
    state["current_step"] = args.resume_step
    state["contract_sha256"] = sha256_file(DEFAULT_CONTRACT)
    state["baseline_worktree"] = baseline
    state["baseline_sha256"] = map_hash(baseline)
    state["last_verified_commit"] = git("rev-parse", "HEAD").decode().strip()
    state["freezes"] = {name: value for name, value in state["freezes"].items() if step_ids.index(contract["freeze_points"][name]) < resume_index}
    for name in contract["freeze_points"]:
        state["freezes"].setdefault(name, None)
    record = {
        "schema_version": 1,
        "workflow_id": contract["workflow_id"],
        "replanned_at": now(),
        "reason": args.reason,
        "approval": args.approval,
        "resume_step": args.resume_step,
        "previous_state_sha256": previous_state_sha256,
        "old_contract_sha256": old_contract_sha256,
        "new_contract_sha256": state["contract_sha256"],
        "baseline_sha256": state["baseline_sha256"],
    }
    replan_number = len(state.get("replans", [])) + 1
    receipt_path = RECEIPTS / f"replan-{replan_number:02d}.json"
    atomic_json(receipt_path, record)
    record_ref = {
        "path": receipt_path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(receipt_path),
    }
    state.setdefault("replans", []).append(record_ref)
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "replanned", "resume_step": args.resume_step, "receipt_sha256": record_ref["sha256"]}))


def cmd_freeze(args: argparse.Namespace) -> None:
    contract = load_contract()
    state = load_state(contract)
    expected = contract["freeze_points"][args.name]
    if state["steps"][expected]["status"] != "completed":
        raise WorkflowError(f"Freeze {args.name} requires completed {expected}")
    state["freezes"][args.name] = {
        "step": expected,
        "created_at": now(),
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "worktree_sha256": map_hash(worktree_map()),
        "artifact_sha256": args.artifact_sha256,
    }
    state["updated_at"] = now()
    atomic_json(DEFAULT_STATE, state)
    print(json.dumps({"status": "frozen", "name": args.name, "step": expected}))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init").set_defaults(func=cmd_init)
    commands.add_parser("status").set_defaults(func=cmd_status)
    hook = commands.add_parser("hook")
    hook.add_argument("phase", choices=("pre-edit", "post-edit"))
    hook.add_argument("--path", dest="paths", action="append", default=[])
    hook.set_defaults(func=cmd_hook)
    verify = commands.add_parser("verify")
    verify.add_argument("step")
    verify.add_argument("--evidence", action="append", default=[])
    verify.set_defaults(func=cmd_verify)
    complete = commands.add_parser("complete")
    complete.add_argument("step")
    complete.set_defaults(func=cmd_complete)
    enter = commands.add_parser("enter")
    enter.add_argument("step")
    enter.set_defaults(func=cmd_enter)
    commands.add_parser("next").set_defaults(func=cmd_next)
    remediate = commands.add_parser("remediate")
    remediate.add_argument("--reason", required=True)
    remediate.set_defaults(func=cmd_remediate)
    replan = commands.add_parser("replan")
    replan.add_argument("--resume-step", required=True)
    replan.add_argument("--reason", required=True)
    replan.add_argument("--approval", required=True)
    replan.set_defaults(func=cmd_replan)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("name", choices=("architecture", "candidate", "release"))
    freeze.add_argument("--artifact-sha256")
    freeze.set_defaults(func=cmd_freeze)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        action = "check-protected-edit" if args.command in {"status", "hook"} else "advance-workflow-step"
        check_admission(action)
        args.func(args)
        return 0
    except WorkflowError as exc:
        print(f"workflow-error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
