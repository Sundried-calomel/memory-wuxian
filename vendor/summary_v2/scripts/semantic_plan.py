#!/usr/bin/env python3
"""Deterministic, resumable execution plans for oversized semantic jobs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar


DEFAULT_PROMPT_CHARACTER_BUDGET = 900_000
DEFAULT_PROMPT_UTF8_BUDGET = 900_000
GENERIC_SEMANTIC_PLAN_MAX_MODEL_CALLS = 16
PLAN_FORMAT = "memory-wuxian-semantic-plan-v1"
RESULT_FORMAT = "memory-wuxian-semantic-plan-result-v1"
_SHA256_FIELDS = (
    "source_sha256",
    "prompt_sha256",
    "schema_sha256",
    "projector_sha256",
    "runner_sha256",
    "worker_sha256",
    "output_projection_sha256",
)
_INPUT_PROJECTION_FIELDS = (
    "ordered_input_projection_sha256",
    "ordered_input_projection_sha256s",
    "input_projection_sha256",
)
_SHA256_HEX = frozenset("0123456789abcdef")
T = TypeVar("T")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


canonical_hash = canonical_sha256


def prompt_size(prompt: str) -> dict[str, int]:
    return {"characters": len(prompt), "utf8_bytes": len(prompt.encode("utf-8"))}


def utf8_size(value: str | bytes) -> int:
    if isinstance(value, bytes):
        return len(value)
    if not isinstance(value, str):
        raise ValueError("utf8_size requires str or bytes")
    return len(value.encode("utf-8"))


def within_budget(prompt: str, character_budget: int, utf8_budget: int) -> bool:
    size = prompt_size(prompt)
    return size["characters"] <= character_budget and size["utf8_bytes"] <= utf8_budget


def partition_ordered(
    items: Sequence[T],
    compile_group: Callable[[list[T]], str | bytes],
    target_bytes: int,
    hard_limit: int,
) -> list[list[T]]:
    """Greedily partition ordered values using their exact compiled byte size."""

    values = list(items)
    if not values:
        raise ValueError("partition input cannot be empty")
    if (
        not callable(compile_group)
        or target_bytes <= 0
        or hard_limit <= 0
        or target_bytes > hard_limit
    ):
        raise ValueError("partition byte limits or compiler are invalid")
    groups: list[list[T]] = []
    current: list[T] = []
    for item in values:
        candidate = [*current, item]
        size = utf8_size(compile_group(candidate))
        if size > hard_limit:
            if not current:
                raise ValueError(f"single item is terminal-unplannable at {size} bytes")
            groups.append(current)
            current = [item]
            singleton_size = utf8_size(compile_group(current))
            if singleton_size > hard_limit:
                raise ValueError(
                    f"single item is terminal-unplannable at {singleton_size} bytes"
                )
        elif size > target_bytes and current:
            groups.append(current)
            current = [item]
            singleton_size = utf8_size(compile_group(current))
            if singleton_size > hard_limit:
                raise ValueError(
                    f"single item is terminal-unplannable at {singleton_size} bytes"
                )
        else:
            current = candidate
    if current:
        groups.append(current)
    if (
        not groups
        or any(not group for group in groups)
        or [item for group in groups for item in group] != values
    ):
        raise ValueError("partition did not preserve one exact ordered input partition")
    if any(utf8_size(compile_group(group)) > hard_limit for group in groups):
        raise ValueError("compiled partition exceeds the hard limit")
    return groups


def plan_reduction_frontiers(
    initial_ids: Sequence[str], max_fan_in: int = 2
) -> dict[str, Any]:
    """Return a finite deterministic reduction DAG with decreasing ranks."""

    frontier = list(initial_ids)
    if not frontier or len(frontier) != len(set(frontier)) or not all(
        isinstance(item, str) and item for item in frontier
    ):
        raise ValueError("initial_ids must be a non-empty unique string sequence")
    if isinstance(max_fan_in, bool) or not isinstance(max_fan_in, int) or max_fan_in < 2:
        raise ValueError("max_fan_in must be at least two")
    initial = list(frontier)
    rank = [len(frontier)]
    layers: list[dict[str, Any]] = []
    stage = 0
    while len(frontier) > 1:
        nodes: list[dict[str, Any]] = []
        next_frontier: list[str] = []
        for offset in range(0, len(frontier), max_fan_in):
            inputs = frontier[offset : offset + max_fan_in]
            if len(inputs) == 1:
                node = {"kind": "carry", "node_id": inputs[0], "input_ids": inputs}
            else:
                node_id = "reduce-" + canonical_sha256(
                    {"stage": stage, "position": len(nodes), "input_ids": inputs}
                )
                node = {"kind": "reduce", "node_id": node_id, "input_ids": inputs}
            nodes.append(node)
            next_frontier.append(node["node_id"])
        if len(next_frontier) >= len(frontier):
            raise ValueError("reduction frontier rank did not strictly decrease")
        layers.append(
            {
                "stage": stage,
                "input_count": len(frontier),
                "output_count": len(next_frontier),
                "nodes": nodes,
            }
        )
        frontier = next_frontier
        rank.append(len(frontier))
        stage += 1
    return {
        "format": "summary-v2-reduction-dag-v1",
        "initial_ids": initial,
        "max_fan_in": max_fan_in,
        "layers": layers,
        "rank": rank,
        "final_id": frontier[0],
    }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value).issubset(_SHA256_HEX)


def validate_node_receipt(
    expected: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, Any]:
    """Require exact bindings for a reusable model-node result."""

    if not isinstance(expected, Mapping) or not isinstance(receipt, Mapping):
        raise ValueError("expected and receipt must be objects")
    for field in _SHA256_FIELDS:
        if field not in expected or field not in receipt:
            raise ValueError(f"node receipt is missing required binding: {field}")
        if not _is_sha256(expected[field]) or not _is_sha256(receipt[field]):
            raise ValueError(f"node receipt binding is malformed: {field}")
        if receipt[field] != expected[field]:
            raise ValueError(f"node receipt binding drifted: {field}")

    def input_projections(value: Mapping[str, Any], label: str) -> list[str]:
        present = [field for field in _INPUT_PROJECTION_FIELDS if field in value]
        if not present:
            raise ValueError(f"{label} is missing ordered input projection bindings")
        projections = value[present[0]]
        if any(value[field] != projections for field in present[1:]):
            raise ValueError(f"{label} contains conflicting input projection aliases")
        return projections

    expected_inputs = input_projections(expected, "expected")
    receipt_inputs = input_projections(receipt, "receipt")
    for label, value in (
        ("expected ordered input projections", expected_inputs),
        ("receipt ordered input projections", receipt_inputs),
    ):
        if not isinstance(value, list) or not all(_is_sha256(item) for item in value):
            raise ValueError(f"{label} must be an ordered SHA-256 list")
    if receipt_inputs != expected_inputs:
        raise ValueError("node receipt ordered input projections drifted")
    for field, expected_value in expected.items():
        if field in _INPUT_PROJECTION_FIELDS:
            continue
        if field not in receipt or receipt[field] != expected_value:
            raise ValueError(f"node receipt expected field drifted: {field}")
    return dict(receipt)


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


def plan_level_1_jobs(
    job: Mapping[str, Any],
    source_builder: Callable[[dict[str, Any]], dict[str, Any]],
    prompt_compiler: Callable[[dict[str, Any]], Mapping[str, Any]],
    source_sha256: Callable[[list[dict[str, Any]]], str],
    target_bytes: int,
    hard_limit: int,
    maximum_source_refs: int = 128,
) -> list[dict[str, Any]]:
    """Plan ordered bounded L1 map jobs while preserving completed rounds."""

    if maximum_source_refs < 1:
        raise ValueError("maximum_source_refs must be positive")

    records_value = job.get("source_records")
    if not isinstance(records_value, list) or not records_value:
        raise ValueError("level-1 job source_records must be a non-empty list")
    records = [dict(record) for record in records_value]
    units: list[list[dict[str, Any]]] = []
    current_round: list[dict[str, Any]] = []
    for record in records:
        current_round.append(record)
        if record.get("completes_round") or record.get("complete_round"):
            units.append(current_round)
            current_round = []
    if current_round:
        units.append(current_round)

    def make(values: list[dict[str, Any]], index: int) -> dict[str, Any]:
        ordered = sorted(values, key=lambda item: int(item["sequence"]))
        return {
            "format_version": 1,
            "job_id": f"{job['job_id']}-rescue-map-{index:03d}",
            "target_summary_id": f"{job['target_summary_id']}-map-{index:03d}",
            "summary_level": 1,
            "conversation_id": job["conversation_id"],
            "source_sha256": source_sha256(ordered),
            "source_message_ids": [item["message_id"] for item in ordered],
            "source_records": ordered,
        }

    def compiled_bytes(values: list[dict[str, Any]], index: int) -> int:
        compiled = prompt_compiler(source_builder(make(values, index)))
        size = compiled.get("prompt_utf8_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("prompt compiler omitted prompt_utf8_bytes")
        return size

    chunks: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []

    def append_oversized_unit(unit: list[dict[str, Any]]) -> None:
        split: list[dict[str, Any]] = []
        for record in unit:
            candidate = [*split, record]
            if split and (
                len(candidate) > maximum_source_refs
                or compiled_bytes(candidate, len(chunks) + 1) > target_bytes
            ):
                chunks.append(split)
                split = [record]
            else:
                split = candidate
            if compiled_bytes(split, len(chunks) + 1) > hard_limit:
                raise ValueError("one source record exceeds the L1 map prompt hard limit")
        if split:
            chunks.append(split)

    for unit in units:
        if (
            len(unit) > maximum_source_refs
            or compiled_bytes(unit, len(chunks) + 1) > target_bytes
        ):
            if active:
                chunks.append(active)
                active = []
            append_oversized_unit(unit)
            continue
        candidate = [*active, *unit]
        if active and (
            len(candidate) > maximum_source_refs
            or compiled_bytes(candidate, len(chunks) + 1) > target_bytes
        ):
            chunks.append(active)
            active = list(unit)
        else:
            active = candidate
    if active:
        chunks.append(active)
    if len(chunks) == 1 and len(chunks[0]) > 1:
        midpoint = len(chunks[0]) // 2
        chunks = [chunks[0][:midpoint], chunks[0][midpoint:]]

    planned = [make(chunk, index) for index, chunk in enumerate(chunks, 1)]
    flattened = [
        record["message_id"] for item in planned for record in item["source_records"]
    ]
    expected = [
        record["message_id"]
        for record in sorted(records, key=lambda item: int(item["sequence"]))
    ]
    if flattened != expected or len(flattened) != len(set(flattened)):
        raise ValueError("L1 plan did not preserve one exact ordered source partition")
    if any(
        prompt_compiler(source_builder(item))["prompt_utf8_bytes"] > hard_limit
        for item in planned
    ):
        raise ValueError("L1 planned prompt exceeds the hard limit")
    if any(len(item["source_message_ids"]) > maximum_source_refs for item in planned):
        raise ValueError("L1 planned chunk exceeds the source-ref limit")
    return planned


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
    if len(units) + 1 > GENERIC_SEMANTIC_PLAN_MAX_MODEL_CALLS:
        raise ValueError(
            "Oversized semantic job requires more than "
            f"{GENERIC_SEMANTIC_PLAN_MAX_MODEL_CALLS} model calls"
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
