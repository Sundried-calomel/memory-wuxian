#!/usr/bin/env python3
"""Run bounded low-frequency Memory Wuxian maintenance without a console UI."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from memory_jobs import write_maintenance_projection
from memory_project_evidence import ProjectEvidenceStore
from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json
from semantic_backfill import run_backfill


def _supervisor_status(result: dict) -> str:
    status = str(result.get("status") or "")
    if (
        result.get("integrity_issues")
        or result.get("repairable_issues")
        or result.get("permanent_failures")
        or (result.get("reconciliation") or {}).get("invalid")
    ):
        return "attention"
    if result.get("skipped") or result.get("remaining_pending_jobs"):
        return "catching-up"
    if status in {"catching-up", "attention"}:
        return status
    return "healthy"


DEFAULT_MAXIMUM_SEMANTIC_JOBS = 8


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_supervisor_tick_unlocked(
    root: Path,
    config_path: Path,
    *,
    maximum_semantic_jobs: int = DEFAULT_MAXIMUM_SEMANTIC_JOBS,
) -> dict:
    pending_before = len(list((root / "pending").glob("job-*.json")))
    started_at = _timestamp()
    started = time.monotonic()
    result = run_backfill(
        root.resolve(),
        config_path.resolve(),
        max_jobs=maximum_semantic_jobs,
        dry_run=False,
    )
    finished_at = _timestamp()
    pending_after = int(result.get("remaining_pending_jobs") or 0)
    completed_jobs = int(result.get("completed_jobs") or 0)
    scheduled_jobs = len(result.get("scheduled_summary_jobs") or [])
    timing = result.get("timing") if isinstance(result.get("timing"), dict) else {}
    semantic_timing = (
        timing.get("semantic")
        if isinstance(timing.get("semantic"), dict)
        else {}
    )
    project_evidence = ProjectEvidenceStore(root).refresh_owners(
        maximum_owners=20,
        apply=True,
    )
    return {
        "format": "memory-wuxian-maintenance-supervisor-state-v1",
        "status": _supervisor_status(result),
        "process_id": os.getpid(),
        "result": result,
        "throughput": {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(max(0.0, time.monotonic() - started), 3),
            "batch_limit": maximum_semantic_jobs,
            "pending_before": pending_before,
            "pending_after": pending_after,
            "completed_jobs": completed_jobs,
            "scheduled_jobs": scheduled_jobs,
            "net_pending_change": pending_after - pending_before,
            "parallel_model_limit": int(result.get("parallel_model_limit") or 1),
            "recovery_seconds": float(timing.get("recovery_seconds") or 0.0),
            "semantic_dispatch_seconds": float(
                timing.get("semantic_dispatch_seconds") or 0.0
            ),
            "average_model_seconds": float(
                semantic_timing.get("average_model_seconds") or 0.0
            ),
            "maximum_model_seconds": float(
                semantic_timing.get("maximum_model_seconds") or 0.0
            ),
        },
        "project_evidence_owners": project_evidence,
    }


def run_supervisor_tick(
    root: Path,
    config_path: Path,
    *,
    maximum_semantic_jobs: int = DEFAULT_MAXIMUM_SEMANTIC_JOBS,
) -> dict:
    if maximum_semantic_jobs < 1 or maximum_semantic_jobs > DEFAULT_MAXIMUM_SEMANTIC_JOBS:
        raise ValueError("maximum_semantic_jobs must be between 1 and 8")
    root = root.resolve()
    with exclusive_lock(root / ".locks" / "maintenance-supervisor.lock"):
        return _run_supervisor_tick_unlocked(
            root,
            config_path,
            maximum_semantic_jobs=maximum_semantic_jobs,
        )


def run_supervisor(
    root: Path,
    config_path: Path,
    *,
    interval_seconds: int = 300,
    maximum_semantic_jobs: int = DEFAULT_MAXIMUM_SEMANTIC_JOBS,
    maximum_cycles: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if not 30 <= interval_seconds <= 86400:
        raise ValueError("interval_seconds must be between 30 and 86400")
    if maximum_cycles < 0:
        raise ValueError("maximum_cycles must be zero or greater")
    root = root.resolve()
    state_path = root / "maintenance" / "supervisor-state.json"
    cycle = 0
    while maximum_cycles == 0 or cycle < maximum_cycles:
        cycle += 1
        try:
            state = run_supervisor_tick(
                root,
                config_path,
                maximum_semantic_jobs=maximum_semantic_jobs,
            )
            state["cycle"] = cycle
            atomic_write_canonical_json(state_path, state)
        except Exception as exc:
            write_maintenance_projection(root)
            atomic_write_canonical_json(
                state_path,
                {
                    "format": "memory-wuxian-maintenance-supervisor-state-v1",
                    "status": "error",
                    "process_id": os.getpid(),
                    "cycle": cycle,
                    "error": str(exc).replace("\r", " ").replace("\n", " ")[:500],
                },
            )
        if maximum_cycles and cycle >= maximum_cycles:
            break
        sleep(interval_seconds)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument(
        "--max-semantic-jobs",
        type=int,
        default=DEFAULT_MAXIMUM_SEMANTIC_JOBS,
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the final state for interactive diagnostics")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    result = run_supervisor(
        root,
        Path(args.config).expanduser().resolve(),
        interval_seconds=args.interval_seconds,
        maximum_semantic_jobs=args.max_semantic_jobs,
        maximum_cycles=1 if args.once else 0,
    )
    if args.json:
        state = root / "maintenance" / "supervisor-state.json"
        print(json.dumps(json.loads(state.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
