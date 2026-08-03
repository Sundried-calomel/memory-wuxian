#!/usr/bin/env python3
"""Run one ephemeral AI summary job and exit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import hashlib
import time
from pathlib import Path
from typing import Callable

from memory_cli import (
    MemoryStore,
    append_jsonl,
    canonical_sha256,
    exclusive_lock,
    load_simple_yaml,
    nested_get,
    now_iso,
    raw_record_sha256,
    raw_source_sha256,
)
from platform_process import no_window_kwargs
from semantic_plan import (
    DEFAULT_PROMPT_CHARACTER_BUDGET,
    DEFAULT_PROMPT_UTF8_BUDGET,
    MAX_MODEL_CALLS,
    load_or_create_plan,
    load_verified_result,
    materialize_unit_job,
    persist_result,
    prompt_size,
    within_budget,
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
        if all(column in item for item in flattened) and values and all(value == values[0] for value in values):
            constants[column] = values[0]
        else:
            variable.append(column)
    packed = {
        "format": "memory-wuxian-lossless-tabular-v1",
        "record_count": len(flattened),
        "constants": constants,
        "columns": variable,
        "rows": [[item.get(column) for column in variable] for item in flattened],
        "presence": [[column in item for column in variable] for item in flattened],
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
    presence = packed.get("presence")
    rows = list(packed.get("rows", []))
    if presence is not None and len(presence) != len(rows):
        raise ValueError("Lossless summary payload presence row count mismatch")
    records = []
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError("Lossless summary payload row width mismatch")
        row_presence = presence[index] if presence is not None else [True] * len(columns)
        if len(row_presence) != len(columns):
            raise ValueError("Lossless summary payload presence width mismatch")
        flat = {
            **constants,
            **{
                column: value
                for column, value, present in zip(columns, row, row_presence)
                if present
            },
        }
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
        if all(column in item for item in flattened) and values and all(value == values[0] for value in values):
            constants[column] = values[0]
        else:
            variable.append(column)
    packed = {
        "format": "memory-wuxian-lossless-summary-tabular-v1",
        "summary_count": len(flattened),
        "constants": constants,
        "columns": variable,
        "rows": [[item.get(column) for column in variable] for item in flattened],
        "presence": [[column in item for column in variable] for item in flattened],
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
    presence = packed.get("presence")
    rows = list(packed.get("rows", []))
    if presence is not None and len(presence) != len(rows):
        raise ValueError("Lossless child-summary payload presence row count mismatch")
    summaries = []
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError("Lossless child-summary payload row width mismatch")
        row_presence = presence[index] if presence is not None else [True] * len(columns)
        if len(row_presence) != len(columns):
            raise ValueError("Lossless child-summary payload presence width mismatch")
        flat = {
            **constants,
            **{
                column: value
                for column, value, present in zip(columns, row, row_presence)
                if present
            },
        }
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
    fragments = job.get("source_record_fragments")
    if fragments:
        metadata = {
            key: value
            for key, value in job.items()
            if key not in {"source_record_fragments", "source_message_ids"}
        }
        return {
            "task": metadata,
            "source_message_ids": job.get("source_message_ids", []),
            "source_record_fragments": fragments,
            "fragment_contract": (
                "Each fragment identifies one original record field and a half-open "
                "character range. Read fragments in listed order; ranges neither overlap "
                "nor omit source field content."
            ),
        }
    records = job.get("source_records")
    if records:
        metadata = {
            key: value
            for key, value in job.items()
            if key not in {"source_records", "source_message_ids"}
        }
        return {
            "task": metadata,
            "allowed_source_message_ids": job.get("source_message_ids", []),
            "source_message_ids_derivation": (
                "Decode records and read message_id in order. Any policy-event citation "
                "must be copied exactly from allowed_source_message_ids."
            ),
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
    if job.get("source_record_fragments"):
        source_contract = (
            "The following JSON contains a deterministic ordered fragment view of one "
            "or more oversized source records. Each fragment names its original record, "
            "field path, and half-open character range. Read every fragment exactly once "
            "in listed order. No source field content has been omitted or overlapped. "
            "Use no information outside this payload.\n\n"
        )
    else:
        source_contract = (
            "The following JSON contains a lossless tabular representation of the complete "
            "assigned source. Apply constants to every row, map columns to row values, and "
            "use each presence bitmap to distinguish an absent field from a present null. "
            "For lossless_source_records, restore source.* keys under source and "
            "deterministically recompute content_sha256. For lossless_source_summaries, "
            "restore metadata.* keys under metadata. No source text or state meaning has been removed. "
            "Use no information outside this payload.\n\n"
        )
    return (
        instructions
        + "\n\n"
        + source_contract
        + payload
        + "\n"
    )


def prompt_contract_sha256() -> str:
    prompt = (SKILL_ROOT / "prompts/summarize.md").read_bytes()
    return hashlib.sha256(prompt + b"\0memory-wuxian-semantic-plan-v1").hexdigest()


def _validate_result_for_source(store: MemoryStore, result: dict, job: dict) -> dict:
    normalized = store.validate_summary_payload(
        result,
        job["required_result_keys"],
        int(job["summary_level"]),
    )
    allowed = set(job.get("source_message_ids", []))
    if int(job["summary_level"]) == 1:
        for index, event in enumerate(normalized.get("policy_events", [])):
            outside = [item for item in event["source_message_ids"] if item not in allowed]
            if outside:
                raise ValueError(
                    f"Policy event {index} cites messages outside its semantic plan unit: "
                    + ", ".join(outside)
                )
    return normalized


def _codex_command(config: dict) -> tuple[list[str], int]:
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
        str(codex_path), "exec", "--ephemeral", "--ignore-user-config",
        "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema",
        str(schema_path),
    ]
    if model:
        command.extend(["--model", model])
    return command, timeout_seconds


def invoke_codex(command: list[str], timeout_seconds: int, prompt: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-summary-") as temporary:
        result_path = Path(temporary) / "summary.json"
        actual_command = [*command, "--output-last-message", str(result_path), "-"]
        completed = subprocess.run(
            actual_command,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            cwd=tempfile.gettempdir(),
            **no_window_kwargs(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"one-shot Codex summary failed ({completed.returncode}): "
                + completed.stderr[-2000:]
            )
        return parse_result(result_path)


def _reduce_job(parent: dict, results: list[dict], stage: int) -> dict:
    payload = []
    for index, result in enumerate(results, 1):
        payload.append({
            "summary_id": f"{parent['job_id']}-map-{stage:02d}-{index:03d}",
            "metadata": {"semantic_plan_stage": stage, "source_sha256": parent.get("source_sha256")},
            "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
            "summary_sha256": canonical_sha256(result),
        })
    return {
        **{
            key: value for key, value in parent.items()
            if key not in {"source_records", "source_summary_payload"}
        },
        "semantic_plan_stage": "reduce",
        "semantic_plan_reduce_level": stage,
        "source_summary_payload": payload,
    }


def execute_oversized_job(
    store: MemoryStore,
    job: dict,
    command: list[str],
    timeout_seconds: int,
    character_budget: int,
    utf8_budget: int,
    invoker: Callable[[list[str], int, str], dict] = invoke_codex,
    timing_records: list[dict[str, float]] | None = None,
) -> tuple[dict, int]:
    embedded_source_sha = raw_source_sha256(job.get("source_records", []))
    if embedded_source_sha != job.get("source_sha256"):
        raise ValueError("Oversized semantic job source SHA-256 mismatch")
    contract_sha = prompt_contract_sha256()
    plan, plan_dir = load_or_create_plan(
        store.pending_dir / "semantic-plans",
        job,
        build_prompt,
        contract_sha,
        character_budget,
        utf8_budget,
    )
    results = []
    calls = 0
    for unit in plan["units"]:
        unit_job = materialize_unit_job(job, unit)
        prompt = build_prompt(unit_job)
        input_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if input_sha != unit["input_sha256"]:
            raise ValueError("Semantic plan unit input hash mismatch")
        result_path = plan_dir / f"{unit['unit_id']}.json"
        normalized = load_verified_result(result_path, input_sha)
        if normalized is None:
            model_started = time.monotonic()
            model_result = invoker(command, timeout_seconds, prompt)
            model_seconds = time.monotonic() - model_started
            validation_started = time.monotonic()
            normalized = _validate_result_for_source(store, model_result, unit_job)
            validation_seconds = time.monotonic() - validation_started
            if timing_records is not None:
                timing_records.append({
                    "model_seconds": model_seconds,
                    "validation_seconds": validation_seconds,
                })
            persist_result(result_path, input_sha, normalized)
            calls += 1
        else:
            validation_started = time.monotonic()
            normalized = _validate_result_for_source(store, normalized, unit_job)
            if timing_records is not None:
                timing_records.append({
                    "model_seconds": 0.0,
                    "validation_seconds": time.monotonic() - validation_started,
                })
        results.append(normalized)

    reduce_level = 1
    logical_calls = len(results)
    while len(results) > 1:
        batches: list[list[dict]] = []
        current: list[dict] = []
        for result in results:
            candidate = current + [result]
            candidate_job = _reduce_job(job, candidate, reduce_level)
            if within_budget(build_prompt(candidate_job), character_budget, utf8_budget):
                current = candidate
                continue
            if not current:
                raise ValueError("A single semantic map result exceeds the reduce prompt budget")
            batches.append(current)
            current = [result]
            if not within_budget(
                build_prompt(_reduce_job(job, current, reduce_level)),
                character_budget,
                utf8_budget,
            ):
                raise ValueError("A single semantic map result exceeds the reduce prompt budget")
        if current:
            batches.append(current)
        if len(batches) == len(results) and all(len(batch) == 1 for batch in batches):
            raise ValueError("Semantic reducer cannot combine results within the configured budget")

        reduced: list[dict] = []
        for batch_index, batch in enumerate(batches, 1):
            reduce_job = _reduce_job(job, batch, reduce_level)
            prompt = build_prompt(reduce_job)
            input_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            result_path = plan_dir / f"reduce-{reduce_level:03d}-{batch_index:03d}.json"
            normalized = load_verified_result(result_path, input_sha)
            if normalized is None:
                if logical_calls >= MAX_MODEL_CALLS:
                    raise ValueError(f"Semantic execution exceeded {MAX_MODEL_CALLS} model calls")
                model_started = time.monotonic()
                model_result = invoker(command, timeout_seconds, prompt)
                model_seconds = time.monotonic() - model_started
                validation_started = time.monotonic()
                normalized = _validate_result_for_source(store, model_result, reduce_job)
                validation_seconds = time.monotonic() - validation_started
                if timing_records is not None:
                    timing_records.append({
                        "model_seconds": model_seconds,
                        "validation_seconds": validation_seconds,
                    })
                persist_result(result_path, input_sha, normalized)
                calls += 1
            else:
                validation_started = time.monotonic()
                normalized = _validate_result_for_source(store, normalized, reduce_job)
                if timing_records is not None:
                    timing_records.append({
                        "model_seconds": 0.0,
                        "validation_seconds": time.monotonic() - validation_started,
                    })
            logical_calls += 1
            reduced.append(normalized)
        results = reduced
        reduce_level += 1
    if not results:
        raise ValueError("Semantic execution plan produced no result")
    return results[0], calls


def run_job(
    root: Path,
    config_path: Path,
    job_path: Path,
    dry_run: bool,
    create_backup: bool = True,
    invoker: Callable[[list[str], int, str], dict] = invoke_codex,
) -> dict:
    total_started = time.monotonic()
    config = load_simple_yaml(config_path)
    store = MemoryStore(root, config)
    job_path = job_path.resolve()
    if job_path.parent != store.pending_dir.resolve() or not job_path.exists():
        raise ValueError("Job must be an existing pending Memory無限 job")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    command, timeout_seconds = _codex_command(config)
    if dry_run:
        return {
            "status": "dry-run",
            "job_id": job["job_id"],
            "command": command,
            "source_messages": len(job.get("source_message_ids", [])),
        }

    prompt = build_prompt(job)
    character_budget = min(
        DEFAULT_PROMPT_CHARACTER_BUDGET,
        int(nested_get(config, ["ai_summary", "max_prompt_characters"], DEFAULT_PROMPT_CHARACTER_BUDGET)),
    )
    utf8_budget = min(
        DEFAULT_PROMPT_UTF8_BUDGET,
        int(nested_get(config, ["ai_summary", "max_prompt_utf8_bytes"], DEFAULT_PROMPT_UTF8_BUDGET)),
    )
    timing_records: list[dict[str, float]] = []
    if within_budget(prompt, character_budget, utf8_budget):
        model_started = time.monotonic()
        model_result = invoker(command, timeout_seconds, prompt)
        model_seconds = time.monotonic() - model_started
        validation_started = time.monotonic()
        normalized = _validate_result_for_source(store, model_result, job)
        timing_records.append({
            "model_seconds": model_seconds,
            "validation_seconds": time.monotonic() - validation_started,
        })
        ai_invocations = 1
    else:
        normalized, ai_invocations = execute_oversized_job(
            store,
            job,
            command,
            timeout_seconds,
            character_budget,
            utf8_budget,
            invoker,
            timing_records,
        )
    ingestion_started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-summary-ingest-") as temporary:
        result_path = Path(temporary) / "summary.json"
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
    ingestion_seconds = time.monotonic() - ingestion_started
    total_seconds = time.monotonic() - total_started
    model_seconds = sum(item["model_seconds"] for item in timing_records)
    validation_seconds = sum(item["validation_seconds"] for item in timing_records)
    local_preparation_seconds = max(
        0.0,
        total_seconds - model_seconds - validation_seconds - ingestion_seconds,
    )
    return {
        "status": "ingested",
        "job_id": job["job_id"],
        "summary": str(summary_path),
        "backup": str(backup_path) if backup_path else None,
        "ai_invocations": ai_invocations,
        "prompt_size": prompt_size(prompt),
        "timing": {
            "local_preparation_seconds": round(local_preparation_seconds, 3),
            "model_seconds": round(model_seconds, 3),
            "validation_seconds": round(validation_seconds, 3),
            "ingestion_seconds": round(ingestion_seconds, 3),
            "total_seconds": round(total_seconds, 3),
        },
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
