#!/usr/bin/env python3
"""Plan and run bounded historical summary-v2 backfill outside the archive."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from console_encoding import configure_unicode_stdio
from memory_cli import MemoryStore, file_sha256, parse_summary_markdown
from memory_summary_v2 import (
    FORMAT,
    SummaryV2Error,
    build_level_1_source,
    build_parent_source,
    validate_sidecar,
)
from platform_transaction import atomic_write_canonical_json
from summary_v2_worker import load_sidecar, run_source


PLAN_FORMAT = "memory-wuxian-summary-v2-backfill-plan-v1"
MAX_BATCH = 20
MAX_PARALLEL = 3
FAILURE_LIMIT = 3


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
        for path in sorted(root.rglob("summary.json")):
            try:
                sidecar = load_sidecar(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
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
        if item.get("reason") not in {"model-failure-limit", "conflicting-existing-sidecars"}
    ]
    for task in plan["tasks"]:
        summary_id = task["summary_id"]
        if summary_id in conflicts:
            task["status"] = "quarantined"
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
            if int(failure.get("attempts", 0)) >= FAILURE_LIMIT:
                task["status"] = "quarantined"
                quarantine.append(
                    {
                        "summary_id": summary_id,
                        "reason": "model-failure-limit",
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
    result = run_source(
        source,
        output_root,
        archive_root,
        config_path=config_path,
    )
    return {"summary_id": task["summary_id"], **result}


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
        (task for task in plan["tasks"] if task["status"] == "ready"),
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
                    error = str(exc).replace("\r", " ").replace("\n", " ")[:500]
                    failure_path = _failure_path(output_root, task["summary_id"])
                    attempts = 1
                    if failure_path.exists():
                        previous = json.loads(failure_path.read_text(encoding="utf-8"))
                        attempts = int(previous.get("attempts", 0)) + 1
                    failure_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_canonical_json(
                        failure_path,
                        {
                            "summary_id": task["summary_id"],
                            "attempts": attempts,
                            "last_error": error,
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
        else:
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
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"memory-wuxian summary-v2 backfill: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
