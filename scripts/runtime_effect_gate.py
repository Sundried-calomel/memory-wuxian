#!/usr/bin/env python3
"""Fail closed when enabled Memory Wuxian background effects are not real."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from memory_cli import MemoryStore, load_simple_yaml, read_jsonl
from memory_guarded_features import raw_source_snapshot


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def semantic_parent_debt(store: MemoryStore) -> list[dict[str, Any]]:
    trigger = int(store.config.get("summaries", {}).get("higher_level_trigger_count", 10))
    maximum_depth = int(store.config.get("summaries", {}).get("maximum_summary_depth", 4))
    summaries = store.summary_records()
    registry = store.summary_registry()
    grouped = {
        str(item["child_summary_id"])
        for item in registry
        if item.get("event") == "grouped" and item.get("child_summary_id")
    }
    pending_signatures = {
        str(item.get("source_signature")) for item in store.pending_jobs()
    }
    debt = []
    for level in range(1, maximum_depth):
        conversations = sorted({
            str(item.get("conversation_id"))
            for item in summaries
            if int(item.get("level", 0)) == level and item.get("conversation_id")
        })
        for conversation_id in conversations:
            candidates = sorted(
                (
                    item for item in summaries
                    if int(item.get("level", 0)) == level
                    and item.get("conversation_id") == conversation_id
                    and item.get("summary_id") not in grouped
                ),
                key=lambda item: (
                    int(item.get("source_start_sequence") or 0),
                    str(item.get("summary_id")),
                ),
            )
            if len(candidates) < trigger:
                continue
            children = candidates[:trigger]
            signature = (
                f"conversation:{conversation_id}:children:"
                + ",".join(str(item["summary_id"]) for item in children)
            )
            if signature not in pending_signatures:
                debt.append({
                    "conversation_id": conversation_id,
                    "source_level": level,
                    "child_count": len(candidates),
                    "expected_signature": signature,
                })
    return debt


def check_runtime_effects(
    root: Path,
    config_path: Path,
    *,
    now: float | None = None,
    supervisor_max_age_seconds: int = 900,
) -> dict[str, Any]:
    root = root.resolve()
    config = load_simple_yaml(config_path.resolve())
    store = MemoryStore(root, config)
    failures: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}

    raw = store.read_all_raw()
    conversation_index = read_jsonl(store.index_dir / "conversations.jsonl")
    raw_ids = [str(item.get("message_id")) for item in raw]
    index_ids = [str(item.get("message_id")) for item in conversation_index]
    observations["conversation_index"] = {
        "raw_records": len(raw_ids),
        "indexed_records": len(index_ids),
    }
    if raw_ids != index_ids:
        failures.append({"code": "conversation-index-not-converged"})

    parent_debt = semantic_parent_debt(store)
    pending_parent_jobs = [
        {
            "job_id": item.get("job_id"),
            "summary_level": item.get("summary_level"),
            "source_signature": item.get("source_signature"),
        }
        for item in store.pending_jobs()
        if int(item.get("summary_level", 0)) > 1
    ]
    observations["semantic_parent_debt"] = parent_debt
    observations["pending_parent_jobs"] = pending_parent_jobs
    if parent_debt and not pending_parent_jobs:
        failures.append({"code": "semantic-parent-job-missing", "count": len(parent_debt)})

    semantic_manifest_path = store.index_dir / "semantic" / "manifest.json"
    if semantic_manifest_path.is_file():
        manifest = load_json(semantic_manifest_path)
        current_source = raw_source_snapshot(raw)
        observations["semantic_index"] = {
            "indexed": manifest.get("raw_source"),
            "current": current_source,
        }
        if manifest.get("raw_source") != current_source:
            failures.append({"code": "semantic-index-stale"})

    backup_root = store.configured_backup_root()
    incomplete = []
    if backup_root and backup_root.is_dir():
        temporary_pattern = re.compile(
            r"^\.\d{4}-\d{2}-\d{2}_\d{6}_\d{6}\.tmp-\d+$"
        )
        incomplete = sorted(
            path.name for path in backup_root.glob(".*.tmp-*")
            if temporary_pattern.fullmatch(path.name)
            and path.is_dir()
            and not path.is_symlink()
        )
    observations["incomplete_backups"] = incomplete
    if incomplete:
        failures.append({"code": "incomplete-backup-residue", "count": len(incomplete)})

    maintenance_jobs = []
    jobs_root = root / "maintenance" / "jobs"
    if jobs_root.is_dir():
        for path in sorted(jobs_root.rglob("*.json")):
            item = load_json(path)
            state = str(item.get("state", ""))
            if state in {"quarantined", "permanent-failure", "failed", "invalid"}:
                maintenance_jobs.append({"job_id": item.get("job_id"), "state": state})
    observations["permanent_maintenance_debt"] = maintenance_jobs
    if maintenance_jobs:
        failures.append({"code": "permanent-maintenance-debt", "count": len(maintenance_jobs)})

    supervisor = root / "maintenance" / "supervisor-state.json"
    current_time = time.time() if now is None else float(now)
    supervisor_age = current_time - supervisor.stat().st_mtime if supervisor.is_file() else None
    supervisor_state = load_json(supervisor)
    observations["supervisor"] = {
        "status": supervisor_state.get("status"),
        "age_seconds": supervisor_age,
    }
    if (
        supervisor_age is None
        or supervisor_age > supervisor_max_age_seconds
        or supervisor_state.get("status") not in {"healthy", "catching-up"}
    ):
        failures.append({"code": "maintenance-supervisor-not-healthy"})

    return {
        "format": "memory-wuxian-runtime-effect-gate-v1",
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--supervisor-max-age-seconds", type=int, default=900)
    args = parser.parse_args()
    result = check_runtime_effects(
        Path(args.root),
        Path(args.config),
        supervisor_max_age_seconds=args.supervisor_max_age_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
