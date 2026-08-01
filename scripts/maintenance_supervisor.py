#!/usr/bin/env python3
"""Run bounded low-frequency Memory Wuxian maintenance without a console UI."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Callable

from memory_jobs import write_maintenance_projection
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


def run_supervisor_tick(root: Path, config_path: Path, *, maximum_semantic_jobs: int = 4) -> dict:
    if maximum_semantic_jobs < 1 or maximum_semantic_jobs > 20:
        raise ValueError("maximum_semantic_jobs must be between 1 and 20")
    result = run_backfill(
        root.resolve(),
        config_path.resolve(),
        max_jobs=maximum_semantic_jobs,
        dry_run=False,
    )
    return {
        "format": "memory-wuxian-maintenance-supervisor-state-v1",
        "status": _supervisor_status(result),
        "process_id": os.getpid(),
        "result": result,
    }


def run_supervisor(
    root: Path,
    config_path: Path,
    *,
    interval_seconds: int = 300,
    maximum_semantic_jobs: int = 4,
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
    parser.add_argument("--max-semantic-jobs", type=int, default=4)
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
