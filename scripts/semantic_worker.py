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
    canonical_sha256,
    exclusive_lock,
    load_simple_yaml,
    nested_get,
    now_iso,
    raw_record_sha256,
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


def pack_source_records(records: list[dict]) -> dict:
    flattened = []
    for record in records:
        item = {key: value for key, value in record.items() if key not in {"source", "content_sha256"}}
        item.update({f"source.{key}": value for key, value in (record.get("source") or {}).items()})
        flattened.append(item)
    columns = sorted(set().union(*(item.keys() for item in flattened))) if flattened else []
    constants = {}
    variable = []
    for column in columns:
        values = [item.get(column) for item in flattened]
        if values and all(value == values[0] for value in values):
            constants[column] = values[0]
        else:
            variable.append(column)
    packed = {
        "format": "memory-wuxian-lossless-tabular-v1",
        "record_count": len(flattened),
        "constants": constants,
        "columns": variable,
        "rows": [[item.get(column) for column in variable] for item in flattened],
        "derived_fields": ["content_sha256"],
    }
    restored = unpack_source_records(packed)
    if canonical_sha256(restored) != canonical_sha256(records):
        raise RuntimeError("Lossless summary payload verification failed")
    return packed


def unpack_source_records(packed: dict) -> list[dict]:
    if packed.get("format") != "memory-wuxian-lossless-tabular-v1":
        raise ValueError("Unsupported lossless summary payload format")
    columns = list(packed.get("columns", []))
    constants = dict(packed.get("constants", {}))
    records = []
    for row in packed.get("rows", []):
        if len(row) != len(columns):
            raise ValueError("Lossless summary payload row width mismatch")
        flat = {**constants, **dict(zip(columns, row))}
        source = {
            key.removeprefix("source."): value
            for key, value in flat.items()
            if key.startswith("source.")
        }
        record = {
            key: value
            for key, value in flat.items()
            if not key.startswith("source.")
        }
        if source:
            record["source"] = source
        if "content_sha256" in packed.get("derived_fields", []):
            record["content_sha256"] = raw_record_sha256(record)
        records.append(record)
    if len(records) != int(packed.get("record_count", -1)):
        raise ValueError("Lossless summary payload record count mismatch")
    return records


def pack_source_summaries(summaries: list[dict]) -> dict:
    flattened = []
    for summary in summaries:
        item = {key: value for key, value in summary.items() if key != "metadata"}
        item.update({f"metadata.{key}": value for key, value in (summary.get("metadata") or {}).items()})
        flattened.append(item)
    columns = sorted(set().union(*(item.keys() for item in flattened))) if flattened else []
    constants = {}
    variable = []
    for column in columns:
        values = [item.get(column) for item in flattened]
        if values and all(value == values[0] for value in values):
            constants[column] = values[0]
        else:
            variable.append(column)
    packed = {
        "format": "memory-wuxian-lossless-summary-tabular-v1",
        "summary_count": len(flattened),
        "constants": constants,
        "columns": variable,
        "rows": [[item.get(column) for column in variable] for item in flattened],
    }
    restored = unpack_source_summaries(packed)
    if canonical_sha256(restored) != canonical_sha256(summaries):
        raise RuntimeError("Lossless child-summary payload verification failed")
    return packed


def unpack_source_summaries(packed: dict) -> list[dict]:
    if packed.get("format") != "memory-wuxian-lossless-summary-tabular-v1":
        raise ValueError("Unsupported lossless child-summary payload format")
    columns = list(packed.get("columns", []))
    constants = dict(packed.get("constants", {}))
    summaries = []
    for row in packed.get("rows", []):
        if len(row) != len(columns):
            raise ValueError("Lossless child-summary payload row width mismatch")
        flat = {**constants, **dict(zip(columns, row))}
        metadata = {
            key.removeprefix("metadata."): value
            for key, value in flat.items()
            if key.startswith("metadata.")
        }
        summary = {
            key: value
            for key, value in flat.items()
            if not key.startswith("metadata.")
        }
        if metadata:
            summary["metadata"] = metadata
        summaries.append(summary)
    if len(summaries) != int(packed.get("summary_count", -1)):
        raise ValueError("Lossless child-summary payload count mismatch")
    return summaries


def build_prompt_payload(job: dict) -> dict:
    records = job.get("source_records")
    if records:
        metadata = {
            key: value
            for key, value in job.items()
            if key not in {"source_records", "source_message_ids"}
        }
        return {
            "task": metadata,
            "source_message_ids_derivation": "Decode records and read message_id in order.",
            "lossless_source_records": pack_source_records(records),
        }
    summaries = job.get("source_summary_payload")
    if summaries:
        metadata = {
            key: value
            for key, value in job.items()
            if key != "source_summary_payload"
        }
        return {
            "task": metadata,
            "lossless_source_summaries": pack_source_summaries(summaries),
        }
    return job


def build_prompt(job: dict) -> str:
    instructions = (SKILL_ROOT / "prompts/summarize.md").read_text(encoding="utf-8")
    payload = json.dumps(build_prompt_payload(job), ensure_ascii=False, separators=(",", ":"))
    return (
        instructions
        + "\n\nThe following JSON contains a lossless tabular representation of the complete "
        + "assigned source. Apply constants to every row and map columns to row values. "
        + "For lossless_source_records, restore source.* keys under source and "
        + "deterministically recompute content_sha256. For lossless_source_summaries, "
        + "restore metadata.* keys under metadata. No source text or state meaning has been removed. "
        + "Use no information outside this payload.\n\n"
        + payload
        + "\n"
    )


def run_job(
    root: Path,
    config_path: Path,
    job_path: Path,
    dry_run: bool,
    create_backup: bool = True,
) -> dict:
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
            backup_path = None
            if create_backup:
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
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the per-job snapshot when a batch controller will create one later.",
    )
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
                create_backup=not args.no_backup,
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
