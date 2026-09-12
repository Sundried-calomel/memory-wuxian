#!/usr/bin/env python3
"""Build traceable, hierarchical summary-v2 sidecars without touching summary-v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
from platform_paths import filesystem_native_path
from platform_transaction import canonical_json_bytes


FORMAT = "memory-wuxian-summary-v2"
FORMAT_VERSION = 2
PROJECTOR = "memory_summary_v2.py:traceable-projector-v1"
PARENT_PROJECTOR = "memory_summary_v2.py:hierarchical-parent-projector-v2"
SOURCE_LEVEL_1 = "closed-level-1-job"
SOURCE_CHILDREN = "summary-v2-children"
SOURCE_RESCUE_MAPS = "summary-v2-rescue-maps"
SOURCE_PARENT_RESCUE_MAPS = "summary-v2-parent-rescue-maps"
LEDGER_FORMAT = "canonical-semantic-ledger-v1"
PARENT_PROJECTION_FORMAT = "summary-v2-parent-model-projection-v1"
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


def _complete_promotion_manifest(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    """Promote visible state plus durable state delegated to direct-child routes."""

    promoted = _promotion_manifest(sidecar)
    by_state = {
        (
            item["atom_type"],
            item["statement"],
            item["epistemic_status"],
            item["scope"],
        ): item
        for item in promoted
    }
    source = sidecar["source"]
    if source["source_kind"] not in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
        return promoted
    for inherited in source["source_manifest"].get("promotion_manifest", []):
        key = (
            inherited["atom_type"],
            inherited["statement"],
            inherited["epistemic_status"],
            inherited["scope"],
        )
        existing = by_state.get(key)
        if existing is None:
            identity = {
                field: inherited[field]
                for field in ("atom_type", "statement", "epistemic_status", "scope")
            }
            existing = {
                "child_summary_id": sidecar["summary_v2_id"],
                "child_item_id": "routed-state-" + canonical_sha256(identity)[:32],
                **identity,
                "promotion_reasons": list(inherited["promotion_reasons"]),
                "source_message_ids": list(inherited["source_message_ids"]),
            }
            by_state[key] = existing
            promoted.append(existing)
            continue
        existing["promotion_reasons"] = list(
            dict.fromkeys(
                [*existing["promotion_reasons"], *inherited["promotion_reasons"]]
            )
        )
        raw_order = {
            message_id: index
            for index, message_id in enumerate(sidecar["source"]["raw_message_ids"])
        }
        existing["source_message_ids"] = sorted(
            set(existing["source_message_ids"]) | set(inherited["source_message_ids"]),
            key=raw_order.__getitem__,
        )
    return promoted


def _promotion_relation_manifest(sidecar: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = {atom["item_id"]: atom for atom in sidecar["atoms"]}
    promoted: list[dict[str, Any]] = []
    for relation in sidecar["relations"]:
        if relation["relation_type"] not in PROMOTED_RELATIONS:
            continue
        left = atoms[relation["from_item_id"]]
        right = atoms[relation["to_item_id"]]
        promoted.append(
            {
                "child_summary_id": sidecar["summary_v2_id"],
                "child_relation_id": relation["item_id"],
                "from_atom": {
                    field: left[field]
                    for field in ("atom_type", "statement", "epistemic_status", "scope")
                },
                "to_atom": {
                    field: right[field]
                    for field in ("atom_type", "statement", "epistemic_status", "scope")
                },
                "relation_type": relation["relation_type"],
                "source_message_ids": list(relation["source_message_ids"]),
            }
        )
    source = sidecar["source"]
    if source["source_kind"] not in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
        return promoted
    by_relation = {
        canonical_sha256(
            {
                "from_atom": item["from_atom"],
                "to_atom": item["to_atom"],
                "relation_type": item["relation_type"],
            }
        ): item
        for item in promoted
    }
    raw_order = {
        message_id: index
        for index, message_id in enumerate(source["raw_message_ids"])
    }
    for inherited in source["source_manifest"].get("promotion_relations", []):
        identity = {
            "from_atom": dict(inherited["from_atom"]),
            "to_atom": dict(inherited["to_atom"]),
            "relation_type": inherited["relation_type"],
        }
        semantic_id = canonical_sha256(identity)
        existing = by_relation.get(semantic_id)
        if existing is None:
            existing = {
                "child_summary_id": sidecar["summary_v2_id"],
                "child_relation_id": "routed-relation-" + semantic_id[:32],
                **identity,
                "source_message_ids": list(inherited["source_message_ids"]),
            }
            by_relation[semantic_id] = existing
            promoted.append(existing)
            continue
        existing["source_message_ids"] = sorted(
            set(existing["source_message_ids"])
            | set(inherited["source_message_ids"]),
            key=raw_order.__getitem__,
        )
    return promoted


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
        for promoted in _complete_promotion_manifest(child)
    ]
    promotion_relations = [
        promoted
        for child in ordered
        for promoted in _promotion_relation_manifest(child)
    ]
    source_manifest = {
        "kind": SOURCE_CHILDREN,
        "children": descriptors,
        "promotion_manifest": promotion_manifest,
        "promotion_relations": promotion_relations,
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
        "promotion_relations": promotion_relations,
        "values_by_ref": values_by_ref,
        "prompt_payload": {"child_sidecars": ordered},
        "compact_parent_prompt": True,
    }


def build_rescue_reduce_source(
    formal_source: dict[str, Any],
    map_sidecars: Iterable[dict[str, Any]],
    *,
    internal_route_sidecars: Iterable[dict[str, Any]] | None = None,
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
    routes = [
        validate_sidecar(sidecar)
        for sidecar in (
            internal_route_sidecars if internal_route_sidecars is not None else ordered
        )
    ]
    route_refs = [
        source_ref
        for sidecar in routes
        for source_ref in sidecar["source"]["source_refs"]
    ]
    if route_refs != formal_source["source_refs"] or len(route_refs) != len(set(route_refs)):
        raise SummaryV2Error("internal rescue routes must partition the formal Level-1 source")
    return {
        **formal_source,
        "source_kind": SOURCE_RESCUE_MAPS,
        "prompt_payload": {"map_sidecars": ordered},
        "internal_route_sidecars": routes,
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


def _internal_route_manifest(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_routes = source.get("internal_route_sidecars", [])
    if not isinstance(raw_routes, list):
        raise SummaryV2Error("internal route sidecars must be a list")
    routes: list[dict[str, Any]] = []
    for sidecar in raw_routes:
        validated = validate_sidecar(sidecar)
        descriptor = {
            "summary_v2_id": validated["summary_v2_id"],
            "summary_level": validated["summary_level"],
            "projection_sha256": validated["projection_sha256"],
            "source_sha256": validated["source"]["source_sha256"],
            "source_refs": list(validated["source"]["source_refs"]),
        }
        routes.append(
            {
                "route_id": "summary-v2-route-" + canonical_sha256(descriptor)[:32],
                **descriptor,
            }
        )
    if routes:
        covered = [ref for route in routes for ref in route["source_refs"]]
        if covered != source["source_refs"] or len(covered) != len(set(covered)):
            raise SummaryV2Error("internal routes must form one exact ordered source partition")
    return routes


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
        canonical_refs = refs(item.get("source_refs"))
        if not isinstance(canonical_refs, list):
            relations.append(item)
            continue
        item["source_refs"] = canonical_refs
        relations.append(item)
    normalized["relations"] = relations
    omissions = normalized.get("omissions")
    if isinstance(omissions, list) and all(isinstance(item, dict) for item in omissions):
        normalized["omissions"] = sorted(
            omissions,
            key=lambda item: source_order.get(item.get("source_ref"), len(source_order)),
        )
    if source["source_kind"] in {SOURCE_RESCUE_MAPS, SOURCE_PARENT_RESCUE_MAPS}:
        normalized["atoms"] = []
        normalized["relations"] = []
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
        represented_omissions = content_refs.intersection(omission_by_ref)
        if represented_omissions:
            raise SummaryV2Error(
                "rescue model represents map-omitted source refs: "
                + ", ".join(sorted(represented_omissions))
            )
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
    if source["source_kind"] in {
        SOURCE_CHILDREN,
        SOURCE_RESCUE_MAPS,
        SOURCE_PARENT_RESCUE_MAPS,
    }:
        atoms = normalized.setdefault("atoms", [])
        canonical_projection = source.get("_canonical_projection")
        if source["source_kind"] in {
            SOURCE_RESCUE_MAPS,
            SOURCE_PARENT_RESCUE_MAPS,
        } and not isinstance(canonical_projection, dict):
            raise SummaryV2Error("rescue normalization requires a canonical projection")
        if isinstance(canonical_projection, dict):
            maximum_model_scenes = canonical_projection.get("maximum_model_scenes")
            if (
                isinstance(maximum_model_scenes, bool)
                or not isinstance(maximum_model_scenes, int)
                or maximum_model_scenes < 1
                or len(normalized.get("scenes", [])) > maximum_model_scenes
            ):
                raise SummaryV2Error(
                    "rescue model scenes exceed the canonical route headroom"
                )
        canonical_atoms = (
            canonical_projection.get("canonical_atoms", [])
            if isinstance(canonical_projection, dict)
            else []
        )
        canonical_relations = (
            canonical_projection.get("canonical_relations", [])
            if isinstance(canonical_projection, dict)
            else []
        )
        if not isinstance(canonical_atoms, list) or not isinstance(canonical_relations, list):
            raise SummaryV2Error("parent canonical projection collections are malformed")
        atom_specs = [
            {
                "atom_type": atom["atom_type"],
                "statement": atom["statement"],
                "epistemic_status": atom["epistemic_status"],
                "scope": atom["scope"],
                "source_refs": atom["source_refs"],
                "local_prefix": "canonical_state",
                "semantic_id": atom["semantic_id"],
            }
            for atom in canonical_atoms
        ]
        if not isinstance(canonical_projection, dict):
            atom_specs.extend(
                {
                    "atom_type": promoted["atom_type"],
                    "statement": promoted["statement"],
                    "epistemic_status": promoted["epistemic_status"],
                    "scope": promoted["scope"],
                    "source_refs": [promoted["child_summary_id"]],
                    "local_prefix": "promoted_state",
                    "semantic_id": None,
                }
                for promoted in source.get("promotion_manifest", [])
            )
        canonical_local_by_id: dict[str, str] = {}
        for index, spec in enumerate(atom_specs, 1):
            matching = next(
                (
                    atom
                    for atom in atoms
                    if isinstance(atom, dict)
                    and atom.get("atom_type") == spec["atom_type"]
                    and atom.get("statement") == spec["statement"]
                    and atom.get("epistemic_status") == spec["epistemic_status"]
                    and atom.get("scope") == spec["scope"]
                ),
                None,
            )
            if matching is not None:
                matching["source_refs"] = refs(
                    [*matching.get("source_refs", []), *spec["source_refs"]]
                )
                if spec["semantic_id"] is not None:
                    canonical_local_by_id[spec["semantic_id"]] = matching["local_id"]
                continue
            local_id = f"{spec['local_prefix']}_{index}"
            while local_id in used_ids:
                local_id = "mw_" + local_id
            used_ids.add(local_id)
            atoms.append(
                {
                    "local_id": local_id,
                    "atom_type": spec["atom_type"],
                    "statement": spec["statement"],
                    "epistemic_status": spec["epistemic_status"],
                    "scope": spec["scope"],
                    "source_refs": refs(spec["source_refs"]),
                }
            )
            if spec["semantic_id"] is not None:
                canonical_local_by_id[spec["semantic_id"]] = local_id
        semantic_atoms: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for atom in atoms:
            if not isinstance(atom, dict):
                continue
            key = tuple(
                str(atom.get(field, ""))
                for field in ("atom_type", "statement", "epistemic_status", "scope")
            )
            semantic_atoms.setdefault(key, []).append(atom)
        relation_by_key = {
            (
                relation.get("from_local_id"),
                relation.get("to_local_id"),
                relation.get("relation_type"),
            ): relation
            for relation in normalized.get("relations", [])
            if isinstance(relation, dict)
        }
        canonical_atom_by_id = {
            atom["semantic_id"]: atom for atom in canonical_atoms
        }
        relation_specs = []
        for relation in canonical_relations:
            try:
                left_source = canonical_atom_by_id[relation["from_semantic_id"]]
                right_source = canonical_atom_by_id[relation["to_semantic_id"]]
            except KeyError as exc:
                raise SummaryV2Error("parent canonical relation has a missing endpoint") from exc
            relation_specs.append(
                {
                    "from_atom": left_source,
                    "to_atom": right_source,
                    "from_local_id": canonical_local_by_id[relation["from_semantic_id"]],
                    "to_local_id": canonical_local_by_id[relation["to_semantic_id"]],
                    "relation_type": relation["relation_type"],
                    "source_refs": relation["source_refs"],
                    "allow_routed": False,
                }
            )
        relation_specs.extend(
            {
                "from_atom": relation["from_atom"],
                "to_atom": relation["to_atom"],
                "from_local_id": None,
                "to_local_id": None,
                "relation_type": relation["relation_type"],
                "source_refs": [relation["child_summary_id"]],
                "allow_routed": (
                    isinstance(canonical_projection, dict)
                    and (
                        semantic_atom_identity(relation["from_atom"])
                        not in canonical_atom_by_id
                        or semantic_atom_identity(relation["to_atom"])
                        not in canonical_atom_by_id
                    )
                ),
            }
            for relation in source.get("promotion_relations", [])
        )
        for relation in relation_specs:
            left_source = relation["from_atom"]
            right_source = relation["to_atom"]
            left_key = tuple(left_source[field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
            right_key = tuple(right_source[field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
            relation_refs = refs(relation["source_refs"])
            left = next(
                (
                    item
                    for item in atoms
                    if item.get("local_id") == relation["from_local_id"]
                ),
                None,
            ) if relation["from_local_id"] is not None else next(
                iter(semantic_atoms.get(left_key, [])), None
            )
            right = next(
                (
                    item
                    for item in atoms
                    if item.get("local_id") == relation["to_local_id"]
                ),
                None,
            ) if relation["to_local_id"] is not None else next(
                iter(semantic_atoms.get(right_key, [])), None
            )
            if (left is None or right is None) and relation["allow_routed"]:
                continue
            if left is None or right is None or left is right:
                raise SummaryV2Error("parent rescue could not bind a canonical relation endpoint")
            relation_key = (
                left["local_id"],
                right["local_id"],
                relation["relation_type"],
            )
            if relation_key in relation_by_key:
                existing_relation = relation_by_key[relation_key]
                existing_relation["source_refs"] = refs(
                    [*existing_relation["source_refs"], *relation_refs]
                )
                continue
            normalized.setdefault("relations", []).append(
                {
                    "from_local_id": left["local_id"],
                    "to_local_id": right["local_id"],
                    "relation_type": relation["relation_type"],
                    "source_refs": relation_refs,
                }
            )
            relation_by_key[relation_key] = normalized["relations"][-1]
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
    if source["source_kind"] in {SOURCE_RESCUE_MAPS, SOURCE_PARENT_RESCUE_MAPS}:
        routable_refs = {
            source_ref
            for sidecar in source["prompt_payload"]["map_sidecars"]
            for source_ref in sidecar["coverage"]["represented_source_refs"]
            if source_ref not in scene_refs
        }
        for map_index, sidecar in enumerate(source["prompt_payload"]["map_sidecars"], 1):
            missing = [
                source_ref
                for source_ref in sidecar["coverage"]["represented_source_refs"]
                if source_ref in routable_refs
            ]
            title = sidecar["scenes"][0]["title"]
            summary = " ".join(item["text"] for item in sidecar["overview"])
            for batch_index in range(0, len(missing), MAX_REFS_PER_ITEM):
                batch = missing[batch_index : batch_index + MAX_REFS_PER_ITEM]
                local_id = f"source_route_{map_index}_{batch_index // MAX_REFS_PER_ITEM + 1}"
                while local_id in used_ids:
                    local_id = "mw_" + local_id
                used_ids.add(local_id)
                normalized.setdefault("scenes", []).append(
                    {
                        "local_id": local_id,
                        "title": title,
                        "summary": summary[:MAX_TEXT_CHARACTERS].strip(),
                        "source_refs": batch,
                    }
                )
                routable_refs.difference_update(batch)
        if routable_refs:
            raise SummaryV2Error("canonical rescue routes lost represented source refs")
    else:
        routable_refs = (content_refs - scene_refs).intersection(source_order)
        for source_ref in sorted(routable_refs, key=source_order.__getitem__):
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
    navigation_refs = set(scene_refs)
    if source["source_kind"] == SOURCE_RESCUE_MAPS:
        navigation_refs = {
            source_ref
            for route in _internal_route_manifest(source)
            for source_ref in route["source_refs"]
        }
    if not is_parent and not represented_refs.issubset(detail_refs | navigation_refs):
        raise SummaryV2Error(
            "every represented source ref must have semantic detail or a deterministic route"
        )

    if is_parent:
        missing_routes = expected_refs - scene_refs
        if missing_routes:
            raise SummaryV2Error(
                "parent candidate has no navigable scene route for child summaries: "
                + ", ".join(sorted(missing_routes))
            )
        required_promoted_ids = None
        canonical_projection = source.get("_canonical_projection")
        if isinstance(canonical_projection, dict):
            required_promoted_ids = {
                atom["semantic_id"]
                for atom in canonical_projection.get("canonical_atoms", [])
            }
        for promoted in source["promotion_manifest"]:
            if (
                required_promoted_ids is not None
                and semantic_atom_identity(promoted) not in required_promoted_ids
            ):
                continue
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
        source_message_ids = _raw_ids_for_refs(source, item["source_refs"])
        if prefix == "atom" and source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
            exact_by_ref = {
                promoted["child_summary_id"]: promoted["source_message_ids"]
                for promoted in source["promotion_manifest"]
                if promoted["atom_type"] == item["atom_type"]
                and promoted["statement"] == item["statement"]
                and promoted["epistemic_status"] == item["epistemic_status"]
                and promoted["scope"] == item["scope"]
                and promoted["child_summary_id"] in item["source_refs"]
            }
            if exact_by_ref:
                catalog = {entry["source_ref"]: entry["source_message_ids"] for entry in source["ref_catalog"]}
                selected = [
                    message_id
                    for source_ref in item["source_refs"]
                    for message_id in exact_by_ref.get(source_ref, catalog[source_ref])
                ]
                order = {value: index for index, value in enumerate(source_message_ids)}
                source_message_ids = sorted(set(selected), key=order.__getitem__)
        projected.append(
            {
                "item_id": item_id,
                **without_local,
                "source_message_ids": source_message_ids,
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
    internal_routes = _internal_route_manifest(source)
    canonical_projection = source.get("_canonical_projection")
    delegated_state = None
    if isinstance(canonical_projection, dict):
        routed_atom_count = canonical_projection.get("routed_atom_count", 0)
        routed_promoted_count = canonical_projection.get("routed_promoted_count", 0)
        routed_relation_count = canonical_projection.get("routed_relation_count", 0)
        routed_promoted_relation_count = canonical_projection.get(
            "routed_promoted_relation_count", 0
        )
        if (
            routed_atom_count
            or routed_promoted_count
            or routed_relation_count
            or routed_promoted_relation_count
        ):
            delegated_state = {
                "canonical_ledger_sha256": canonical_projection["ledger_sha256"],
                "prompt_projection_sha256": canonical_projection["projection_sha256"],
                "total_atom_count": (
                    len(canonical_projection["canonical_atoms"]) + routed_atom_count
                ),
                "top_level_atom_count": len(canonical_projection["canonical_atoms"]),
                "routed_atom_count": routed_atom_count,
                "promoted_atom_count": canonical_projection.get(
                    "promoted_atom_count", 0
                ),
                "routed_promoted_count": routed_promoted_count,
                "total_relation_count": (
                    len(canonical_projection["canonical_relations"])
                    + routed_relation_count
                ),
                "top_level_relation_count": len(
                    canonical_projection["canonical_relations"]
                ),
                "routed_relation_count": routed_relation_count,
                "promoted_relation_count": canonical_projection.get(
                    "promoted_relation_count", 0
                ),
                "routed_promoted_relation_count": routed_promoted_relation_count,
            }
    summary_identity = {
        "summary_level": source["summary_level"],
        "source_kind": source["source_kind"],
        "job_id": source["job_id"],
        "parallel_summary_id": source["parallel_summary_id"],
        "conversation_id": source["conversation_id"],
        "source_sha256": source["source_sha256"],
        "candidate": _candidate_semantic_identity(normalized),
    }
    if internal_routes:
        summary_identity["internal_routes"] = internal_routes
    if delegated_state:
        summary_identity["delegated_state"] = delegated_state
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
    normalized_atoms = {item["local_id"]: item for item in normalized["atoms"]}
    relations: list[dict[str, Any]] = []
    for relation in normalized["relations"]:
        source_message_ids = _raw_ids_for_refs(source, relation["source_refs"])
        if source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}:
            left = normalized_atoms[relation["from_local_id"]]
            right = normalized_atoms[relation["to_local_id"]]
            exact_by_ref = {
                promoted["child_summary_id"]: promoted["source_message_ids"]
                for promoted in source.get("promotion_relations", [])
                if promoted["relation_type"] == relation["relation_type"]
                and all(left[field] == promoted["from_atom"][field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
                and all(right[field] == promoted["to_atom"][field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
                and promoted["child_summary_id"] in relation["source_refs"]
            }
            if exact_by_ref:
                catalog = {entry["source_ref"]: entry["source_message_ids"] for entry in source["ref_catalog"]}
                selected = [
                    message_id
                    for source_ref in relation["source_refs"]
                    for message_id in exact_by_ref.get(source_ref, catalog[source_ref])
                ]
                order = {value: index for index, value in enumerate(source_message_ids)}
                source_message_ids = sorted(set(selected), key=order.__getitem__)
        projected = {
            "from_item_id": atom_id_by_local[relation["from_local_id"]],
            "to_item_id": atom_id_by_local[relation["to_local_id"]],
            "relation_type": relation["relation_type"],
            "source_refs": relation["source_refs"],
            "source_message_ids": source_message_ids,
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
    if internal_routes:
        result["internal_routes"] = internal_routes
    if delegated_state:
        result["delegated_state"] = delegated_state
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
    required_sidecar_fields = {
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
        }
    if not isinstance(value, dict):
        raise SummaryV2Error("sidecar must be an object")
    supplied_fields = set(value)
    optional_sidecar_fields = {"internal_routes", "delegated_state"}
    supplied_optional = supplied_fields - required_sidecar_fields
    if not supplied_optional.issubset(optional_sidecar_fields):
        raise SummaryV2Error("sidecar has unsupported top-level fields")
    sidecar = _exact(value, required_sidecar_fields | supplied_optional, "sidecar")
    has_internal_routes = "internal_routes" in supplied_optional
    internal_routes = sidecar.get("internal_routes", [])
    delegated_state = sidecar.get("delegated_state")
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
    if not isinstance(internal_routes, list):
        raise SummaryV2Error("sidecar.internal_routes must be a list")
    route_refs: list[str] = []
    route_ids: set[str] = set()
    for index, raw_route in enumerate(internal_routes):
        route = _exact(
            raw_route,
            {
                "route_id",
                "summary_v2_id",
                "summary_level",
                "projection_sha256",
                "source_sha256",
                "source_refs",
            },
            f"sidecar.internal_routes[{index}]",
        )
        if (
            not isinstance(route["route_id"], str)
            or route["route_id"] in route_ids
            or not isinstance(route["summary_v2_id"], str)
            or not isinstance(route["summary_level"], int)
            or route["summary_level"] != sidecar["summary_level"]
            or not _is_sha256_digest(route["projection_sha256"])
            or not _is_sha256_digest(route["source_sha256"])
        ):
            raise SummaryV2Error("sidecar internal route descriptor is malformed")
        descriptor = {key: route[key] for key in route if key != "route_id"}
        if route["route_id"] != "summary-v2-route-" + canonical_sha256(descriptor)[:32]:
            raise SummaryV2Error("sidecar internal route ID does not match its descriptor")
        route_ids.add(route["route_id"])
        route_refs.extend(route["source_refs"])
    if delegated_state is not None:
        delegated_state = _exact(
            delegated_state,
            {
                "canonical_ledger_sha256",
                "prompt_projection_sha256",
                "total_atom_count",
                "top_level_atom_count",
                "routed_atom_count",
                "promoted_atom_count",
                "routed_promoted_count",
                "total_relation_count",
                "top_level_relation_count",
                "routed_relation_count",
                "promoted_relation_count",
                "routed_promoted_relation_count",
            },
            "sidecar.delegated_state",
        )
        if (
            not _is_sha256_digest(delegated_state["canonical_ledger_sha256"])
            or not _is_sha256_digest(delegated_state["prompt_projection_sha256"])
            or any(
                isinstance(delegated_state[field], bool)
                or not isinstance(delegated_state[field], int)
                or delegated_state[field] < 0
                for field in (
                    "total_atom_count",
                    "top_level_atom_count",
                    "routed_atom_count",
                    "promoted_atom_count",
                    "routed_promoted_count",
                    "total_relation_count",
                    "top_level_relation_count",
                    "routed_relation_count",
                    "promoted_relation_count",
                    "routed_promoted_relation_count",
                )
            )
            or delegated_state["total_atom_count"]
            != delegated_state["top_level_atom_count"]
            + delegated_state["routed_atom_count"]
            or delegated_state["routed_promoted_count"]
            > delegated_state["promoted_atom_count"]
            or delegated_state["total_relation_count"]
            != delegated_state["top_level_relation_count"]
            + delegated_state["routed_relation_count"]
            or delegated_state["routed_promoted_relation_count"]
            > delegated_state["promoted_relation_count"]
        ):
            raise SummaryV2Error("sidecar delegated-state receipt is malformed")
    if not isinstance(source["source_sha256"], str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise SummaryV2Error("sidecar.source.source_sha256 is malformed")
    source_refs = _ordered_unique_strings(
        source["source_refs"], "sidecar.source.source_refs", maximum=MAX_SOURCE_REFS
    )
    if internal_routes and (
        route_refs != source_refs or len(route_refs) != len(set(route_refs))
    ):
        raise SummaryV2Error("sidecar internal routes do not partition its source refs")
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
        manifest_keys = set(source["source_manifest"]) if isinstance(source["source_manifest"], dict) else set()
        allowed_manifest_keys = {"kind", "children", "promotion_manifest"}
        if manifest_keys == allowed_manifest_keys:
            manifest = _exact(source["source_manifest"], allowed_manifest_keys, "sidecar.source.source_manifest")
            promotion_relations: list[dict[str, Any]] = []
        else:
            manifest = _exact(
                source["source_manifest"],
                {*allowed_manifest_keys, "promotion_relations"},
                "sidecar.source.source_manifest",
            )
            promotion_relations = manifest["promotion_relations"]
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
        if not isinstance(promotion_relations, list):
            raise SummaryV2Error("parent promotion relation manifest is malformed")
        relation_ids: set[str] = set()
        for index, raw_relation in enumerate(promotion_relations):
            relation = _exact(
                raw_relation,
                {
                    "child_summary_id",
                    "child_relation_id",
                    "from_atom",
                    "to_atom",
                    "relation_type",
                    "source_message_ids",
                },
                f"sidecar.source.source_manifest.promotion_relations[{index}]",
            )
            if (
                relation["child_summary_id"] not in child_ids
                or not isinstance(relation["child_relation_id"], str)
                or not relation["child_relation_id"]
                or relation["child_relation_id"] in relation_ids
                or relation["relation_type"] not in PROMOTED_RELATIONS
            ):
                raise SummaryV2Error("parent promotion relation entry is malformed")
            relation_ids.add(relation["child_relation_id"])
            for endpoint_name in ("from_atom", "to_atom"):
                endpoint = _exact(
                    relation[endpoint_name],
                    {"atom_type", "statement", "epistemic_status", "scope"},
                    f"parent promotion relation {endpoint_name}",
                )
                if (
                    endpoint["atom_type"] not in ATOM_TYPES
                    or endpoint["epistemic_status"] not in ALLOWED_STATUS_BY_TYPE[endpoint["atom_type"]]
                ):
                    raise SummaryV2Error("parent promotion relation endpoint is malformed")
                _string(endpoint["statement"], "promotion relation endpoint statement")
                _string(endpoint["scope"], "promotion relation endpoint scope", maximum=MAX_SCOPE_CHARACTERS)
            relation_raw = _ordered_unique_strings(
                relation["source_message_ids"],
                "promotion relation raw message IDs",
                maximum=MAX_SOURCE_REFS,
            )
            if not set(relation_raw).issubset(catalog[relation["child_summary_id"]]):
                raise SummaryV2Error("promotion relation cites raw messages outside its child")
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
    atom_by_id: dict[str, dict[str, Any]] = {}
    deterministic_anchor_limit = max(MAX_ANCHORS, len(required_locators))
    for group_name, fields, maximum, minimum in (
        ("overview", {"item_id", "text", "source_refs", "source_message_ids"}, MAX_OVERVIEW_ITEMS, 1),
        ("scenes", {"item_id", "title", "summary", "source_refs", "source_message_ids"}, MAX_SCENES, 1),
        ("atoms", {"item_id", "atom_type", "statement", "epistemic_status", "scope", "source_refs", "source_message_ids"}, MAX_ATOMS, 1 if sidecar["summary_level"] == 1 else 0),
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
            if (
                group_name == "atoms"
                and source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
                and "promotion_relations" in manifest
            ):
                exact_by_ref = {
                    promoted["child_summary_id"]: promoted["source_message_ids"]
                    for promoted in manifest["promotion_manifest"]
                    if promoted["atom_type"] == item["atom_type"]
                    and promoted["statement"] == item["statement"]
                    and promoted["epistemic_status"] == item["epistemic_status"]
                    and promoted["scope"] == item["scope"]
                    and promoted["child_summary_id"] in refs
                }
                if exact_by_ref:
                    expected_raw = sorted(
                        {
                            message_id
                            for ref in refs
                            for message_id in exact_by_ref.get(ref, catalog[ref])
                        },
                        key=lambda message_id: int(raw_by_id[message_id]["sequence"]),
                    )
            if item["source_message_ids"] != expected_raw:
                raise SummaryV2Error(f"{location} raw backreferences are incorrect")
            content_refs.update(refs)
            if group_name == "atoms":
                atom_ids.add(item["item_id"])
                atom_by_id[item["item_id"]] = item
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
        if (
            source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
            and "promotion_relations" in manifest
        ):
            left = atom_by_id[item["from_item_id"]]
            right = atom_by_id[item["to_item_id"]]
            exact_by_ref = {
                promoted["child_summary_id"]: promoted["source_message_ids"]
                for promoted in manifest.get("promotion_relations", [])
                if promoted["relation_type"] == item["relation_type"]
                and all(left[field] == promoted["from_atom"][field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
                and all(right[field] == promoted["to_atom"][field] for field in ("atom_type", "statement", "epistemic_status", "scope"))
                and promoted["child_summary_id"] in refs
            }
            if exact_by_ref:
                expected_raw = sorted(
                    {
                        message_id
                        for ref in refs
                        for message_id in exact_by_ref.get(ref, catalog[ref])
                    },
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
        visible_promotions = source["source_manifest"]["promotion_manifest"]
        if delegated_state is not None:
            manifest_semantic_ids = {
                semantic_atom_identity(promoted) for promoted in visible_promotions
            }
            sidecar_semantic_ids = {
                semantic_atom_identity(atom) for atom in sidecar["atoms"]
            }
            if (
                delegated_state["top_level_atom_count"] != len(sidecar["atoms"])
                or delegated_state["promoted_atom_count"] != len(manifest_semantic_ids)
                or not sidecar_semantic_ids.issubset(manifest_semantic_ids)
            ):
                raise SummaryV2Error("parent delegated-state counts disagree with the sidecar")
            visible_promotions = [
                promoted
                for promoted in visible_promotions
                if any(
                    atom["atom_type"] == promoted["atom_type"]
                    and atom["statement"] == promoted["statement"]
                    and atom["epistemic_status"] == promoted["epistemic_status"]
                    and atom["scope"] == promoted["scope"]
                    and promoted["child_summary_id"] in atom["source_refs"]
                    for atom in sidecar["atoms"]
                )
            ]
        for promoted in visible_promotions:
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
    expected_identity = {
            "summary_level": sidecar["summary_level"],
            "source_kind": source["source_kind"],
            "job_id": source["job_id"],
            "parallel_summary_id": source["parallel_summary_id"],
            "conversation_id": source["conversation_id"],
            "source_sha256": source["source_sha256"],
            "candidate": reconstructed_candidate,
        }
    if has_internal_routes:
        expected_identity["internal_routes"] = internal_routes
    if delegated_state is not None:
        expected_identity["delegated_state"] = delegated_state
    expected_summary_id = "summary-v2-" + canonical_sha256(expected_identity)[:32]
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


def semantic_atom_identity(atom: Mapping[str, Any]) -> str:
    """Return a stable semantic ID independent of summary and revision IDs."""

    if not isinstance(atom, Mapping):
        raise SummaryV2Error("atom must be an object")
    identity: dict[str, str] = {}
    for field in ("atom_type", "statement", "epistemic_status", "scope"):
        value = atom.get(field)
        if not isinstance(value, str) or not value:
            raise SummaryV2Error(f"atom.{field} must be a non-empty string")
        identity[field] = value
    return "semantic-atom-" + canonical_sha256(identity)


def _ledger_ordered_unique(
    values: Iterable[str], order: Mapping[str, int], location: str
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value not in order:
            raise SummaryV2Error(f"{location} contains an out-of-scope source ref")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return sorted(result, key=order.__getitem__)


def _ledger_message_union(values: Iterable[Iterable[str]], location: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in values:
        for value in group:
            if not isinstance(value, str) or not value:
                raise SummaryV2Error(f"{location} contains an invalid source message ID")
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def _ledger_source_position(item: Mapping[str, Any], order: Mapping[str, int]) -> int:
    refs = item.get("source_refs")
    if not isinstance(refs, list) or not refs:
        return len(order)
    return min(order[ref] for ref in refs)


def _ledger_with_hash(payload: dict[str, Any]) -> dict[str, Any]:
    if "ledger_sha256" in payload:
        raise SummaryV2Error("ledger payload already contains ledger_sha256")
    return {**payload, "ledger_sha256": canonical_sha256(payload)}


def _is_sha256_digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _validate_ledger_shape(ledger: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(ledger, Mapping) or ledger.get("format") != LEDGER_FORMAT:
        raise SummaryV2Error("ledger format is unsupported")
    supplied_hash = ledger.get("ledger_sha256")
    if not _is_sha256_digest(supplied_hash):
        raise SummaryV2Error("ledger_sha256 is malformed")
    payload = {key: value for key, value in ledger.items() if key != "ledger_sha256"}
    if canonical_sha256(payload) != supplied_hash:
        raise SummaryV2Error("ledger_sha256 does not match the ledger")
    required = {
        "format",
        "conversation_id",
        "summary_level",
        "ordered_source_refs",
        "source_sha256s",
        "atoms",
        "relations",
        "routes",
        "anchors",
        "omissions",
    }
    if set(payload) != required:
        raise SummaryV2Error("ledger fields are incomplete or unsupported")
    refs = payload["ordered_source_refs"]
    if not isinstance(refs, list) or len(refs) != len(set(refs)) or not all(
        isinstance(item, str) and item for item in refs
    ):
        raise SummaryV2Error("ledger ordered_source_refs is malformed")
    return dict(ledger)


def _assert_ledger_source_scope(ledger: Mapping[str, Any]) -> None:
    allowed = set(ledger["ordered_source_refs"])
    referenced: list[str] = []
    for atom in ledger["atoms"]:
        for occurrence in atom["occurrences"]:
            referenced.extend(occurrence["source_refs"])
    for relation in ledger["relations"]:
        for occurrence in relation["occurrences"]:
            referenced.extend(occurrence["source_refs"])
    for field in ("routes", "anchors"):
        for item in ledger[field]:
            referenced.extend(item["source_refs"])
    referenced.extend(item["source_ref"] for item in ledger["omissions"])
    if any(not isinstance(value, str) or value not in allowed for value in referenced):
        raise SummaryV2Error("ledger content cites a source ref outside its own scope")


def sidecar_to_ledger(sidecar: Any) -> dict[str, Any]:
    """Validate a sidecar and derive its canonical semantic ledger."""

    validated = validate_sidecar(sidecar)
    origin_summary_id = validated["summary_v2_id"]
    source = validated["source"]
    ordered_refs = list(source["source_refs"])
    source_order = {value: index for index, value in enumerate(ordered_refs)}
    atom_id_map: dict[str, str] = {}
    atoms: dict[str, dict[str, Any]] = {}
    for atom in validated["atoms"]:
        semantic_id = semantic_atom_identity(atom)
        atom_id_map[atom["item_id"]] = semantic_id
        occurrence = {
            "origin_summary_id": origin_summary_id,
            "origin_item_id": atom["item_id"],
            "source_refs": _ledger_ordered_unique(
                atom["source_refs"], source_order, "atom.source_refs"
            ),
            "source_message_ids": list(atom["source_message_ids"]),
        }
        semantic = {
            "semantic_id": semantic_id,
            "atom_type": atom["atom_type"],
            "statement": atom["statement"],
            "epistemic_status": atom["epistemic_status"],
            "scope": atom["scope"],
            "occurrences": [occurrence],
        }
        previous = atoms.get(semantic_id)
        if previous is None:
            atoms[semantic_id] = semantic
        elif {
            key: previous[key]
            for key in ("atom_type", "statement", "epistemic_status", "scope")
        } != {
            key: semantic[key]
            for key in ("atom_type", "statement", "epistemic_status", "scope")
        }:
            raise SummaryV2Error("semantic atom hash collision")
        else:
            previous["occurrences"].append(occurrence)

    relations: dict[str, dict[str, Any]] = {}
    for relation in validated["relations"]:
        try:
            from_semantic_id = atom_id_map[relation["from_item_id"]]
            to_semantic_id = atom_id_map[relation["to_item_id"]]
        except KeyError as exc:
            raise SummaryV2Error("relation has a dangling atom endpoint") from exc
        if from_semantic_id == to_semantic_id:
            raise SummaryV2Error("relation collapses to one canonical atom")
        identity = {
            "from_semantic_id": from_semantic_id,
            "to_semantic_id": to_semantic_id,
            "relation_type": relation["relation_type"],
        }
        relation_id = "semantic-relation-" + canonical_sha256(identity)
        occurrence = {
            "origin_summary_id": origin_summary_id,
            "origin_relation_id": relation["item_id"],
            "source_refs": _ledger_ordered_unique(
                relation["source_refs"], source_order, "relation.source_refs"
            ),
            "source_message_ids": list(relation["source_message_ids"]),
        }
        previous = relations.get(relation_id)
        if previous is None:
            relations[relation_id] = {
                "relation_id": relation_id,
                **identity,
                "occurrences": [occurrence],
            }
        else:
            previous["occurrences"].append(occurrence)

    routes: list[dict[str, Any]] = []
    for kind, group in (("overview", validated["overview"]), ("scene", validated["scenes"])):
        for item in group:
            content = (
                {"text": item["text"]}
                if kind == "overview"
                else {"title": item["title"], "summary": item["summary"]}
            )
            routes.append(
                {
                    "route_id": "semantic-route-"
                    + canonical_sha256(
                        {
                            "kind": kind,
                            **content,
                            "source_refs": item["source_refs"],
                            "origin_summary_id": origin_summary_id,
                            "origin_item_id": item["item_id"],
                        }
                    ),
                    "kind": kind,
                    **content,
                    "origin_summary_id": origin_summary_id,
                    "origin_item_id": item["item_id"],
                    "source_refs": _ledger_ordered_unique(
                        item["source_refs"], source_order, "route.source_refs"
                    ),
                    "source_message_ids": list(item["source_message_ids"]),
                }
            )
    anchors = [
        {
            "anchor_id": "semantic-anchor-"
            + canonical_sha256(
                {
                    "text": item["text"],
                    "kind": item["kind"],
                    "source_refs": item["source_refs"],
                    "origin_summary_id": origin_summary_id,
                    "origin_item_id": item["item_id"],
                }
            ),
            "text": item["text"],
            "kind": item["kind"],
            "origin_summary_id": origin_summary_id,
            "origin_item_id": item["item_id"],
            "source_refs": _ledger_ordered_unique(
                item["source_refs"], source_order, "anchor.source_refs"
            ),
            "source_message_ids": list(item["source_message_ids"]),
        }
        for item in validated["retrieval_anchors"]
    ]
    omissions = [
        {
            "source_ref": item["source_ref"],
            "reasons": [item["reason"]],
            "occurrences": [
                {
                    "origin_summary_id": origin_summary_id,
                    "origin_item_id": item["item_id"],
                    "source_message_ids": list(item["source_message_ids"]),
                }
            ],
        }
        for item in validated["omissions"]
    ]
    return _ledger_with_hash(
        {
            "format": LEDGER_FORMAT,
            "conversation_id": validated["conversation_id"],
            "summary_level": validated["summary_level"],
            "ordered_source_refs": ordered_refs,
            "source_sha256s": [source["source_sha256"]],
            "atoms": sorted(atoms.values(), key=lambda item: item["semantic_id"]),
            "relations": sorted(relations.values(), key=lambda item: item["relation_id"]),
            "routes": sorted(
                routes,
                key=lambda item: (
                    _ledger_source_position(item, source_order),
                    item["route_id"],
                ),
            ),
            "anchors": sorted(
                anchors,
                key=lambda item: (
                    _ledger_source_position(item, source_order),
                    item["anchor_id"],
                ),
            ),
            "omissions": sorted(
                omissions, key=lambda item: source_order[item["source_ref"]]
            ),
        }
    )


def merge_ledgers(
    ledgers: Iterable[Mapping[str, Any]], ordered_source_refs: Sequence[str]
) -> dict[str, Any]:
    """Merge compatible ledgers with canonical atom and relation de-duplication."""

    refs = list(ordered_source_refs)
    if not refs or len(refs) != len(set(refs)) or not all(
        isinstance(item, str) and item for item in refs
    ):
        raise SummaryV2Error("ordered_source_refs must be a non-empty unique string sequence")
    order = {value: index for index, value in enumerate(refs)}
    values = [_validate_ledger_shape(value) for value in ledgers]
    if not values:
        raise SummaryV2Error("at least one ledger is required")
    conversations = {value["conversation_id"] for value in values}
    levels = {value["summary_level"] for value in values}
    if len(conversations) != 1 or len(levels) != 1:
        raise SummaryV2Error("ledgers must share one conversation and summary level")
    for ledger in values:
        if not set(ledger["ordered_source_refs"]).issubset(order):
            raise SummaryV2Error("ledger contains source refs outside the formal source")
        _assert_ledger_source_scope(ledger)

    atom_by_id: dict[str, dict[str, Any]] = {}
    occurrence_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for ledger in values:
        for atom in ledger["atoms"]:
            semantic_id = semantic_atom_identity(atom)
            if atom.get("semantic_id") != semantic_id:
                raise SummaryV2Error("ledger atom semantic_id is inconsistent")
            base = {
                key: atom[key]
                for key in (
                    "semantic_id",
                    "atom_type",
                    "statement",
                    "epistemic_status",
                    "scope",
                )
            }
            previous = atom_by_id.setdefault(semantic_id, {**base, "occurrences": []})
            if {key: previous[key] for key in base} != base:
                raise SummaryV2Error("conflicting canonical atoms share one semantic_id")
            for occurrence in atom["occurrences"]:
                normalized = {
                    **occurrence,
                    "source_refs": _ledger_ordered_unique(
                        occurrence["source_refs"], order, "atom occurrence.source_refs"
                    ),
                    "source_message_ids": _ledger_message_union(
                        [occurrence["source_message_ids"]],
                        "atom occurrence.source_message_ids",
                    ),
                }
                key = (normalized["origin_summary_id"], normalized["origin_item_id"])
                old = occurrence_by_key.get(key)
                if old is not None and old != {"semantic_id": semantic_id, **normalized}:
                    raise SummaryV2Error("one origin atom maps to conflicting canonical facts")
                if old is None:
                    occurrence_by_key[key] = {"semantic_id": semantic_id, **normalized}
                    previous["occurrences"].append(normalized)

    relation_by_id: dict[str, dict[str, Any]] = {}
    relation_occurrences: dict[tuple[str, str], dict[str, Any]] = {}
    for ledger in values:
        for relation in ledger["relations"]:
            if (
                relation["from_semantic_id"] not in atom_by_id
                or relation["to_semantic_id"] not in atom_by_id
            ):
                raise SummaryV2Error("merged relation has a dangling canonical endpoint")
            identity = {
                "from_semantic_id": relation["from_semantic_id"],
                "to_semantic_id": relation["to_semantic_id"],
                "relation_type": relation["relation_type"],
            }
            relation_id = "semantic-relation-" + canonical_sha256(identity)
            if relation.get("relation_id") != relation_id:
                raise SummaryV2Error("ledger relation_id is inconsistent")
            merged = relation_by_id.setdefault(
                relation_id,
                {"relation_id": relation_id, **identity, "occurrences": []},
            )
            for occurrence in relation["occurrences"]:
                normalized = {
                    **occurrence,
                    "source_refs": _ledger_ordered_unique(
                        occurrence["source_refs"],
                        order,
                        "relation occurrence.source_refs",
                    ),
                    "source_message_ids": _ledger_message_union(
                        [occurrence["source_message_ids"]],
                        "relation occurrence.source_message_ids",
                    ),
                }
                key = (
                    normalized["origin_summary_id"],
                    normalized["origin_relation_id"],
                )
                old = relation_occurrences.get(key)
                if old is not None and old != {"relation_id": relation_id, **normalized}:
                    raise SummaryV2Error("one origin relation maps to conflicting canonical edges")
                if old is None:
                    relation_occurrences[key] = {"relation_id": relation_id, **normalized}
                    merged["occurrences"].append(normalized)

    def merge_origin_items(field: str, id_field: str) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        origin_bindings: dict[tuple[str, str], str] = {}
        for ledger in values:
            for item in ledger[field]:
                item_id = item[id_field]
                normalized = {
                    **item,
                    "source_refs": _ledger_ordered_unique(
                        item["source_refs"], order, f"{field}.source_refs"
                    ),
                    "source_message_ids": _ledger_message_union(
                        [item["source_message_ids"]], f"{field}.source_message_ids"
                    ),
                }
                origin = (normalized["origin_summary_id"], normalized["origin_item_id"])
                prior_id = origin_bindings.get(origin)
                if prior_id is not None and (
                    prior_id != item_id or by_id[prior_id] != normalized
                ):
                    raise SummaryV2Error(f"one origin {field} item has conflicting projections")
                origin_bindings[origin] = item_id
                old = by_id.get(item_id)
                if old is not None and old != normalized:
                    raise SummaryV2Error(f"{field} ID collision")
                by_id[item_id] = normalized
        return sorted(
            by_id.values(),
            key=lambda item: (_ledger_source_position(item, order), item[id_field]),
        )

    omissions_by_ref: dict[str, dict[str, Any]] = {}
    omission_origins: dict[tuple[str, str], str] = {}
    for ledger in values:
        for omission in ledger["omissions"]:
            source_ref = omission["source_ref"]
            if source_ref not in order:
                raise SummaryV2Error("omission source ref is outside the formal source")
            merged = omissions_by_ref.setdefault(
                source_ref, {"source_ref": source_ref, "reasons": [], "occurrences": []}
            )
            for reason in omission["reasons"]:
                if not isinstance(reason, str) or not reason:
                    raise SummaryV2Error("omission reason is malformed")
                if reason not in merged["reasons"]:
                    merged["reasons"].append(reason)
            for occurrence in omission["occurrences"]:
                normalized = {
                    **occurrence,
                    "source_message_ids": _ledger_message_union(
                        [occurrence["source_message_ids"]],
                        "omission occurrence.source_message_ids",
                    ),
                }
                key = (normalized["origin_summary_id"], normalized["origin_item_id"])
                prior_ref = omission_origins.get(key)
                if prior_ref is not None and prior_ref != source_ref:
                    raise SummaryV2Error("one origin omission maps to conflicting source refs")
                omission_origins[key] = source_ref
                if normalized not in merged["occurrences"]:
                    merged["occurrences"].append(normalized)

    for atom in atom_by_id.values():
        atom["occurrences"].sort(
            key=lambda item: (
                _ledger_source_position(item, order),
                item["origin_summary_id"],
                item["origin_item_id"],
            )
        )
    for relation in relation_by_id.values():
        relation["occurrences"].sort(
            key=lambda item: (
                _ledger_source_position(item, order),
                item["origin_summary_id"],
                item["origin_relation_id"],
            )
        )
    for omission in omissions_by_ref.values():
        omission["reasons"].sort()
        omission["occurrences"].sort(
            key=lambda item: (item["origin_summary_id"], item["origin_item_id"])
        )
    source_hashes = sorted(
        {digest for ledger in values for digest in ledger["source_sha256s"]}
    )
    if not all(_is_sha256_digest(value) for value in source_hashes):
        raise SummaryV2Error("ledger source SHA-256 is malformed")
    return _ledger_with_hash(
        {
            "format": LEDGER_FORMAT,
            "conversation_id": next(iter(conversations)),
            "summary_level": next(iter(levels)),
            "ordered_source_refs": refs,
            "source_sha256s": source_hashes,
            "atoms": sorted(atom_by_id.values(), key=lambda item: item["semantic_id"]),
            "relations": sorted(
                relation_by_id.values(), key=lambda item: item["relation_id"]
            ),
            "routes": merge_origin_items("routes", "route_id"),
            "anchors": merge_origin_items("anchors", "anchor_id"),
            "omissions": sorted(
                omissions_by_ref.values(), key=lambda item: order[item["source_ref"]]
            ),
        }
    )


def promotion_ledger(formal_source: Mapping[str, Any]) -> dict[str, Any]:
    """Build the canonical durable-fact ledger from a formal parent source."""

    if not isinstance(formal_source, Mapping):
        raise SummaryV2Error("formal_source must be an object")
    refs = formal_source.get("source_refs")
    manifest = formal_source.get("source_manifest")
    if (
        not isinstance(refs, list)
        or len(refs) != len(set(refs))
        or not refs
        or not all(isinstance(item, str) and item for item in refs)
    ):
        raise SummaryV2Error("formal source refs are malformed")
    if not isinstance(manifest, Mapping) or not isinstance(
        manifest.get("promotion_manifest"), list
    ):
        raise SummaryV2Error("formal source has no valid promotion manifest")
    children = manifest.get("children")
    if (
        manifest.get("kind") != SOURCE_CHILDREN
        or not isinstance(children, list)
        or [item.get("summary_v2_id") for item in children if isinstance(item, Mapping)]
        != refs
        or len(children) != len(refs)
        or any(
            not isinstance(item, Mapping)
            or not _is_sha256_digest(item.get("projection_sha256"))
            for item in children
        )
    ):
        raise SummaryV2Error("formal parent child manifest is malformed")
    order = {value: index for index, value in enumerate(refs)}
    catalog_raw = formal_source.get("ref_catalog")
    if not isinstance(catalog_raw, list):
        raise SummaryV2Error("formal source ref catalog is malformed")
    catalog = {
        item.get("source_ref"): item.get("source_message_ids")
        for item in catalog_raw
        if isinstance(item, Mapping)
    }
    if list(catalog) != refs or not all(isinstance(catalog[ref], list) for ref in refs):
        raise SummaryV2Error("formal source ref catalog disagrees with source refs")

    atoms: dict[str, dict[str, Any]] = {}
    occurrence_keys: set[tuple[str, str]] = set()
    for promoted in manifest["promotion_manifest"]:
        if not isinstance(promoted, Mapping):
            raise SummaryV2Error("promotion entry must be an object")
        child_id = promoted.get("child_summary_id")
        child_item_id = promoted.get("child_item_id")
        if child_id not in order or not isinstance(child_item_id, str) or not child_item_id:
            raise SummaryV2Error("promotion origin is outside the formal source")
        semantic_id = semantic_atom_identity(promoted)
        raw_ids = promoted.get("source_message_ids")
        if not isinstance(raw_ids, list) or not set(raw_ids).issubset(catalog[child_id]):
            raise SummaryV2Error("promotion cites messages outside its child")
        reasons = promoted.get("promotion_reasons")
        if not isinstance(reasons, list) or not reasons or not all(
            isinstance(reason, str) and reason for reason in reasons
        ):
            raise SummaryV2Error("promotion reasons are malformed")
        occurrence_key = (child_id, child_item_id)
        if occurrence_key in occurrence_keys:
            raise SummaryV2Error("promotion manifest contains duplicate origin items")
        occurrence_keys.add(occurrence_key)
        occurrence = {
            "origin_summary_id": child_id,
            "origin_item_id": child_item_id,
            "source_refs": [child_id],
            "source_message_ids": list(raw_ids),
            "promotion_reasons": sorted(set(reasons)),
        }
        atom = atoms.setdefault(
            semantic_id,
            {
                "semantic_id": semantic_id,
                "atom_type": promoted["atom_type"],
                "statement": promoted["statement"],
                "epistemic_status": promoted["epistemic_status"],
                "scope": promoted["scope"],
                "occurrences": [],
            },
        )
        atom["occurrences"].append(occurrence)
    for atom in atoms.values():
        atom["occurrences"].sort(
            key=lambda item: (order[item["source_refs"][0]], item["origin_item_id"])
        )

    relations: list[dict[str, Any]] = []
    for promoted in manifest.get("promotion_relations", []):
        if not isinstance(promoted, Mapping):
            raise SummaryV2Error("promotion relation entry must be an object")
        child_id = promoted.get("child_summary_id")
        child_relation_id = promoted.get("child_relation_id")
        if (
            child_id not in order
            or not isinstance(child_relation_id, str)
            or not child_relation_id
        ):
            raise SummaryV2Error("promotion relation origin is outside the formal source")
        left_id = semantic_atom_identity(promoted.get("from_atom", {}))
        right_id = semantic_atom_identity(promoted.get("to_atom", {}))
        if left_id not in atoms or right_id not in atoms or left_id == right_id:
            raise SummaryV2Error(
                "promotion relation has a missing or collapsed semantic endpoint"
            )
        identity = {
            "from_semantic_id": left_id,
            "to_semantic_id": right_id,
            "relation_type": promoted.get("relation_type"),
        }
        relation_id = "semantic-relation-" + canonical_sha256(identity)
        raw_ids = promoted.get("source_message_ids")
        if not isinstance(raw_ids, list) or not set(raw_ids).issubset(catalog[child_id]):
            raise SummaryV2Error("promotion relation cites messages outside its child")
        relations.append(
            {
                "relation_id": relation_id,
                **identity,
                "occurrences": [
                    {
                        "origin_summary_id": child_id,
                        "origin_relation_id": child_relation_id,
                        "source_refs": [child_id],
                        "source_message_ids": list(raw_ids),
                    }
                ],
            }
        )
    source_sha = formal_source.get("source_sha256")
    if not _is_sha256_digest(source_sha) or source_sha != canonical_sha256(dict(manifest)):
        raise SummaryV2Error("formal source SHA-256 disagrees with its manifest")
    conversation_id = formal_source.get("conversation_id")
    summary_level = formal_source.get("summary_level")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise SummaryV2Error("formal source conversation_id is malformed")
    if isinstance(summary_level, bool) or not isinstance(summary_level, int) or summary_level < 2:
        raise SummaryV2Error("formal parent summary_level is malformed")
    return _ledger_with_hash(
        {
            "format": LEDGER_FORMAT,
            "conversation_id": conversation_id,
            "summary_level": summary_level,
            "ordered_source_refs": list(refs),
            "source_sha256s": [source_sha],
            "atoms": sorted(atoms.values(), key=lambda item: item["semantic_id"]),
            "relations": sorted(relations, key=lambda item: item["relation_id"]),
            "routes": [],
            "anchors": [],
            "omissions": [],
        }
    )


def rescue_model_projection(
    formal_source: Mapping[str, Any], map_sidecars: Iterable[Any]
) -> dict[str, Any]:
    """Compile canonical-once rescue evidence without hiding map semantics."""

    maps = list(map_sidecars)
    if not maps:
        raise SummaryV2Error("parent projection requires at least one map sidecar")
    refs = formal_source.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise SummaryV2Error("formal source refs are malformed")
    source_kind = formal_source.get("source_kind")
    if source_kind not in {
        SOURCE_LEVEL_1,
        SOURCE_RESCUE_MAPS,
        SOURCE_CHILDREN,
        SOURCE_PARENT_RESCUE_MAPS,
    }:
        raise SummaryV2Error("canonical rescue projection requires a rescue source")
    ledgers = [sidecar_to_ledger(sidecar) for sidecar in maps]
    validated_maps = [validate_sidecar(sidecar) for sidecar in maps]
    covered = [
        source_ref
        for sidecar in validated_maps
        for source_ref in sidecar["source"]["source_refs"]
    ]
    if covered != refs or len(covered) != len(set(covered)):
        raise SummaryV2Error(
            "parent map sidecars must form one exact ordered source partition"
        )
    promoted = (
        promotion_ledger(formal_source)
        if source_kind in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
        else None
    )
    ledger = merge_ledgers(
        [*ledgers, promoted] if promoted is not None else ledgers,
        refs,
    )
    order = {value: index for index, value in enumerate(refs)}

    all_canonical_atoms = [
        {
            "semantic_id": atom["semantic_id"],
            "atom_type": atom["atom_type"],
            "statement": atom["statement"],
            "epistemic_status": atom["epistemic_status"],
            "scope": atom["scope"],
            "source_refs": _ledger_ordered_unique(
                (
                    ref
                    for occurrence in atom["occurrences"]
                    for ref in occurrence["source_refs"]
                ),
                order,
                "canonical atom source refs",
            ),
        }
        for atom in ledger["atoms"]
    ]
    promoted_ids = (
        {item["semantic_id"] for item in promoted["atoms"]}
        if promoted is not None
        else set()
    )
    promoted_relation_ids = (
        {item["relation_id"] for item in promoted["relations"]}
        if promoted is not None
        else set()
    )
    relation_durable_ids = {
        semantic_id
        for relation in ledger["relations"]
        if relation["relation_type"] in PROMOTED_RELATIONS
        for semantic_id in (
            relation["from_semantic_id"],
            relation["to_semantic_id"],
        )
    }
    route_headroom = sum(
        (len(sidecar["coverage"]["represented_source_refs"]) + MAX_REFS_PER_ITEM - 1)
        // MAX_REFS_PER_ITEM
        for sidecar in validated_maps
    )
    top_level_limit = (
        MAX_ATOMS
        if promoted is not None
        else max(1, MAX_ATOMS - route_headroom)
    )
    if promoted is not None:
        top_level_ids = promoted_ids
    else:
        top_level_ids = {
            atom["semantic_id"]
            for atom in all_canonical_atoms
            if atom["epistemic_status"] in PROMOTED_STATUSES
            or atom["atom_type"] in {"work_task", "work_artifact"}
            or atom["semantic_id"] in relation_durable_ids
        }
        if not top_level_ids and all_canonical_atoms:
            top_level_ids = {all_canonical_atoms[0]["semantic_id"]}
    top_level_candidates = [
        atom for atom in all_canonical_atoms if atom["semantic_id"] in top_level_ids
    ]
    status_priority = {
        "withdrawn": 0,
        "accepted_decision": 1,
        "open_question": 2,
        "proposal": 3,
        "uncertain": 4,
        "explicit_fact": 5,
    }
    top_level_candidates.sort(
        key=lambda atom: (
            status_priority.get(atom["epistemic_status"], 9),
            0 if atom["atom_type"] == "work_task" else 1,
            min(order[ref] for ref in atom["source_refs"]),
            atom["semantic_id"],
        )
    )
    canonical_atoms = top_level_candidates[:top_level_limit]
    if any(len(atom["source_refs"]) > MAX_REFS_PER_ITEM for atom in canonical_atoms):
        raise SummaryV2Error(
            "canonical rescue atom provenance exceeds the per-item capacity; split the source"
        )
    atom_ids = {item["semantic_id"] for item in canonical_atoms}
    relations: list[dict[str, Any]] = []
    for relation in ledger["relations"]:
        if (
            relation["from_semantic_id"] not in atom_ids
            or relation["to_semantic_id"] not in atom_ids
        ):
            continue
        relations.append(
            {
                "relation_id": relation["relation_id"],
                "from_semantic_id": relation["from_semantic_id"],
                "to_semantic_id": relation["to_semantic_id"],
                "relation_type": relation["relation_type"],
                "source_refs": _ledger_ordered_unique(
                    (
                        ref
                        for occurrence in relation["occurrences"]
                        for ref in occurrence["source_refs"]
                    ),
                    order,
                    "canonical relation source refs",
                ),
            }
        )
    if len(relations) > MAX_RELATIONS:
        raise SummaryV2Error(
            "canonical rescue relations exceed the final sidecar capacity; split the source"
        )
    if any(len(relation["source_refs"]) > MAX_REFS_PER_ITEM for relation in relations):
        raise SummaryV2Error(
            "canonical rescue relation provenance exceeds the per-item capacity; split the source"
        )

    map_payloads: list[dict[str, Any]] = []
    for validated, map_ledger in zip(validated_maps, ledgers, strict=True):
        map_payloads.append(
            {
                "origin_summary_id": validated["summary_v2_id"],
                "projection_sha256": validated["projection_sha256"],
                "source_sha256": validated["source"]["source_sha256"],
                "source_refs": list(validated["source"]["source_refs"]),
                "overview": [
                    {"text": item["text"], "source_refs": list(item["source_refs"])}
                    for item in validated["overview"]
                ],
                "scenes": [
                    {
                        "title": item["title"],
                        "summary": item["summary"],
                        "source_refs": list(item["source_refs"]),
                    }
                    for item in validated["scenes"]
                ],
                "omissions": [
                    {"source_ref": item["source_ref"], "reason": item["reason"]}
                    for item in validated["omissions"]
                ],
                "atom_semantic_ids": [
                    item["semantic_id"] for item in map_ledger["atoms"]
                ],
                "relation_ids": [
                    item["relation_id"] for item in map_ledger["relations"]
                ],
            }
        )
    scene_routes = [
        {
            "route_id": item["route_id"],
            "title": item["title"],
            "summary": item["summary"],
            "source_refs": list(item["source_refs"]),
        }
        for item in ledger["routes"]
        if item["kind"] == "scene"
    ]
    omission_refs = {item["source_ref"] for item in ledger["omissions"]}
    expected_route_refs = set(refs) - omission_refs
    if {ref for route in scene_routes for ref in route["source_refs"]} != expected_route_refs:
        raise SummaryV2Error("canonical rescue projection lacks a represented-source scene route")
    if route_headroom >= MAX_SCENES:
        raise SummaryV2Error(
            "canonical rescue scene routes exceed the final sidecar capacity; split the source"
        )
    if promoted is not None and not atom_ids.issubset(promoted_ids):
        raise SummaryV2Error("parent projection exposed non-promoted child detail")
    anchor_projection = [
        {
            "anchor_id": item["anchor_id"],
            "text": item["text"],
            "kind": item["kind"],
            "source_refs": list(item["source_refs"]),
        }
        for item in ledger["anchors"]
    ]
    payload = {
        "format": PARENT_PROJECTION_FORMAT,
        "conversation_id": formal_source.get("conversation_id"),
        "summary_level": formal_source.get("summary_level"),
        "source_sha256": formal_source.get("source_sha256"),
        "ordered_source_refs": list(refs),
        "ledger_sha256": ledger["ledger_sha256"],
        "canonical_atoms": canonical_atoms,
        "routed_atom_count": len(all_canonical_atoms) - len(canonical_atoms),
        "canonical_relations": relations,
        "routed_relation_count": len(ledger["relations"]) - len(relations),
        "promoted_atom_count": len(promoted_ids),
        "promoted_semantic_ids_sha256": canonical_sha256(sorted(promoted_ids)),
        "routed_promoted_count": len(promoted_ids - atom_ids),
        "promoted_relation_count": len(promoted_relation_ids),
        "routed_promoted_relation_count": len(
            promoted_relation_ids
            - {item["relation_id"] for item in relations}
        ),
        "maximum_model_scenes": MAX_SCENES - route_headroom,
        "direct_child_routes": scene_routes,
        "map_payloads": map_payloads,
        "anchor_count": len(anchor_projection),
        "anchors_sha256": canonical_sha256(anchor_projection),
        "omissions": [
            {"source_ref": item["source_ref"], "reasons": list(item["reasons"])}
            for item in ledger["omissions"]
        ],
    }
    projection_hash = canonical_sha256(payload)
    projection = {**payload, "projection_sha256": projection_hash}
    if len({item["semantic_id"] for item in canonical_atoms}) != len(canonical_atoms):
        raise SummaryV2Error("parent projection contains duplicate canonical atoms")
    if canonical_sha256(
        {key: value for key, value in projection.items() if key != "projection_sha256"}
    ) != projection_hash:
        raise SummaryV2Error("parent projection hash self-check failed")
    return projection


def parent_model_projection(
    formal_source: Mapping[str, Any], map_sidecars: Iterable[Any]
) -> dict[str, Any]:
    """Compatibility wrapper for the shared canonical rescue projection."""

    return rescue_model_projection(formal_source, map_sidecars)


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
    if sidecar.get("internal_routes"):
        lines.extend(["", "## Internal Detail Routes", ""])
        for route in sidecar["internal_routes"]:
            lines.append(
                f"- `{route['summary_v2_id']}` / `{route['projection_sha256']}` "
                f"({len(route['source_refs'])} source refs)"
            )
    if sidecar.get("delegated_state"):
        delegated = sidecar["delegated_state"]
        lines.extend(
            [
                "",
                "## Delegated State",
                "",
                f"- Top-level atoms: {delegated['top_level_atom_count']}",
                f"- Routed atoms: {delegated['routed_atom_count']}",
                f"- Routed promoted atoms: {delegated['routed_promoted_count']}",
                f"- Routed relations: {delegated['routed_relation_count']}",
                "- Routed promoted relations: "
                f"{delegated['routed_promoted_relation_count']}",
                f"- Canonical ledger: `{delegated['canonical_ledger_sha256']}`",
            ]
        )
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
    native_parent = Path(filesystem_native_path(parent))
    native_destination = Path(filesystem_native_path(destination))
    json_bytes = canonical_json_bytes(sidecar)
    markdown_bytes = render_markdown(sidecar).encode("utf-8")
    if native_destination.exists():
        if (
            native_destination.is_dir()
            and (native_destination / "summary.json").read_bytes() == json_bytes
            and (native_destination / "summary.md").read_bytes() == markdown_bytes
        ):
            return native_destination, "existing-identical"
        raise SummaryV2Error("summary-v2 destination exists with different contents")
    native_parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    for _ in range(100):
        candidate = Path(
            filesystem_native_path(
                parent / f".{sidecar['summary_v2_id']}.{secrets.token_hex(4)}"
            )
        )
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.mkdir()
        except FileExistsError:
            continue
        temporary = candidate
        break
    if temporary is None:
        raise SummaryV2Error("could not allocate a unique sidecar staging directory")
    try:
        for name, payload in (("summary.json", json_bytes), ("summary.md", markdown_bytes)):
            with (temporary / name).open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(temporary, native_destination)
        except FileExistsError:
            if (
                native_destination.is_dir()
                and (native_destination / "summary.json").read_bytes() == json_bytes
                and (native_destination / "summary.md").read_bytes() == markdown_bytes
            ):
                return native_destination, "existing-identical"
            raise SummaryV2Error("summary-v2 destination won a race with different contents")
        return native_destination, "created"
    finally:
        if temporary.exists():
            for name in ("summary.json", "summary.md"):
                (temporary / name).unlink(missing_ok=True)
            temporary.rmdir()


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
