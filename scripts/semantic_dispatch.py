#!/usr/bin/env python3
"""Persist eligibility, then run one explicitly eligible semantic summary job."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

from memory_cli import append_jsonl, load_simple_yaml, nested_get, now_iso
from memory_jobs import MaintenanceQueue, redact_error, semantic_eligibility_payload
from platform_lock import exclusive_lock
from platform_process import no_window_kwargs
from semantic_worker import run_job


def eligibility_payload(job_path: Path) -> Dict[str, Any]:
    return semantic_eligibility_payload(job_path)


def codex_runtime_available(config_path: Path, *, timeout_seconds: int = 10) -> tuple[bool, str]:
    """Check local Codex presence and authentication without invoking a model."""
    config = load_simple_yaml(config_path)
    codex_key = "codex_cli_path_windows" if os.name == "nt" else "codex_cli_path"
    configured = str(nested_get(config, ["ai_summary", codex_key], "codex.exe" if os.name == "nt" else "codex"))
    expanded = Path(configured).expanduser()
    executable = shutil.which(configured) or (str(expanded) if expanded.is_file() else None)
    if not executable:
        return False, "Codex CLI executable is unavailable"
    try:
        completed = subprocess.run(
            [str(executable), "login", "status"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Codex availability probe failed: {exc}"
    if completed.returncode != 0:
        detail = redact_error((completed.stderr or completed.stdout or "not logged in").strip()[-500:])
        return False, f"Codex is unavailable: {detail}"
    return True, "available"


def dispatch_job(
    root: Path,
    config_path: Path,
    job_path: Path,
    *,
    dry_run: bool = False,
    create_backup: bool = False,
    retry_delay_seconds: int = 60,
    check_availability: bool = False,
    availability_probe=codex_runtime_available,
) -> Dict[str, Any]:
    root = root.resolve()
    job_path = job_path.resolve()
    payload = eligibility_payload(job_path)
    queue = MaintenanceQueue(root)
    queued = queue.enqueue(
        "semantic-summary-eligibility",
        f"summary:{payload['source_signature']}",
        payload,
        max_attempts=4,
    )
    if queued["state"] in {"queued", "retry"}:
        queue.mark_semantic_ready_bulk(maximum_jobs=10000)
    current = next(job for job in queue.jobs() if job["job_id"] == queued["job_id"])
    if current["state"] == "completed":
        return {"status": "already-completed", "maintenance_job_id": current["job_id"]}
    if current["state"] == "quarantined":
        return {"status": "quarantined", "maintenance_job_id": current["job_id"]}
    if check_availability:
        available, reason = availability_probe(config_path)
        if not available:
            return {
                "status": "unavailable",
                "reason": reason,
                "maintenance_job_id": current["job_id"],
                "ai_invocations": 0,
            }
    owner = f"semantic-dispatch:{os.getpid()}"
    claimed = queue.claim_semantic(current["job_id"], owner)
    if claimed is None:
        return {"status": "deferred", "maintenance_job_id": current["job_id"]}
    try:
        config = load_simple_yaml(config_path)
        configured_worker = Path(
            str(nested_get(config, ["ai_summary", "worker_path"], "scripts/semantic_worker.py"))
        )
        if not configured_worker.is_absolute():
            configured_worker = config_path.parent / configured_worker
        bundled_worker = Path(__file__).with_name("semantic_worker.py").resolve()
        if configured_worker.resolve() == bundled_worker:
            with exclusive_lock(root / ".locks/semantic-worker.lock"):
                result = run_job(
                    root,
                    config_path.resolve(),
                    job_path,
                    dry_run=dry_run,
                    create_backup=create_backup,
                )
        else:
            command = [
                __import__("sys").executable,
                str(configured_worker.resolve()),
                "--root", str(root),
                "--config", str(config_path.resolve()),
                "--job", str(job_path),
            ]
            if dry_run:
                command.append("--dry-run")
            if not create_backup:
                command.append("--no-backup")
            completed = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                **no_window_kwargs(),
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    completed.stderr[-2000:] or "configured semantic worker failed"
                )
            try:
                result = json.loads(completed.stdout.strip())
            except json.JSONDecodeError:
                result = {
                    "status": "completed",
                    "worker_output_sha256": __import__("hashlib").sha256(
                        completed.stdout.encode("utf-8")
                    ).hexdigest(),
                }
        if dry_run:
            queue.fail_semantic(current["job_id"], owner, "dry run", retry_delay_seconds=0)
            return {**result, "maintenance_job_id": current["job_id"], "ai_invocations": 0}
        queue.complete(current["job_id"], owner, result)
        return {**result, "maintenance_job_id": current["job_id"], "ai_invocations": 1}
    except Exception as exc:
        if check_availability:
            available, reason = availability_probe(config_path)
            if not available:
                queue.defer_semantic(
                    current["job_id"], owner, reason, retry_delay_seconds=retry_delay_seconds
                )
                return {
                    "status": "unavailable",
                    "reason": redact_error(reason),
                    "maintenance_job_id": current["job_id"],
                    "ai_invocations": 0,
                }
        failed = queue.fail_semantic(
            current["job_id"], owner, exc, retry_delay_seconds=retry_delay_seconds
        )
        raise RuntimeError(f"semantic dispatch {failed['state']}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    log_path = root / "pending/semantic-dispatch.jsonl"
    try:
        result = dispatch_job(
            root,
            Path(args.config).expanduser().resolve(),
            Path(args.job).expanduser().resolve(),
            dry_run=args.dry_run,
            create_backup=not args.no_backup,
        )
        append_jsonl(log_path, {"timestamp": now_iso(), **result})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        append_jsonl(log_path, {"timestamp": now_iso(), "status": "failed", "error": str(exc)})
        print(f"memory-wuxian semantic dispatch: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
