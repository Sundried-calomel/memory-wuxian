#!/usr/bin/env python3
"""Drain bounded Memory Wuxian semantic-summary debt."""

import argparse
import json
from pathlib import Path

from memory_cli import MemoryStore, load_simple_yaml, now_iso
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
    completed = []

    while max_jobs == 0 or len(completed) < max_jobs:
        pending = ordered_pending_jobs(store)
        job_path = pending[0] if pending else store.make_summary_job()
        if job_path is None:
            break
        result = dispatch_job(
            root,
            config_path,
            job_path,
            dry_run=dry_run,
            create_backup=False,
        )
        completed.append(result)
        if dry_run or result.get("status") in {"deferred", "quarantined"}:
            break

    backup = None
    backup_debt_drained = False
    if not dry_run and store.backup_debt_path.exists():
        backup = store.drain_backup_debt()
        backup_debt_drained = backup is not None
    elif completed and not dry_run:
        backup = store.create_backup_snapshot(
            "semantic-backfill-batch",
            {
                "completed_jobs": len(completed),
                "job_ids": [item["job_id"] for item in completed],
            },
        )
    return {
        "status": "dry-run" if dry_run else "completed",
        "timestamp": now_iso(),
        "completed_jobs": len(completed),
        "job_ids": [item["job_id"] for item in completed],
        "backup": str(backup) if backup else None,
        "backup_debt_drained": backup_debt_drained,
        "remaining_pending_jobs": len(store.pending_jobs()),
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
