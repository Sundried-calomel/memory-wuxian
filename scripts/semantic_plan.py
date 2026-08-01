#!/usr/bin/env python3
"""Deterministic, resumable execution plans for oversized semantic jobs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_PROMPT_CHARACTER_BUDGET = 900_000
DEFAULT_PROMPT_UTF8_BUDGET = 900_000
MAX_MODEL_CALLS = 16
PLAN_FORMAT = "memory-wuxian-semantic-plan-v1"
RESULT_FORMAT = "memory-wuxian-semantic-plan-result-v1"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def prompt_size(prompt: str) -> dict[str, int]:
    return {"characters": len(prompt), "utf8_bytes": len(prompt.encode("utf-8"))}


def within_budget(prompt: str, character_budget: int, utf8_budget: int) -> bool:
    size = prompt_size(prompt)
    return size["characters"] <= character_budget and size["utf8_bytes"] <= utf8_budget


def _base_job(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key not in {"source_records", "source_summary_payload", "source_message_ids"}
    }


def _record_job(job: dict, records: list[dict], unit_id: str) -> dict:
    result = {
        **_base_job(job),
        "semantic_plan_stage": "map",
        "semantic_plan_unit_id": unit_id,
        "source_records": records,
        "source_message_ids": [
            str(record["message_id"])
            for record in records
            if record.get("message_id") is not None
        ],
    }
    return result


def _fragment_job(job: dict, fragments: list[dict], unit_id: str) -> dict:
    return {
        **_base_job(job),
        "semantic_plan_stage": "map",
        "semantic_plan_unit_id": unit_id,
        "source_record_fragments": fragments,
        "source_message_ids": list(dict.fromkeys(
            str(fragment["message_id"])
            for fragment in fragments
            if fragment.get("message_id") is not None
        )),
    }


def _flatten_fields(value: Any, path: tuple[Any, ...] = ()) -> Iterable[tuple[tuple[Any, ...], Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _flatten_fields(value[key], path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_fields(item, path + (index,))
    else:
        yield path, value


def _field_fragments(record: dict, record_index: int) -> list[dict]:
    message_id = record.get("message_id")
    fragments = []
    for path, value in _flatten_fields(record):
        fragment = {
            "record_index": record_index,
            "message_id": message_id,
            "field_path": list(path),
            "value_type": "string" if isinstance(value, str) else "scalar",
        }
        if isinstance(value, str):
            fragment.update({"start_character": 0, "end_character": len(value), "value": value})
        else:
            fragment["value"] = value
        fragments.append(fragment)
    return fragments


def _split_fragment_until_fit(
    job: dict,
    fragment: dict,
    build_prompt: Callable[[dict], str],
    character_budget: int,
    utf8_budget: int,
) -> list[dict]:
    candidate = _fragment_job(job, [fragment], "probe")
    if within_budget(build_prompt(candidate), character_budget, utf8_budget):
        return [fragment]
    value = fragment.get("value")
    if fragment.get("value_type") != "string" or not isinstance(value, str) or len(value) < 2:
        raise ValueError("A single semantic source field exceeds the configured prompt budget")
    midpoint = len(value) // 2
    start = int(fragment["start_character"])
    left = {**fragment, "end_character": start + midpoint, "value": value[:midpoint]}
    right = {
        **fragment,
        "start_character": start + midpoint,
        "value": value[midpoint:],
    }
    return (
        _split_fragment_until_fit(job, left, build_prompt, character_budget, utf8_budget)
        + _split_fragment_until_fit(job, right, build_prompt, character_budget, utf8_budget)
    )


def _pack_fragment_units(
    job: dict,
    record: dict,
    record_index: int,
    build_prompt: Callable[[dict], str],
    character_budget: int,
    utf8_budget: int,
) -> list[dict]:
    fragments = []
    for fragment in _field_fragments(record, record_index):
        fragments.extend(
            _split_fragment_until_fit(
                job, fragment, build_prompt, character_budget, utf8_budget
            )
        )
    units: list[list[dict]] = []
    current: list[dict] = []
    for fragment in fragments:
        candidate = current + [fragment]
        if current and not within_budget(
            build_prompt(_fragment_job(job, candidate, "probe")),
            character_budget,
            utf8_budget,
        ):
            units.append(current)
            current = [fragment]
        else:
            current = candidate
    if current:
        units.append(current)
    return [{"kind": "field-fragments", "fragments": unit} for unit in units]


def _pack_record_group(
    job: dict,
    indexed_records: list[tuple[int, dict]],
    build_prompt: Callable[[dict], str],
    character_budget: int,
    utf8_budget: int,
) -> list[dict]:
    units: list[dict] = []
    current: list[tuple[int, dict]] = []
    for indexed in indexed_records:
        candidate = current + [indexed]
        records = [record for _, record in candidate]
        if within_budget(
            build_prompt(_record_job(job, records, "probe")),
            character_budget,
            utf8_budget,
        ):
            current = candidate
            continue
        if current:
            units.append({
                "kind": "records",
                "record_start": current[0][0],
                "record_end": current[-1][0] + 1,
            })
            current = []
        index, record = indexed
        if within_budget(
            build_prompt(_record_job(job, [record], "probe")),
            character_budget,
            utf8_budget,
        ):
            current = [indexed]
        else:
            units.extend(
                _pack_fragment_units(
                    job, record, index, build_prompt, character_budget, utf8_budget
                )
            )
    if current:
        units.append({
            "kind": "records",
            "record_start": current[0][0],
            "record_end": current[-1][0] + 1,
        })
    return units


def materialize_unit_job(parent_job: dict, unit: dict) -> dict:
    records = list(parent_job.get("source_records", []))
    if unit["kind"] == "records":
        selected = records[int(unit["record_start"]):int(unit["record_end"])]
        return _record_job(parent_job, selected, str(unit["unit_id"]))
    if unit["kind"] == "field-fragments":
        return _fragment_job(parent_job, list(unit["fragments"]), str(unit["unit_id"]))
    raise ValueError(f"Unsupported semantic plan unit kind: {unit.get('kind')}")


def build_execution_plan(
    job: dict,
    build_prompt: Callable[[dict], str],
    prompt_contract_sha256: str,
    character_budget: int = DEFAULT_PROMPT_CHARACTER_BUDGET,
    utf8_budget: int = DEFAULT_PROMPT_UTF8_BUDGET,
) -> dict:
    records = list(job.get("source_records", []))
    if not records:
        raise ValueError("Oversized execution planning currently requires Level-1 source records")
    indexed = list(enumerate(records))
    round_groups: list[list[tuple[int, dict]]] = []
    for item in indexed:
        round_number = item[1].get("round_number")
        if not round_groups or round_groups[-1][0][1].get("round_number") != round_number:
            round_groups.append([item])
        else:
            round_groups[-1].append(item)

    raw_units: list[dict] = []
    current_rounds: list[tuple[int, dict]] = []
    for group in round_groups:
        candidate = current_rounds + group
        if within_budget(
            build_prompt(_record_job(job, [record for _, record in candidate], "probe")),
            character_budget,
            utf8_budget,
        ):
            current_rounds = candidate
            continue
        if current_rounds:
            raw_units.extend(
                _pack_record_group(
                    job, current_rounds, build_prompt, character_budget, utf8_budget
                )
            )
            current_rounds = []
        raw_units.extend(
            _pack_record_group(job, group, build_prompt, character_budget, utf8_budget)
        )
    if current_rounds:
        raw_units.extend(
            _pack_record_group(job, current_rounds, build_prompt, character_budget, utf8_budget)
        )

    units = []
    for index, raw in enumerate(raw_units, 1):
        unit = {"unit_id": f"map-{index:03d}", **raw}
        unit_job = materialize_unit_job(job, unit)
        prompt = build_prompt(unit_job)
        size = prompt_size(prompt)
        if not within_budget(prompt, character_budget, utf8_budget):
            raise AssertionError("Semantic planner emitted an over-budget map unit")
        unit.update({
            "input_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_characters": size["characters"],
            "prompt_utf8_bytes": size["utf8_bytes"],
            "source_message_ids": unit_job.get("source_message_ids", []),
        })
        units.append(unit)
    if len(units) + 1 > MAX_MODEL_CALLS:
        raise ValueError(
            f"Oversized semantic job requires more than {MAX_MODEL_CALLS} model calls"
        )
    identity = {
        "format": PLAN_FORMAT,
        "job_id": job.get("job_id"),
        "target_summary_id": job.get("target_summary_id"),
        "source_sha256": job.get("source_sha256"),
        "parent_job_sha256": canonical_sha256(job),
        "prompt_contract_sha256": prompt_contract_sha256,
        "character_budget": character_budget,
        "utf8_budget": utf8_budget,
        "units": units,
    }
    return {**identity, "plan_sha256": canonical_sha256(identity)}


def validate_plan(plan: dict, job: dict, prompt_contract_sha256: str) -> None:
    if plan.get("format") != PLAN_FORMAT:
        raise ValueError("Unsupported semantic execution plan format")
    claimed = plan.get("plan_sha256")
    actual = canonical_sha256({key: value for key, value in plan.items() if key != "plan_sha256"})
    if claimed != actual:
        raise ValueError("Semantic execution plan hash mismatch")
    expected = {
        "job_id": job.get("job_id"),
        "target_summary_id": job.get("target_summary_id"),
        "source_sha256": job.get("source_sha256"),
        "parent_job_sha256": canonical_sha256(job),
        "prompt_contract_sha256": prompt_contract_sha256,
    }
    for key, value in expected.items():
        if plan.get(key) != value:
            raise ValueError(f"Semantic execution plan is not bound to the parent {key}")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_or_create_plan(
    plan_root: Path,
    job: dict,
    build_prompt: Callable[[dict], str],
    prompt_contract_sha256: str,
    character_budget: int,
    utf8_budget: int,
) -> tuple[dict, Path]:
    candidate = build_execution_plan(
        job, build_prompt, prompt_contract_sha256, character_budget, utf8_budget
    )
    plan_dir = plan_root / str(job["job_id"]) / str(candidate["plan_sha256"])
    manifest = plan_dir / "manifest.json"
    if manifest.exists():
        plan = json.loads(manifest.read_text(encoding="utf-8"))
        validate_plan(plan, job, prompt_contract_sha256)
        if plan != candidate:
            raise ValueError("Persisted semantic plan differs from deterministic reconstruction")
        return plan, plan_dir
    atomic_write_json(manifest, candidate)
    return candidate, plan_dir


def load_verified_result(path: Path, input_sha256: str) -> dict | None:
    if not path.exists():
        return None
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope.get("format") != RESULT_FORMAT:
        raise ValueError("Unsupported semantic plan result format")
    if envelope.get("input_sha256") != input_sha256:
        raise ValueError("Semantic plan result input hash mismatch")
    result = envelope.get("result")
    if not isinstance(result, dict) or envelope.get("result_sha256") != canonical_sha256(result):
        raise ValueError("Semantic plan result hash mismatch")
    return result


def persist_result(path: Path, input_sha256: str, result: dict) -> None:
    atomic_write_json(path, {
        "format": RESULT_FORMAT,
        "input_sha256": input_sha256,
        "result_sha256": canonical_sha256(result),
        "result": result,
    })
