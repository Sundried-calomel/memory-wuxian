#!/usr/bin/env python3
"""Drain bounded Memory Wuxian semantic-summary debt."""

import argparse
import json
import os
from pathlib import Path

from memory_cli import MemoryStore, load_simple_yaml, now_iso
from memory_jobs import (
    MaintenanceQueue,
    commit_backup_debt_generation,
    read_backup_debt_generation,
    reconcile_pending_debt,
    redact_error,
    write_maintenance_projection,
)
from semantic_dispatch import dispatch_job


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
    recovery_debt_path = root / "pending" / "native-recovery-debt.json"
    recovery = None
    if recovery_debt_path.exists() and not dry_run:
        recovery = store.heartbeat(create_jobs=False, repair=True)
        if recovery.get("status") == "ok":
            recovery_debt_path.unlink(missing_ok=True)
    queue = MaintenanceQueue(root)
    reconciliation = reconcile_pending_debt(root, queue)
    queue.mark_semantic_ready_bulk(maximum_jobs=10000)
    completed = []
    skipped = []
    attempted = 0
    pending = ordered_pending_jobs(store)
    maintenance_by_path = {
        str(Path(job["payload"].get("summary_job", "")).resolve()): job
        for job in queue.jobs()
        if job["kind"] == "semantic-summary-eligibility"
        and job["payload"].get("summary_job")
    }
    limit = len(pending) if max_jobs == 0 else max_jobs
    for job_path in pending:
        if attempted >= limit:
            break
        maintenance = maintenance_by_path.get(str(job_path.resolve()))
        if maintenance is None:
            skipped.append({"job": str(job_path), "reason": "not-reconciled"})
            continue
        if maintenance["state"] in {"completed", "quarantined", "running"}:
            skipped.append({"job": str(job_path), "reason": maintenance["state"]})
            continue
        attempted += 1
        try:
            result = dispatch_job(
                root,
                config_path,
                job_path,
                dry_run=dry_run,
                create_backup=False,
                check_availability=not dry_run,
            )
        except Exception as exc:
            skipped.append({"job": str(job_path), "reason": "dispatch-failed", "error": redact_error(exc)})
            continue
        if result.get("status") == "unavailable":
            skipped.append({"job": str(job_path), "reason": "runtime-unavailable"})
            break
        if result.get("status") in {"deferred", "quarantined"}:
            skipped.append({"job": str(job_path), "reason": str(result["status"])})
            continue
        completed.append(result)
        if dry_run:
            break

    backup = None
    backup_debt_drained = False
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
    projection = write_maintenance_projection(root, queue)
    return {
        "status": "dry-run" if dry_run else "completed",
        "timestamp": now_iso(),
        "completed_jobs": len(completed),
        "attempted_jobs": attempted,
        "job_ids": [item["job_id"] for item in completed],
        "backup": str(backup) if backup else None,
        "backup_debt_drained": backup_debt_drained,
        "remaining_pending_jobs": len(store.pending_jobs()),
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
