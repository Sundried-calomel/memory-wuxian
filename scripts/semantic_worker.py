#!/usr/bin/env python3
"""Run one ephemeral AI summary job and exit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from memory_cli import (
    MemoryStore,
    append_jsonl,
    exclusive_lock,
    load_simple_yaml,
    nested_get,
    now_iso,
)


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODEX = Path(
    "codex.exe" if os.name == "nt" else "/Applications/ChatGPT.app/Contents/Resources/codex"
)


def parse_result(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("AI summary result must be a JSON object")
    return value


def build_prompt(job: dict) -> str:
    instructions = (SKILL_ROOT / "prompts/summarize.md").read_text(encoding="utf-8")
    payload = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
    return (
        instructions
        + "\n\nThe following JSON is the complete assigned source payload. "
        + "Use no information outside it.\n\n"
        + payload
        + "\n"
    )


def run_job(root: Path, config_path: Path, job_path: Path, dry_run: bool) -> dict:
    config = load_simple_yaml(config_path)
    store = MemoryStore(root, config)
    job_path = job_path.resolve()
    if job_path.parent != store.pending_dir.resolve() or not job_path.exists():
        raise ValueError("Job must be an existing pending Memory無限 job")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    codex_key = "codex_cli_path_windows" if os.name == "nt" else "codex_cli_path"
    codex_path = Path(
        os.environ.get(
            "MEMORY_WUXIAN_CODEX",
            str(nested_get(config, ["ai_summary", codex_key], DEFAULT_CODEX)),
        )
    ).expanduser()
    timeout_seconds = int(nested_get(config, ["ai_summary", "timeout_seconds"], 900))
    model = str(nested_get(config, ["ai_summary", "model"], "")).strip()
    schema_path = SKILL_ROOT / "schemas/summary-result.schema.json"
    command = [
        str(codex_path),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
    ]
    if model:
        command.extend(["--model", model])
    if dry_run:
        return {
            "status": "dry-run",
            "job_id": job["job_id"],
            "command": command,
            "source_messages": len(job.get("source_message_ids", [])),
        }

    with tempfile.TemporaryDirectory(prefix="memory-wuxian-summary-") as temporary:
        result_path = Path(temporary) / "summary.json"
        command.extend(["--output-last-message", str(result_path), "-"])
        completed = subprocess.run(
            command,
            input=build_prompt(job),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            cwd=tempfile.gettempdir(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"one-shot Codex summary failed ({completed.returncode}): "
                + completed.stderr[-2000:]
            )
        payload = parse_result(result_path)
        normalized = store.validate_summary_payload(payload, job["required_result_keys"])
        result_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with exclusive_lock(root / ".locks/archive.lock"):
            summary_path = store.ingest_summary(job_path, result_path)
            backup_path = store.create_backup_snapshot(
                "one-shot-ai-summary-ingested",
                {"job_id": job["job_id"], "summary": str(summary_path)},
            )
    return {
        "status": "ingested",
        "job_id": job["job_id"],
        "summary": str(summary_path),
        "backup": str(backup_path) if backup_path else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    log_path = root / "pending/semantic-worker.jsonl"
    try:
        with exclusive_lock(root / ".locks/semantic-worker.lock"):
            result = run_job(
                root,
                Path(args.config).expanduser().resolve(),
                Path(args.job).expanduser(),
                args.dry_run,
            )
        append_jsonl(log_path, {"timestamp": now_iso(), **result})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        append_jsonl(
            log_path,
            {
                "timestamp": now_iso(),
                "status": "failed",
                "job": args.job,
                "error": str(exc),
            },
        )
        print(f"memory-wuxian semantic worker: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
