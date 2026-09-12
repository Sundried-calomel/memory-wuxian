#!/usr/bin/env python3
"""Plan and run bounded historical summary-v2 backfill outside the archive."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

from console_encoding import configure_unicode_stdio
from memory_cli import MemoryStore, file_sha256, load_simple_yaml, parse_summary_markdown
from memory_atoms import _source_sha256
from memory_summary_v2 import (
    FORMAT,
    PARENT_PROJECTOR,
    PROJECTOR,
    SummaryV2Error,
    build_level_1_source,
    build_parent_source,
    build_parent_rescue_reduce_source,
    build_rescue_reduce_source,
    validate_sidecar,
    render_markdown,
)
from platform_transaction import atomic_write_canonical_json
from platform_process import no_window_kwargs
from semantic_plan import plan_level_1_jobs, plan_reduction_frontiers
from summary_v2_worker import build_prompt, codex_command, compile_prompt, load_sidecar, run_source


PLAN_FORMAT = "memory-wuxian-summary-v2-backfill-plan-v1"
MAX_BATCH = 20
MAX_PARALLEL = 3
FAILURE_LIMIT = 1
RUNNER_REVISION = "summary-v2-backfill-normalizer-v10"
MAP_RESCUE_REVISION = "summary-v2-map-reduce-v23"
PARENT_RESCUE_REVISION = "summary-v2-parent-map-reduce-v21"
MAP_PROMPT_TARGET = 160_000
MAP_SOURCE_REF_TARGET = 96
REDUCE_PROMPT_LIMIT = 900_000
L1_RESCUE_MODEL_CALL_LIMIT = 64
PARENT_RESCUE_MODEL_CALL_LIMIT = 10
WINDOWS_SIDECAR_PATH_LIMIT = 32_760
EXECUTION_CONTRACT_FORMAT = "memory-wuxian-summary-v2-execution-contract-v1"
NODE_STATE_FORMAT = "memory-wuxian-summary-v2-node-state-v1"
NODE_STATES = {
    "not-started",
    "in-progress",
    "completed",
    "content-failed-terminal",
    "infra-blocked",
}
INFRA_ERROR_MARKERS = (
    "timed out after",
    "network",
    "permission denied",
    "access is denied",
    "model-process-failure",
    "same-revision model retry is forbidden",
    "rescue model-call budget exhausted before dispatch",
    "l1 rescue full-dag model-call budget is undersized before attempt",
    "parent rescue full-dag model-call budget is undersized before attempt",
    "winerror 3",
    "system cannot find the path specified",
    "the system cannot find the path specified",
    "系统找不到指定的路径",
    "sidecar path budget exceeded",
)


class ContentStageFailure(SummaryV2Error):
    """A dispatched model candidate failed semantic projection or validation."""

    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


class BudgetYield(Exception):
    """A runtime invocation yielded before claiming another model stage."""

    def __init__(self, reason: str, next_stage: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.next_stage = next_stage


class TickBudget:
    """Admit at most one wave; already claimed work always drains to a receipt."""

    def __init__(self, *, seconds: float = 960, call_timeout: int = 900,
                 maximum_dispatches: int = MAX_PARALLEL, clock=time.monotonic, config=None):
        if not 0 < call_timeout <= 900 or not 0 < maximum_dispatches <= MAX_PARALLEL:
            raise SummaryV2Error("invalid runtime invocation budget")
        if not call_timeout + 45 <= seconds <= 1200:
            raise SummaryV2Error("runtime tick must include its call timeout plus 45 seconds, within 1200 seconds")
        self.clock = clock
        self.deadline = clock() + seconds
        self.call_timeout = call_timeout
        self.maximum_dispatches = maximum_dispatches
        self.admitted = 0
        self.wave = None
        self.lock = Lock()
        self.config = config

    def admit(self, state: dict[str, Any], stage_key: str) -> None:
        if stage_key.startswith("map/"):
            stage_wave = "maps"
        elif stage_key.startswith("reduction/"):
            stage_wave = stage_key.rsplit("-map-", 1)[0]
        else:
            stage_wave = stage_key
        wave = (state["revision"], state["summary_id"], stage_wave)
        with self.lock:
            # Preserve the configured model timeout, reserving time for receipt persistence.
            if self.deadline - self.clock() < self.call_timeout + 30:
                raise BudgetYield("deadline-before-dispatch", stage_key)
            if self.wave is not None and self.wave != wave:
                raise BudgetYield("wave-completed", stage_key)
            if self.admitted >= self.maximum_dispatches:
                raise BudgetYield("dispatch-quota", stage_key)
            self.wave = wave
            self.admitted += 1


_runtime_tick: ContextVar[TickBudget | None] = ContextVar("summary_v2_runtime_tick", default=None)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _exclusive_runner_lock(output_root: Path, operation: str):
    lock_root = Path(output_root).expanduser().resolve() / "backfill" / ".runner-lock"
    metadata_path = lock_root / "owner.json"
    owner = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "operation": operation,
    }
    try:
        lock_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        existing: dict[str, Any] = {}
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        same_host = existing.get("hostname") == owner["hostname"]
        if same_host and not _pid_is_alive(int(existing.get("pid", -1))):
            try:
                metadata_path.unlink(missing_ok=True)
                lock_root.rmdir()
                lock_root.mkdir(parents=True, exist_ok=False)
            except OSError as cleanup_error:
                raise SummaryV2Error(
                    f"stale summary-v2 runner lock could not be recovered: {cleanup_error}"
                ) from cleanup_error
        else:
            raise SummaryV2Error(
                "another summary-v2 runner owns the output root: "
                f"pid={existing.get('pid', 'unknown')} "
                f"operation={existing.get('operation', 'unknown')}"
            ) from exc
    atomic_write_canonical_json(metadata_path, owner)
    try:
        yield
    finally:
        try:
            metadata_path.unlink(missing_ok=True)
            lock_root.rmdir()
        except OSError:
            pass


def _single_instance(operation: str):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            output_root = kwargs.get("output_root")
            if output_root is None and len(args) >= 2:
                output_root = args[1]
            if output_root is None:
                raise SummaryV2Error("summary-v2 runner output root is missing")
            with _exclusive_runner_lock(Path(output_root), operation):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def _write_rescue_state(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    if path.exists():
        disk = json.loads(path.read_text(encoding="utf-8"))
        if (
            disk.get("revision") != state.get("revision")
            or disk.get("summary_id") != state.get("summary_id")
        ):
            raise SummaryV2Error("rescue state identity changed before write")
        merged = {**disk, **state}
        merged["maps"] = {**disk.get("maps", {}), **state.get("maps", {})}
        merged["reductions"] = {
            **disk.get("reductions", {}),
            **state.get("reductions", {}),
        }
        merged["dispatches"] = {
            **disk.get("dispatches", {}),
            **state.get("dispatches", {}),
        }
    merged["format"] = NODE_STATE_FORMAT
    merged["attempt_status"] = str(merged.get("attempt_status", "not-started"))
    if merged["attempt_status"] == "failed":
        merged["attempt_status"] = "content-failed-terminal"
    if merged["attempt_status"] not in NODE_STATES:
        raise SummaryV2Error("node state uses an unsupported attempt status")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_canonical_json(path, merged)
    state.clear()
    state.update(merged)
    return state


def _rescue_state_path(
    output_root: Path,
    family: str,
    revision: str,
    summary_id: str,
) -> Path:
    safe_revision = "".join(
        character if character.isalnum() or character in {"-", "."} else "_"
        for character in revision
    )
    del family
    return output_root / "backfill" / "rescue" / "node-state" / safe_revision / f"{summary_id}.json"


def _rescue_artifact_root(
    output_root: Path,
    family: str,
    revision: str,
    summary_id: str,
) -> Path:
    safe_revision = "".join(
        character if character.isalnum() or character in {"-", "."} else "_"
        for character in revision
    )
    return output_root / "backfill" / "rescue" / "artifacts" / family / safe_revision / summary_id


def _reduction_output_root(artifact_root: Path, stage: int) -> Path:
    if stage < 1:
        raise SummaryV2Error("reduction stage must be positive")
    # The receipt preserves the full logical stage identity. Keep only the
    # physical directory compact so temporary sidecars stay below MAX_PATH.
    return artifact_root / "r" / f"{stage:03d}"


def _validate_sidecar_path_budget(output_root: Path, summary_level: int) -> None:
    """Reject paths beyond Windows' extended-length limit before a model call."""
    if os.name != "nt":
        return
    longest_temporary_file = (
        Path(output_root)
        / FORMAT
        / f"level-{summary_level}"
        / (".summary-v2-" + "0" * 32 + "." + "x" * 8)
        / "summary.json"
    )
    length = len(str(longest_temporary_file))
    if length > WINDOWS_SIDECAR_PATH_LIMIT:
        raise OSError(
            f"Windows sidecar path budget exceeded before model call: "
            f"{length}>{WINDOWS_SIDECAR_PATH_LIMIT}: {longest_temporary_file}"
        )


def _classify_failure(exc: Exception) -> str:
    if isinstance(exc, ContentStageFailure):
        return "content-failed-terminal"
    if isinstance(exc, OSError):
        return "infra-blocked"
    diagnostic = getattr(exc, "diagnostic", {})
    classification = str(diagnostic.get("classification", ""))
    text = f"{classification} {exc}".lower()
    if any(marker in text for marker in INFRA_ERROR_MARKERS):
        return "infra-blocked"
    # Untyped failures occur outside the proven model-candidate boundary and
    # therefore cannot consume the one content attempt.
    return "infra-blocked"


def _decide_route(
    task: dict[str, Any],
    source: dict[str, Any],
    *,
    failure_reason: str | None = None,
    has_conflict: bool = False,
) -> dict[str, Any]:
    """Return the deterministic route for a node, independent of CLI spelling."""
    level = int(task["level"])
    status = str(task.get("status", "ready")).replace("_", "-")
    dependencies_ready = bool(task.get("dependency_ready", status != "waiting-for-children"))
    prompt_bytes = int(compile_prompt(source)["prompt_utf8_bytes"])
    if status == "existing":
        route = "reuse-existing"
    elif has_conflict:
        route = "source-conflict"
    elif not dependencies_ready or status == "waiting-for-children":
        route = "waiting"
    elif status == "quarantined" and not _rescue_quarantine_is_eligible(failure_reason):
        route = "terminal-unplannable"
    elif status == "quarantined" or prompt_bytes > REDUCE_PROMPT_LIMIT:
        route = "l1-map-reduce-dag" if level == 1 else "parent-map-reduce-dag"
    else:
        route = "direct-dag"
    return {
        "summary_id": str(task["summary_id"]),
        "level": level,
        "status": status,
        "failure_reason": failure_reason,
        "prompt_utf8_bytes": prompt_bytes,
        "route": route,
    }


def _dispatch_route(
    route_plan: dict[str, Any],
    handlers: dict[str, Any],
) -> Any:
    """Dispatch a frozen route; missing handlers fail before a model call."""
    route = str(route_plan["route"])
    handler = handlers.get(route)
    if handler is None:
        raise SummaryV2Error(f"route has no admitted executor: {route}")
    return handler()


def _execute_model_node(
    source: dict[str, Any],
    output_root: Path,
    archive_root: Path,
    *,
    config_path: Path,
    rejected_candidate_path: Path,
    diagnostic_path: Path,
    invocation_context: dict[str, Any],
) -> dict[str, Any]:
    """Execute one planned model node through the sole persistence boundary."""
    summary_level = int(source.get("summary_level", 1))
    output_root = Path(output_root)
    rejected_candidate_path = Path(rejected_candidate_path)
    diagnostic_path = Path(diagnostic_path)
    _validate_sidecar_path_budget(output_root, summary_level)
    output_root.mkdir(parents=True, exist_ok=True)
    rejected_candidate_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    return run_source(
        source,
        output_root,
        archive_root,
        config_path=config_path,
        rejected_candidate_path=rejected_candidate_path,
        diagnostic_path=diagnostic_path,
        invocation_context=invocation_context,
        **({"config_override": _runtime_tick.get().config} if _runtime_tick.get() is not None else {}),
    )


def _reserve_model_calls(
    budget: dict[str, int],
    count: int,
    *,
    reserve_after: int = 0,
) -> None:
    """Reserve calls before dispatch so concurrent work cannot overspend."""
    required = budget["used"] + count + reserve_after
    if required > budget["maximum"]:
        raise SummaryV2Error(
            "rescue model-call budget exhausted before dispatch: "
            f"used={budget['used']} requested={count} "
            f"reserved={reserve_after} maximum={budget['maximum']}"
        )
    budget["used"] += count


def _rescue_call_upper_bound(map_count: int) -> int:
    """Cover maps, binary compaction, and the final reduce."""
    if isinstance(map_count, bool) or not isinstance(map_count, int) or map_count < 1:
        raise SummaryV2Error("rescue map_count must be a positive integer")
    return 2 * map_count


_l1_rescue_call_upper_bound = _rescue_call_upper_bound
_parent_rescue_call_upper_bound = _rescue_call_upper_bound


def _plan_parent_rescue_groups(
    children: list[dict[str, Any]], summary_id: str
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    for child in children:
        candidate = [*active, child]
        if len(candidate) >= 2:
            probe = build_parent_source(
                candidate, parallel_summary_id=summary_id + "-probe"
            )
            probe["compact_parent_prompt"] = True
            if active and len(build_prompt(probe).encode("utf-8")) > MAP_PROMPT_TARGET:
                groups.append(active)
                active = [child]
                continue
        active = candidate
    if active:
        if len(active) == 1 and groups:
            groups[-1].extend(active)
        else:
            groups.append(active)
    if len(groups) < 2 or any(len(group) < 2 for group in groups):
        groups = [children[index : index + 2] for index in range(0, len(children), 2)]
        if len(groups) > 1 and len(groups[-1]) == 1:
            groups[-2].extend(groups.pop())
    return groups


def _bounded_parallel_results(
    items: list[Any],
    worker,
    *,
    maximum_parallel: int = MAX_PARALLEL,
    failure_handler=None,
):
    """Yield in-flight successes and stop submitting after the first failure."""
    if not items:
        return
    worker_count = min(maximum_parallel, len(items))
    pending = iter(items)
    active: dict[Any, Any] = {}
    first_error: Exception | None = None
    budget_yield: BudgetYield | None = None
    tick = _runtime_tick.get()
    submitted = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        def submit_next() -> bool:
            nonlocal submitted
            if tick is not None and submitted >= min(worker_count, tick.maximum_dispatches):
                return False
            try:
                item = next(pending)
            except StopIteration:
                return False
            active[executor.submit(copy_context().run, worker, item)] = item
            submitted += 1
            return True

        while len(active) < worker_count and submit_next():
            pass
        while active:
            done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
            succeeded: list[tuple[Any, Any]] = []
            for future in done:
                item = active.pop(future)
                try:
                    succeeded.append((item, future.result()))
                except BudgetYield as exc:
                    budget_yield = budget_yield or exc
                except Exception as exc:
                    if failure_handler is not None:
                        failure_handler(item, exc)
                    if first_error is None:
                        first_error = exc
            for item, result in succeeded:
                yield item, result
            if first_error is None and budget_yield is None:
                while len(active) < worker_count and submit_next():
                    pass

    if first_error is not None:
        raise first_error
    if budget_yield is not None:
        raise budget_yield
    if tick is not None and submitted < len(items):
        raise BudgetYield("wave-completed")


def _stage_receipt(
    source: dict[str, Any],
    sidecar: dict[str, Any],
    ordered_input_projections: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        **_stage_input_binding(source, ordered_input_projections),
        "output_projection_sha256": sidecar["projection_sha256"],
    }


def _stage_input_binding(
    source: dict[str, Any],
    ordered_input_projections: Iterable[str] = (),
) -> dict[str, Any]:
    compiled = compile_prompt(source)
    parent = source["source_kind"] in {
        "summary-v2-children",
        "summary-v2-parent-rescue-maps",
    }
    schema = Path(__file__).resolve().parent.parent / "schemas" / (
        "summary-v2-parent-result.schema.json" if parent else "summary-v2-result.schema.json"
    )
    return {
        "format": "memory-wuxian-summary-v2-stage-receipt-v1",
        "source_sha256": source["source_sha256"],
        "prompt_sha256": compiled["prompt_sha256"],
        "prompt_utf8_bytes": compiled["prompt_utf8_bytes"],
        "canonical_ledger_sha256": compiled.get("canonical_ledger_sha256"),
        "prompt_projection_sha256": compiled.get("prompt_projection_sha256"),
        "ordered_input_projection_sha256s": list(ordered_input_projections),
        "schema_sha256": file_sha256(schema),
        "projector_sha256": file_sha256(Path(__file__).with_name("memory_summary_v2.py")),
        "runner_sha256": file_sha256(Path(__file__)),
        "worker_sha256": file_sha256(Path(__file__).with_name("summary_v2_worker.py")),
        "planner_sha256": file_sha256(Path(__file__).with_name("semantic_plan.py")),
    }


def _model_stage_claims(
    stages: Iterable[tuple[str, dict[str, Any], Iterable[str]]],
) -> dict[str, dict[str, Any]]:
    return {
        key: _stage_input_binding(source, ordered_input_projections)
        for key, source, ordered_input_projections in stages
    }


def _require_model_stages_unclaimed(
    state: dict[str, Any], claims: dict[str, dict[str, Any]]
) -> None:
    dispatches = state.get("dispatches", {})
    for key, binding in claims.items():
        previous = dispatches.get(key)
        if previous is None:
            continue
        if previous != binding:
            raise SummaryV2Error(
                "model stage dispatch binding changed within one revision"
            )
        raise SummaryV2Error(
            "infra-blocked: model stage was already dispatched without a "
            "persisted success; same-revision retry is forbidden"
        )


def _claim_model_stages(
    state_path: Path,
    state: dict[str, Any],
    stages: Iterable[tuple[str, dict[str, Any], Iterable[str]]],
) -> None:
    claims = _model_stage_claims(stages)
    _require_model_stages_unclaimed(state, claims)
    dispatches = state.setdefault("dispatches", {})
    dispatches.update(claims)
    _write_rescue_state(state_path, state)


def _stage_invocation_context(
    family: str,
    revision: str,
    summary_id: str,
    stage: str,
    source: dict[str, Any],
    ordered_input_projections: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "family": family,
        "revision": revision,
        "summary_id": summary_id,
        "stage": stage,
        "stage_binding": _stage_input_binding(source, ordered_input_projections),
    }


def _require_stage_receipt(
    saved: dict[str, Any],
    source: dict[str, Any],
    sidecar: dict[str, Any],
    ordered_input_projections: Iterable[str] = (),
    *, state: dict[str, Any] | None = None, stage_key: str | None = None,
) -> None:
    expected = _stage_receipt(source, sidecar, ordered_input_projections)
    actual = saved.get("receipt")
    if actual != expected:
        raise SummaryV2Error("saved Summary V2 stage receipt drifted")
    if _runtime_tick.get() is not None:
        claim = _stage_input_binding(source, ordered_input_projections)
        if state is None or state.get('dispatches', {}).get(stage_key) != claim:
            raise SummaryV2Error('saved runtime stage dispatch claim changed')


def _result_stage_receipt(
    result: dict[str, Any],
    source: dict[str, Any],
    sidecar: dict[str, Any],
    ordered_input_projections: Iterable[str] = (),
) -> dict[str, Any]:
    expected = _stage_receipt(source, sidecar, ordered_input_projections)
    actual = result.get("stage_receipt")
    if actual != expected:
        raise SummaryV2Error("worker did not return the exact generation-time stage receipt")
    return dict(actual)


def _execute_model_stage(
    *,
    state: dict[str, Any],
    state_path: Path,
    state_lock: Lock,
    model_call_budget: dict[str, int],
    stage_key: str,
    source: dict[str, Any],
    ordered_input_projections: Iterable[str],
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    rejected_candidate_path: Path,
    diagnostic_path: Path,
    invocation_context: dict[str, Any],
    success_collection: str | None,
    success_key: str | None = None,
    reserve_after: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Claim, call, verify, and persist one model stage as one lifecycle."""
    input_projections = list(ordered_input_projections)
    with state_lock:
        tick = _runtime_tick.get()
        if tick is not None and success_collection is None and state.get("completed"):
            result = state["completed"]
            sidecar = load_sidecar(Path(result["bundle"]))
            receipt = _result_stage_receipt(result, source, sidecar, input_projections)
            if state.get("completed_receipt") != receipt:
                raise SummaryV2Error("completed runtime stage receipt changed")
            expected = _model_stage_claims([(stage_key, source, input_projections)])
            if any(state.get("dispatches", {}).get(key) != value for key, value in expected.items()):
                raise SummaryV2Error("completed runtime stage claim changed")
            return result, sidecar
        if tick is not None:
            if state.get("completed") or state.get("attempt_status") == "completed":
                raise SummaryV2Error("completed runtime node has a missing intermediate stage receipt")
            _require_model_stages_unclaimed(
                state, _model_stage_claims([(stage_key, source, input_projections)])
            )
            tick.admit(state, stage_key)
        _reserve_model_calls(model_call_budget, 1, reserve_after=reserve_after)
        _claim_model_stages(
            state_path,
            state,
            [(stage_key, source, input_projections)],
        )
    try:
        result = _execute_model_node(
            source,
            output_root,
            archive_root,
            config_path=config_path,
            rejected_candidate_path=rejected_candidate_path,
            diagnostic_path=diagnostic_path,
            invocation_context=invocation_context,
        )
    except Exception as exc:
        try:
            diagnostic = json.loads(Path(diagnostic_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            diagnostic = {}
        if (
            diagnostic.get("classification") == "candidate-validation-failure"
            and diagnostic.get("model_called") is True
        ):
            raise ContentStageFailure(str(exc), diagnostic) from exc
        raise
    sidecar = load_sidecar(Path(result["bundle"]))
    receipt = _result_stage_receipt(result, source, sidecar, input_projections)
    with state_lock:
        if success_collection is None:
            state["completed"] = result
            if _runtime_tick.get() is not None:
                state["completed_receipt"] = receipt
            state["attempt_status"] = "completed"
            state["content_attempts"] = 1
        else:
            if success_key is None:
                raise SummaryV2Error("model stage success key is missing")
            state.setdefault(success_collection, {})[success_key] = {
                "bundle": result["bundle"],
                "receipt": receipt,
            }
        _write_rescue_state(state_path, state)
    return result, sidecar


def _bind_rescue_attempt(
    path: Path,
    state: dict[str, Any],
    source: dict[str, Any],
    *,
    parent: bool,
    config_path: Path,
) -> None:
    compiled = compile_prompt(source)
    schema = Path(__file__).resolve().parent.parent / "schemas" / (
        "summary-v2-parent-result.schema.json" if parent else "summary-v2-result.schema.json"
    )
    tick = _runtime_tick.get()
    config = tick.config if tick is not None and tick.config is not None else load_simple_yaml(Path(config_path))
    command, _, _ = codex_command(config, source)
    codex_path = Path(command[0]).expanduser().resolve()
    try:
        version = subprocess.run(
            [str(codex_path), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SummaryV2Error(f"cannot identify configured Codex CLI: {exc}") from exc
    if version.returncode != 0 or not version.stdout.strip():
        raise SummaryV2Error(
            f"configured Codex CLI version check failed ({version.returncode}): {version.stderr}"
        )
    binding = {
        "source_sha256": source["source_sha256"],
        "prompt_sha256": compiled["prompt_sha256"],
        "prompt_utf8_bytes": compiled["prompt_utf8_bytes"],
        "canonical_ledger_sha256": compiled.get("canonical_ledger_sha256"),
        "prompt_projection_sha256": compiled.get("prompt_projection_sha256"),
        "schema_sha256": file_sha256(schema),
        "projector": PARENT_PROJECTOR if parent else PROJECTOR,
        "runner_sha256": file_sha256(Path(__file__)),
        "worker_sha256": file_sha256(Path(__file__).with_name("summary_v2_worker.py")),
        "planner_sha256": file_sha256(Path(__file__).with_name("semantic_plan.py")),
        "codex_executable": str(codex_path),
        "codex_sha256": file_sha256(codex_path),
        "codex_version": version.stdout.strip(),
    }
    previous = state.get("binding")
    if previous is not None and previous != binding:
        raise SummaryV2Error("rescue attempt binding changed within one revision")
    state["binding"] = binding
    _write_rescue_state(path, state)


def _load_rescue_attempt_state(
    path: Path,
    revision: str,
    summary_id: str,
) -> dict[str, Any]:
    source_path = path
    if not source_path.exists():
        rescue_root = path.parents[2]
        legacy_matches = sorted(
            candidate
            for candidate in rescue_root.glob(f"*-state/{path.parent.name}/{path.name}")
            if candidate.parent.parent.name != "node-state"
        )
        if len(legacy_matches) > 1:
            raise SummaryV2Error("multiple legacy rescue states claim one node revision")
        if legacy_matches:
            source_path = legacy_matches[0]
    if not source_path.exists():
        return {
            "format": NODE_STATE_FORMAT,
            "revision": revision,
            "summary_id": summary_id,
            "maps": {},
            "reductions": {},
            "dispatches": {},
            "attempt_status": "not-started",
        }
    state = json.loads(source_path.read_text(encoding="utf-8"))
    if state.get("revision") != revision or state.get("summary_id") != summary_id:
        raise SummaryV2Error("rescue attempt state identity changed")
    state = {
        **state,
        "format": NODE_STATE_FORMAT,
        "maps": dict(state.get("maps", {})),
        "reductions": dict(state.get("reductions", {})),
        "dispatches": dict(state.get("dispatches", {})),
    }
    if state.get("attempt_status") == "failed":
        state["attempt_status"] = "content-failed-terminal"
    state.setdefault("attempt_status", "not-started")
    if state["attempt_status"] not in NODE_STATES:
        raise SummaryV2Error("legacy rescue state has an unsupported attempt status")
    return state


def _rescue_attempt_is_terminal(state: dict[str, Any]) -> bool:
    if state.get("completed"):
        return True
    return state.get("attempt_status") in {
        "completed",
        "content-failed-terminal",
        "infra-blocked",
    }


def _begin_rescue_attempt(path: Path, state: dict[str, Any]) -> None:
    status = state.get("attempt_status")
    if _runtime_tick.get() is not None and status == "completed" and state.get("completed_receipt"):
        return  # Replay rebuilds every source and verifies the final receipt, without dispatch.
    if _rescue_attempt_is_terminal(state):
        raise SummaryV2Error("rescue attempt is already terminal for this revision")
    if status == "in-progress":
        return
    state["attempt_status"] = "in-progress"
    state["orchestration_attempts"] = 1
    state["content_attempts"] = 0
    _write_rescue_state(path, state)


def _select_single_attempt_candidates(
    candidate_pool: Iterable[dict[str, Any]],
    output_root: Path,
    family: str,
    revision: str,
    maximum_jobs: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected: list[dict[str, Any]] = []
    deferred: list[str] = []
    for task in candidate_pool:
        summary_id = str(task["summary_id"])
        state = _load_rescue_attempt_state(
            _rescue_state_path(output_root, family, revision, summary_id),
            revision,
            summary_id,
        )
        if _rescue_attempt_is_terminal(state):
            deferred.append(summary_id)
            continue
        if len(selected) < maximum_jobs:
            selected.append(task)
    return selected, deferred


def _rescue_subjob(
    job: dict[str, Any],
    source_refs: list[str],
    *,
    stage: int,
    index: int,
) -> dict[str, Any]:
    wanted = set(source_refs)
    records = [
        record for record in job["source_records"] if record["message_id"] in wanted
    ]
    recovered = [record["message_id"] for record in records]
    if recovered != source_refs:
        raise SummaryV2Error("rescue reduction source refs drifted from the raw job")
    return {
        "format_version": 1,
        "job_id": f"{job['job_id']}-rescue-reduce-{stage:03d}-{index:03d}",
        "target_summary_id": (
            f"{job['target_summary_id']}-reduce-{stage:03d}-{index:03d}"
        ),
        "summary_level": 1,
        "conversation_id": job["conversation_id"],
        "source_sha256": _source_sha256(records),
        "source_message_ids": source_refs,
        "source_records": records,
    }


def _compact_rescue_maps(
    map_sidecars: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    model_call_budget: dict[str, int],
    *,
    family: str,
    revision: str,
    summary_id: str,
    build_final_source,
    build_group_source,
    label: str,
) -> list[dict[str, Any]]:
    current = list(map_sidecars)
    stage = 1
    artifact_root = _rescue_artifact_root(
        output_root, family, revision, summary_id
    )
    while len(build_prompt(build_final_source(current)).encode("utf-8")) > REDUCE_PROMPT_LIMIT:
        if len(current) < 3:
            raise SummaryV2Error(
                f"two {label} maps still exceed the staged reduce prompt limit"
            )
        reduced_by_index: dict[int, dict[str, Any]] = {}
        ordered_ids = [
            f"input-{index:03d}-{sidecar['projection_sha256']}"
            for index, sidecar in enumerate(current, 1)
        ]
        by_id = dict(zip(ordered_ids, current, strict=True))
        layer = plan_reduction_frontiers(ordered_ids)["layers"][0]
        groups = [
            [by_id[input_id] for input_id in node["input_ids"]]
            for node in layer["nodes"]
        ]
        reduction_root = _reduction_output_root(artifact_root, stage)
        reductions = state.setdefault("reductions", {})
        pending_reductions = []
        for index, group in enumerate(groups, 1):
            if len(group) == 1:
                reduced_by_index[index] = group[0]
                continue
            reduce_source = build_group_source(group, stage, index)
            prompt_size = len(build_prompt(reduce_source).encode("utf-8"))
            if prompt_size > REDUCE_PROMPT_LIMIT:
                raise SummaryV2Error(
                    f"intermediate {label} reduce prompt exceeds staged limit: "
                    f"{prompt_size} bytes"
                )
            key = f"stage-{stage:03d}-map-{index:03d}"
            input_projections = [sidecar["projection_sha256"] for sidecar in group]
            saved = reductions.get(key)
            if saved:
                sidecar = load_sidecar(Path(saved["bundle"]))
                _require_stage_receipt(saved, reduce_source, sidecar, input_projections,
                                       state=state, stage_key=f'reduction/{key}')
                reduced_by_index[index] = sidecar
            else:
                pending_reductions.append(
                    (index, key, reduce_source, input_projections)
                )

        _require_model_stages_unclaimed(
            state,
            _model_stage_claims(
                (
                    f"reduction/{key}",
                    reduce_source,
                    input_projections,
                )
                for _, key, reduce_source, input_projections in pending_reductions
            ),
        )
        state_lock = Lock()

        def run_reduction(item):
            index, key, reduce_source, input_projections = item
            _, sidecar = _execute_model_stage(
                state=state,
                state_path=state_path,
                state_lock=state_lock,
                model_call_budget=model_call_budget,
                stage_key=f"reduction/{key}",
                source=reduce_source,
                ordered_input_projections=input_projections,
                output_root=reduction_root,
                archive_root=archive_root,
                config_path=config_path,
                rejected_candidate_path=(
                    artifact_root
                    / "rejected-reduction-candidates"
                    / f"{key}.json"
                ),
                diagnostic_path=artifact_root / "diagnostics" / f"{key}.json",
                invocation_context=_stage_invocation_context(
                    family,
                    revision,
                    summary_id,
                    key,
                    reduce_source,
                    input_projections,
                ),
                success_collection="reductions",
                success_key=key,
                reserve_after=1,
            )
            return index, sidecar

        for _, (index, sidecar) in _bounded_parallel_results(
            pending_reductions, run_reduction
        ):
            reduced_by_index[index] = sidecar
        current = [reduced_by_index[index] for index in range(1, len(groups) + 1)]
        stage += 1
    return current


def _compact_l1_rescue_maps(
    job: dict[str, Any],
    formal_source: dict[str, Any],
    map_sidecars: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    model_call_budget: dict[str, int],
) -> list[dict[str, Any]]:
    def build_group_source(group, stage, index):
        source_refs = [
            message_id
            for sidecar in group
            for message_id in sidecar["source"]["raw_message_ids"]
        ]
        subjob = _rescue_subjob(job, source_refs, stage=stage, index=index)
        return build_rescue_reduce_source(build_level_1_source(subjob), group)

    return _compact_rescue_maps(
        map_sidecars,
        state,
        state_path,
        output_root,
        archive_root,
        config_path,
        model_call_budget,
        family="map",
        revision=MAP_RESCUE_REVISION,
        summary_id=str(job["target_summary_id"]),
        build_final_source=lambda current: build_rescue_reduce_source(
            formal_source, current
        ),
        build_group_source=build_group_source,
        label="rescue",
    )


def _compact_parent_rescue_maps(
    formal_source: dict[str, Any],
    children: list[dict[str, Any]],
    map_sidecars: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    model_call_budget: dict[str, int],
) -> list[dict[str, Any]]:
    child_by_id = {child["summary_v2_id"]: child for child in children}

    def build_group_source(group, stage, index):
        source_refs = [
            source_ref
            for sidecar in group
            for source_ref in sidecar["source"]["source_refs"]
        ]
        if len(source_refs) != len(set(source_refs)):
            raise SummaryV2Error("parent reduction maps overlap direct child summaries")
        try:
            source_children = [child_by_id[source_ref] for source_ref in source_refs]
        except KeyError as exc:
            raise SummaryV2Error(
                f"parent reduction lost direct child source: {exc.args[0]}"
            ) from exc
        subformal = build_parent_source(
            source_children,
            parallel_summary_id=(
                f"{formal_source['parallel_summary_id']}-reduce-"
                f"{stage:03d}-{index:03d}"
            ),
        )
        subformal["compact_parent_prompt"] = True
        if subformal["source_refs"] != source_refs:
            raise SummaryV2Error("parent reduction direct child order drifted")
        return build_parent_rescue_reduce_source(subformal, group)

    return _compact_rescue_maps(
        map_sidecars,
        state,
        state_path,
        output_root,
        archive_root,
        config_path,
        model_call_budget,
        family="parent",
        revision=PARENT_RESCUE_REVISION,
        summary_id=str(formal_source["parallel_summary_id"]),
        build_final_source=lambda current: build_parent_rescue_reduce_source(
            formal_source, current
        ),
        build_group_source=build_group_source,
        label="parent rescue",
    )


def _relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_roots(archive_root: Path, output_root: Path) -> tuple[Path, Path]:
    archive_root = archive_root.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if output_root == archive_root or _relative_to(output_root, archive_root):
        raise SummaryV2Error("backfill output must be outside the archive root")
    return archive_root, output_root


def _load_sidecars(
    roots: Iterable[Path],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    by_parallel_id: dict[str, dict[str, Any]] = {}
    origins: dict[str, dict[str, str]] = {}
    conflicts: dict[str, list[dict[str, str]]] = {}
    seen_summary_ids: set[str] = set()
    for root in roots:
        root = Path(root).expanduser().resolve()
        if not root.exists():
            continue
        paths: list[Path] = []

        def traversal_error(error: OSError) -> None:
            raise SummaryV2Error(
                f"cannot read summary-v2 sidecar tree {root}: {error}"
            ) from error

        for directory, directory_names, filenames in os.walk(root, onerror=traversal_error):
            if Path(directory) == root:
                directory_names[:] = [name for name in directory_names if name != "backfill"]
            directory_names.sort()
            if "summary.json" in filenames:
                paths.append(Path(directory) / "summary.json")
        for path in sorted(paths):
            try:
                sidecar = load_sidecar(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise SummaryV2Error(
                    f"cannot read or validate existing summary-v2 sidecar {path}: {exc}"
                ) from exc
            if sidecar["summary_v2_id"] in seen_summary_ids:
                continue
            seen_summary_ids.add(sidecar["summary_v2_id"])
            parallel_id = sidecar["parallel_summary_id"]
            previous = by_parallel_id.get(parallel_id)
            if previous is not None and previous["projection_sha256"] != sidecar["projection_sha256"]:
                conflicts.setdefault(parallel_id, [origins[parallel_id]]).append(
                    {
                        "path": str(path),
                        "summary_v2_id": sidecar["summary_v2_id"],
                        "projection_sha256": sidecar["projection_sha256"],
                    }
                )
                by_parallel_id.pop(parallel_id, None)
                continue
            if parallel_id in conflicts:
                conflicts[parallel_id].append(
                    {
                        "path": str(path),
                        "summary_v2_id": sidecar["summary_v2_id"],
                        "projection_sha256": sidecar["projection_sha256"],
                    }
                )
                continue
            by_parallel_id[parallel_id] = sidecar
            origins[parallel_id] = {
                "path": str(path),
                "summary_v2_id": sidecar["summary_v2_id"],
                "projection_sha256": sidecar["projection_sha256"],
            }
    return by_parallel_id, conflicts


def _windows_user() -> str:
    if os.name != "nt":
        return ""
    size = ctypes.c_ulong(0)
    ctypes.windll.advapi32.GetUserNameW(None, ctypes.byref(size))
    buffer = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
        raise OSError("GetUserNameW failed")
    return buffer.value


def _execution_fingerprint() -> dict[str, Any]:
    return {
        "format": EXECUTION_CONTRACT_FORMAT,
        "python_executable": str(Path(sys.executable).resolve()),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "windows_user": _windows_user(),
    }


def _validate_execution_contract(output_root: Path) -> dict[str, Any]:
    contract_path = output_root / "backfill" / "execution-contract.json"
    current = _execution_fingerprint()
    if contract_path.exists():
        expected = json.loads(contract_path.read_text(encoding="utf-8"))
        if expected != current:
            raise SummaryV2Error(
                "summary-v2 execution identity or Python changed; refusing runner fallback"
            )
        return expected
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_canonical_json(contract_path, current)
    return current


def _summary_records_from_files(
    store: MemoryStore,
    raw_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(store.summaries_dir.glob("level-*/*.md")):
        parsed = parse_summary_markdown(path)
        start_record = raw_by_id.get(parsed.get("source_start"))
        end_record = raw_by_id.get(parsed.get("source_end"))
        conversation_id = parsed.get("conversation_id")
        if not conversation_id and start_record and end_record:
            if start_record.get("conversation_id") == end_record.get("conversation_id"):
                conversation_id = start_record.get("conversation_id")
        records.append(
            {
                "summary_id": parsed["summary_id"],
                "level": int(parsed["summary_level"]),
                "conversation_id": conversation_id,
                "source_summaries": parsed.get("source_summaries") or [],
                "source_message_ids": parsed.get("source_message_ids") or [],
                "source_sha256": parsed.get("source_sha256"),
                "summary_sha256": file_sha256(path),
            }
        )
    return sorted(records, key=lambda item: (item["level"], item["summary_id"]))


def _clean_raw(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _write_l1_job(
    output_root: Path,
    summary: dict[str, Any],
    raw_by_id: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    source_ids = list(summary.get("source_message_ids") or [])
    if not source_ids:
        return None, "missing-source-message-ids"
    missing = [message_id for message_id in source_ids if message_id not in raw_by_id]
    if missing:
        return None, "missing-raw-message:" + missing[0]
    job = {
        "format_version": 1,
        "job_id": "summary-v2-backfill-" + summary["summary_id"],
        "target_summary_id": summary["summary_id"],
        "summary_level": 1,
        "conversation_id": summary["conversation_id"],
        "source_sha256": summary["source_sha256"],
        "source_message_ids": source_ids,
        "source_records": [_clean_raw(raw_by_id[message_id]) for message_id in source_ids],
    }
    try:
        build_level_1_source(job)
    except (ValueError, KeyError) as exc:
        return None, "source-validation:" + str(exc)[:300]
    path = output_root / "backfill" / "jobs" / "level-1" / f"{summary['summary_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != job:
            raise SummaryV2Error(f"backfill job drifted: {path}")
    else:
        atomic_write_canonical_json(path, job)
    return str(path), None


def build_plan(
    archive_root: Path,
    output_root: Path,
    existing_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    archive_root, output_root = _validate_roots(archive_root, output_root)
    store = MemoryStore(archive_root, {})
    raw_records = store.read_all_raw()
    raw_by_id = {record["message_id"]: record for record in raw_records}
    summaries = _summary_records_from_files(store, raw_by_id)
    summaries_by_id = {item["summary_id"]: item for item in summaries}
    sidecars, conflicts = _load_sidecars([output_root, *existing_roots])
    tasks: list[dict[str, Any]] = []
    quarantine: list[dict[str, str]] = []

    for summary in summaries:
        summary_id = summary["summary_id"]
        level = int(summary["level"])
        if summary_id in conflicts:
            tasks.append(
                {
                    "summary_id": summary_id,
                    "level": level,
                    "status": "quarantined",
                    "conversation_id": summary["conversation_id"],
                    "job": None,
                    "children": list(summary.get("source_summaries") or []),
                }
            )
            quarantine.append(
                {
                    "summary_id": summary_id,
                    "reason": "conflicting-existing-sidecars",
                    "candidates": conflicts[summary_id],
                }
            )
            continue
        existing = sidecars.get(summary_id)
        if existing is not None:
            tasks.append(
                {
                    "summary_id": summary_id,
                    "level": level,
                    "status": "existing",
                    "conversation_id": summary["conversation_id"],
                    "job": None,
                    "children": list(summary.get("source_summaries") or []),
                }
            )
            continue
        if level == 1:
            job_path, error = _write_l1_job(output_root, summary, raw_by_id)
            status = "ready" if job_path else "quarantined"
            tasks.append(
                {
                    "summary_id": summary_id,
                    "level": level,
                    "status": status,
                    "conversation_id": summary["conversation_id"],
                    "job": job_path,
                    "children": [],
                }
            )
            if error:
                quarantine.append({"summary_id": summary_id, "reason": error})
            continue
        children = list(summary.get("source_summaries") or [])
        invalid_children = [child for child in children if child not in summaries_by_id]
        if invalid_children:
            status = "quarantined"
            quarantine.append(
                {
                    "summary_id": summary_id,
                    "reason": "unknown-child-summary:" + invalid_children[0],
                }
            )
        elif all(child in sidecars for child in children):
            status = "ready"
        else:
            status = "waiting-for-children"
        tasks.append(
            {
                "summary_id": summary_id,
                "level": level,
                "status": status,
                "conversation_id": summary["conversation_id"],
                "job": None,
                "children": children,
            }
        )

    counts: dict[str, int] = {}
    for task in tasks:
        key = f"level_{task['level']}_{task['status'].replace('-', '_')}"
        counts[key] = counts.get(key, 0) + 1
    plan = {
        "format": PLAN_FORMAT,
        "archive_root": str(archive_root),
        "output_root": str(output_root),
        "raw_message_count": len(raw_records),
        "summary_v1_count": len(summaries),
        "tasks": tasks,
        "quarantine": quarantine,
        "counts": counts,
    }
    plan_path = output_root / "backfill" / "plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_canonical_json(plan_path, plan)
    return plan


def _legacy_failure_path(output_root: Path, summary_id: str) -> Path:
    return output_root / "backfill" / "failures" / f"{summary_id}.json"


def _refresh_plan(
    plan: dict[str, Any], existing_roots: Iterable[Path]
) -> dict[str, Any]:
    output_root = Path(plan["output_root"])
    sidecars, conflicts = _load_sidecars([output_root, *existing_roots])
    quarantine = [
        item
        for item in plan.get("quarantine", [])
        if item.get("reason") not in {"model-failure-limit", "infra-blocked"}
    ]
    for task in plan["tasks"]:
        summary_id = task["summary_id"]
        if summary_id in conflicts:
            task["status"] = "quarantined"
            quarantine = [
                item
                for item in quarantine
                if not (
                    item.get("summary_id") == summary_id
                    and item.get("reason") == "conflicting-existing-sidecars"
                )
            ]
            quarantine.append(
                {
                    "summary_id": summary_id,
                    "reason": "conflicting-existing-sidecars",
                    "candidates": conflicts[summary_id],
                }
            )
            continue
        if summary_id in sidecars:
            task["status"] = "existing"
            continue
        if task["status"] == "quarantined" and task["level"] == 1 and not task.get("job"):
            continue
        normal_state = _load_rescue_attempt_state(
            _rescue_state_path(output_root, "normal", RUNNER_REVISION, summary_id),
            RUNNER_REVISION,
            summary_id,
        )
        failure = normal_state if _rescue_attempt_is_terminal(normal_state) else None
        legacy_failure_path = _legacy_failure_path(output_root, summary_id)
        if failure is None and legacy_failure_path.exists():
            failure = json.loads(legacy_failure_path.read_text(encoding="utf-8"))
        if failure is not None:
            if (
                int(failure.get("content_attempts", failure.get("attempts", 0)))
                >= FAILURE_LIMIT
                or failure.get("attempt_status") == "infra-blocked"
            ):
                task["status"] = "quarantined"
                reason = (
                    "infra-blocked"
                    if failure.get("attempt_status") == "infra-blocked"
                    else "model-failure-limit"
                )
                quarantine.append(
                    {
                        "summary_id": summary_id,
                        "reason": reason,
                        "attempts": int(
                            failure.get("content_attempts", failure.get("attempts", 0))
                        ),
                    }
                )
                continue
        if int(task["level"]) == 1:
            task["status"] = "ready"
        elif all(child in sidecars for child in task["children"]):
            task["status"] = "ready"
        else:
            task["status"] = "waiting-for-children"
    quarantine_reason = {
        str(item.get("summary_id")): str(item.get("reason"))
        for item in quarantine
        if item.get("summary_id")
    }
    for task in plan["tasks"]:
        summary_id = str(task["summary_id"])
        dependency_ready = int(task["level"]) == 1 or all(
            child in sidecars for child in task.get("children", [])
        )
        task["dependency_status"] = "ready" if dependency_ready else "waiting-for-children"
        reason = quarantine_reason.get(summary_id)
        if task["status"] == "existing":
            campaign_status = "completed"
        elif reason == "conflicting-existing-sidecars":
            campaign_status = "conflict-quarantined"
        elif reason == "infra-blocked":
            campaign_status = "infra-blocked"
        elif reason == "model-failure-limit":
            family = "map" if int(task["level"]) == 1 else "parent"
            revision = MAP_RESCUE_REVISION if family == "map" else PARENT_RESCUE_REVISION
            rescue_state = _load_rescue_attempt_state(
                _rescue_state_path(output_root, family, revision, summary_id),
                revision,
                summary_id,
            )
            campaign_status = str(rescue_state.get("attempt_status", "pending"))
        else:
            campaign_status = "pending"
        eligible = dependency_ready and campaign_status == "pending" and task["status"] == "ready"
        task["campaign_status"] = campaign_status
        task["eligible"] = eligible
        task["blocking_reason"] = None if eligible else (
            reason or ("waiting-for-children" if not dependency_ready else campaign_status)
        )
    counts: dict[str, int] = {}
    for task in plan["tasks"]:
        key = f"level_{task['level']}_{task['status'].replace('-', '_')}"
        counts[key] = counts.get(key, 0) + 1
    plan["counts"] = counts
    plan["quarantine"] = quarantine
    atomic_write_canonical_json(output_root / "backfill" / "plan.json", plan)
    return plan


def _run_task(
    task: dict[str, Any],
    plan: dict[str, Any],
    config_path: Path,
    existing_roots: list[Path],
    normal_model_calls: tuple[dict[str, int], Lock],
) -> dict[str, Any]:
    archive_root = Path(plan["archive_root"])
    output_root = Path(plan["output_root"])
    if task["level"] == 1:
        job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
        source = build_level_1_source(job)
    else:
        sidecars, conflicts = _load_sidecars([output_root, *existing_roots])
        if any(child in conflicts for child in task["children"]):
            raise SummaryV2Error("parent has a conflicted child sidecar")
        source = build_parent_source(
            [sidecars[child] for child in task["children"]],
            parallel_summary_id=task["summary_id"],
        )
    route_plan = _decide_route(task, source)
    summary_id = str(task["summary_id"])
    state_path = _rescue_state_path(
        output_root, "normal", RUNNER_REVISION, summary_id
    )
    state = _load_rescue_attempt_state(state_path, RUNNER_REVISION, summary_id)
    artifact_root = _rescue_artifact_root(
        output_root, "normal", RUNNER_REVISION, summary_id
    )

    def execute_direct():
        _begin_rescue_attempt(state_path, state)
        _bind_rescue_attempt(
            state_path,
            state,
            source,
            parent=int(task["level"]) > 1,
            config_path=config_path,
        )
        result, _ = _execute_model_stage(
            state=state,
            state_path=state_path,
            state_lock=normal_model_calls[1],
            model_call_budget=normal_model_calls[0],
            stage_key="final/direct",
            source=source,
            ordered_input_projections=(),
            output_root=output_root,
            archive_root=archive_root,
            config_path=config_path,
            rejected_candidate_path=artifact_root / "rejected-candidate.json",
            diagnostic_path=artifact_root / "diagnostic.json",
            invocation_context=_stage_invocation_context(
                "normal", RUNNER_REVISION, summary_id, "direct", source
            ),
            success_collection=None,
        )
        return result

    def defer_to_rescue():
        raise SummaryV2Error(
            f"normal route deferred to its admitted rescue executor: {route_plan['route']}"
        )

    result = _dispatch_route(
        route_plan,
        {
            "direct-dag": execute_direct,
            "l1-map-reduce-dag": defer_to_rescue,
            "parent-map-reduce-dag": defer_to_rescue,
        },
    )
    return {"summary_id": task["summary_id"], **result}


@_single_instance("run")
def run_batch(
    archive_root: Path,
    output_root: Path,
    config_path: Path,
    existing_roots: Iterable[Path] = (),
    *,
    maximum_jobs: int = MAX_BATCH,
) -> dict[str, Any]:
    if not 1 <= maximum_jobs <= MAX_BATCH:
        raise SummaryV2Error(f"maximum_jobs must be between 1 and {MAX_BATCH}")
    existing_roots = [Path(path) for path in existing_roots]
    archive_root, output_root = _validate_roots(archive_root, output_root)
    # An inaccessible tree must never be interpreted as an empty tree.
    _load_sidecars([output_root, *existing_roots])
    _validate_execution_contract(output_root)
    plan_path = output_root / "backfill" / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan.get("format") != PLAN_FORMAT:
            raise SummaryV2Error("backfill plan format is not supported")
        if Path(plan["archive_root"]).resolve() != archive_root:
            raise SummaryV2Error("backfill plan archive root changed")
        plan = _refresh_plan(plan, existing_roots)
    else:
        plan = build_plan(archive_root, output_root, existing_roots)
    ready = sorted(
        (
            task
            for task in plan["tasks"]
            if task["status"] == "ready" and task.get("eligible", True)
        ),
        key=lambda item: (int(item["level"]), item["summary_id"]),
    )[:maximum_jobs]
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    if ready:
        normal_model_calls = ({"maximum": len(ready), "used": 0}, Lock())

        def run_ready(task):
            return _run_task(task, plan, config_path, existing_roots, normal_model_calls)

        def record_failure(task, exc):
            error = str(exc).replace("\r", " ").replace("\n", " ")
            attempt_status = _classify_failure(exc)
            summary_id = str(task["summary_id"])
            state_path = _rescue_state_path(
                output_root, "normal", RUNNER_REVISION, summary_id
            )
            state = _load_rescue_attempt_state(
                state_path, RUNNER_REVISION, summary_id
            )
            state["attempt_status"] = attempt_status
            state["content_attempts"] = (
                1 if attempt_status == "content-failed-terminal" else 0
            )
            state["last_error"] = error
            state["diagnostic"] = getattr(exc, "diagnostic", None)
            _write_rescue_state(state_path, state)
            failed.append(
                {
                    "summary_id": summary_id,
                    "error": error,
                    "attempts": state["content_attempts"],
                }
            )

        try:
            for _, result in _bounded_parallel_results(
                ready,
                run_ready,
                failure_handler=record_failure,
            ):
                completed.append(result)
        except Exception:
            if not failed:
                raise
    refreshed = _refresh_plan(plan, existing_roots)
    receipt = {
        "status": "completed" if not failed else "attention",
        "attempted": len(completed) + len(failed),
        "completed": sorted(completed, key=lambda item: item["summary_id"]),
        "failed": sorted(failed, key=lambda item: item["summary_id"]),
        "remaining_ready": sum(
            1 for task in refreshed["tasks"] if task["status"] == "ready"
        ),
        "remaining_waiting": sum(
            1
            for task in refreshed["tasks"]
            if task["status"] == "waiting-for-children"
        ),
        "quarantined": len(refreshed["quarantine"]),
    }
    receipt_path = Path(refreshed["output_root"]) / "backfill" / "last-run.json"
    atomic_write_canonical_json(receipt_path, receipt)
    return receipt


def _quarantine_reason_by_id(plan: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["summary_id"]): str(item.get("reason", ""))
        for item in plan.get("quarantine", [])
    }


def _rescue_quarantine_is_eligible(reason: str | None) -> bool:
    return reason in {"model-failure-limit", "infra-blocked"}


def _chunk_job(job: dict[str, Any], target_bytes: int = MAP_PROMPT_TARGET) -> list[dict[str, Any]]:
    try:
        return plan_level_1_jobs(
            job,
            build_level_1_source,
            compile_prompt,
            _source_sha256,
            target_bytes,
            REDUCE_PROMPT_LIMIT,
            MAP_SOURCE_REF_TARGET,
        )
    except ValueError as exc:
        raise SummaryV2Error(str(exc)) from exc


def _execute_map_rescue_dag(
    *,
    job: dict[str, Any],
    formal_source: dict[str, Any],
    summary_id: str,
    state: dict[str, Any],
    state_path: Path,
    artifact_root: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    maximum_model_calls: int,
) -> dict[str, Any]:
    chunks = _chunk_job(job)
    required_model_calls = _l1_rescue_call_upper_bound(len(chunks))
    if required_model_calls > maximum_model_calls:
        raise SummaryV2Error(
            "L1 rescue full-DAG model-call budget is undersized before attempt: "
            f"maps={len(chunks)} required={required_model_calls} "
            f"maximum={maximum_model_calls}"
        )
    _begin_rescue_attempt(state_path, state)
    _bind_rescue_attempt(
        state_path,
        state,
        formal_source,
        parent=False,
        config_path=config_path,
    )
    model_call_budget = {
        "maximum": maximum_model_calls,
        "used": len(state.get("maps", {})) + len(state.get("reductions", {})),
    }
    map_sidecars_by_key: dict[str, dict[str, Any]] = {}
    map_root = artifact_root / "maps"
    missing: list[tuple[str, dict[str, Any]]] = []
    for index, chunk in enumerate(chunks, 1):
        key = f"map-{index:03d}"
        saved = state["maps"].get(key)
        if saved:
            sidecar = load_sidecar(Path(saved["bundle"]))
            _require_stage_receipt(saved, build_level_1_source(chunk), sidecar,
                                   state=state, stage_key=f'map/{key}')
            map_sidecars_by_key[key] = sidecar
            continue
        missing.append((key, build_level_1_source(chunk)))
    if missing:
        _require_model_stages_unclaimed(
            state,
            _model_stage_claims(
                (f"map/{key}", source, ()) for key, source in missing
            ),
        )
        state_lock = Lock()

        def run_missing(item):
            key, source = item
            _, sidecar = _execute_model_stage(
                state=state,
                state_path=state_path,
                state_lock=state_lock,
                model_call_budget=model_call_budget,
                stage_key=f"map/{key}",
                source=source,
                ordered_input_projections=(),
                output_root=map_root,
                archive_root=archive_root,
                config_path=config_path,
                rejected_candidate_path=(
                    artifact_root / "rejected-map-candidates" / f"{key}.json"
                ),
                diagnostic_path=artifact_root / "diagnostics" / f"{key}.json",
                invocation_context=_stage_invocation_context(
                    "map", MAP_RESCUE_REVISION, summary_id, key, source
                ),
                success_collection="maps",
                success_key=key,
                reserve_after=1,
            )
            return sidecar

        for (key, _), sidecar in _bounded_parallel_results(missing, run_missing):
            map_sidecars_by_key[key] = sidecar
    map_sidecars = [
        map_sidecars_by_key[f"map-{index:03d}"]
        for index in range(1, len(chunks) + 1)
    ]
    leaf_route_sidecars = list(map_sidecars)
    map_sidecars = _compact_l1_rescue_maps(
        job,
        formal_source,
        map_sidecars,
        state,
        state_path,
        output_root,
        archive_root,
        config_path,
        model_call_budget,
    )
    rescue_source = build_rescue_reduce_source(
        formal_source,
        map_sidecars,
        internal_route_sidecars=leaf_route_sidecars,
    )
    reduce_bytes = len(build_prompt(rescue_source).encode("utf-8"))
    if reduce_bytes > REDUCE_PROMPT_LIMIT:
        raise SummaryV2Error(
            f"rescue reduce prompt exceeds staged limit: {reduce_bytes} bytes"
        )
    rejected_candidate_path = artifact_root / "rejected-candidates" / "reduce.json"
    diagnostic_path = artifact_root / "diagnostics" / "reduce.json"
    input_projections = [sidecar["projection_sha256"] for sidecar in map_sidecars]
    invocation_context = _stage_invocation_context(
        "map", MAP_RESCUE_REVISION, summary_id, "reduce", rescue_source, input_projections
    )
    result, _ = _execute_model_stage(
        state=state,
        state_path=state_path,
        state_lock=Lock(),
        model_call_budget=model_call_budget,
        stage_key="final/reduce",
        source=rescue_source,
        ordered_input_projections=input_projections,
        output_root=output_root,
        archive_root=archive_root,
        config_path=config_path,
        rejected_candidate_path=rejected_candidate_path,
        diagnostic_path=diagnostic_path,
        invocation_context=invocation_context,
        success_collection=None,
    )
    return {"summary_id": summary_id, "map_count": len(chunks), **result}


@_single_instance("rescue-map")
def run_map_rescue(
    archive_root: Path,
    output_root: Path,
    config_path: Path,
    existing_roots: Iterable[Path] = (),
    *,
    maximum_jobs: int = 1,
    maximum_model_calls: int = L1_RESCUE_MODEL_CALL_LIMIT,
) -> dict[str, Any]:
    if not 1 <= maximum_jobs <= MAX_BATCH:
        raise SummaryV2Error(
            f"map rescue maximum_jobs must be between 1 and {MAX_BATCH}"
        )
    if maximum_model_calls < 1:
        raise SummaryV2Error("map rescue maximum_model_calls must be positive")
    if maximum_model_calls < L1_RESCUE_MODEL_CALL_LIMIT:
        raise SummaryV2Error(
            "map rescue maximum_model_calls cannot be lower than the admitted "
            f"L1 ceiling: {maximum_model_calls}<{L1_RESCUE_MODEL_CALL_LIMIT}"
        )
    if maximum_model_calls > L1_RESCUE_MODEL_CALL_LIMIT:
        raise SummaryV2Error(
            "map rescue maximum_model_calls cannot exceed the admitted "
            f"L1 ceiling: {maximum_model_calls}>{L1_RESCUE_MODEL_CALL_LIMIT}"
        )
    existing_roots = [Path(path) for path in existing_roots]
    archive_root, output_root = _validate_roots(archive_root, output_root)
    _load_sidecars([output_root, *existing_roots])
    _validate_execution_contract(output_root)
    plan_path = output_root / "backfill" / "plan.json"
    plan = _refresh_plan(json.loads(plan_path.read_text(encoding="utf-8")), existing_roots)
    reasons = _quarantine_reason_by_id(plan)
    candidate_pool = sorted(
        (
            task
            for task in plan["tasks"]
            if task["status"] == "quarantined"
            and task.get("job")
            and _rescue_quarantine_is_eligible(reasons.get(task["summary_id"]))
        ),
        key=lambda item: item["summary_id"],
    )
    candidates, deferred_current_revision = _select_single_attempt_candidates(
        candidate_pool,
        output_root,
        "map",
        MAP_RESCUE_REVISION,
        maximum_jobs,
    )
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for task in candidates:
        summary_id = task["summary_id"]
        state_path = _rescue_state_path(
            output_root,
            "map",
            MAP_RESCUE_REVISION,
            summary_id,
        )
        state = _load_rescue_attempt_state(
            state_path,
            MAP_RESCUE_REVISION,
            summary_id,
        )
        try:
            artifact_root = _rescue_artifact_root(
                output_root, "map", MAP_RESCUE_REVISION, summary_id
            )
            _validate_sidecar_path_budget(artifact_root / "maps", 1)
            job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
            formal_source = build_level_1_source(job)
            route_plan = _decide_route(
                task,
                formal_source,
                failure_reason=reasons.get(summary_id),
            )
            completed.append(
                _dispatch_route(
                    route_plan,
                    {
                        "l1-map-reduce-dag": lambda: _execute_map_rescue_dag(
                            job=job,
                            formal_source=formal_source,
                            summary_id=summary_id,
                            state=state,
                            state_path=state_path,
                            artifact_root=artifact_root,
                            output_root=output_root,
                            archive_root=archive_root,
                            config_path=config_path,
                            maximum_model_calls=maximum_model_calls,
                        )
                    },
                )
            )
        except Exception as exc:
            error = str(exc).replace("\r", " ").replace("\n", " ")
            state["last_error"] = error
            state["attempt_status"] = _classify_failure(exc)
            state["content_attempts"] = 0 if state["attempt_status"] == "infra-blocked" else 1
            state["diagnostic"] = getattr(exc, "diagnostic", None)
            _write_rescue_state(state_path, state)
            failed.append({"summary_id": summary_id, "error": error})
    refreshed = _refresh_plan(plan, existing_roots)
    receipt = {
        "status": "completed" if not failed else "attention",
        "revision": MAP_RESCUE_REVISION,
        "attempted": len(candidates),
        "completed": completed,
        "failed": failed,
        "deferred_current_revision": deferred_current_revision,
        "quarantined": len(refreshed["quarantine"]),
        "remaining_ready": sum(
            1 for task in refreshed["tasks"] if task["status"] == "ready"
        ),
        "remaining_waiting": sum(
            1 for task in refreshed["tasks"] if task["status"] == "waiting-for-children"
        ),
    }
    atomic_write_canonical_json(
        output_root / "backfill" / "rescue" / "map-last-run.json", receipt
    )
    return receipt


def _execute_parent_rescue_dag(
    *,
    children: list[dict[str, Any]],
    formal_source: dict[str, Any],
    formal_binding_source: dict[str, Any],
    summary_id: str,
    state: dict[str, Any],
    state_path: Path,
    artifact_root: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
    maximum_model_calls: int,
) -> dict[str, Any]:
    if len(children) < 4:
        if len(children) < 2:
            raise SummaryV2Error("parent rescue needs at least two direct children")
        if maximum_model_calls < 1:
            raise SummaryV2Error(
                "parent rescue full-DAG model-call budget is undersized before attempt: "
                f"maps=0 required=1 maximum={maximum_model_calls}"
            )
        _begin_rescue_attempt(state_path, state)
        _bind_rescue_attempt(
            state_path,
            state,
            formal_source,
            parent=True,
            config_path=config_path,
        )
        result, _ = _execute_model_stage(
            state=state,
            state_path=state_path,
            state_lock=Lock(),
            model_call_budget={"maximum": maximum_model_calls, "used": 0},
            stage_key="final/direct",
            source=formal_source,
            ordered_input_projections=(),
            output_root=output_root,
            archive_root=archive_root,
            config_path=config_path,
            rejected_candidate_path=(
                artifact_root / "rejected-parent-candidates" / "direct.json"
            ),
            diagnostic_path=artifact_root / "diagnostics" / "direct.json",
            invocation_context=_stage_invocation_context(
                "parent",
                PARENT_RESCUE_REVISION,
                summary_id,
                "direct",
                formal_source,
            ),
            success_collection=None,
        )
        return {
            "summary_id": summary_id,
            "map_count": 0,
            "model_calls_used": 1,
            **result,
        }
    groups = _plan_parent_rescue_groups(children, summary_id)
    required_model_calls = _parent_rescue_call_upper_bound(len(groups))
    if required_model_calls > maximum_model_calls:
        raise SummaryV2Error(
            "parent rescue full-DAG model-call budget is undersized before attempt: "
            f"maps={len(groups)} required={required_model_calls} "
            f"maximum={maximum_model_calls}"
        )
    _begin_rescue_attempt(state_path, state)
    _bind_rescue_attempt(
        state_path,
        state,
        formal_binding_source,
        parent=True,
        config_path=config_path,
    )
    model_call_budget = {
        "maximum": maximum_model_calls,
        "used": len(state["maps"]) + len(state["reductions"]),
    }
    map_root = artifact_root / "maps"
    map_sources: list[dict[str, Any]] = []
    for index, group in enumerate(groups, 1):
        source = build_parent_source(
            group,
            parallel_summary_id=f"{summary_id}-map-{index:03d}",
        )
        source["compact_parent_prompt"] = True
        map_sources.append(source)
    map_sidecars_by_key: dict[str, dict[str, Any]] = {}
    missing_maps: list[tuple[str, dict[str, Any]]] = []
    for index, source in enumerate(map_sources, 1):
        key = f"map-{index:03d}"
        saved = state["maps"].get(key)
        if saved:
            sidecar = load_sidecar(Path(saved["bundle"]))
            _require_stage_receipt(saved, source, sidecar, state=state, stage_key=f'map/{key}')
            map_sidecars_by_key[key] = sidecar
            continue
        missing_maps.append((key, source))

    _require_model_stages_unclaimed(
        state,
        _model_stage_claims(
            (f"map/{key}", source, ()) for key, source in missing_maps
        ),
    )
    state_lock = Lock()

    def run_missing_parent_map(item: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        key, source = item
        _, sidecar = _execute_model_stage(
            state=state,
            state_path=state_path,
            state_lock=state_lock,
            model_call_budget=model_call_budget,
            stage_key=f"map/{key}",
            source=source,
            ordered_input_projections=(),
            output_root=map_root,
            archive_root=archive_root,
            config_path=config_path,
            rejected_candidate_path=(
                artifact_root / "rejected-map-candidates" / f"{key}.json"
            ),
            diagnostic_path=artifact_root / "diagnostics" / f"{key}.json",
            invocation_context=_stage_invocation_context(
                "parent", PARENT_RESCUE_REVISION, summary_id, key, source
            ),
            success_collection="maps",
            success_key=key,
            reserve_after=1,
        )
        return sidecar

    for (key, _), sidecar in _bounded_parallel_results(
        missing_maps, run_missing_parent_map
    ):
        map_sidecars_by_key[key] = sidecar
    map_sidecars = [
        map_sidecars_by_key[f"map-{index:03d}"]
        for index in range(1, len(map_sources) + 1)
    ]
    map_sidecars = _compact_parent_rescue_maps(
        formal_source,
        children,
        map_sidecars,
        state,
        state_path,
        output_root,
        archive_root,
        config_path,
        model_call_budget,
    )
    rescue_source = build_parent_rescue_reduce_source(formal_source, map_sidecars)
    reduce_bytes = len(build_prompt(rescue_source).encode("utf-8"))
    if reduce_bytes > REDUCE_PROMPT_LIMIT:
        raise SummaryV2Error(
            f"parent rescue reduce prompt exceeds staged limit: {reduce_bytes} bytes"
        )
    diagnostic_path = artifact_root / "diagnostics" / "reduce.json"
    input_projections = [sidecar["projection_sha256"] for sidecar in map_sidecars]
    invocation_context = _stage_invocation_context(
        "parent",
        PARENT_RESCUE_REVISION,
        summary_id,
        "reduce",
        rescue_source,
        input_projections,
    )
    result, _ = _execute_model_stage(
        state=state,
        state_path=state_path,
        state_lock=Lock(),
        model_call_budget=model_call_budget,
        stage_key="final/reduce",
        source=rescue_source,
        ordered_input_projections=input_projections,
        output_root=output_root,
        archive_root=archive_root,
        config_path=config_path,
        rejected_candidate_path=(
            artifact_root / "rejected-parent-candidates" / "reduce.json"
        ),
        diagnostic_path=diagnostic_path,
        invocation_context=invocation_context,
        success_collection=None,
    )
    return {
        "summary_id": summary_id,
        "map_count": len(groups),
        "model_calls_used": model_call_budget["used"],
        **result,
    }


@_single_instance("rescue-parent")
def run_parent_rescue(
    archive_root: Path,
    output_root: Path,
    config_path: Path,
    existing_roots: Iterable[Path] = (),
    *,
    maximum_jobs: int = 1,
    maximum_model_calls: int = PARENT_RESCUE_MODEL_CALL_LIMIT,
) -> dict[str, Any]:
    if not 1 <= maximum_jobs <= 3:
        raise SummaryV2Error("parent rescue maximum_jobs must be between 1 and 3")
    if maximum_model_calls < 1:
        raise SummaryV2Error("parent rescue maximum_model_calls must be positive")
    if maximum_model_calls > PARENT_RESCUE_MODEL_CALL_LIMIT:
        raise SummaryV2Error(
            "parent rescue maximum_model_calls cannot exceed the admitted "
            f"parent ceiling: {maximum_model_calls}>{PARENT_RESCUE_MODEL_CALL_LIMIT}"
        )
    existing_roots = [Path(path) for path in existing_roots]
    archive_root, output_root = _validate_roots(archive_root, output_root)
    sidecars, conflicts = _load_sidecars([output_root, *existing_roots])
    _validate_execution_contract(output_root)
    plan_path = output_root / "backfill" / "plan.json"
    plan = _refresh_plan(json.loads(plan_path.read_text(encoding="utf-8")), existing_roots)
    reasons = _quarantine_reason_by_id(plan)
    candidate_pool = sorted(
        (
            task
            for task in plan["tasks"]
            if task["status"] == "quarantined"
            and int(task["level"]) > 1
            and _rescue_quarantine_is_eligible(reasons.get(task["summary_id"]))
        ),
        key=lambda item: (int(item["level"]), item["summary_id"]),
    )
    candidates, deferred_current_revision = _select_single_attempt_candidates(
        candidate_pool,
        output_root,
        "parent",
        PARENT_RESCUE_REVISION,
        maximum_jobs,
    )
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for task in candidates:
        summary_id = task["summary_id"]
        state_path = _rescue_state_path(
            output_root,
            "parent",
            PARENT_RESCUE_REVISION,
            summary_id,
        )
        state = _load_rescue_attempt_state(
            state_path,
            PARENT_RESCUE_REVISION,
            summary_id,
        )
        try:
            artifact_root = _rescue_artifact_root(
                output_root, "parent", PARENT_RESCUE_REVISION, summary_id
            )
            _validate_sidecar_path_budget(artifact_root / "maps", int(task["level"]))
            if any(child in conflicts or child not in sidecars for child in task["children"]):
                raise SummaryV2Error("parent rescue child sidecars are incomplete or conflicted")
            children = [sidecars[child] for child in task["children"]]
            formal_source = build_parent_source(children, parallel_summary_id=summary_id)
            formal_binding_source = dict(formal_source)
            formal_binding_source["compact_parent_prompt"] = True
            route_plan = _decide_route(
                task,
                formal_binding_source,
                failure_reason=reasons.get(summary_id),
            )
            completed.append(
                _dispatch_route(
                    route_plan,
                    {
                        "parent-map-reduce-dag": lambda: _execute_parent_rescue_dag(
                            children=children,
                            formal_source=formal_source,
                            formal_binding_source=formal_binding_source,
                            summary_id=summary_id,
                            state=state,
                            state_path=state_path,
                            artifact_root=artifact_root,
                            output_root=output_root,
                            archive_root=archive_root,
                            config_path=config_path,
                            maximum_model_calls=maximum_model_calls,
                        )
                    },
                )
            )
        except Exception as exc:
            error = str(exc).replace("\r", " ").replace("\n", " ")
            state["last_error"] = error
            state["attempt_status"] = _classify_failure(exc)
            state["content_attempts"] = 0 if state["attempt_status"] == "infra-blocked" else 1
            state["diagnostic"] = getattr(exc, "diagnostic", None)
            _write_rescue_state(state_path, state)
            failed.append({"summary_id": summary_id, "error": error})
    refreshed = _refresh_plan(plan, existing_roots)
    receipt = {
        "status": "completed" if not failed else "attention",
        "revision": PARENT_RESCUE_REVISION,
        "attempted": len(candidates),
        "completed": completed,
        "failed": failed,
        "deferred_current_revision": deferred_current_revision,
        "quarantined": len(refreshed["quarantine"]),
        "remaining_ready": sum(
            1 for task in refreshed["tasks"] if task["status"] == "ready"
        ),
        "remaining_waiting": sum(
            1 for task in refreshed["tasks"] if task["status"] == "waiting-for-children"
        ),
    }
    atomic_write_canonical_json(
        output_root / "backfill" / "rescue" / "parent-last-run.json", receipt
    )
    return receipt


def run_closed_node(
    archive_root: Path, output_root: Path, *, config_path: Path,
    job: dict[str, Any], children: list[dict[str, Any]] | None = None,
    tick_seconds: float = 960,
) -> dict[str, Any]:
    """Execute one closed mainline job without historical planning or archive scans."""
    archive_root, output_root = _validate_roots(archive_root, output_root)
    with _exclusive_runner_lock(output_root, "runtime-node"):
        return _run_closed_node(archive_root, output_root, config_path=config_path,
                                job=job, children=children, tick_seconds=tick_seconds)


def _closed_timeout_predecessor(output_root: Path, summary_id: str,
                                source: dict[str, Any], request: dict[str, Any],
                                config: dict[str, Any]) -> dict[str, Any] | None:
    """Admit the existing rescue revision, never another claimed direct stage."""
    path = _rescue_state_path(output_root, 'normal', RUNNER_REVISION, summary_id)
    previous = _load_rescue_attempt_state(path, RUNNER_REVISION, summary_id)
    diagnostic_path = _rescue_artifact_root(output_root, 'normal', RUNNER_REVISION,
                                           summary_id) / 'diagnostic.json'
    if previous['attempt_status'] == 'not-started' and diagnostic_path.is_file():
        raise SummaryV2Error('closed direct state is missing but diagnostic evidence remains')
    if previous['attempt_status'] != 'infra-blocked':
        return None
    if not diagnostic_path.is_file():
        return None  # An ambiguous claim is not evidence of a finished timeout.
    diagnostic = json.loads(diagnostic_path.read_text(encoding='utf-8'))
    if (diagnostic.get('classification') != 'infra-timeout' or
        diagnostic.get('candidate_exists') is not False or
        diagnostic.get('candidate_sha256') is not None or
        diagnostic.get('model_called') is not True):
        return None
    if (previous.get('runtime_request') != request or previous.get('content_attempts') != 0 or
        previous.get('orchestration_attempts') != 1 or previous.get('maps') or
        previous.get('reductions') or previous.get('completed') or previous.get('completed_receipt')):
        raise SummaryV2Error('closed timeout predecessor request or outcome changed')
    claims = previous.get('dispatches', {})
    binding = previous.get('binding', {})
    expected = _stage_input_binding(source)
    # candidate04 is the sole pre-repair closed-runtime producer admitted here.
    predecessor_runners = {expected['runner_sha256'],
                          '1ab62310e8473039e1207fb0534d91d68c56434ee1596e0aaf3edd1f8f1a8f21'}
    if binding.get('runner_sha256') not in predecessor_runners:
        raise SummaryV2Error('closed timeout predecessor runner is not admitted')
    expected['runner_sha256'] = binding['runner_sha256']
    if claims != {'final/direct': expected} or diagnostic.get('stage_binding') != expected:
        raise SummaryV2Error('closed timeout predecessor claim or diagnostic changed')
    for key in ('source_sha256', 'prompt_sha256', 'prompt_utf8_bytes',
                'canonical_ledger_sha256', 'prompt_projection_sha256',
                'schema_sha256', 'worker_sha256', 'planner_sha256'):
        if binding.get(key) != expected[key]:
            raise SummaryV2Error('closed timeout predecessor binding changed: ' + key)
    codex = Path(codex_command(config, source)[0][0]).expanduser().resolve()
    if (binding.get('codex_executable') != str(codex) or
        binding.get('codex_sha256') != file_sha256(codex) or
        diagnostic.get('status') != 'failed' or diagnostic.get('summary_id') != summary_id or
        diagnostic.get('revision') != RUNNER_REVISION or diagnostic.get('family') != 'normal' or
        diagnostic.get('stage') != 'direct' or diagnostic.get('source_sha256') != source['source_sha256'] or
        diagnostic.get('job_id') != source['job_id'] or
        diagnostic.get('error') != previous.get('last_error')):
        raise SummaryV2Error('closed timeout predecessor runtime or diagnostic identity changed')
    return {'revision': RUNNER_REVISION, 'state_sha256': file_sha256(path),
            'diagnostic_sha256': file_sha256(diagnostic_path), 'reason': 'infra-timeout'}


def _run_closed_node(archive_root: Path, output_root: Path, *, config_path: Path,
                     job: dict[str, Any], children: list[dict[str, Any]] | None,
                     tick_seconds: float) -> dict[str, Any]:
    _validate_execution_contract(output_root)
    summary_id = str(job.get("target_summary_id", ""))
    if not summary_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-" for c in summary_id):
        raise SummaryV2Error("runtime node requires a safe, exact target summary ID")
    level = int(job["summary_level"])
    if level == 1:
        if children:
            raise SummaryV2Error("L1 runtime job cannot contain child summaries")
        source = build_level_1_source(job)
    else:
        if [child.get("parallel_summary_id") for child in children or []] != job.get("source_summaries"):
            raise SummaryV2Error("runtime parent ordered children disagree with the closed job")
        source = build_parent_source(children or [], parallel_summary_id=summary_id)
        if source["conversation_id"] != job["conversation_id"] or source["summary_level"] != level:
            raise SummaryV2Error("runtime parent identity disagrees with its direct children")
    route = _decide_route({"level": level, "summary_id": summary_id}, source)["route"]
    config = load_simple_yaml(config_path)
    request_binding = {
        "job_sha256": hashlib.sha256(json.dumps(job, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode("utf-8")).hexdigest(),
        "source_sha256": source["source_sha256"],
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest(),
        "route": route,
    }
    predecessor = (_closed_timeout_predecessor(output_root, summary_id, source, request_binding, config)
                   if route == 'direct-dag' else None)
    if route == 'direct-dag' and predecessor is None:
        rescue_family, rescue_revision = (('map', MAP_RESCUE_REVISION) if level == 1
                                          else ('parent', PARENT_RESCUE_REVISION))
        rescue = _load_rescue_attempt_state(
            _rescue_state_path(output_root, rescue_family, rescue_revision, summary_id),
            rescue_revision, summary_id)
        if rescue['attempt_status'] != 'not-started' or rescue.get('runtime_recovery') is not None:
            raise SummaryV2Error('existing closed rescue requires its intact timeout predecessor')
    if predecessor is not None:
        route = _decide_route({'level': level, 'summary_id': summary_id, 'status': 'quarantined'},
                              source, failure_reason='infra-blocked')['route']
        request_binding['route'] = route
    family, revision = {
        "direct-dag": ("normal", RUNNER_REVISION),
        "l1-map-reduce-dag": ("map", MAP_RESCUE_REVISION),
        "parent-map-reduce-dag": ("parent", PARENT_RESCUE_REVISION),
    }[route]
    state_path = _rescue_state_path(output_root, family, revision, summary_id)
    state = _load_rescue_attempt_state(state_path, revision, summary_id)
    if predecessor is not None:
        if (state.get('runtime_recovery', predecessor if state['attempt_status'] == 'not-started' else None)
            != predecessor):
            raise SummaryV2Error('closed rescue predecessor evidence changed')
        state['runtime_recovery'] = predecessor
    if state.get("runtime_request", request_binding) != request_binding:
        raise SummaryV2Error("closed runtime request changed within one node")
    _, timeout, _ = codex_command(config, source)
    maximum_dispatches = config.get('ai_summary', {}).get('maximum_parallel_model_calls', MAX_PARALLEL)
    if type(maximum_dispatches) is not int:
        raise SummaryV2Error('Runtime maximum_parallel_model_calls must be an integer')
    tick = TickBudget(seconds=tick_seconds, call_timeout=timeout, config=config,
                      maximum_dispatches=maximum_dispatches)
    artifact_root = _rescue_artifact_root(output_root, family, revision, summary_id)
    state["runtime_request"] = request_binding
    token = _runtime_tick.set(tick)
    try:
        if state["attempt_status"] in {"content-failed-terminal", "infra-blocked"}:
            return {"status": "blocked", "node_state": state["attempt_status"],
                    "error": state.get("last_error", "terminal node"), "ai_invocations": 0,
                    "summary_id": summary_id}
        common = dict(state=state, state_path=state_path, artifact_root=artifact_root,
                      output_root=output_root, archive_root=archive_root, config_path=config_path)
        if route == "direct-dag":
            _begin_rescue_attempt(state_path, state)
            _bind_rescue_attempt(state_path, state, source, parent=level > 1, config_path=config_path)
            result, _ = _execute_model_stage(
                state=state, state_path=state_path, state_lock=Lock(),
                model_call_budget={"maximum": 1, "used": 0}, stage_key="final/direct",
                source=source, ordered_input_projections=(), output_root=output_root,
                archive_root=archive_root, config_path=config_path,
                rejected_candidate_path=artifact_root / "rejected-candidate.json",
                diagnostic_path=artifact_root / "diagnostic.json",
                invocation_context=_stage_invocation_context(family, revision, summary_id, "direct", source),
                success_collection=None,
            )
        elif level == 1:
            result = _execute_map_rescue_dag(
                **common, job=job, formal_source=source, summary_id=summary_id,
                maximum_model_calls=L1_RESCUE_MODEL_CALL_LIMIT,
            )
        else:
            binding_source = {**source, "compact_parent_prompt": True}
            result = _execute_parent_rescue_dag(
                **common, children=children or [], formal_source=source,
                formal_binding_source=binding_source, summary_id=summary_id,
                maximum_model_calls=PARENT_RESCUE_MODEL_CALL_LIMIT,
            )
        return {**result, "status": "completed", "summary_id": summary_id,
                "request_job_sha256": request_binding["job_sha256"],
                "core_source_job_id": source["job_id"], "source_sha256": source["source_sha256"],
                "ai_invocations": tick.admitted, "state_path": str(state_path)}
    except BudgetYield as exc:
        return {"status": "yielded", "summary_id": summary_id, "reason": exc.reason,
                "next_stage": exc.next_stage, "ai_invocations": tick.admitted,
                "node_state": state["attempt_status"], "state_path": str(state_path)}
    except Exception as exc:
        # Never turn a corrupted completed receipt into a newly eligible attempt.
        state["last_error"] = str(exc)
        state["attempt_status"] = _classify_failure(exc)
        state["content_attempts"] = int(state["attempt_status"] == "content-failed-terminal")
        _write_rescue_state(state_path, state)
        return {"status": "blocked", "summary_id": summary_id,
                "node_state": state["attempt_status"], "error": str(exc),
                "ai_invocations": tick.admitted, "state_path": str(state_path)}
    finally:
        _runtime_tick.reset(token)


def describe_completed_node(result: dict[str, Any]) -> dict[str, Any]:
    """Expose the exact node-owned bundle inventory for completion and backup."""
    if result.get("status") != "completed":
        return result
    state = json.loads(Path(result["state_path"]).read_text(encoding="utf-8"))
    paths = {result["bundle"]}
    paths.update(item["bundle"] for group in ("maps", "reductions") for item in state[group].values())
    objects = []
    for value in sorted(paths):
        path = Path(value)
        sidecar = load_sidecar(path)
        if (path / 'summary.md').read_bytes() != render_markdown(sidecar).encode('utf-8'):
            raise SummaryV2Error('Completed runtime Markdown disagrees with its canonical projection')
        objects.append({"path": str(path), "summary_v2_id": sidecar["summary_v2_id"],
                        "projection_sha256": sidecar["projection_sha256"],
                        "json_sha256": file_sha256(path / "summary.json"),
                        "markdown_sha256": file_sha256(path / "summary.md")})
    bundle = Path(result["bundle"])
    return {**result, "sidecar": load_sidecar(bundle), "objects": objects,
            "json_sha256": file_sha256(bundle / "summary.json"),
            "markdown_sha256": file_sha256(bundle / "summary.md")}


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--existing-root", action="append", default=[])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    runtime = commands.add_parser("runtime-node")
    runtime.add_argument("--config", required=True)
    runtime.add_argument("--request", required=True)
    inspect = commands.add_parser("inspect-bundles")
    inspect.add_argument("--request", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--max-jobs", type=int, default=MAX_BATCH)
    map_rescue = commands.add_parser("rescue-map")
    map_rescue.add_argument("--config", required=True)
    map_rescue.add_argument("--max-jobs", type=int, default=1)
    map_rescue.add_argument(
        "--max-model-calls", type=int, default=L1_RESCUE_MODEL_CALL_LIMIT
    )
    parent_rescue = commands.add_parser("rescue-parent")
    parent_rescue.add_argument("--config", required=True)
    parent_rescue.add_argument("--max-jobs", type=int, default=1)
    parent_rescue.add_argument(
        "--max-model-calls", type=int, default=PARENT_RESCUE_MODEL_CALL_LIMIT
    )
    args = parser.parse_args()
    try:
        common = {
            "archive_root": Path(args.archive_root),
            "output_root": Path(args.output_root),
            "existing_roots": [Path(path) for path in args.existing_root],
        }
        if args.command in {"runtime-node", "inspect-bundles"}:
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            sidecars = []
            for descriptor in request.get("children", []):
                bundle = Path(descriptor["path"]).resolve()
                for name, field in (("summary.json", "json_sha256"), ("summary.md", "markdown_sha256")):
                    if file_sha256(bundle / name) != descriptor[field]:
                        raise SummaryV2Error("closed child bundle bytes changed")
                sidecars.append(load_sidecar(bundle))
                if (bundle / "summary.md").read_bytes() != render_markdown(sidecars[-1]).encode("utf-8"):
                    raise SummaryV2Error("closed child Markdown disagrees with the canonical projection")
            if args.command == "inspect-bundles":
                result = {"status": "verified", "sidecars": sidecars}
                if request.get("parent_alias"):
                    source = build_parent_source(sidecars, parallel_summary_id=request["parent_alias"])
                    result["parent_source"] = {key: source[key] for key in
                                               ("source_sha256", "job_id", "conversation_id", "summary_level")}
            else:
                result = run_closed_node(
                    common["archive_root"], common["output_root"], config_path=Path(args.config),
                    job=request["job"], children=sidecars,
                    tick_seconds=request.get("tick_seconds", 960),
                )
                result = describe_completed_node(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        elif args.command == "plan":
            result = build_plan(**common)
            compact = {
                "format": result["format"],
                "archive_root": result["archive_root"],
                "output_root": result["output_root"],
                "raw_message_count": result["raw_message_count"],
                "summary_v1_count": result["summary_v1_count"],
                "counts": result["counts"],
                "quarantine": result["quarantine"],
            }
            print(json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "run":
            print(
                json.dumps(
                    run_batch(
                        **common,
                        config_path=Path(args.config),
                        maximum_jobs=args.max_jobs,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        elif args.command == "rescue-map":
            print(
                json.dumps(
                    run_map_rescue(
                        **common,
                        config_path=Path(args.config),
                        maximum_jobs=args.max_jobs,
                        maximum_model_calls=args.max_model_calls,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(
                json.dumps(
                    run_parent_rescue(
                        **common,
                        config_path=Path(args.config),
                        maximum_jobs=args.max_jobs,
                        maximum_model_calls=args.max_model_calls,
                    ),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"memory-wuxian summary-v2 backfill: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
