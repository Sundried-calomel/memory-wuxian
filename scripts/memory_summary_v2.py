#!/usr/bin/env python3
"""Build traceable, hierarchical summary-v2 sidecars without touching summary-v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from console_encoding import configure_unicode_stdio
from memory_atoms import (
    ALLOWED_STATUS_BY_TYPE,
    ATOM_TYPES,
    LIVE_ARCHIVE_PARTS,
    MAX_SCOPE_CHARACTERS,
    MAX_STATEMENT_CHARACTERS,
    RELATION_TYPES,
    SHA256_RE,
    canonical_sha256,
    read_json,
    validate_job,
)
from memory_guarded_features import raw_record_sha256
from platform_transaction import canonical_json_bytes


FORMAT = "memory-wuxian-summary-v2"
FORMAT_VERSION = 2
PROJECTOR = "memory_summary_v2.py:traceable-projector-v1"
PARENT_PROJECTOR = "memory_summary_v2.py:hierarchical-parent-projector-v2"
SOURCE_LEVEL_1 = "closed-level-1-job"
SOURCE_CHILDREN = "summary-v2-children"
SOURCE_RESCUE_MAPS = "summary-v2-rescue-maps"
SOURCE_PARENT_RESCUE_MAPS = "summary-v2-parent-rescue-maps"
MAX_SOURCE_REFS = 4096
MAX_REFS_PER_ITEM = 128
MAX_OVERVIEW_ITEMS = 24
MAX_SCENES = 128
MAX_ATOMS = 512
MAX_RELATIONS = 1024
MAX_ANCHORS = 512
MAX_DETERMINISTIC_ANCHORS = 4096
MAX_OMISSIONS = 4096
MAX_TEXT_CHARACTERS = 4000
MAX_ANCHOR_CHARACTERS = 1000
MAX_SIDECAR_BYTES = 32 * 1024 * 1024
LOCAL_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
FINAL_ID_RE = re.compile(
    r"^(?:overview|scene|atom|anchor|relation|omission)-[0-9a-f]{32}$"
)
ANCHOR_KINDS = {
    "person",
    "project",
    "file",
    "path",
    "command",
    "tool",
    "artifact",
    "identifier",
    "concept",
    "time",
    "other",
}


class SummaryV2Error(ValueError):
    """A summary-v2 source, candidate, projection, or persistence check failed."""


def _exact(value: Any, fields: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SummaryV2Error(f"{location} must be an object")
    actual = set(value)
    if actual != fields:
        raise SummaryV2Error(
            f"{location} fields mismatch; "
            f"missing={sorted(fields - actual)}, extra={sorted(actual - fields)}"
        )
    return value


def _string(
    value: Any,
    location: str,
    *,
    maximum: int = MAX_TEXT_CHARACTERS,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SummaryV2Error(f"{location} must be a non-empty string")
    if value != value.strip():
        raise SummaryV2Error(f"{location} must not have surrounding whitespace")
    if len(value) > maximum:
        raise SummaryV2Error(f"{location} exceeds {maximum} characters")
    return value


def _ordered_unique_strings(
    value: Any,
    location: str,
    *,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise SummaryV2Error(
            f"{location} must contain between 1 and {maximum} strings"
        )
    if any(not isinstance(item, str) or not item for item in value):
        raise SummaryV2Error(f"{location} contains an invalid string")
    if len(value) != len(set(value)):
        raise SummaryV2Error(f"{location} contains duplicates")
    return value


def _source_refs(
    value: Any,
    source: dict[str, Any],
    location: str,
) -> list[str]:
    refs = _ordered_unique_strings(
        value, location, maximum=MAX_REFS_PER_ITEM
    )
    allowed = set(source["source_refs"])
    outside = sorted(set(refs) - allowed)
    if outside:
        raise SummaryV2Error(
            f"{location} contains refs outside the source: {', '.join(outside)}"
        )
    order = {source_ref: index for index, source_ref in enumerate(source["source_refs"])}
    if refs != sorted(refs, key=order.__getitem__):
        raise SummaryV2Error(f"{location} is not in source order")
    return refs


def _flatten_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        if value:
            strings.append(value)
    elif isinstance(value, dict):
        for key in sorted(value):
            strings.extend(_flatten_strings(value[key]))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_flatten_strings(item))
    return strings


def _tool_locators(record: dict[str, Any]) -> list[dict[str, str]]:
    speaker = str(record.get("speaker") or record.get("role") or "")
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    phase = str(source.get("phase") or "")
    if speaker != "tool" and phase not in {"tool_activity", "file_change"}:
        return []
    text = str(record.get("text") or record.get("content") or "")
    lines = text.splitlines()
    locators: list[dict[str, str]] = []
    if lines and lines[0].strip():
        first = lines[0].strip()[:MAX_ANCHOR_CHARACTERS]
        kind = "command" if first.startswith("Ran ") else "tool"
        locators.append({"text": first, "kind": kind})
    for line in lines:
        match = re.match(r"^File:\s+(.+?)\s+\[[^]]+\]", line.strip())
        if match:
            locators.append(
                {
                    "text": match.group(1).strip()[:MAX_ANCHOR_CHARACTERS],
                    "kind": "path",
                }
            )
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for locator in locators:
        text = locator["text"].strip()[:MAX_ANCHOR_CHARACTERS]
        if text:
            normalized = {**locator, "text": text}
            unique[(text, locator["kind"])] = normalized
    return list(unique.values())


def _raw_source_manifest(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": int(record["sequence"]),
            "message_id": str(record["message_id"]),
            "content_sha256": raw_record_sha256(record),
        }
        for record in sorted(records, key=lambda item: int(item["sequence"]))
    ]


def build_level_1_source(job: dict[str, Any]) -> dict[str, Any]:
    validated = validate_job(job)
    records = validated["source_records"]
    values_by_ref = {
        str(record["message_id"]): _flatten_strings(record) for record in records
    }
    required_locators: list[dict[str, str]] = []
    for record in records:
        message_id = str(record["message_id"])
        for locator in _tool_locators(record):
            required_locators.append({"source_ref": message_id, **locator})
    source_refs = list(validated["source_message_ids"])
    target_summary_id = job.get("target_summary_id")
    parallel_summary_id = (
        str(target_summary_id)
        if isinstance(target_summary_id, str) and target_summary_id
        else f"L1-v2-{validated['source_sha256'][:16]}"
    )
    return {
        "source_kind": SOURCE_LEVEL_1,
        "summary_level": 1,
        "job_id": validated["job_id"],
        "parallel_summary_id": parallel_summary_id,
        "conversation_id": validated["conversation_id"],
        "source_sha256": validated["source_sha256"],
        "source_refs": source_refs,
        "ref_catalog": [
            {"source_ref": message_id, "source_message_ids": [message_id]}
            for message_id in source_refs
        ],
        "source_manifest": {
            "kind": SOURCE_LEVEL_1,
            "records": _raw_source_manifest(records),
        },
        "required_locators": required_locators,
        "values_by_ref": values_by_ref,
        "prompt_payload": {
            "source_records": records,
        },
    }


def _sidecar_source_values(sidecar: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for group in ("overview", "scenes", "atoms", "retrieval_anchors"):
        for item in sidecar[group]:
            result[item["item_id"]] = _flatten_strings(item)
    return result


def _sidecar_ref_catalog(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for group in ("overview", "scenes", "atoms", "retrieval_anchors"):
        for item in sidecar[group]:
            result.append(
                {
                    "source_ref": item["item_id"],
                    "source_message_ids": item["source_message_ids"],
                }
            )
    return result


PROMOTED_STATUSES = {
    "accepted_decision",
    "open_question",
    "uncertain",
    "withdrawn",
}
PROMOTED_RELATIONS = {"revises", "contradicts", "supersedes"}


def _promotion_manifest(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    """Select durable state that a parent must carry, without copying ordinary detail."""
    related_ids = {
        item_id
        for relation in sidecar["relations"]
        if relation["relation_type"] in PROMOTED_RELATIONS
        for item_id in (relation["from_item_id"], relation["to_item_id"])
    }
    promoted_by_state: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for atom in sidecar["atoms"]:
        reasons: list[str] = []
        if atom["epistemic_status"] in PROMOTED_STATUSES:
            reasons.append("durable-status")
        if atom["atom_type"] == "work_task":
            reasons.append("task-or-commitment")
        if atom["atom_type"] == "work_artifact":
            reasons.append("artifact-route")
        if atom["item_id"] in related_ids:
            reasons.append("correction-or-conflict")
        if not reasons:
            continue
        key = (
            atom["atom_type"],
            atom["statement"],
            atom["epistemic_status"],
            atom["scope"],
        )
        existing = promoted_by_state.get(key)
        if existing is None:
            promoted_by_state[key] = {
                "child_summary_id": sidecar["summary_v2_id"],
                "child_item_id": atom["item_id"],
                "atom_type": atom["atom_type"],
                "statement": atom["statement"],
                "epistemic_status": atom["epistemic_status"],
                "scope": atom["scope"],
                "promotion_reasons": reasons,
                "source_message_ids": atom["source_message_ids"],
            }
            continue
        existing["promotion_reasons"] = list(
            dict.fromkeys([*existing["promotion_reasons"], *reasons])
        )
        raw_order = {
            message_id: index
            for index, message_id in enumerate(sidecar["source"]["raw_message_ids"])
        }
        existing["source_message_ids"] = sorted(
            set(existing["source_message_ids"]) | set(atom["source_message_ids"]),
            key=raw_order.__getitem__,
        )
    return list(promoted_by_state.values())


def build_parent_source(
    children: Iterable[dict[str, Any]],
    *,
    parallel_summary_id: str | None = None,
) -> dict[str, Any]:
    validated_children = [validate_sidecar(child) for child in children]
    if len(validated_children) < 2:
        raise SummaryV2Error("a parent summary-v2 requires at least two child sidecars")
    levels = {int(child["summary_level"]) for child in validated_children}
    conversations = {str(child["conversation_id"]) for child in validated_children}
    if len(levels) != 1 or len(conversations) != 1:
        raise SummaryV2Error("parent children must share one level and conversation")
    child_level = next(iter(levels))
    ordered = sorted(
        validated_children,
        key=lambda child: (
            min(
                int(record["sequence"])
                for record in child["source"]["raw_message_manifest"]
            ),
            child["summary_v2_id"],
        ),
    )
    descriptors = [
        {
            "summary_v2_id": child["summary_v2_id"],
            "summary_level": child["summary_level"],
            "projection_sha256": child["projection_sha256"],
        }
        for child in ordered
    ]
    promotion_manifest = [
        promoted
        for child in ordered
        for promoted in _promotion_manifest(child)
    ]
    source_manifest = {
        "kind": SOURCE_CHILDREN,
        "children": descriptors,
        "promotion_manifest": promotion_manifest,
    }
    source_sha = canonical_sha256(source_manifest)
    source_refs = [child["summary_v2_id"] for child in ordered]
    ref_catalog = [
        {
            "source_ref": child["summary_v2_id"],
            "source_message_ids": child["source"]["raw_message_ids"],
        }
        for child in ordered
    ]
    values_by_ref = {
        child["summary_v2_id"]: _flatten_strings(
            {
                "overview": child["overview"],
                "scenes": child["scenes"],
                "atoms": child["atoms"],
                "relations": child["relations"],
                "retrieval_anchors": child["retrieval_anchors"],
            }
        )
        for child in ordered
    }
    if len(source_refs) > MAX_SOURCE_REFS:
        raise SummaryV2Error(
            f"parent source exceeds the {MAX_SOURCE_REFS}-evidence-unit staged limit"
        )
    conversation_id = next(iter(conversations))
    return {
        "source_kind": SOURCE_CHILDREN,
        "summary_level": child_level + 1,
        "job_id": f"summary-v2-parent-{source_sha[:24]}",
        "parallel_summary_id": (
            parallel_summary_id
            if parallel_summary_id
            else f"L{child_level + 1}-v2-{source_sha[:16]}"
        ),
        "conversation_id": conversation_id,
        "source_sha256": source_sha,
        "source_refs": source_refs,
        "ref_catalog": ref_catalog,
        "source_manifest": source_manifest,
        "required_locators": [],
        "promotion_manifest": promotion_manifest,
        "values_by_ref": values_by_ref,
        "prompt_payload": {"child_sidecars": ordered},
    }


def build_rescue_reduce_source(
    formal_source: dict[str, Any],
    map_sidecars: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if formal_source.get("source_kind") != SOURCE_LEVEL_1:
        raise SummaryV2Error("rescue reduce requires one formal Level-1 source")
    maps = [validate_sidecar(sidecar) for sidecar in map_sidecars]
    if len(maps) < 2:
        raise SummaryV2Error("rescue reduce requires at least two map sidecars")
    if any(
        sidecar["summary_level"] != 1
        or sidecar["conversation_id"] != formal_source["conversation_id"]
        or sidecar["source"]["source_kind"] not in {SOURCE_LEVEL_1, SOURCE_RESCUE_MAPS}
        for sidecar in maps
    ):
        raise SummaryV2Error("rescue maps must be Level-1 summaries from one conversation")
    sequence = {
        record["message_id"]: int(record["sequence"])
        for record in formal_source["source_manifest"]["records"]
    }
    ordered = sorted(
        maps,
        key=lambda sidecar: min(sequence[item] for item in sidecar["source"]["raw_message_ids"]),
    )
    covered = [
        message_id
        for sidecar in ordered
        for message_id in sidecar["source"]["raw_message_ids"]
    ]
    if covered != formal_source["source_refs"] or len(covered) != len(set(covered)):
        raise SummaryV2Error("rescue maps must form one exact ordered partition")
    return {
        **formal_source,
        "source_kind": SOURCE_RESCUE_MAPS,
        "prompt_payload": {"map_sidecars": ordered},
    }


def build_parent_rescue_reduce_source(
    formal_source: dict[str, Any],
    map_sidecars: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if formal_source.get("source_kind") != SOURCE_CHILDREN:
        raise SummaryV2Error("parent rescue requires one formal parent source")
    maps = [validate_sidecar(sidecar) for sidecar in map_sidecars]
    if len(maps) < 2:
        raise SummaryV2Error("parent rescue requires at least two map sidecars")
    if any(
        sidecar["summary_level"] != formal_source["summary_level"]
        or sidecar["conversation_id"] != formal_source["conversation_id"]
        for sidecar in maps
    ):
        raise SummaryV2Error("parent rescue maps must share the formal parent identity scope")
    formal_order = {value: index for index, value in enumerate(formal_source["source_refs"])}
    ordered = sorted(
        maps,
        key=lambda sidecar: min(
            formal_order[value] for value in sidecar["source"]["source_refs"]
        ),
    )
    covered = [
        source_ref
        for sidecar in ordered
        for source_ref in sidecar["source"]["source_refs"]
    ]
    if covered != formal_source["source_refs"] or len(covered) != len(set(covered)):
        raise SummaryV2Error("parent rescue maps must partition the direct child summaries")
    return {
        **formal_source,
        "source_kind": SOURCE_PARENT_RESCUE_MAPS,
        "prompt_payload": {"map_sidecars": ordered},
    }


def public_source(source: dict[str, Any]) -> dict[str, Any]:
    catalog = {entry["source_ref"]: entry["source_message_ids"] for entry in source["ref_catalog"]}
    raw_ids: list[str] = []
    for source_ref in source["source_refs"]:
        for message_id in catalog[source_ref]:
            if message_id not in raw_ids:
                raw_ids.append(message_id)
    raw_sequence: dict[str, int] = {}
    if source["source_kind"] in {SOURCE_LEVEL_1, SOURCE_RESCUE_MAPS}:
        raw_manifest = list(source["source_manifest"]["records"])
    else:
        raw_manifest_by_id: dict[str, dict[str, Any]] = {}
        payload_key = (
            "map_sidecars"
            if source["source_kind"] == SOURCE_PARENT_RESCUE_MAPS
            else "child_sidecars"
        )
        for child in source["prompt_payload"][payload_key]:
            for record in child["source"]["raw_message_manifest"]:
                previous = raw_manifest_by_id.get(record["message_id"])
                if previous is not None and previous != record:
                    raise SummaryV2Error("child sidecars disagree on raw message identity")
                raw_manifest_by_id[record["message_id"]] = record
        raw_manifest = sorted(
            raw_manifest_by_id.values(), key=lambda item: int(item["sequence"])
        )
    for record in raw_manifest:
        raw_sequence[record["message_id"]] = int(record["sequence"])
    raw_ids = sorted(set(raw_ids), key=raw_sequence.__getitem__)
    return {
        "source_kind": source["source_kind"],
        "summary_level": source["summary_level"],
        "job_id": source["job_id"],
        "parallel_summary_id": source["parallel_summary_id"],
        "conversation_id": source["conversation_id"],
        "source_sha256": source["source_sha256"],
        "source_refs": source["source_refs"],
        "ref_catalog": source["ref_catalog"],
        "source_manifest": source["source_manifest"],
        "raw_message_ids": raw_ids,
        "raw_message_manifest": raw_manifest,
        "required_locators": source["required_locators"],
    }


def _validate_local_id(value: Any, location: str, used: set[str]) -> str:
    local_id = _string(value, location, maximum=64)
    if LOCAL_ID_RE.fullmatch(local_id) is None:
        raise SummaryV2Error(f"{location} has an invalid format")
    if local_id in used:
        raise SummaryV2Error(f"duplicate candidate local ID: {local_id}")
    used.add(local_id)
    return local_id


def normalize_model_candidate(candidate: Any, source: dict[str, Any]) -> Any:
    """Canonicalize harmless model formatting while preserving strict validation."""
    if not isinstance(candidate, dict):
        return candidate
    normalized = json.loads(json.dumps(candidate, ensure_ascii=False))
    source_order = {
        source_ref: index for index, source_ref in enumerate(source["source_refs"])
    }

    def refs(value: Any) -> Any:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return value
        repaired: list[str] = []
        for item in value:
            if item in source_order:
                repaired.append(item)
                continue
            prefix_matches = [
                source_ref
                for source_ref in source["source_refs"]
                if item.startswith(source_ref)
                and len(item) > len(source_ref)
            ]
            repaired.append(prefix_matches[0] if len(prefix_matches) == 1 else item)
        unique: list[Any] = []
        for item in repaired:
            if item not in unique:
                unique.append(item)
        return sorted(
            unique,
            key=lambda item: source_order.get(item, len(source_order) + unique.index(item)),
        )

    text_fields = {
        "overview": ("text",),
        "scenes": ("title", "summary"),
        "atoms": ("statement", "scope"),
        "omissions": ("reason",),
    }
    for group, fields in text_fields.items():
        for item in normalized.get(group, []):
            if not isinstance(item, dict):
                continue
            if "source_refs" in item:
                item["source_refs"] = refs(item["source_refs"])
            for field in fields:
                if isinstance(item.get(field), str):
                    item[field] = item[field].strip()

    used_ids = {
        item.get("local_id")
        for group in ("overview", "scenes", "atoms")
        for item in normalized.get(group, [])
        if isinstance(item, dict) and isinstance(item.get("local_id"), str)
    }
    anchors: list[dict[str, Any]] = []
    for index, locator in enumerate(source["required_locators"], 1):
        local_id = f"required_locator_{index}"
        while local_id in used_ids:
            local_id = "mw_" + local_id
        used_ids.add(local_id)
        anchors.append(
            {
                "local_id": local_id,
                "text": locator["text"],
                "kind": locator["kind"],
                "source_refs": [locator["source_ref"]],
            }
        )
    normalized["retrieval_anchors"] = anchors

    atom_by_id = {
        item.get("local_id"): item
        for item in normalized.get("atoms", [])
        if isinstance(item, dict) and isinstance(item.get("local_id"), str)
    }
    relations: list[Any] = []
    for item in normalized.get("relations", []):
        if not isinstance(item, dict):
            relations.append(item)
            continue
        left = atom_by_id.get(item.get("from_local_id"))
        right = atom_by_id.get(item.get("to_local_id"))
        if left is None or right is None or left is right:
            continue
        if item.get("relation_type") not in RELATION_TYPES:
            continue
        allowed = set(left.get("source_refs", [])) | set(right.get("source_refs", []))
        canonical_refs = refs(item.get("source_refs"))
        if not isinstance(canonical_refs, list):
            relations.append(item)
            continue
        relation_refs = [value for value in canonical_refs if value in allowed]
        if not relation_refs:
            continue
        item["source_refs"] = relation_refs
        relations.append(item)
    normalized["relations"] = relations
    omissions = normalized.get("omissions")
    if isinstance(omissions, list) and all(isinstance(item, dict) for item in omissions):
        normalized["omissions"] = sorted(
            omissions,
            key=lambda item: source_order.get(item.get("source_ref"), len(source_order)),
        )
    if source["source_kind"] in {SOURCE_RESCUE_MAPS, SOURCE_PARENT_RESCUE_MAPS}:
        maps = source["prompt_payload"]["map_sidecars"]
        content_refs = {
            source_ref
            for group in ("overview", "scenes", "atoms", "retrieval_anchors")
            for item in normalized.get(group, [])
            if isinstance(item, dict)
            for source_ref in item.get("source_refs", [])
        }
        for relation in normalized.get("relations", []):
            if isinstance(relation, dict):
                content_refs.update(relation.get("source_refs", []))
        omission_by_ref = {
            item["source_ref"]: item["reason"]
            for sidecar in maps
            for item in sidecar.get("omissions", [])
        }
        existing_omissions = {
            item.get("source_ref")
            for item in normalized.get("omissions", [])
            if isinstance(item, dict)
        }
        for source_ref in source["source_refs"]:
            if source_ref in content_refs or source_ref in existing_omissions:
                continue
            if source_ref in omission_by_ref:
                normalized.setdefault("omissions", []).append(
                    {"source_ref": source_ref, "reason": omission_by_ref[source_ref]}
                )
                existing_omissions.add(source_ref)
                continue
            evidence = next(
                (
                    sidecar
                    for sidecar in maps
                    if source_ref in sidecar["source"]["source_refs"]
                    and source_ref in sidecar["coverage"]["represented_source_refs"]
                ),
                None,
            )
            if evidence is None:
                continue
            scene = next(
                (item for item in evidence["scenes"] if source_ref in item["source_refs"]),
                evidence["scenes"][0],
            )
            scene_id = f"restored_scene_{len(normalized.get('scenes', [])) + 1}"
            while scene_id in used_ids:
                scene_id = "mw_" + scene_id
            used_ids.add(scene_id)
            normalized.setdefault("scenes", []).append(
                {
                    "local_id": scene_id,
                    "title": scene["title"],
                    "summary": scene["summary"],
                    "source_refs": [source_ref],
                }
            )
            if source["source_kind"] == SOURCE_RESCUE_MAPS:
                atom = next(
                    (item for item in evidence["atoms"] if source_ref in item["source_refs"]),
                    None,
                )
                if atom is not None:
                    atom_id = f"restored_atom_{len(normalized.get('atoms', [])) + 1}"
                    while atom_id in used_ids:
                        atom_id = "mw_" + atom_id
                    used_ids.add(atom_id)
                    normalized.setdefault("atoms", []).append(
                        {
                            "local_id": atom_id,
                            "atom_type": atom["atom_type"],
                            "statement": atom["statement"],
                            "epistemic_status": atom["epistemic_status"],
                            "scope": atom["scope"],
                            "source_refs": [source_ref],
                        }
                    )
            content_refs.add(source_ref)
        scene_refs = {
            source_ref
            for scene in normalized.get("scenes", [])
            if isinstance(scene, dict)
            for source_ref in scene.get("source_refs", [])
        }
        missing_scene_refs = content_refs - scene_refs
        for map_index, sidecar in enumerate(maps, 1):
            missing = [
                source_ref
                for source_ref in sidecar["source"]["source_refs"]
                if source_ref in missing_scene_refs
            ]
            if not missing:
                continue
            title = sidecar["scenes"][0]["title"]
            summary = " ".join(item["text"] for item in sidecar["overview"])
            summary = summary[:MAX_TEXT_CHARACTERS].strip()
            for batch_index in range(0, len(missing), MAX_REFS_PER_ITEM):
                local_id = f"rescue_route_{map_index}_{batch_index // MAX_REFS_PER_ITEM + 1}"
                while local_id in used_ids:
                    local_id = "mw_" + local_id
                used_ids.add(local_id)
                batch = missing[batch_index : batch_index + MAX_REFS_PER_ITEM]
                normalized.setdefault("scenes", []).append(
                    {
                        "local_id": local_id,
                        "title": title,
                        "summary": summary,
                        "source_refs": batch,
                    }
                )
                missing_scene_refs.difference_update(batch)
    content_refs = {
        source_ref
        for group in ("overview", "scenes", "atoms", "retrieval_anchors")
        for item in normalized.get(group, [])
        if isinstance(item, dict)
        for source_ref in item.get("source_refs", [])
    }
    for relation in normalized.get("relations", []):
        if isinstance(relation, dict):
            content_refs.update(relation.get("source_refs", []))
    scene_refs = {
        source_ref
        for scene in normalized.get("scenes", [])
        if isinstance(scene, dict)
        for source_ref in scene.get("source_refs", [])
    }
    for source_ref in sorted(content_refs - scene_refs, key=source_order.__getitem__):
        evidence_text = next(
            (
                str(item.get(field, "")).strip()
                for group, field in (("atoms", "statement"), ("overview", "text"))
                for item in normalized.get(group, [])
                if isinstance(item, dict) and source_ref in item.get("source_refs", [])
                if str(item.get(field, "")).strip()
            ),
            "Source evidence retained for raw-message verification.",
        )[:MAX_TEXT_CHARACTERS]
        local_id = f"source_route_{len(normalized.get('scenes', [])) + 1}"
        while local_id in used_ids:
            local_id = "mw_" + local_id
        used_ids.add(local_id)
        normalized.setdefault("scenes", []).append(
            {
                "local_id": local_id,
                "title": "Source evidence route",
                "summary": evidence_text,
                "source_refs": [source_ref],
            }
        )
    if source["source_kind"] not in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
        detail_refs = {
            source_ref
            for group in ("atoms", "retrieval_anchors")
            for item in normalized.get(group, [])
            if isinstance(item, dict)
            for source_ref in item.get("source_refs", [])
        }
        represented_refs = {
            source_ref
            for group in ("overview", "scenes", "atoms", "retrieval_anchors")
            for item in normalized.get(group, [])
            if isinstance(item, dict)
            for source_ref in item.get("source_refs", [])
        }
        for relation in normalized.get("relations", []):
            if isinstance(relation, dict):
                represented_refs.update(relation.get("source_refs", []))
        missing_detail_refs = represented_refs - detail_refs
        for scene_index, scene in enumerate(normalized.get("scenes", []), 1):
            if not isinstance(scene, dict):
                continue
            missing = [
                source_ref
                for source_ref in scene.get("source_refs", [])
                if source_ref in missing_detail_refs
            ]
            if not missing:
                continue
            title = str(scene.get("title", "")).strip()
            if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", title):
                statement = f"相关细节由场景“{title}”建立导航，需按引用回查原始消息。"
                scope = "Summary V2 原文导航"
            else:
                statement = (
                    f"Source details are routed through the scene '{title}' and "
                    "must be verified against the cited raw messages."
                )
                scope = "Summary V2 source navigation"
            for batch_index in range(0, len(missing), MAX_REFS_PER_ITEM):
                local_id = (
                    f"detail_route_{scene_index}_"
                    f"{batch_index // MAX_REFS_PER_ITEM + 1}"
                )
                while local_id in used_ids:
                    local_id = "mw_" + local_id
                used_ids.add(local_id)
                batch = missing[batch_index : batch_index + MAX_REFS_PER_ITEM]
                normalized.setdefault("atoms", []).append(
                    {
                        "local_id": local_id,
                        "atom_type": "work_fact",
                        "statement": statement,
                        "epistemic_status": "explicit_fact",
                        "scope": scope,
                        "source_refs": batch,
                    }
                )
                missing_detail_refs.difference_update(batch)
    represented_refs = {
        source_ref
        for group in ("overview", "scenes", "atoms", "retrieval_anchors")
        for item in normalized.get(group, [])
        if isinstance(item, dict)
        for source_ref in item.get("source_refs", [])
    }
    for relation in normalized.get("relations", []):
        if isinstance(relation, dict):
            represented_refs.update(relation.get("source_refs", []))
    if isinstance(normalized.get("omissions"), list):
        normalized["omissions"] = sorted(
            [
                item
                for item in normalized["omissions"]
                if isinstance(item, dict)
                and item.get("source_ref") not in represented_refs
            ],
            key=lambda item: source_order.get(item.get("source_ref"), len(source_order)),
        )
    return normalized


def validate_candidate(candidate: Any, source: dict[str, Any]) -> dict[str, Any]:
    is_parent = source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
    candidate = _exact(
        candidate,
        {
            "format_version",
            "job_id",
            "summary_level",
            "source_sha256",
            "overview",
            "scenes",
            "atoms",
            "relations",
            "retrieval_anchors",
            "omissions",
        },
        "candidate",
    )
    if candidate["format_version"] != FORMAT_VERSION:
        raise SummaryV2Error("candidate.format_version must be 2")
    if candidate["job_id"] != source["job_id"]:
        raise SummaryV2Error("candidate.job_id does not match the source")
    if candidate["summary_level"] != source["summary_level"]:
        raise SummaryV2Error("candidate.summary_level does not match the source")
    if candidate["source_sha256"] != source["source_sha256"]:
        raise SummaryV2Error("candidate.source_sha256 does not match the source")
    used_local_ids: set[str] = set()

    def array(name: str, maximum: int, *, minimum: int = 0) -> list[Any]:
        value = candidate[name]
        if not isinstance(value, list) or not minimum <= len(value) <= maximum:
            raise SummaryV2Error(
                f"candidate.{name} must contain between {minimum} and {maximum} items"
            )
        return value

    overview: list[dict[str, Any]] = []
    for index, raw in enumerate(array("overview", MAX_OVERVIEW_ITEMS, minimum=1)):
        location = f"candidate.overview[{index}]"
        item = _exact(raw, {"local_id", "text", "source_refs"}, location)
        overview.append(
            {
                "local_id": _validate_local_id(item["local_id"], f"{location}.local_id", used_local_ids),
                "text": _string(item["text"], f"{location}.text"),
                "source_refs": _source_refs(item["source_refs"], source, f"{location}.source_refs"),
            }
        )

    scenes: list[dict[str, Any]] = []
    for index, raw in enumerate(array("scenes", MAX_SCENES, minimum=1)):
        location = f"candidate.scenes[{index}]"
        item = _exact(raw, {"local_id", "title", "summary", "source_refs"}, location)
        scenes.append(
            {
                "local_id": _validate_local_id(item["local_id"], f"{location}.local_id", used_local_ids),
                "title": _string(item["title"], f"{location}.title", maximum=512),
                "summary": _string(item["summary"], f"{location}.summary"),
                "source_refs": _source_refs(item["source_refs"], source, f"{location}.source_refs"),
            }
        )

    atoms: list[dict[str, Any]] = []
    atom_by_local: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(array("atoms", MAX_ATOMS, minimum=0 if is_parent else 1)):
        location = f"candidate.atoms[{index}]"
        item = _exact(
            raw,
            {
                "local_id",
                "atom_type",
                "statement",
                "epistemic_status",
                "scope",
                "source_refs",
            },
            location,
        )
        local_id = _validate_local_id(item["local_id"], f"{location}.local_id", used_local_ids)
        atom_type = item["atom_type"]
        status = item["epistemic_status"]
        if atom_type not in ATOM_TYPES:
            raise SummaryV2Error(f"{location}.atom_type is unsupported")
        if status not in ALLOWED_STATUS_BY_TYPE[atom_type]:
            raise SummaryV2Error(f"{location}.epistemic_status is incompatible")
        normalized = {
            "local_id": local_id,
            "atom_type": atom_type,
            "statement": _string(
                item["statement"],
                f"{location}.statement",
                maximum=MAX_STATEMENT_CHARACTERS,
            ),
            "epistemic_status": status,
            "scope": _string(item["scope"], f"{location}.scope", maximum=MAX_SCOPE_CHARACTERS),
            "source_refs": _source_refs(item["source_refs"], source, f"{location}.source_refs"),
        }
        atoms.append(normalized)
        atom_by_local[local_id] = normalized

    anchors: list[dict[str, Any]] = []
    anchor_limit = max(MAX_ANCHORS, len(source["required_locators"]))
    if anchor_limit > MAX_DETERMINISTIC_ANCHORS:
        raise SummaryV2Error("deterministic retrieval anchors exceed the staged limit")
    for index, raw in enumerate(array("retrieval_anchors", anchor_limit)):
        location = f"candidate.retrieval_anchors[{index}]"
        item = _exact(raw, {"local_id", "text", "kind", "source_refs"}, location)
        kind = item["kind"]
        if kind not in ANCHOR_KINDS:
            raise SummaryV2Error(f"{location}.kind is unsupported")
        text = _string(item["text"], f"{location}.text", maximum=MAX_ANCHOR_CHARACTERS)
        refs = _source_refs(item["source_refs"], source, f"{location}.source_refs")
        if not any(
            text in source_value
            for source_ref in refs
            for source_value in source["values_by_ref"].get(source_ref, [])
        ):
            raise SummaryV2Error(f"{location}.text is not an exact source substring")
        anchors.append(
            {
                "local_id": _validate_local_id(item["local_id"], f"{location}.local_id", used_local_ids),
                "text": text,
                "kind": kind,
                "source_refs": refs,
            }
        )
    if is_parent and anchors:
        raise SummaryV2Error(
            "parent candidate must route exact locators through child summaries"
        )

    relations: list[dict[str, Any]] = []
    relation_keys: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(array("relations", MAX_RELATIONS)):
        location = f"candidate.relations[{index}]"
        item = _exact(
            raw,
            {"from_local_id", "to_local_id", "relation_type", "source_refs"},
            location,
        )
        from_id = item["from_local_id"]
        to_id = item["to_local_id"]
        relation_type = item["relation_type"]
        if from_id not in atom_by_local or to_id not in atom_by_local or from_id == to_id:
            raise SummaryV2Error(f"{location} has invalid atom references")
        if relation_type not in RELATION_TYPES:
            raise SummaryV2Error(f"{location}.relation_type is unsupported")
        key = (from_id, to_id, relation_type)
        if key in relation_keys:
            raise SummaryV2Error(f"{location} duplicates a relation")
        relation_keys.add(key)
        refs = _source_refs(item["source_refs"], source, f"{location}.source_refs")
        if not set(refs).issubset(
            set(atom_by_local[from_id]["source_refs"])
            | set(atom_by_local[to_id]["source_refs"])
        ):
            raise SummaryV2Error(f"{location}.source_refs is outside its atoms")
        relations.append(
            {
                "from_local_id": from_id,
                "to_local_id": to_id,
                "relation_type": relation_type,
                "source_refs": refs,
            }
        )

    omissions: list[dict[str, str]] = []
    omitted_refs: set[str] = set()
    source_order = {source_ref: index for index, source_ref in enumerate(source["source_refs"])}
    for index, raw in enumerate(array("omissions", MAX_OMISSIONS)):
        location = f"candidate.omissions[{index}]"
        item = _exact(raw, {"source_ref", "reason"}, location)
        source_ref = item["source_ref"]
        if source_ref not in source_order:
            raise SummaryV2Error(f"{location}.source_ref is outside the source")
        if source_ref in omitted_refs:
            raise SummaryV2Error(f"{location}.source_ref is duplicated")
        omitted_refs.add(source_ref)
        omissions.append(
            {
                "source_ref": source_ref,
                "reason": _string(item["reason"], f"{location}.reason", maximum=1000),
            }
        )
    if omissions != sorted(omissions, key=lambda item: source_order[item["source_ref"]]):
        raise SummaryV2Error("candidate.omissions is not in source order")
    if is_parent and omissions:
        raise SummaryV2Error("parent candidate cannot omit a direct child summary")

    overview_refs = {source_ref for item in overview for source_ref in item["source_refs"]}
    scene_refs = {source_ref for item in scenes for source_ref in item["source_refs"]}
    detail_refs = {
        source_ref
        for item in [*atoms, *anchors]
        for source_ref in item["source_refs"]
    }
    relation_refs = {source_ref for item in relations for source_ref in item["source_refs"]}
    represented_refs = overview_refs | scene_refs | detail_refs | relation_refs
    overlap = represented_refs & omitted_refs
    if overlap:
        raise SummaryV2Error(
            "candidate represents and omits the same refs: " + ", ".join(sorted(overlap))
        )
    expected_refs = set(source["source_refs"])
    if represented_refs | omitted_refs != expected_refs:
        missing = sorted(expected_refs - represented_refs - omitted_refs)
        raise SummaryV2Error("candidate silently loses source refs: " + ", ".join(missing))
    if not represented_refs:
        raise SummaryV2Error("candidate cannot omit every source ref")
    if not represented_refs.issubset(scene_refs):
        raise SummaryV2Error("every represented source ref must appear in a scene")
    if not is_parent and not represented_refs.issubset(detail_refs):
        raise SummaryV2Error(
            "every represented source ref must appear in an atom or retrieval anchor"
        )

    if is_parent:
        missing_routes = expected_refs - scene_refs
        if missing_routes:
            raise SummaryV2Error(
                "parent candidate has no navigable scene route for child summaries: "
                + ", ".join(sorted(missing_routes))
            )
        for promoted in source["promotion_manifest"]:
            matches = [
                atom
                for atom in atoms
                if atom["atom_type"] == promoted["atom_type"]
                and atom["statement"] == promoted["statement"]
                and atom["epistemic_status"] == promoted["epistemic_status"]
                and atom["scope"] == promoted["scope"]
                and promoted["child_summary_id"] in atom["source_refs"]
            ]
            if not matches:
                raise SummaryV2Error(
                    "parent candidate lost promoted durable state: "
                    + promoted["child_item_id"]
                )

    for required in source["required_locators"]:
        matches = [
            anchor
            for anchor in anchors
            if anchor["text"] == required["text"]
            and required["source_ref"] in anchor["source_refs"]
        ]
        if not matches:
            raise SummaryV2Error(
                "candidate lost required locator: " + required["text"]
            )

    return {
        "overview": overview,
        "scenes": scenes,
        "atoms": atoms,
        "relations": relations,
        "retrieval_anchors": anchors,
        "omissions": omissions,
    }


def _raw_ids_for_refs(source: dict[str, Any], refs: list[str]) -> list[str]:
    catalog = {
        entry["source_ref"]: entry["source_message_ids"]
        for entry in source["ref_catalog"]
    }
    sequence = {
        record["message_id"]: int(record["sequence"])
        for record in public_source(source)["raw_message_manifest"]
    }
    return sorted(
        {
            message_id
            for source_ref in refs
            for message_id in catalog[source_ref]
        },
        key=sequence.__getitem__,
    )


def _project_items(
    items: list[dict[str, Any]],
    prefix: str,
    source: dict[str, Any],
    summary_identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    projected: list[dict[str, Any]] = []
    id_by_local: dict[str, str] = {}
    for item in items:
        without_local = {key: value for key, value in item.items() if key != "local_id"}
        item_id = prefix + "-" + canonical_sha256(
            {**summary_identity, **without_local}
        )[:32]
        id_by_local[item["local_id"]] = item_id
        projected.append(
            {
                "item_id": item_id,
                **without_local,
                "source_message_ids": _raw_ids_for_refs(source, item["source_refs"]),
            }
        )
    return projected, id_by_local


def _candidate_semantic_identity(normalized: dict[str, Any]) -> dict[str, Any]:
    atom_index = {
        atom["local_id"]: index for index, atom in enumerate(normalized["atoms"])
    }
    return {
        "overview": [
            {key: value for key, value in item.items() if key != "local_id"}
            for item in normalized["overview"]
        ],
        "scenes": [
            {key: value for key, value in item.items() if key != "local_id"}
            for item in normalized["scenes"]
        ],
        "atoms": [
            {key: value for key, value in item.items() if key != "local_id"}
            for item in normalized["atoms"]
        ],
        "relations": sorted(
            [
                {
                    "from_atom_index": atom_index[item["from_local_id"]],
                    "to_atom_index": atom_index[item["to_local_id"]],
                    "relation_type": item["relation_type"],
                    "source_refs": item["source_refs"],
                }
                for item in normalized["relations"]
            ],
            key=lambda item: (
                item["from_atom_index"],
                item["to_atom_index"],
                item["relation_type"],
            ),
        ),
        "retrieval_anchors": [
            {key: value for key, value in item.items() if key != "local_id"}
            for item in normalized["retrieval_anchors"]
        ],
        "omissions": normalized["omissions"],
    }


def project(source: dict[str, Any], candidate: Any) -> dict[str, Any]:
    normalized = validate_candidate(candidate, source)
    summary_identity = {
        "summary_level": source["summary_level"],
        "source_kind": source["source_kind"],
        "job_id": source["job_id"],
        "parallel_summary_id": source["parallel_summary_id"],
        "conversation_id": source["conversation_id"],
        "source_sha256": source["source_sha256"],
        "candidate": _candidate_semantic_identity(normalized),
    }
    summary_v2_id = "summary-v2-" + canonical_sha256(summary_identity)[:32]
    item_identity = {
        "summary_v2_id": summary_v2_id,
        "source_sha256": source["source_sha256"],
    }
    overview, _ = _project_items(normalized["overview"], "overview", source, item_identity)
    scenes, _ = _project_items(normalized["scenes"], "scene", source, item_identity)
    atoms, atom_id_by_local = _project_items(normalized["atoms"], "atom", source, item_identity)
    anchors, _ = _project_items(
        normalized["retrieval_anchors"], "anchor", source, item_identity
    )
    relations: list[dict[str, Any]] = []
    for relation in normalized["relations"]:
        projected = {
            "from_item_id": atom_id_by_local[relation["from_local_id"]],
            "to_item_id": atom_id_by_local[relation["to_local_id"]],
            "relation_type": relation["relation_type"],
            "source_refs": relation["source_refs"],
            "source_message_ids": _raw_ids_for_refs(source, relation["source_refs"]),
        }
        relations.append(
            {
                "item_id": "relation-" + canonical_sha256({**item_identity, **projected})[:32],
                **projected,
            }
        )
    relations.sort(key=lambda item: item["item_id"])
    omissions = []
    for omission in normalized["omissions"]:
        projected = {
            **omission,
            "source_message_ids": _raw_ids_for_refs(source, [omission["source_ref"]]),
        }
        omissions.append(
            {
                "item_id": "omission-" + canonical_sha256({**item_identity, **projected})[:32],
                **projected,
            }
        )
    represented_refs = sorted(
        {
            source_ref
            for group in (overview, scenes, atoms, anchors, relations)
            for item in group
            for source_ref in item["source_refs"]
        },
        key={value: index for index, value in enumerate(source["source_refs"])}.__getitem__,
    )
    result = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "projector": (
            PARENT_PROJECTOR
            if source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
            else PROJECTOR
        ),
        "summary_v2_id": summary_v2_id,
        "summary_level": source["summary_level"],
        "parallel_summary_id": source["parallel_summary_id"],
        "conversation_id": source["conversation_id"],
        "source": public_source(source),
        "overview": overview,
        "scenes": scenes,
        "atoms": atoms,
        "relations": relations,
        "retrieval_anchors": anchors,
        "omissions": omissions,
        "coverage": {
            "source_ref_count": len(source["source_refs"]),
            "represented_source_refs": represented_refs,
            "omitted_source_refs": [item["source_ref"] for item in omissions],
            "raw_message_count": len(public_source(source)["raw_message_ids"]),
            "raw_message_ids": public_source(source)["raw_message_ids"],
            "silent_loss_count": 0,
        },
        "metrics": {
            "overview_count": len(overview),
            "scene_count": len(scenes),
            "atom_count": len(atoms),
            "relation_count": len(relations),
            "retrieval_anchor_count": len(anchors),
            "omission_count": len(omissions),
            "required_locator_count": len(source["required_locators"]),
        },
    }
    result["projection_sha256"] = canonical_sha256(result)
    validate_sidecar(result, source)
    return result


def _validate_item_id(item: dict[str, Any], location: str) -> None:
    item_id = item.get("item_id")
    if not isinstance(item_id, str) or FINAL_ID_RE.fullmatch(item_id) is None:
        raise SummaryV2Error(f"{location}.item_id is malformed")


def validate_sidecar(
    value: Any,
    expected_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sidecar = _exact(
        value,
        {
            "format",
            "format_version",
            "projector",
            "summary_v2_id",
            "summary_level",
            "parallel_summary_id",
            "conversation_id",
            "source",
            "overview",
            "scenes",
            "atoms",
            "relations",
            "retrieval_anchors",
            "omissions",
            "coverage",
            "metrics",
            "projection_sha256",
        },
        "sidecar",
    )
    if sidecar["format"] != FORMAT or sidecar["format_version"] != FORMAT_VERSION:
        raise SummaryV2Error("sidecar format is unsupported")
    if not isinstance(sidecar["summary_level"], int) or sidecar["summary_level"] < 1:
        raise SummaryV2Error("sidecar.summary_level is malformed")
    _string(sidecar["summary_v2_id"], "sidecar.summary_v2_id", maximum=128)
    _string(sidecar["parallel_summary_id"], "sidecar.parallel_summary_id", maximum=256)
    _string(sidecar["conversation_id"], "sidecar.conversation_id", maximum=512)
    source = _exact(
        sidecar["source"],
        {
            "source_kind",
            "summary_level",
            "job_id",
            "parallel_summary_id",
            "conversation_id",
            "source_sha256",
            "source_refs",
            "ref_catalog",
            "source_manifest",
            "raw_message_ids",
            "raw_message_manifest",
            "required_locators",
        },
        "sidecar.source",
    )
    if expected_source is not None and source != public_source(expected_source):
        raise SummaryV2Error("sidecar source does not match the validated source bundle")
    if source["source_kind"] not in {
        SOURCE_LEVEL_1,
        SOURCE_CHILDREN,
        SOURCE_RESCUE_MAPS,
        SOURCE_PARENT_RESCUE_MAPS,
    }:
        raise SummaryV2Error("sidecar.source.source_kind is unsupported")
    expected_projector = (
        PARENT_PROJECTOR
        if source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
        else PROJECTOR
    )
    if sidecar["projector"] != expected_projector:
        raise SummaryV2Error("sidecar projector is unsupported for its source kind")
    _string(source["job_id"], "sidecar.source.job_id", maximum=256)
    _string(
        source["parallel_summary_id"],
        "sidecar.source.parallel_summary_id",
        maximum=256,
    )
    _string(
        source["conversation_id"],
        "sidecar.source.conversation_id",
        maximum=512,
    )
    if (
        source["summary_level"] != sidecar["summary_level"]
        or source["parallel_summary_id"] != sidecar["parallel_summary_id"]
        or source["conversation_id"] != sidecar["conversation_id"]
    ):
        raise SummaryV2Error("sidecar top-level identity disagrees with its source")
    if not isinstance(source["source_sha256"], str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise SummaryV2Error("sidecar.source.source_sha256 is malformed")
    source_refs = _ordered_unique_strings(
        source["source_refs"], "sidecar.source.source_refs", maximum=MAX_SOURCE_REFS
    )
    ref_catalog = source["ref_catalog"]
    if not isinstance(ref_catalog, list) or len(ref_catalog) != len(source_refs):
        raise SummaryV2Error("sidecar.source.ref_catalog is malformed")
    catalog: dict[str, list[str]] = {}
    for index, raw in enumerate(ref_catalog):
        entry = _exact(raw, {"source_ref", "source_message_ids"}, f"sidecar.source.ref_catalog[{index}]")
        source_ref = entry["source_ref"]
        if source_ref in catalog:
            raise SummaryV2Error("sidecar.source.ref_catalog contains duplicates")
        catalog[source_ref] = _ordered_unique_strings(
            entry["source_message_ids"],
            f"sidecar.source.ref_catalog[{index}].source_message_ids",
            maximum=MAX_SOURCE_REFS,
        )
    if list(catalog) != source_refs:
        raise SummaryV2Error("sidecar.source.ref_catalog order disagrees with source_refs")
    raw_manifest = source["raw_message_manifest"]
    if not isinstance(raw_manifest, list) or not raw_manifest:
        raise SummaryV2Error("sidecar.source.raw_message_manifest is malformed")
    raw_by_id: dict[str, dict[str, Any]] = {}
    sequences: set[int] = set()
    for index, raw in enumerate(raw_manifest):
        record = _exact(
            raw,
            {"sequence", "message_id", "content_sha256"},
            f"sidecar.source.raw_message_manifest[{index}]",
        )
        sequence = record["sequence"]
        message_id = record["message_id"]
        digest = record["content_sha256"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(message_id, str)
            or not message_id
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or sequence in sequences
            or message_id in raw_by_id
        ):
            raise SummaryV2Error("sidecar raw message identity is malformed or duplicated")
        sequences.add(sequence)
        raw_by_id[message_id] = record
    if raw_manifest != sorted(raw_manifest, key=lambda item: item["sequence"]):
        raise SummaryV2Error("sidecar raw message manifest is not sequence ordered")
    raw_message_ids = list(raw_by_id)
    if source["raw_message_ids"] != raw_message_ids:
        raise SummaryV2Error("sidecar raw message IDs disagree with the manifest")
    for source_ref, message_ids in catalog.items():
        if not set(message_ids).issubset(raw_by_id):
            raise SummaryV2Error(f"source ref {source_ref} cites unknown raw messages")
        if message_ids != sorted(
            message_ids, key=lambda message_id: int(raw_by_id[message_id]["sequence"])
        ):
            raise SummaryV2Error(f"source ref {source_ref} raw messages are not ordered")
    if source["source_kind"] in {SOURCE_LEVEL_1, SOURCE_RESCUE_MAPS}:
        manifest = _exact(
            source["source_manifest"], {"kind", "records"}, "sidecar.source.source_manifest"
        )
        if manifest["kind"] != SOURCE_LEVEL_1 or manifest["records"] != raw_manifest:
            raise SummaryV2Error("Level-1 source manifest disagrees with raw manifest")
        if source_refs != raw_message_ids:
            raise SummaryV2Error("Level-1 source refs must equal raw message IDs")
        if any(catalog[source_ref] != [source_ref] for source_ref in source_refs):
            raise SummaryV2Error("Level-1 source refs must map to themselves")
        expected_source_sha = canonical_sha256(raw_manifest)
        if source["source_sha256"] != expected_source_sha:
            raise SummaryV2Error("Level-1 source SHA-256 disagrees with raw manifest")
    else:
        manifest = _exact(
            source["source_manifest"],
            {"kind", "children", "promotion_manifest"},
            "sidecar.source.source_manifest",
        )
        if manifest["kind"] != SOURCE_CHILDREN or not isinstance(manifest["children"], list):
            raise SummaryV2Error("parent source manifest is malformed")
        child_ids: set[str] = set()
        for index, raw_child in enumerate(manifest["children"]):
            child = _exact(
                raw_child,
                {"summary_v2_id", "summary_level", "projection_sha256"},
                f"sidecar.source.source_manifest.children[{index}]",
            )
            if (
                not isinstance(child["summary_v2_id"], str)
                or child["summary_v2_id"] in child_ids
                or child["summary_level"] != sidecar["summary_level"] - 1
                or not isinstance(child["projection_sha256"], str)
                or SHA256_RE.fullmatch(child["projection_sha256"]) is None
            ):
                raise SummaryV2Error("parent child descriptor is malformed")
            child_ids.add(child["summary_v2_id"])
        if source_refs != [child["summary_v2_id"] for child in manifest["children"]]:
            raise SummaryV2Error("parent source refs must be direct child summary IDs")
        promotions = manifest["promotion_manifest"]
        if not isinstance(promotions, list):
            raise SummaryV2Error("parent promotion manifest is malformed")
        promotion_ids: set[str] = set()
        for index, raw_promotion in enumerate(promotions):
            promotion = _exact(
                raw_promotion,
                {
                    "child_summary_id",
                    "child_item_id",
                    "atom_type",
                    "statement",
                    "epistemic_status",
                    "scope",
                    "promotion_reasons",
                    "source_message_ids",
                },
                f"sidecar.source.source_manifest.promotion_manifest[{index}]",
            )
            if (
                promotion["child_summary_id"] not in child_ids
                or promotion["child_item_id"] in promotion_ids
                or promotion["atom_type"] not in ATOM_TYPES
                or promotion["epistemic_status"]
                not in ALLOWED_STATUS_BY_TYPE[promotion["atom_type"]]
            ):
                raise SummaryV2Error("parent promotion entry is malformed")
            promotion_ids.add(promotion["child_item_id"])
            _string(promotion["statement"], "promotion statement")
            _string(promotion["scope"], "promotion scope", maximum=MAX_SCOPE_CHARACTERS)
            promotion_reasons = _ordered_unique_strings(
                promotion["promotion_reasons"],
                "promotion reasons",
                maximum=4,
            )
            if not set(promotion_reasons).issubset(
                {
                    "durable-status",
                    "task-or-commitment",
                    "artifact-route",
                    "correction-or-conflict",
                }
            ):
                raise SummaryV2Error("promotion reason is unsupported")
            promoted_raw = _ordered_unique_strings(
                promotion["source_message_ids"],
                "promotion raw message IDs",
                maximum=MAX_SOURCE_REFS,
            )
            if not set(promoted_raw).issubset(catalog[promotion["child_summary_id"]]):
                raise SummaryV2Error("promotion cites raw messages outside its child")
        if source["source_sha256"] != canonical_sha256(manifest):
            raise SummaryV2Error("parent source SHA-256 disagrees with child manifest")
    required_locators = source["required_locators"]
    if (
        not isinstance(required_locators, list)
        or len(required_locators) > MAX_DETERMINISTIC_ANCHORS
    ):
        raise SummaryV2Error("sidecar.source.required_locators is malformed")
    locator_keys: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(required_locators):
        locator = _exact(
            raw,
            {"source_ref", "text", "kind"},
            f"sidecar.source.required_locators[{index}]",
        )
        if locator["source_ref"] not in catalog:
            raise SummaryV2Error("required locator cites an unknown source ref")
        _string(locator["text"], "required locator text", maximum=MAX_ANCHOR_CHARACTERS)
        if locator["kind"] not in ANCHOR_KINDS:
            raise SummaryV2Error("required locator kind is unsupported")
        key = (locator["source_ref"], locator["text"], locator["kind"])
        if key in locator_keys:
            raise SummaryV2Error("sidecar source contains duplicate required locators")
        locator_keys.add(key)

    all_item_ids: set[str] = set()
    content_refs: set[str] = set()
    atom_ids: set[str] = set()
    deterministic_anchor_limit = max(MAX_ANCHORS, len(required_locators))
    for group_name, fields, maximum, minimum in (
        ("overview", {"item_id", "text", "source_refs", "source_message_ids"}, MAX_OVERVIEW_ITEMS, 1),
        ("scenes", {"item_id", "title", "summary", "source_refs", "source_message_ids"}, MAX_SCENES, 1),
        ("atoms", {"item_id", "atom_type", "statement", "epistemic_status", "scope", "source_refs", "source_message_ids"}, MAX_ATOMS, 1),
        ("retrieval_anchors", {"item_id", "text", "kind", "source_refs", "source_message_ids"}, deterministic_anchor_limit, 0),
    ):
        group = sidecar[group_name]
        if not isinstance(group, list) or not minimum <= len(group) <= maximum:
            raise SummaryV2Error(f"sidecar.{group_name} count is invalid")
        for index, raw in enumerate(group):
            location = f"sidecar.{group_name}[{index}]"
            item = _exact(raw, fields, location)
            _validate_item_id(item, location)
            if item["item_id"] in all_item_ids:
                raise SummaryV2Error("sidecar contains duplicate item IDs")
            all_item_ids.add(item["item_id"])
            refs = _ordered_unique_strings(item["source_refs"], f"{location}.source_refs", maximum=MAX_REFS_PER_ITEM)
            if not set(refs).issubset(catalog):
                raise SummaryV2Error(f"{location} cites unknown source refs")
            expected_raw = sorted(
                {message_id for ref in refs for message_id in catalog[ref]},
                key=lambda message_id: int(raw_by_id[message_id]["sequence"]),
            )
            if item["source_message_ids"] != expected_raw:
                raise SummaryV2Error(f"{location} raw backreferences are incorrect")
            content_refs.update(refs)
            if group_name == "atoms":
                atom_ids.add(item["item_id"])
                if item["atom_type"] not in ATOM_TYPES or item["epistemic_status"] not in ALLOWED_STATUS_BY_TYPE[item["atom_type"]]:
                    raise SummaryV2Error(f"{location} atom contract is invalid")
            if group_name == "retrieval_anchors" and item["kind"] not in ANCHOR_KINDS:
                raise SummaryV2Error(f"{location}.kind is unsupported")
            without_identity = {
                key: value
                for key, value in item.items()
                if key not in {"item_id", "source_message_ids"}
            }
            prefix = {
                "overview": "overview",
                "scenes": "scene",
                "atoms": "atom",
                "retrieval_anchors": "anchor",
            }[group_name]
            expected_item_id = prefix + "-" + canonical_sha256(
                {
                    "summary_v2_id": sidecar["summary_v2_id"],
                    "source_sha256": source["source_sha256"],
                    **without_identity,
                }
            )[:32]
            if item["item_id"] != expected_item_id:
                raise SummaryV2Error(f"{location}.item_id does not match its contents")

    relations = sidecar["relations"]
    if not isinstance(relations, list) or len(relations) > MAX_RELATIONS:
        raise SummaryV2Error("sidecar.relations is malformed")
    for index, raw in enumerate(relations):
        location = f"sidecar.relations[{index}]"
        item = _exact(
            raw,
            {"item_id", "from_item_id", "to_item_id", "relation_type", "source_refs", "source_message_ids"},
            location,
        )
        _validate_item_id(item, location)
        if item["item_id"] in all_item_ids:
            raise SummaryV2Error("sidecar contains duplicate item IDs")
        all_item_ids.add(item["item_id"])
        if item["from_item_id"] not in atom_ids or item["to_item_id"] not in atom_ids or item["from_item_id"] == item["to_item_id"]:
            raise SummaryV2Error(f"{location} has invalid atom references")
        if item["relation_type"] not in RELATION_TYPES:
            raise SummaryV2Error(f"{location}.relation_type is unsupported")
        refs = _ordered_unique_strings(item["source_refs"], f"{location}.source_refs", maximum=MAX_REFS_PER_ITEM)
        content_refs.update(refs)
        expected_raw = sorted(
            {message_id for ref in refs for message_id in catalog[ref]},
            key=lambda message_id: int(raw_by_id[message_id]["sequence"]),
        )
        if item["source_message_ids"] != expected_raw:
            raise SummaryV2Error(f"{location} raw backreferences are incorrect")
        expected_relation_id = "relation-" + canonical_sha256(
            {
                "summary_v2_id": sidecar["summary_v2_id"],
                "source_sha256": source["source_sha256"],
                **{key: value for key, value in item.items() if key != "item_id"},
            }
        )[:32]
        if item["item_id"] != expected_relation_id:
            raise SummaryV2Error(f"{location}.item_id does not match its contents")
    if relations != sorted(relations, key=lambda item: item["item_id"]):
        raise SummaryV2Error("sidecar.relations is not deterministic")

    omissions = sidecar["omissions"]
    if not isinstance(omissions, list) or len(omissions) > MAX_OMISSIONS:
        raise SummaryV2Error("sidecar.omissions is malformed")
    omitted_refs: list[str] = []
    for index, raw in enumerate(omissions):
        location = f"sidecar.omissions[{index}]"
        item = _exact(raw, {"item_id", "source_ref", "reason", "source_message_ids"}, location)
        _validate_item_id(item, location)
        if item["item_id"] in all_item_ids or item["source_ref"] not in catalog:
            raise SummaryV2Error(f"{location} is malformed")
        all_item_ids.add(item["item_id"])
        omitted_refs.append(item["source_ref"])
        expected_raw = catalog[item["source_ref"]]
        if item["source_message_ids"] != expected_raw:
            raise SummaryV2Error(f"{location} raw backreferences are incorrect")
        expected_omission_id = "omission-" + canonical_sha256(
            {
                "summary_v2_id": sidecar["summary_v2_id"],
                "source_sha256": source["source_sha256"],
                **{key: value for key, value in item.items() if key != "item_id"},
            }
        )[:32]
        if item["item_id"] != expected_omission_id:
            raise SummaryV2Error(f"{location}.item_id does not match its contents")
    if len(omitted_refs) != len(set(omitted_refs)):
        raise SummaryV2Error("sidecar omissions contain duplicate source refs")
    if content_refs & set(omitted_refs):
        raise SummaryV2Error("sidecar both represents and omits a source ref")
    if content_refs | set(omitted_refs) != set(source_refs):
        raise SummaryV2Error("sidecar source accounting is incomplete")
    if source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
        scene_refs = {
            source_ref
            for scene in sidecar["scenes"]
            for source_ref in scene["source_refs"]
        }
        if scene_refs != set(source_refs):
            raise SummaryV2Error(
                "parent sidecar must provide a scene route to every direct child"
            )
        if sidecar["retrieval_anchors"] or sidecar["omissions"]:
            raise SummaryV2Error(
                "parent sidecar must delegate locators and cannot omit children"
            )
        for promoted in source["source_manifest"]["promotion_manifest"]:
            if not any(
                atom["atom_type"] == promoted["atom_type"]
                and atom["statement"] == promoted["statement"]
                and atom["epistemic_status"] == promoted["epistemic_status"]
                and atom["scope"] == promoted["scope"]
                and promoted["child_summary_id"] in atom["source_refs"]
                for atom in sidecar["atoms"]
            ):
                raise SummaryV2Error(
                    "parent sidecar lost promoted durable state: "
                    + promoted["child_item_id"]
                )

    coverage = _exact(
        sidecar["coverage"],
        {
            "source_ref_count",
            "represented_source_refs",
            "omitted_source_refs",
            "raw_message_count",
            "raw_message_ids",
            "silent_loss_count",
        },
        "sidecar.coverage",
    )
    source_order = {source_ref: index for index, source_ref in enumerate(source_refs)}
    expected_represented = sorted(content_refs, key=source_order.__getitem__)
    expected_omitted = sorted(omitted_refs, key=source_order.__getitem__)
    if coverage != {
        "source_ref_count": len(source_refs),
        "represented_source_refs": expected_represented,
        "omitted_source_refs": expected_omitted,
        "raw_message_count": len(raw_message_ids),
        "raw_message_ids": raw_message_ids,
        "silent_loss_count": 0,
    }:
        raise SummaryV2Error("sidecar.coverage does not match its contents")
    metrics = _exact(
        sidecar["metrics"],
        {
            "overview_count",
            "scene_count",
            "atom_count",
            "relation_count",
            "retrieval_anchor_count",
            "omission_count",
            "required_locator_count",
        },
        "sidecar.metrics",
    )
    if metrics != {
        "overview_count": len(sidecar["overview"]),
        "scene_count": len(sidecar["scenes"]),
        "atom_count": len(sidecar["atoms"]),
        "relation_count": len(sidecar["relations"]),
        "retrieval_anchor_count": len(sidecar["retrieval_anchors"]),
        "omission_count": len(sidecar["omissions"]),
        "required_locator_count": len(source["required_locators"]),
    }:
        raise SummaryV2Error("sidecar.metrics does not match its contents")
    anchors_by_text = {
        (item["text"], source_ref)
        for item in sidecar["retrieval_anchors"]
        for source_ref in item["source_refs"]
    }
    for locator in required_locators:
        if (locator["text"], locator["source_ref"]) not in anchors_by_text:
            raise SummaryV2Error("sidecar lost a required locator anchor")
    atom_position = {
        atom["item_id"]: index for index, atom in enumerate(sidecar["atoms"])
    }
    reconstructed_candidate = {
        "overview": [
            {key: value for key, value in item.items() if key not in {"item_id", "source_message_ids"}}
            for item in sidecar["overview"]
        ],
        "scenes": [
            {key: value for key, value in item.items() if key not in {"item_id", "source_message_ids"}}
            for item in sidecar["scenes"]
        ],
        "atoms": [
            {key: value for key, value in item.items() if key not in {"item_id", "source_message_ids"}}
            for item in sidecar["atoms"]
        ],
        "relations": sorted(
            [
                {
                    "from_atom_index": atom_position[item["from_item_id"]],
                    "to_atom_index": atom_position[item["to_item_id"]],
                    "relation_type": item["relation_type"],
                    "source_refs": item["source_refs"],
                }
                for item in sidecar["relations"]
            ],
            key=lambda item: (
                item["from_atom_index"],
                item["to_atom_index"],
                item["relation_type"],
            ),
        ),
        "retrieval_anchors": [
            {key: value for key, value in item.items() if key not in {"item_id", "source_message_ids"}}
            for item in sidecar["retrieval_anchors"]
        ],
        "omissions": [
            {"source_ref": item["source_ref"], "reason": item["reason"]}
            for item in sidecar["omissions"]
        ],
    }
    expected_summary_id = "summary-v2-" + canonical_sha256(
        {
            "summary_level": sidecar["summary_level"],
            "source_kind": source["source_kind"],
            "job_id": source["job_id"],
            "parallel_summary_id": source["parallel_summary_id"],
            "conversation_id": source["conversation_id"],
            "source_sha256": source["source_sha256"],
            "candidate": reconstructed_candidate,
        }
    )[:32]
    if sidecar["summary_v2_id"] != expected_summary_id:
        raise SummaryV2Error("sidecar.summary_v2_id does not match its semantic contents")
    claimed = sidecar["projection_sha256"]
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise SummaryV2Error("sidecar.projection_sha256 is malformed")
    actual = canonical_sha256(
        {key: item for key, item in sidecar.items() if key != "projection_sha256"}
    )
    if claimed != actual:
        raise SummaryV2Error("sidecar projection SHA-256 mismatch")
    return sidecar


def render_markdown(sidecar: dict[str, Any]) -> str:
    sidecar = validate_sidecar(sidecar)
    is_parent = sidecar["source"]["source_kind"] in {
        SOURCE_CHILDREN,
        SOURCE_PARENT_RESCUE_MAPS,
    }

    def refs(item: dict[str, Any]) -> str:
        source_refs = ", ".join(f"`{value}`" for value in item.get("source_refs", []))
        raw_refs = ", ".join(f"`{value}`" for value in item["source_message_ids"])
        return f"Source refs: {source_refs}; raw messages: {raw_refs}"

    lines = [
        "---",
        f"format: {FORMAT}",
        f"format_version: {FORMAT_VERSION}",
        f"summary_v2_id: {sidecar['summary_v2_id']}",
        f"summary_level: {sidecar['summary_level']}",
        f"parallel_summary_id: {json.dumps(sidecar['parallel_summary_id'], ensure_ascii=False)}",
        f"conversation_id: {json.dumps(sidecar['conversation_id'], ensure_ascii=False)}",
        f"source_sha256: {sidecar['source']['source_sha256']}",
        f"projection_sha256: {sidecar['projection_sha256']}",
        "---",
        "",
        f"# Traceable Level-{sidecar['summary_level']} Summary",
        "",
        "## Overview",
        "",
    ]
    for item in sidecar["overview"]:
        lines.extend([f"- {item['text']}", f"  - {refs(item)}"])
    lines.extend(["", "## Phases And Child Routes" if is_parent else "## Scenes", ""])
    for item in sidecar["scenes"]:
        lines.extend([f"### {item['title']}", "", item["summary"], "", refs(item), ""])
    lines.extend(["## Promoted Durable State" if is_parent else "## Memory Atoms", ""])
    for item in sidecar["atoms"]:
        lines.extend(
            [
                f"- **{item['atom_type']} / {item['epistemic_status']}**: {item['statement']}",
                f"  - Scope: {item['scope']}",
                f"  - {refs(item)}",
            ]
        )
    lines.extend(["", "## Retrieval Anchors", ""])
    if sidecar["retrieval_anchors"]:
        for item in sidecar["retrieval_anchors"]:
            lines.extend([f"- **{item['kind']}**: `{item['text']}`", f"  - {refs(item)}"])
    else:
        lines.append(
            "- Delegated to direct child summaries."
            if is_parent
            else "- None recorded."
        )
    lines.extend(["", "## Relations", ""])
    if sidecar["relations"]:
        for item in sidecar["relations"]:
            lines.extend(
                [
                    f"- `{item['from_item_id']}` **{item['relation_type']}** `{item['to_item_id']}`",
                    f"  - {refs(item)}",
                ]
            )
    else:
        lines.append("- None recorded.")
    lines.extend(["", "## Explicit Omissions", ""])
    if sidecar["omissions"]:
        for item in sidecar["omissions"]:
            lines.extend(
                [
                    f"- `{item['source_ref']}`: {item['reason']}",
                    "  - Raw messages: "
                    + ", ".join(f"`{value}`" for value in item["source_message_ids"]),
                ]
            )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- Source evidence units: {sidecar['coverage']['source_ref_count']}",
            f"- Represented units: {len(sidecar['coverage']['represented_source_refs'])}",
            f"- Explicitly omitted units: {len(sidecar['coverage']['omitted_source_refs'])}",
            f"- Raw messages reachable: {sidecar['coverage']['raw_message_count']}",
            f"- Silent loss: {sidecar['coverage']['silent_loss_count']}",
            "",
        ]
    )
    return "\n".join(lines)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def persist_sidecar(
    sidecar: dict[str, Any],
    output_directory: Path,
    archive_root: Path,
) -> tuple[Path, str]:
    sidecar = validate_sidecar(sidecar)
    output_directory = Path(output_directory).expanduser().resolve()
    archive_root = Path(archive_root).expanduser().resolve()
    if _is_relative_to(output_directory, archive_root):
        raise SummaryV2Error("summary-v2 output must be outside the archive root")
    if any(part.lower() in LIVE_ARCHIVE_PARTS for part in output_directory.parts):
        raise SummaryV2Error("summary-v2 output resembles a live archive directory")
    parent = output_directory / FORMAT / f"level-{sidecar['summary_level']}"
    destination = parent / sidecar["summary_v2_id"]
    json_bytes = canonical_json_bytes(sidecar)
    markdown_bytes = render_markdown(sidecar).encode("utf-8")
    if destination.exists():
        if (
            destination.is_dir()
            and (destination / "summary.json").read_bytes() == json_bytes
            and (destination / "summary.md").read_bytes() == markdown_bytes
        ):
            return destination, "existing-identical"
        raise SummaryV2Error("summary-v2 destination exists with different contents")
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{sidecar['summary_v2_id']}.", dir=str(parent)))
    try:
        for name, payload in (("summary.json", json_bytes), ("summary.md", markdown_bytes)):
            with (temporary / name).open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            if (
                destination.is_dir()
                and (destination / "summary.json").read_bytes() == json_bytes
                and (destination / "summary.md").read_bytes() == markdown_bytes
            ):
                return destination, "existing-identical"
            raise SummaryV2Error("summary-v2 destination won a race with different contents")
        return destination, "created"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def comparison_report(summary_v1: Path, summary_v2: dict[str, Any]) -> dict[str, Any]:
    summary_v2 = validate_sidecar(summary_v2)
    summary_v1 = Path(summary_v1)
    raw = summary_v1.read_bytes()
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SummaryV2Error(f"summary-v1 is not UTF-8: {exc}") from exc
    return {
        "format": "memory-wuxian-summary-v2-ab-report-v1",
        "summary_v1": {
            "path": str(summary_v1),
            "bytes": len(raw),
            "nonempty_line_count": sum(1 for line in text.splitlines() if line.strip()),
            "explicit_source_message_id_mentions": text.count("source_message_ids"),
        },
        "summary_v2": {
            "summary_v2_id": summary_v2["summary_v2_id"],
            "canonical_json_bytes": len(canonical_json_bytes(summary_v2)),
            **summary_v2["metrics"],
            **summary_v2["coverage"],
        },
        "human_review_questions": [
            "Can a reader explain the main events without opening raw history?",
            "Can every decision, task, method, artifact, file, tool, and command be located in raw messages?",
            "Are proposals, uncertainty, withdrawals, and unresolved questions preserved without strengthening?",
            "Does the higher-level summary retain enough child evidence to find the correct detailed scene?",
            "Did any explicit omission remove information needed for future work?",
        ],
        "interpretation_limit": (
            "Structural coverage and byte counts do not prove semantic quality; "
            "human A/B review is required before activation."
        ),
    }


def _load_sidecar(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "summary.json"
    return validate_sidecar(read_json(path, MAX_SIDECAR_BYTES))


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    l1 = commands.add_parser("l1-project")
    l1.add_argument("--job", required=True)
    l1.add_argument("--candidate", required=True)
    l1.add_argument("--output-dir", required=True)
    l1.add_argument("--archive-root", required=True)
    parent = commands.add_parser("parent-project")
    parent.add_argument("--child", action="append", required=True)
    parent.add_argument("--candidate", required=True)
    parent.add_argument("--output-dir", required=True)
    parent.add_argument("--archive-root", required=True)
    validate = commands.add_parser("validate-sidecar")
    validate.add_argument("--sidecar", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--summary-v1", required=True)
    compare.add_argument("--summary-v2", required=True)
    args = parser.parse_args()
    try:
        if args.command == "l1-project":
            source = build_level_1_source(read_json(Path(args.job), 16 * 1024 * 1024))
            sidecar = project(source, read_json(Path(args.candidate), 8 * 1024 * 1024))
            path, status = persist_sidecar(sidecar, Path(args.output_dir), Path(args.archive_root))
            _print({"status": status, "path": str(path), "summary_v2_id": sidecar["summary_v2_id"]})
        elif args.command == "parent-project":
            source = build_parent_source(_load_sidecar(Path(path)) for path in args.child)
            sidecar = project(source, read_json(Path(args.candidate), 8 * 1024 * 1024))
            path, status = persist_sidecar(sidecar, Path(args.output_dir), Path(args.archive_root))
            _print({"status": status, "path": str(path), "summary_v2_id": sidecar["summary_v2_id"]})
        elif args.command == "validate-sidecar":
            sidecar = _load_sidecar(Path(args.sidecar))
            _print({"status": "valid", "summary_v2_id": sidecar["summary_v2_id"]})
        else:
            _print(comparison_report(Path(args.summary_v1), _load_sidecar(Path(args.summary_v2))))
        return 0
    except (SummaryV2Error, OSError, ValueError) as exc:
        print(f"memory-wuxian summary-v2: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
