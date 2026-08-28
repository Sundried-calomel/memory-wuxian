#!/usr/bin/env python3
"""Drain bounded Memory Wuxian semantic-summary debt."""

import argparse
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from memory_cli import MemoryStore, load_simple_yaml, nested_get, now_iso
from memory_jobs import (
    MaintenanceQueue,
    commit_backup_debt_generation,
    read_backup_debt_generation,
    reconcile_pending_debt,
    redact_error,
    stable_path_identity,
    write_maintenance_projection,
)
from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json
from semantic_dispatch import dispatch_job


DEFAULT_RECOVERY_AUDIT_INTERVAL_SECONDS = 86400
DEFAULT_MAXIMUM_PARALLEL_MODEL_CALLS = 3
MAXIMUM_PARALLEL_MODEL_CALLS = 3
BATCH_STATE_FORMAT = "memory-wuxian-semantic-batch-state-v1"


def _batch_state_path(root: Path) -> Path:
    return root / "maintenance" / "semantic-batch-state.json"


def _write_batch_state(
    root: Path,
    *,
    batch_id: str,
    stage: str,
    selected_jobs: int,
    finished_jobs: int,
    failed_jobs: int,
    timing: dict[str, float],
) -> None:
    atomic_write_canonical_json(
        _batch_state_path(root),
        {
            "format": BATCH_STATE_FORMAT,
            "batch_id": batch_id,
            "stage": stage,
            "updated_at": now_iso(),
            "selected_jobs": selected_jobs,
            "finished_jobs": finished_jobs,
            "failed_jobs": failed_jobs,
            "timing": {
                key: round(max(0.0, value), 3) for key, value in timing.items()
            },
        },
    )


def _file_generation(path: Path) -> tuple[int, int, int, int, str] | None:
    try:
        payload = path.read_bytes()
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        hashlib.sha256(payload).hexdigest(),
    )


def _bounded_parallelism(config: dict, job_count: int, dry_run: bool) -> int:
    configured = int(
        nested_get(
            config,
            ["ai_summary", "maximum_parallel_model_calls"],
            DEFAULT_MAXIMUM_PARALLEL_MODEL_CALLS,
        )
    )
    if configured < 1 or configured > MAXIMUM_PARALLEL_MODEL_CALLS:
        raise ValueError("ai_summary.maximum_parallel_model_calls must be between 1 and 3")
    return 1 if dry_run else max(1, min(configured, job_count))


def _recovery_audit_interval(config: dict) -> int:
    interval = int(
        nested_get(
            config,
            ["maintenance", "full_recovery_audit_interval_seconds"],
            DEFAULT_RECOVERY_AUDIT_INTERVAL_SECONDS,
        )
    )
    if interval < 3600 or interval > 604800:
        raise ValueError(
            "maintenance.full_recovery_audit_interval_seconds must be between 3600 and 604800"
        )
    return interval


def _timing_totals(results: list[dict]) -> dict:
    fields = (
        "local_preparation_seconds",
        "model_seconds",
        "validation_seconds",
        "ingestion_seconds",
        "total_seconds",
    )
    totals = {
        field: round(
            sum(float((item.get("timing") or {}).get(field) or 0.0) for item in results),
            3,
        )
        for field in fields
    }
    model_values = [
        float((item.get("timing") or {}).get("model_seconds") or 0.0)
        for item in results
    ]
    return {
        "completed_jobs": len(results),
        "totals": totals,
        "average_model_seconds": round(
            sum(model_values) / len(model_values), 3
        ) if model_values else 0.0,
        "maximum_model_seconds": round(max(model_values), 3) if model_values else 0.0,
    }


def recent_recovery_audit(
    root: Path,
    *,
    maximum_age_seconds: int = DEFAULT_RECOVERY_AUDIT_INTERVAL_SECONDS,
    current_time: datetime | None = None,
) -> dict | None:
    state_path = root / "maintenance" / "supervisor-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        recovery = state["result"]["native_recovery"]
        timestamp = datetime.fromisoformat(str(recovery["timestamp"]).replace("Z", "+00:00"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    now = current_time or datetime.now(timezone.utc)
    age_seconds = (now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    if age_seconds < 0 or age_seconds > maximum_age_seconds:
        return None
    return {**recovery, "mode": "cached-recovery-audit", "repairs": []}


def ordered_pending_jobs(store: MemoryStore) -> list[Path]:
    jobs = store.pending_jobs()
    jobs.sort(
        key=lambda item: (
            -int(item.get("summary_level", 1)),
            str(item.get("created_at", "")),
            str(item.get("job_id", "")),
        )
    )
    return [Path(item["_path"]) for item in jobs]


def run_backfill(
    root: Path,
    config_path: Path,
    max_jobs: int,
    dry_run: bool,
) -> dict:
    if max_jobs < 0:
        raise ValueError("--max-jobs must be zero or greater")
    config = load_simple_yaml(config_path)
    store = MemoryStore(root, config)
    store.init()
    batch_started = time.monotonic()
    batch_id = f"semantic-batch-{os.getpid()}-{time.time_ns()}"
    stage_timing = {
        "snapshot_seconds": 0.0,
        "semantic_dispatch_seconds": 0.0,
        "finalize_seconds": 0.0,
        "scheduling_seconds": 0.0,
        "backup_seconds": 0.0,
    }
    recovery_debt_path = root / "pending" / "native-recovery-debt.json"
    with exclusive_lock(root / ".locks" / "archive.lock"):
        recovery_debt_generation = _file_generation(recovery_debt_path)
    recovery = None
    recovery_started = time.monotonic()
    if not dry_run:
        recovery = (
            None
            if recovery_debt_path.exists()
            else recent_recovery_audit(
                root,
                maximum_age_seconds=_recovery_audit_interval(config),
            )
        )
        if recovery is None:
            recovery = store.heartbeat(create_jobs=False, repair=True)
        if recovery_debt_generation is not None and recovery.get("status") == "ok":
            with exclusive_lock(root / ".locks" / "archive.lock"):
                if _file_generation(recovery_debt_path) == recovery_debt_generation:
                    recovery_debt_path.unlink(missing_ok=True)
    recovery_seconds = time.monotonic() - recovery_started
    integrity_issues = list((recovery or {}).get("integrity_issues") or [])
    repairable_issues = list((recovery or {}).get("repairable_issues") or [])
    dispatch_blocked = bool(integrity_issues)
    scheduling_blocked = bool(integrity_issues or repairable_issues)
    queue = MaintenanceQueue(root)
    scheduled = []
    if not scheduling_blocked:
        due_job = store.make_summary_job()
        if due_job is not None:
            scheduled.append(str(due_job))
    reconciliation = reconcile_pending_debt(root, queue)
    queue.mark_semantic_ready_bulk(maximum_jobs=10000)
    completed: list[dict] = []
    skipped = []
    pending = ordered_pending_jobs(store)
    maintenance_by_path = {
        stable_path_identity(job["payload"].get("summary_job", "")): job
        for job in queue.jobs()
        if job["kind"] == "semantic-summary-eligibility"
        and job["payload"].get("summary_job")
    }
    limit = len(pending) if max_jobs == 0 else max_jobs
    if dispatch_blocked:
        skipped.append({
            "reason": "integrity-failure",
            "error": redact_error(
                "; ".join(str(item) for item in integrity_issues)
            ),
        })
    selected: list[Path] = []
    for job_path in ([] if dispatch_blocked else pending):
        if len(selected) >= limit:
            break
        maintenance = maintenance_by_path.get(stable_path_identity(job_path))
        if maintenance is None:
            skipped.append({"job": str(job_path), "reason": "not-reconciled"})
            continue
        if maintenance["state"] in {"completed", "quarantined", "running"}:
            skipped.append({"job": str(job_path), "reason": maintenance["state"]})
            continue
        selected.append(job_path)
        if dry_run:
            break

    snapshot_started = time.monotonic()
    source_snapshot = None
    snapshot_builder = getattr(store, "build_summary_source_snapshot", None)
    if selected and not dry_run and callable(snapshot_builder):
        source_snapshot = snapshot_builder()
    stage_timing["snapshot_seconds"] = time.monotonic() - snapshot_started
    _write_batch_state(
        root,
        batch_id=batch_id,
        stage="dispatching" if selected else "idle",
        selected_jobs=len(selected),
        finished_jobs=0,
        failed_jobs=0,
        timing=stage_timing,
    )

    dispatch_started = time.monotonic()
    results_by_path: dict[Path, dict | BaseException] = {}
    dispatched = 0
    dispatch_failures = 0
    if selected:
        parallelism = _bounded_parallelism(config, len(selected), dry_run)
        with ThreadPoolExecutor(
            max_workers=parallelism,
            thread_name_prefix="memory-wuxian-semantic",
        ) as executor:
            futures = {
                executor.submit(
                    dispatch_job,
                    root,
                    config_path,
                    job_path,
                    dry_run=dry_run,
                    create_backup=False,
                    check_availability=not dry_run,
                    source_snapshot=source_snapshot,
                    defer_derived_updates=not dry_run,
                ): job_path
                for job_path in selected
            }
            for future in as_completed(futures):
                job_path = futures[future]
                try:
                    results_by_path[job_path] = future.result()
                except Exception as exc:
                    results_by_path[job_path] = exc
                    dispatch_failures += 1
                dispatched += 1
                stage_timing["semantic_dispatch_seconds"] = (
                    time.monotonic() - dispatch_started
                )
                _write_batch_state(
                    root,
                    batch_id=batch_id,
                    stage="dispatching",
                    selected_jobs=len(selected),
                    finished_jobs=dispatched,
                    failed_jobs=dispatch_failures,
                    timing=stage_timing,
                )
    dispatch_seconds = time.monotonic() - dispatch_started
    stage_timing["semantic_dispatch_seconds"] = dispatch_seconds

    for job_path in selected:
        result = results_by_path[job_path]
        if isinstance(result, BaseException):
            skipped.append({
                "job": str(job_path),
                "reason": "dispatch-failed",
                "error": redact_error(result),
            })
            continue
        if result.get("status") == "unavailable":
            skipped.append({
                "job": str(job_path),
                "reason": "runtime-unavailable",
                "error": redact_error(str(result.get("reason") or "Codex runtime is unavailable")),
            })
            continue
        if result.get("status") in {"deferred", "quarantined"}:
            skipped.append({
                "job": str(job_path),
                "reason": str(result["status"]),
                **({"error": redact_error(result["error"])} if result.get("error") else {}),
            })
            continue
        completed.append(result)

    finalize_result = None
    finalize_started = time.monotonic()
    deferred_results = [
        result for result in completed if result.get("derived_updates_deferred")
    ]
    if deferred_results and not dry_run:
        finalizer = getattr(store, "finalize_summary_batch", None)
        if not callable(finalizer):
            raise RuntimeError(
                "Deferred summary ingestion requires MemoryStore.finalize_summary_batch"
            )
        _write_batch_state(
            root,
            batch_id=batch_id,
            stage="finalizing",
            selected_jobs=len(selected),
            finished_jobs=len(completed),
            failed_jobs=len(selected) - len(completed),
            timing=stage_timing,
        )
        finalize_result = finalizer(completed)
    stage_timing["finalize_seconds"] = time.monotonic() - finalize_started

    scheduling_started = time.monotonic()
    if (
        any(result.get("status") == "ingested" for result in completed)
        and not scheduling_blocked
    ):
        due_job = store.make_summary_job()
        if due_job is not None:
            scheduled.append(str(due_job))
    stage_timing["scheduling_seconds"] = time.monotonic() - scheduling_started

    backup = None
    backup_debt_drained = False
    backup_started = time.monotonic()
    if not dry_run:
        owner = f"semantic-backfill-backup:{os.getpid()}"
        for _ in range(100):
            backup_job = queue.claim(owner, kinds={"backup-debt"})
            if backup_job is None:
                break
            expected = str(backup_job["payload"]["debt_sha256"])
            current = read_backup_debt_generation(root)
            if current is None or current["debt_sha256"] != expected:
                queue.complete(
                    backup_job["job_id"], owner,
                    {"status": "superseded", "debt_sha256": expected},
                )
                continue
            try:
                backup = store.create_backup_snapshot(
                    "coalesced-archive-mutations",
                    {"backup_debt": current["debt"], "debt_sha256": expected},
                )
                if backup is None:
                    raise RuntimeError("Backup is not configured")
                backup_debt_drained = commit_backup_debt_generation(root, expected)
                queue.complete(
                    backup_job["job_id"], owner,
                    {
                        "status": "snapshotted",
                        "snapshot": str(backup),
                        "debt_sha256": expected,
                        "debt_cleared": backup_debt_drained,
                    },
                )
            except Exception as exc:
                queue.fail(backup_job["job_id"], owner, exc, retry_delay_seconds=300)
            break
    stage_timing["backup_seconds"] = time.monotonic() - backup_started
    remaining_pending_jobs = len(store.pending_jobs())
    queue_status = queue.status()
    permanent_failures = queue_status["quarantined"]
    if dry_run:
        status = "dry-run"
    elif integrity_issues or repairable_issues or reconciliation["invalid"] or permanent_failures:
        status = "attention"
    elif skipped or remaining_pending_jobs or read_backup_debt_generation(root) is not None:
        status = "catching-up"
    else:
        status = "completed"
    projection = write_maintenance_projection(root, queue)
    semantic_timing = _timing_totals(completed)
    batch_seconds = time.monotonic() - batch_started
    _write_batch_state(
        root,
        batch_id=batch_id,
        stage="completed",
        selected_jobs=len(selected),
        finished_jobs=len(completed),
        failed_jobs=len(selected) - len(completed),
        timing={**stage_timing, "batch_seconds": batch_seconds},
    )
    return {
        "status": status,
        "timestamp": now_iso(),
        "completed_jobs": len(completed),
        "attempted_jobs": len(selected),
        "parallel_model_limit": _bounded_parallelism(config, max(1, len(selected)), dry_run),
        "job_ids": [item["job_id"] for item in completed],
        "batch_id": batch_id,
        "source_snapshot_built": source_snapshot is not None,
        "finalize_result": finalize_result,
        "timing": {
            "recovery_seconds": round(recovery_seconds, 3),
            "semantic_dispatch_seconds": round(dispatch_seconds, 3),
            "snapshot_seconds": round(stage_timing["snapshot_seconds"], 3),
            "finalize_seconds": round(stage_timing["finalize_seconds"], 3),
            "scheduling_seconds": round(stage_timing["scheduling_seconds"], 3),
            "backup_seconds": round(stage_timing["backup_seconds"], 3),
            "batch_seconds": round(batch_seconds, 3),
            "semantic": semantic_timing,
        },
        "backup": str(backup) if backup else None,
        "backup_debt_drained": backup_debt_drained,
        "remaining_pending_jobs": remaining_pending_jobs,
        "scheduled_summary_jobs": scheduled,
        "permanent_failures": permanent_failures,
        "integrity_issues": integrity_issues,
        "repairable_issues": repairable_issues,
        "native_recovery": recovery,
        "reconciliation": reconciliation,
        "skipped": skipped,
        "status_projection": str(projection),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=20,
        help="Maximum jobs for this run; zero drains all currently due work",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_backfill(
        Path(args.root).expanduser().resolve(),
        Path(args.config).expanduser().resolve(),
        args.max_jobs,
        args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
