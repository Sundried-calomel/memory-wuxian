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
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
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
)
from platform_transaction import atomic_write_canonical_json
from platform_process import no_window_kwargs
from summary_v2_worker import build_prompt, codex_command, load_sidecar, run_source


PLAN_FORMAT = "memory-wuxian-summary-v2-backfill-plan-v1"
MAX_BATCH = 20
MAX_PARALLEL = 3
FAILURE_LIMIT = 1
RUNNER_REVISION = "summary-v2-backfill-normalizer-v5"
DIRECT_RESCUE_REVISION = "summary-v2-direct-rescue-v1"
MAP_RESCUE_REVISION = "summary-v2-map-reduce-v5"
PARENT_RESCUE_REVISION = "summary-v2-parent-map-reduce-v5"
MAP_PROMPT_TARGET = 240_000
REDUCE_PROMPT_LIMIT = 900_000
EXECUTION_CONTRACT_FORMAT = "memory-wuxian-summary-v2-execution-contract-v1"
NON_RETRYABLE_ERRORS = (
    "prompt exceeds the",
    "backfill job drifted",
    "source-validation:",
)
INFRA_ERROR_MARKERS = (
    "timed out after",
    "network",
    "permission denied",
    "access is denied",
    "model-process-failure",
)


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
    return (
        output_root
        / "backfill"
        / "rescue"
        / f"{family}-state"
        / safe_revision
        / f"{summary_id}.json"
    )


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


def _classify_failure(exc: Exception) -> str:
    diagnostic = getattr(exc, "diagnostic", {})
    classification = str(diagnostic.get("classification", ""))
    text = f"{classification} {exc}".lower()
    if any(marker in text for marker in INFRA_ERROR_MARKERS):
        return "infra-blocked"
    return "content-failed-terminal"


def _bind_rescue_attempt(
    path: Path,
    state: dict[str, Any],
    source: dict[str, Any],
    *,
    parent: bool,
    config_path: Path,
) -> None:
    prompt = build_prompt(source).encode("utf-8")
    schema = Path(__file__).resolve().parent.parent / "schemas" / (
        "summary-v2-parent-result.schema.json" if parent else "summary-v2-result.schema.json"
    )
    command, _, _ = codex_command(load_simple_yaml(Path(config_path)), source)
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
        "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "schema_sha256": file_sha256(schema),
        "projector": PARENT_PROJECTOR if parent else PROJECTOR,
        "runner_sha256": file_sha256(Path(__file__)),
        "worker_sha256": file_sha256(Path(__file__).with_name("summary_v2_worker.py")),
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
    if not path.exists():
        return {
            "revision": revision,
            "summary_id": summary_id,
            "maps": {},
        }
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("revision") != revision or state.get("summary_id") != summary_id:
        raise SummaryV2Error("rescue attempt state identity changed")
    return state


def _rescue_attempt_is_terminal(state: dict[str, Any]) -> bool:
    if state.get("completed"):
        return True
    return state.get("attempt_status") in {
        "completed",
        "failed",
        "content-failed-terminal",
        "infra-blocked",
    }


def _begin_rescue_attempt(path: Path, state: dict[str, Any]) -> None:
    status = state.get("attempt_status")
    if _rescue_attempt_is_terminal(state):
        raise SummaryV2Error("rescue attempt is already terminal for this revision")
    state["attempt_status"] = "in-progress"
    state["attempts"] = 1
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


def _recover_rescue_maps(
    map_root: Path,
    chunks: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    expected = {
        chunk["source_sha256"]: f"map-{index:03d}"
        for index, chunk in enumerate(chunks, 1)
        if f"map-{index:03d}" not in state.get("maps", {})
    }
    if not expected or not map_root.exists():
        return
    recovered: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in sorted(map_root.rglob("summary.json")):
        sidecar = load_sidecar(path)
        source_sha = sidecar["source"]["source_sha256"]
        key = expected.get(source_sha)
        if key is None:
            continue
        previous = recovered.get(key)
        if previous and previous[0]["projection_sha256"] != sidecar["projection_sha256"]:
            raise SummaryV2Error(f"conflicting orphan rescue maps found: {key}")
        recovered[key] = (sidecar, path.parent)
    for key, (sidecar, bundle) in recovered.items():
        state.setdefault("maps", {})[key] = {
            "bundle": str(bundle),
            "source_sha256": sidecar["source"]["source_sha256"],
            "projection_sha256": sidecar["projection_sha256"],
        }


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


def _compact_l1_rescue_maps(
    job: dict[str, Any],
    formal_source: dict[str, Any],
    map_sidecars: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    output_root: Path,
    archive_root: Path,
    config_path: Path,
) -> list[dict[str, Any]]:
    current = list(map_sidecars)
    stage = 1
    while len(build_prompt(build_rescue_reduce_source(formal_source, current)).encode("utf-8")) > REDUCE_PROMPT_LIMIT:
        if len(current) < 3:
            raise SummaryV2Error(
                "two rescue maps still exceed the staged reduce prompt limit"
            )
        reduced: list[dict[str, Any]] = []
        groups = [current[index : index + 2] for index in range(0, len(current), 2)]
        artifact_root = _rescue_artifact_root(
            output_root,
            "map",
            MAP_RESCUE_REVISION,
            str(job["target_summary_id"]),
        )
        reduction_root = artifact_root / "map-reductions" / f"stage-{stage:03d}"
        reductions = state.setdefault("reductions", {})
        for index, group in enumerate(groups, 1):
            if len(group) == 1:
                reduced.append(group[0])
                continue
            source_refs = [
                message_id
                for sidecar in group
                for message_id in sidecar["source"]["raw_message_ids"]
            ]
            subjob = _rescue_subjob(
                job,
                source_refs,
                stage=stage,
                index=index,
            )
            subformal = build_level_1_source(subjob)
            reduce_source = build_rescue_reduce_source(subformal, group)
            prompt_size = len(build_prompt(reduce_source).encode("utf-8"))
            if prompt_size > REDUCE_PROMPT_LIMIT:
                raise SummaryV2Error(
                    f"intermediate rescue reduce prompt exceeds staged limit: {prompt_size} bytes"
                )
            key = f"stage-{stage:03d}-map-{index:03d}"
            saved = reductions.get(key)
            if saved:
                sidecar = load_sidecar(Path(saved["bundle"]))
                if sidecar["source"]["source_sha256"] != subformal["source_sha256"]:
                    raise SummaryV2Error(f"saved rescue reduction drifted: {key}")
            else:
                result = run_source(
                    reduce_source,
                    reduction_root,
                    archive_root,
                    config_path=config_path,
                    rejected_candidate_path=(
                        artifact_root
                        / "rejected-reduction-candidates"
                        / f"{key}.json"
                    ),
                    diagnostic_path=(
                        artifact_root / "diagnostics" / f"{key}.json"
                    ),
                    invocation_context={
                        "family": "map",
                        "revision": MAP_RESCUE_REVISION,
                        "summary_id": str(job["target_summary_id"]),
                        "stage": key,
                    },
                )
                sidecar = load_sidecar(Path(result["bundle"]))
                reductions[key] = {
                    "bundle": result["bundle"],
                    "source_sha256": subformal["source_sha256"],
                    "projection_sha256": sidecar["projection_sha256"],
                }
                _write_rescue_state(state_path, state)
            reduced.append(sidecar)
        current = reduced
        stage += 1
    return current


def _run_rescue_map_chunk(
    source: dict[str, Any],
    map_root: Path,
    archive_root: Path,
    config_path: Path,
    rejected_candidate_path: Path,
) -> dict[str, Any]:
    diagnostic_path = rejected_candidate_path.with_name(
        rejected_candidate_path.stem + ".diagnostic.json"
    )
    return run_source(
        source,
        map_root,
        archive_root,
        config_path=config_path,
        rejected_candidate_path=rejected_candidate_path,
        diagnostic_path=diagnostic_path,
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


def _failure_path(output_root: Path, summary_id: str) -> Path:
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
        failure_path = _failure_path(output_root, summary_id)
        if failure_path.exists():
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            if (
                int(failure.get("attempts", 0)) >= FAILURE_LIMIT
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
                        "attempts": int(failure["attempts"]),
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
    artifact_root = _rescue_artifact_root(
        output_root, "normal", RUNNER_REVISION, str(task["summary_id"])
    )
    result = run_source(
        source,
        output_root,
        archive_root,
        config_path=config_path,
        rejected_candidate_path=artifact_root / "rejected-candidate.json",
        diagnostic_path=artifact_root / "diagnostic.json",
        invocation_context={
            "family": "normal",
            "revision": RUNNER_REVISION,
            "summary_id": str(task["summary_id"]),
            "stage": "direct",
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
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(ready))) as executor:
            futures = {
                executor.submit(
                    _run_task,
                    task,
                    plan,
                    config_path,
                    existing_roots,
                ): task
                for task in ready
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    completed.append(future.result())
                except Exception as exc:
                    error = str(exc).replace("\r", " ").replace("\n", " ")
                    attempt_status = _classify_failure(exc)
                    failure_path = _failure_path(output_root, task["summary_id"])
                    attempts = 0 if attempt_status == "infra-blocked" else FAILURE_LIMIT if any(
                        marker in error for marker in NON_RETRYABLE_ERRORS
                    ) else 1
                    if failure_path.exists():
                        previous = json.loads(failure_path.read_text(encoding="utf-8"))
                        if (
                            attempts != FAILURE_LIMIT
                            and previous.get("runner_revision") == RUNNER_REVISION
                        ):
                            attempts = int(previous.get("attempts", 0)) + 1
                    failure_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_canonical_json(
                        failure_path,
                        {
                            "summary_id": task["summary_id"],
                            "runner_revision": RUNNER_REVISION,
                            "attempts": attempts,
                            "attempt_status": attempt_status,
                            "last_error": error,
                            "diagnostic": getattr(exc, "diagnostic", None),
                        },
                    )
                    failed.append(
                        {"summary_id": task["summary_id"], "error": error, "attempts": attempts}
                    )
    refreshed = _refresh_plan(plan, existing_roots)
    receipt = {
        "status": "completed" if not failed else "attention",
        "attempted": len(ready),
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


@_single_instance("rescue-direct")
def run_direct_rescue(
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
    _load_sidecars([output_root, *existing_roots])
    _validate_execution_contract(output_root)
    plan_path = output_root / "backfill" / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = _refresh_plan(plan, existing_roots)
    state_path = output_root / "backfill" / "rescue" / "direct-state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {"revision": DIRECT_RESCUE_REVISION, "attempted": []}
    )
    if state.get("revision") != DIRECT_RESCUE_REVISION:
        raise SummaryV2Error("direct rescue state revision changed")
    attempted = set(map(str, state.get("attempted", [])))
    reasons = _quarantine_reason_by_id(plan)
    eligible = sorted(
        (
            task
            for task in plan["tasks"]
            if task["status"] == "quarantined"
            and task.get("job")
            and task["summary_id"] not in attempted
            and _rescue_quarantine_is_eligible(reasons.get(task["summary_id"]))
        ),
        key=lambda item: item["summary_id"],
    )
    ready: list[dict[str, Any]] = []
    skipped_oversized: list[str] = []
    for task in eligible:
        job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
        source = build_level_1_source(job)
        if len(build_prompt(source).encode("utf-8")) > 900_000:
            skipped_oversized.append(task["summary_id"])
            continue
        ready.append(task)
        if len(ready) >= maximum_jobs:
            break

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    if ready:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(ready))) as executor:
            futures = {
                executor.submit(_run_task, task, plan, config_path, existing_roots): task
                for task in ready
            }
            for future in as_completed(futures):
                task = futures[future]
                summary_id = task["summary_id"]
                attempted.add(summary_id)
                try:
                    completed.append(future.result())
                except Exception as exc:
                    error = str(exc).replace("\r", " ").replace("\n", " ")[:500]
                    failure_path = (
                        output_root
                        / "backfill"
                        / "rescue"
                        / "direct-failures"
                        / f"{summary_id}.json"
                    )
                    failure_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_canonical_json(
                        failure_path,
                        {
                            "summary_id": summary_id,
                            "runner_revision": DIRECT_RESCUE_REVISION,
                            "attempts": 1,
                            "last_error": error,
                        },
                    )
                    failed.append({"summary_id": summary_id, "error": error})
    state["attempted"] = sorted(attempted)
    _write_rescue_state(state_path, state)
    refreshed = _refresh_plan(plan, existing_roots)
    receipt = {
        "status": "completed" if not failed else "attention",
        "revision": DIRECT_RESCUE_REVISION,
        "attempted": len(ready),
        "completed": sorted(completed, key=lambda item: item["summary_id"]),
        "failed": sorted(failed, key=lambda item: item["summary_id"]),
        "skipped_oversized": skipped_oversized,
        "remaining_direct_eligible": sum(
            1
            for task in refreshed["tasks"]
            if task["status"] == "quarantined"
            and task.get("job")
            and task["summary_id"] not in attempted
        ),
        "quarantined": len(refreshed["quarantine"]),
    }
    atomic_write_canonical_json(
        output_root / "backfill" / "rescue" / "direct-last-run.json", receipt
    )
    return receipt


def _chunk_job(job: dict[str, Any], target_bytes: int = MAP_PROMPT_TARGET) -> list[dict[str, Any]]:
    records = list(job["source_records"])
    units: list[list[dict[str, Any]]] = []
    current_round: list[dict[str, Any]] = []
    for record in records:
        current_round.append(record)
        if record.get("completes_round") or record.get("complete_round"):
            units.append(current_round)
            current_round = []
    if current_round:
        units.append(current_round)

    def make(records_for_chunk: list[dict[str, Any]], index: int) -> dict[str, Any]:
        ordered = sorted(records_for_chunk, key=lambda item: int(item["sequence"]))
        return {
            "format_version": 1,
            "job_id": f"{job['job_id']}-rescue-map-{index:03d}",
            "target_summary_id": f"{job['target_summary_id']}-map-{index:03d}",
            "summary_level": 1,
            "conversation_id": job["conversation_id"],
            "source_sha256": _source_sha256(ordered),
            "source_message_ids": [item["message_id"] for item in ordered],
            "source_records": ordered,
        }

    def prompt_bytes(records_for_chunk: list[dict[str, Any]], index: int) -> int:
        return len(
            build_prompt(build_level_1_source(make(records_for_chunk, index))).encode("utf-8")
        )

    def split_oversized_unit(
        unit: list[dict[str, Any]], start_index: int
    ) -> list[list[dict[str, Any]]]:
        result: list[list[dict[str, Any]]] = []
        active_records: list[dict[str, Any]] = []
        for record in unit:
            candidate = [*active_records, record]
            if active_records and prompt_bytes(candidate, start_index + len(result)) > target_bytes:
                result.append(active_records)
                active_records = [record]
            else:
                active_records = candidate
            if prompt_bytes(active_records, start_index + len(result)) > 900_000:
                raise SummaryV2Error("one source record exceeds the map prompt limit")
        if active_records:
            result.append(active_records)
        return result

    chunks: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    for unit in units:
        if prompt_bytes(unit, len(chunks) + 1) > target_bytes:
            if active:
                chunks.append(active)
                active = []
            chunks.extend(split_oversized_unit(unit, len(chunks) + 1))
            continue
        candidate_records = [*active, *unit]
        if active and prompt_bytes(candidate_records, len(chunks) + 1) > target_bytes:
            chunks.append(active)
            active = list(unit)
        else:
            active = candidate_records
    if active:
        chunks.append(active)
    if len(chunks) == 1 and len(chunks[0]) > 1:
        midpoint = len(chunks[0]) // 2
        chunks = [chunks[0][:midpoint], chunks[0][midpoint:]]
    return [make(chunk, index) for index, chunk in enumerate(chunks, 1)]


@_single_instance("rescue-map")
def run_map_rescue(
    archive_root: Path,
    output_root: Path,
    config_path: Path,
    existing_roots: Iterable[Path] = (),
    *,
    maximum_jobs: int = 1,
) -> dict[str, Any]:
    if not 1 <= maximum_jobs <= MAX_BATCH:
        raise SummaryV2Error(
            f"map rescue maximum_jobs must be between 1 and {MAX_BATCH}"
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
            _begin_rescue_attempt(state_path, state)
            job = json.loads(Path(task["job"]).read_text(encoding="utf-8"))
            formal_source = build_level_1_source(job)
            _bind_rescue_attempt(
                state_path,
                state,
                formal_source,
                parent=False,
                config_path=config_path,
            )
            chunks = _chunk_job(job)
            map_sidecars_by_key: dict[str, dict[str, Any]] = {}
            artifact_root = _rescue_artifact_root(
                output_root, "map", MAP_RESCUE_REVISION, summary_id
            )
            map_root = artifact_root / "maps"
            _recover_rescue_maps(map_root, chunks, state)
            _write_rescue_state(state_path, state)
            missing: list[tuple[str, dict[str, Any]]] = []
            for index, chunk in enumerate(chunks, 1):
                key = f"map-{index:03d}"
                saved = state["maps"].get(key)
                if saved:
                    sidecar = load_sidecar(Path(saved["bundle"]))
                    if sidecar["source"]["source_sha256"] != chunk["source_sha256"]:
                        raise SummaryV2Error(f"saved rescue map drifted: {key}")
                    map_sidecars_by_key[key] = sidecar
                    continue
                missing.append((key, chunk))
            if missing:
                with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(missing))) as executor:
                    futures = {
                        executor.submit(
                            _run_rescue_map_chunk,
                            build_level_1_source(chunk),
                            map_root,
                            archive_root,
                            config_path,
                            (
                                artifact_root / "rejected-map-candidates" / f"{key}.json"
                            ),
                        ): (key, chunk)
                        for key, chunk in missing
                    }
                    for future in as_completed(futures):
                        key, chunk = futures[future]
                        result = future.result()
                        sidecar = load_sidecar(Path(result["bundle"]))
                        state["maps"][key] = {
                            "bundle": result["bundle"],
                            "source_sha256": chunk["source_sha256"],
                            "projection_sha256": sidecar["projection_sha256"],
                        }
                        map_sidecars_by_key[key] = sidecar
                        _write_rescue_state(state_path, state)
            map_sidecars = [
                map_sidecars_by_key[f"map-{index:03d}"]
                for index in range(1, len(chunks) + 1)
            ]
            map_sidecars = _compact_l1_rescue_maps(
                job,
                formal_source,
                map_sidecars,
                state,
                state_path,
                output_root,
                archive_root,
                config_path,
            )
            rescue_source = build_rescue_reduce_source(formal_source, map_sidecars)
            reduce_bytes = len(build_prompt(rescue_source).encode("utf-8"))
            if reduce_bytes > REDUCE_PROMPT_LIMIT:
                raise SummaryV2Error(
                    f"rescue reduce prompt exceeds staged limit: {reduce_bytes} bytes"
                )
            rejected_candidate_path = artifact_root / "rejected-candidates" / "reduce.json"
            result = run_source(
                rescue_source,
                output_root,
                archive_root,
                config_path=config_path,
                rejected_candidate_path=rejected_candidate_path,
                diagnostic_path=artifact_root / "diagnostics" / "reduce.json",
                invocation_context={
                    "family": "map",
                    "revision": MAP_RESCUE_REVISION,
                    "summary_id": summary_id,
                    "stage": "reduce",
                },
            )
            state["completed"] = result
            state["attempt_status"] = "completed"
            _write_rescue_state(state_path, state)
            completed.append({"summary_id": summary_id, "map_count": len(chunks), **result})
        except Exception as exc:
            error = str(exc).replace("\r", " ").replace("\n", " ")
            state["last_error"] = error
            state["reduce_attempts"] = 1
            state["attempt_status"] = _classify_failure(exc)
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


@_single_instance("rescue-parent")
def run_parent_rescue(
    archive_root: Path,
    output_root: Path,
    config_path: Path,
    existing_roots: Iterable[Path] = (),
    *,
    maximum_jobs: int = 1,
) -> dict[str, Any]:
    if not 1 <= maximum_jobs <= 3:
        raise SummaryV2Error("parent rescue maximum_jobs must be between 1 and 3")
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
            _begin_rescue_attempt(state_path, state)
            if any(child in conflicts or child not in sidecars for child in task["children"]):
                raise SummaryV2Error("parent rescue child sidecars are incomplete or conflicted")
            children = [sidecars[child] for child in task["children"]]
            if len(children) < 4:
                raise SummaryV2Error("parent rescue needs at least four direct children")
            formal_source = build_parent_source(children, parallel_summary_id=summary_id)
            formal_binding_source = dict(formal_source)
            formal_binding_source["compact_parent_prompt"] = True
            _bind_rescue_attempt(
                state_path,
                state,
                formal_binding_source,
                parent=True,
                config_path=config_path,
            )
            groups: list[list[dict[str, Any]]] = []
            active: list[dict[str, Any]] = []
            for child in children:
                candidate = [*active, child]
                if len(candidate) >= 2:
                    probe = build_parent_source(candidate, parallel_summary_id=summary_id + "-probe")
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
                if len(groups[-1]) == 1:
                    groups[-2].extend(groups.pop())
            map_sidecars: list[dict[str, Any]] = []
            artifact_root = _rescue_artifact_root(
                output_root, "parent", PARENT_RESCUE_REVISION, summary_id
            )
            map_root = artifact_root / "maps"
            for index, group in enumerate(groups, 1):
                key = f"map-{index:03d}"
                source = build_parent_source(
                    group,
                    parallel_summary_id=f"{summary_id}-map-{index:03d}",
                )
                source["compact_parent_prompt"] = True
                saved = state["maps"].get(key)
                if saved:
                    sidecar = load_sidecar(Path(saved["bundle"]))
                    if sidecar["source"]["source_sha256"] != source["source_sha256"]:
                        raise SummaryV2Error(f"saved parent rescue map drifted: {key}")
                    map_sidecars.append(sidecar)
                    continue
                result = run_source(
                    source,
                    map_root,
                    archive_root,
                    config_path=config_path,
                    rejected_candidate_path=(
                        artifact_root / "rejected-map-candidates" / f"{key}.json"
                    ),
                    diagnostic_path=artifact_root / "diagnostics" / f"{key}.json",
                    invocation_context={
                        "family": "parent",
                        "revision": PARENT_RESCUE_REVISION,
                        "summary_id": summary_id,
                        "stage": key,
                    },
                )
                sidecar = load_sidecar(Path(result["bundle"]))
                state["maps"][key] = {
                    "bundle": result["bundle"],
                    "source_sha256": source["source_sha256"],
                    "projection_sha256": sidecar["projection_sha256"],
                }
                _write_rescue_state(state_path, state)
                map_sidecars.append(sidecar)
            rescue_source = build_parent_rescue_reduce_source(formal_source, map_sidecars)
            reduce_bytes = len(build_prompt(rescue_source).encode("utf-8"))
            if reduce_bytes > 900_000:
                raise SummaryV2Error(
                    f"parent rescue reduce prompt exceeds staged limit: {reduce_bytes} bytes"
                )
            result = run_source(
                rescue_source,
                output_root,
                archive_root,
                config_path=config_path,
                rejected_candidate_path=(
                    artifact_root / "rejected-parent-candidates" / "reduce.json"
                ),
                diagnostic_path=artifact_root / "diagnostics" / "reduce.json",
                invocation_context={
                    "family": "parent",
                    "revision": PARENT_RESCUE_REVISION,
                    "summary_id": summary_id,
                    "stage": "reduce",
                },
            )
            state["completed"] = result
            state["attempt_status"] = "completed"
            _write_rescue_state(state_path, state)
            completed.append({"summary_id": summary_id, "map_count": len(groups), **result})
        except Exception as exc:
            error = str(exc).replace("\r", " ").replace("\n", " ")
            state["last_error"] = error
            state["attempts"] = 1
            state["attempt_status"] = _classify_failure(exc)
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


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--existing-root", action="append", default=[])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.add_argument("--max-jobs", type=int, default=MAX_BATCH)
    direct_rescue = commands.add_parser("rescue-direct")
    direct_rescue.add_argument("--config", required=True)
    direct_rescue.add_argument("--max-jobs", type=int, default=MAX_BATCH)
    map_rescue = commands.add_parser("rescue-map")
    map_rescue.add_argument("--config", required=True)
    map_rescue.add_argument("--max-jobs", type=int, default=1)
    parent_rescue = commands.add_parser("rescue-parent")
    parent_rescue.add_argument("--config", required=True)
    parent_rescue.add_argument("--max-jobs", type=int, default=1)
    args = parser.parse_args()
    try:
        common = {
            "archive_root": Path(args.archive_root),
            "output_root": Path(args.output_root),
            "existing_roots": [Path(path) for path in args.existing_root],
        }
        if args.command == "plan":
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
        elif args.command == "rescue-direct":
            print(
                json.dumps(
                    run_direct_rescue(
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
