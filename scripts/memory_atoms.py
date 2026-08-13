#!/usr/bin/env python3
"""Validate and project source-bound Memory Wuxian memory atoms."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from console_encoding import configure_unicode_stdio
from memory_guarded_features import raw_record_sha256
from platform_transaction import canonical_json_bytes


FORMAT = "memory-wuxian-memory-atoms-v1"
FORMAT_VERSION = 1
PROJECTOR = "memory_atoms.py:deterministic-projector-v1"
MAX_JOB_BYTES = 16 * 1024 * 1024
MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 8 * 1024 * 1024
MAX_SOURCE_RECORDS = 4096
MAX_ATOMS = 512
MAX_RELATIONS = 1024
MAX_STATEMENT_CHARACTERS = 4000
MAX_SCOPE_CHARACTERS = 512
MAX_SOURCE_IDS_PER_ITEM = 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ATOM_TYPES = {"work_fact", "work_task", "work_method", "work_artifact"}
EPISTEMIC_STATUSES = {
    "explicit_fact",
    "accepted_decision",
    "proposal",
    "open_question",
    "uncertain",
    "withdrawn",
}
RELATION_TYPES = {
    "supports",
    "duplicates",
    "revises",
    "contradicts",
    "supersedes",
}
ALLOWED_STATUS_BY_TYPE = {
    "work_fact": {"explicit_fact", "uncertain", "withdrawn"},
    "work_task": {
        "accepted_decision",
        "proposal",
        "open_question",
        "uncertain",
        "withdrawn",
    },
    "work_method": {
        "accepted_decision",
        "proposal",
        "uncertain",
        "withdrawn",
    },
    "work_artifact": {"explicit_fact", "proposal", "uncertain", "withdrawn"},
}
LIVE_ARCHIVE_PARTS = {
    "raw",
    "summaries",
    "pending",
    "indexes",
    "backups",
    "imports",
    "federation",
    "cloud",
}


class MemoryAtomsError(ValueError):
    """A memory-atoms input or operation failed closed validation."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MemoryAtomsError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def read_json(path: Path, maximum_bytes: int) -> Any:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MemoryAtomsError(f"JSON input is not readable: {path}: {exc}") from exc
    if size > maximum_bytes:
        raise MemoryAtomsError(
            f"JSON input exceeds the {maximum_bytes}-byte limit: {path}"
        )
    try:
        return json.loads(
            path.read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                MemoryAtomsError(f"JSON contains unsupported constant: {value}")
            ),
        )
    except MemoryAtomsError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemoryAtomsError(f"JSON input is malformed UTF-8 JSON: {path}: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_exact_fields(value: Any, fields: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryAtomsError(f"{location} must be an object")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        raise MemoryAtomsError(
            f"{location} fields mismatch; missing={missing}, extra={extra}"
        )
    return value


def _require_string(
    value: Any,
    location: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str) or len(value) < minimum:
        raise MemoryAtomsError(f"{location} must be a non-empty string")
    if maximum is not None and len(value) > maximum:
        raise MemoryAtomsError(f"{location} exceeds {maximum} characters")
    return value


def _require_source_ids(
    value: Any,
    allowed: set[str],
    location: str,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MemoryAtomsError(f"{location} must be a non-empty array")
    if len(value) > MAX_SOURCE_IDS_PER_ITEM:
        raise MemoryAtomsError(
            f"{location} exceeds {MAX_SOURCE_IDS_PER_ITEM} source IDs"
        )
    if any(not isinstance(item, str) or not item for item in value):
        raise MemoryAtomsError(f"{location} contains an invalid source ID")
    if len(value) != len(set(value)):
        raise MemoryAtomsError(f"{location} contains duplicate source IDs")
    outside = sorted(set(value) - allowed)
    if outside:
        raise MemoryAtomsError(
            f"{location} cites source IDs outside the job: {', '.join(outside)}"
        )
    return value


def _source_sha256(records: list[dict[str, Any]]) -> str:
    payload = [
        {
            "sequence": int(record["sequence"]),
            "message_id": record["message_id"],
            "content_sha256": raw_record_sha256(record),
        }
        for record in sorted(records, key=lambda item: int(item["sequence"]))
    ]
    return canonical_sha256(payload)


def validate_job(job: Any) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise MemoryAtomsError("job must be an object")
    required = {
        "job_id",
        "summary_level",
        "conversation_id",
        "source_sha256",
        "source_message_ids",
        "source_records",
    }
    missing = sorted(required - set(job))
    if missing:
        raise MemoryAtomsError(f"job is missing required fields: {', '.join(missing)}")
    if job["summary_level"] != 1:
        raise MemoryAtomsError("memory-atoms-v1 accepts only closed Level-1 jobs")
    job_id = _require_string(job["job_id"], "job.job_id", maximum=256)
    conversation_id = _require_string(
        job["conversation_id"], "job.conversation_id", maximum=512
    )
    source_sha = _require_string(job["source_sha256"], "job.source_sha256")
    if SHA256_RE.fullmatch(source_sha) is None:
        raise MemoryAtomsError("job.source_sha256 must be a lowercase SHA-256")
    records = job["source_records"]
    if not isinstance(records, list) or not records:
        raise MemoryAtomsError("job.source_records must be a non-empty array")
    if len(records) > MAX_SOURCE_RECORDS:
        raise MemoryAtomsError(
            f"job.source_records exceeds the {MAX_SOURCE_RECORDS}-record limit"
        )
    identities: list[tuple[int, str]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise MemoryAtomsError(f"job.source_records[{index}] must be an object")
        sequence = record.get("sequence")
        message_id = record.get("message_id")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(message_id, str)
            or not message_id
        ):
            raise MemoryAtomsError(
                f"job.source_records[{index}] has malformed identity fields"
            )
        identities.append((sequence, message_id))
    if len({sequence for sequence, _ in identities}) != len(identities):
        raise MemoryAtomsError("job.source_records contains duplicate sequences")
    ordered_ids = [message_id for _, message_id in sorted(identities)]
    if job["source_message_ids"] != ordered_ids:
        raise MemoryAtomsError(
            "job.source_message_ids must exactly match source_records in sequence order"
        )
    if len(ordered_ids) != len(set(ordered_ids)):
        raise MemoryAtomsError("job.source_message_ids contains duplicate IDs")
    actual_sha = _source_sha256(records)
    if source_sha != actual_sha:
        raise MemoryAtomsError("job.source_sha256 does not match embedded source_records")
    return {
        "job_id": job_id,
        "conversation_id": conversation_id,
        "source_sha256": source_sha,
        "source_message_ids": ordered_ids,
        "source_records": records,
    }


def validate_candidate(candidate: Any, source: dict[str, Any]) -> dict[str, Any]:
    candidate = _require_exact_fields(
        candidate,
        {"format_version", "job_id", "source_sha256", "atoms", "relations"},
        "candidate",
    )
    if candidate["format_version"] != FORMAT_VERSION:
        raise MemoryAtomsError("candidate.format_version must be 1")
    if candidate["job_id"] != source["job_id"]:
        raise MemoryAtomsError("candidate.job_id does not match the source job")
    if candidate["source_sha256"] != source["source_sha256"]:
        raise MemoryAtomsError("candidate.source_sha256 does not match the source job")
    atoms = candidate["atoms"]
    relations = candidate["relations"]
    if not isinstance(atoms, list) or len(atoms) > MAX_ATOMS:
        raise MemoryAtomsError(f"candidate.atoms must contain at most {MAX_ATOMS} items")
    if not isinstance(relations, list) or len(relations) > MAX_RELATIONS:
        raise MemoryAtomsError(
            f"candidate.relations must contain at most {MAX_RELATIONS} items"
        )

    allowed_ids = set(source["source_message_ids"])
    source_order = {
        message_id: index for index, message_id in enumerate(source["source_message_ids"])
    }
    local_atoms: dict[str, dict[str, Any]] = {}
    normalized_atoms: list[dict[str, Any]] = []
    for index, raw_atom in enumerate(atoms):
        location = f"candidate.atoms[{index}]"
        atom = _require_exact_fields(
            raw_atom,
            {
                "local_id",
                "atom_type",
                "statement",
                "epistemic_status",
                "scope",
                "source_message_ids",
            },
            location,
        )
        local_id = _require_string(atom["local_id"], f"{location}.local_id")
        if LOCAL_ID_RE.fullmatch(local_id) is None:
            raise MemoryAtomsError(f"{location}.local_id has an invalid format")
        if local_id in local_atoms:
            raise MemoryAtomsError(f"duplicate atom local_id: {local_id}")
        atom_type = atom["atom_type"]
        if atom_type not in ATOM_TYPES:
            raise MemoryAtomsError(f"{location}.atom_type is unsupported")
        status = atom["epistemic_status"]
        if status not in EPISTEMIC_STATUSES:
            raise MemoryAtomsError(f"{location}.epistemic_status is unsupported")
        if status not in ALLOWED_STATUS_BY_TYPE[atom_type]:
            raise MemoryAtomsError(
                f"{location}.epistemic_status is incompatible with {atom_type}"
            )
        statement = _require_string(
            atom["statement"],
            f"{location}.statement",
            maximum=MAX_STATEMENT_CHARACTERS,
        )
        scope = _require_string(
            atom["scope"], f"{location}.scope", maximum=MAX_SCOPE_CHARACTERS
        )
        source_ids = _require_source_ids(
            atom["source_message_ids"], allowed_ids, f"{location}.source_message_ids"
        )
        if source_ids != sorted(source_ids, key=source_order.__getitem__):
            raise MemoryAtomsError(f"{location}.source_message_ids is not source ordered")
        normalized = {
            "local_id": local_id,
            "atom_type": atom_type,
            "statement": statement,
            "epistemic_status": status,
            "scope": scope,
            "source_message_ids": source_ids,
        }
        local_atoms[local_id] = normalized
        normalized_atoms.append(normalized)

    normalized_relations: list[dict[str, Any]] = []
    relation_keys: set[tuple[str, str, str]] = set()
    for index, raw_relation in enumerate(relations):
        location = f"candidate.relations[{index}]"
        relation = _require_exact_fields(
            raw_relation,
            {
                "from_local_id",
                "to_local_id",
                "relation_type",
                "source_message_ids",
            },
            location,
        )
        from_id = relation["from_local_id"]
        to_id = relation["to_local_id"]
        if from_id not in local_atoms or to_id not in local_atoms:
            raise MemoryAtomsError(f"{location} references an unknown local atom ID")
        if from_id == to_id:
            raise MemoryAtomsError(f"{location} cannot relate an atom to itself")
        relation_type = relation["relation_type"]
        if relation_type not in RELATION_TYPES:
            raise MemoryAtomsError(f"{location}.relation_type is unsupported")
        key = (from_id, to_id, relation_type)
        if key in relation_keys:
            raise MemoryAtomsError(f"duplicate relation: {from_id}->{to_id}:{relation_type}")
        relation_keys.add(key)
        relation_allowed = set(local_atoms[from_id]["source_message_ids"]) | set(
            local_atoms[to_id]["source_message_ids"]
        )
        source_ids = _require_source_ids(
            relation["source_message_ids"],
            relation_allowed,
            f"{location}.source_message_ids",
        )
        if source_ids != sorted(source_ids, key=source_order.__getitem__):
            raise MemoryAtomsError(f"{location}.source_message_ids is not source ordered")
        normalized_relations.append(
            {
                "from_local_id": from_id,
                "to_local_id": to_id,
                "relation_type": relation_type,
                "source_message_ids": source_ids,
            }
        )
    return {"atoms": normalized_atoms, "relations": normalized_relations}


def project(job: Any, candidate: Any) -> dict[str, Any]:
    source = validate_job(job)
    normalized = validate_candidate(candidate, source)
    sequence_by_id = {
        record["message_id"]: int(record["sequence"])
        for record in source["source_records"]
    }
    atoms: list[dict[str, Any]] = []
    id_by_local: dict[str, str] = {}
    seen_atom_ids: set[str] = set()
    for atom in normalized["atoms"]:
        identity = {
            "job_id": source["job_id"],
            "source_sha256": source["source_sha256"],
            **{key: value for key, value in atom.items() if key != "local_id"},
        }
        atom_id = "atom-" + canonical_sha256(identity)[:32]
        if atom_id in seen_atom_ids:
            raise MemoryAtomsError("candidate contains duplicate atom identities")
        seen_atom_ids.add(atom_id)
        id_by_local[atom["local_id"]] = atom_id
        atoms.append(
            {
                "atom_id": atom_id,
                **{key: value for key, value in atom.items() if key != "local_id"},
            }
        )
    atoms.sort(
        key=lambda item: (
            min(sequence_by_id[value] for value in item["source_message_ids"]),
            item["atom_id"],
        )
    )

    relations: list[dict[str, Any]] = []
    for relation in normalized["relations"]:
        projected = {
            "from_atom_id": id_by_local[relation["from_local_id"]],
            "to_atom_id": id_by_local[relation["to_local_id"]],
            "relation_type": relation["relation_type"],
            "source_message_ids": relation["source_message_ids"],
        }
        relation_id = "relation-" + canonical_sha256(
            {"source_sha256": source["source_sha256"], **projected}
        )[:32]
        relations.append({"relation_id": relation_id, **projected})
    relations.sort(key=lambda item: item["relation_id"])

    scenes: list[dict[str, Any]] = []
    atoms_by_scope: dict[str, list[dict[str, Any]]] = {}
    for atom in atoms:
        atoms_by_scope.setdefault(atom["scope"], []).append(atom)
    for scope, scoped_atoms in atoms_by_scope.items():
        source_ids = sorted(
            {item for atom in scoped_atoms for item in atom["source_message_ids"]},
            key=sequence_by_id.__getitem__,
        )
        scene_identity = {
            "source_sha256": source["source_sha256"],
            "scope": scope,
            "atom_ids": [atom["atom_id"] for atom in scoped_atoms],
            "source_message_ids": source_ids,
        }
        scenes.append(
            {
                "scene_id": "scene-" + canonical_sha256(scene_identity)[:32],
                "scope": scope,
                "atom_ids": scene_identity["atom_ids"],
                "source_message_ids": source_ids,
                "source_sequence_start": min(sequence_by_id[item] for item in source_ids),
                "source_sequence_end": max(sequence_by_id[item] for item in source_ids),
            }
        )
    scenes.sort(key=lambda item: (item["source_sequence_start"], item["scene_id"]))
    cited_ids = {item for atom in atoms for item in atom["source_message_ids"]}
    source_records = [
        {
            "sequence": int(record["sequence"]),
            "message_id": record["message_id"],
            "content_sha256": raw_record_sha256(record),
        }
        for record in sorted(source["source_records"], key=lambda item: int(item["sequence"]))
    ]
    result = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "projector": PROJECTOR,
        "source": {
            "job_id": source["job_id"],
            "conversation_id": source["conversation_id"],
            "source_sha256": source["source_sha256"],
            "source_message_ids": source["source_message_ids"],
            "source_records": source_records,
        },
        "atoms": atoms,
        "relations": relations,
        "scenes": scenes,
        "metrics": {
            "source_message_count": len(source["source_message_ids"]),
            "cited_source_message_count": len(cited_ids),
            "atom_count": len(atoms),
            "relation_count": len(relations),
            "scene_count": len(scenes),
        },
    }
    result["projection_sha256"] = canonical_sha256(result)
    return result


def validate_sidecar(value: Any) -> dict[str, Any]:
    sidecar = _require_exact_fields(
        value,
        {
            "format",
            "format_version",
            "projector",
            "source",
            "atoms",
            "relations",
            "scenes",
            "metrics",
            "projection_sha256",
        },
        "sidecar",
    )
    if sidecar["format"] != FORMAT or sidecar["format_version"] != FORMAT_VERSION:
        raise MemoryAtomsError("sidecar format is unsupported")
    if sidecar["projector"] != PROJECTOR:
        raise MemoryAtomsError("sidecar projector is unsupported")
    source = _require_exact_fields(
        sidecar["source"],
        {
            "job_id",
            "conversation_id",
            "source_sha256",
            "source_message_ids",
            "source_records",
        },
        "sidecar.source",
    )
    _require_string(source["job_id"], "sidecar.source.job_id", maximum=256)
    _require_string(
        source["conversation_id"], "sidecar.source.conversation_id", maximum=512
    )
    if (
        not isinstance(source["source_sha256"], str)
        or SHA256_RE.fullmatch(source["source_sha256"]) is None
    ):
        raise MemoryAtomsError("sidecar.source.source_sha256 must be a lowercase SHA-256")
    source_ids = source["source_message_ids"]
    if (
        not isinstance(source_ids, list)
        or any(not isinstance(item, str) or not item for item in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        raise MemoryAtomsError("sidecar.source.source_message_ids is malformed")
    source_records = source["source_records"]
    if not isinstance(source_records, list) or len(source_records) != len(source_ids):
        raise MemoryAtomsError("sidecar.source.source_records is malformed")
    sequence_by_id: dict[str, int] = {}
    normalized_source_records: list[dict[str, Any]] = []
    for index, raw_record in enumerate(source_records):
        location = f"sidecar.source.source_records[{index}]"
        record = _require_exact_fields(
            raw_record, {"sequence", "message_id", "content_sha256"}, location
        )
        sequence = record["sequence"]
        message_id = record["message_id"]
        content_sha = record["content_sha256"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(message_id, str)
            or not message_id
            or not isinstance(content_sha, str)
            or SHA256_RE.fullmatch(content_sha) is None
        ):
            raise MemoryAtomsError(f"{location} is malformed")
        if message_id in sequence_by_id or sequence in sequence_by_id.values():
            raise MemoryAtomsError(f"{location} duplicates a source identity")
        sequence_by_id[message_id] = sequence
        normalized_source_records.append(record)
    if normalized_source_records != sorted(
        normalized_source_records, key=lambda item: item["sequence"]
    ):
        raise MemoryAtomsError("sidecar.source.source_records is not sequence ordered")
    if [item["message_id"] for item in normalized_source_records] != source_ids:
        raise MemoryAtomsError("sidecar source message IDs and records disagree")
    if canonical_sha256(normalized_source_records) != source["source_sha256"]:
        raise MemoryAtomsError("sidecar source SHA-256 does not match source records")
    allowed_source_ids = set(source_ids)

    atoms = sidecar["atoms"]
    if not isinstance(atoms, list) or len(atoms) > MAX_ATOMS:
        raise MemoryAtomsError("sidecar.atoms is malformed")
    atom_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_atom in enumerate(atoms):
        location = f"sidecar.atoms[{index}]"
        atom = _require_exact_fields(
            raw_atom,
            {
                "atom_id",
                "atom_type",
                "statement",
                "epistemic_status",
                "scope",
                "source_message_ids",
            },
            location,
        )
        atom_id = atom["atom_id"]
        if not isinstance(atom_id, str) or re.fullmatch(r"atom-[0-9a-f]{32}", atom_id) is None:
            raise MemoryAtomsError(f"{location}.atom_id is malformed")
        if atom_id in atom_by_id:
            raise MemoryAtomsError(f"duplicate sidecar atom ID: {atom_id}")
        if atom["atom_type"] not in ATOM_TYPES:
            raise MemoryAtomsError(f"{location}.atom_type is unsupported")
        if atom["epistemic_status"] not in ALLOWED_STATUS_BY_TYPE[atom["atom_type"]]:
            raise MemoryAtomsError(f"{location}.epistemic_status is incompatible")
        _require_string(
            atom["statement"], f"{location}.statement", maximum=MAX_STATEMENT_CHARACTERS
        )
        _require_string(atom["scope"], f"{location}.scope", maximum=MAX_SCOPE_CHARACTERS)
        _require_source_ids(
            atom["source_message_ids"], allowed_source_ids, f"{location}.source_message_ids"
        )
        if atom["source_message_ids"] != sorted(
            atom["source_message_ids"], key=sequence_by_id.__getitem__
        ):
            raise MemoryAtomsError(f"{location}.source_message_ids is not source ordered")
        expected_atom_id = "atom-" + canonical_sha256(
            {
                "job_id": source["job_id"],
                "source_sha256": source["source_sha256"],
                **{key: item for key, item in atom.items() if key != "atom_id"},
            }
        )[:32]
        if atom_id != expected_atom_id:
            raise MemoryAtomsError(f"{location}.atom_id does not match its contents")
        atom_by_id[atom_id] = atom
    expected_atoms = sorted(
        atoms,
        key=lambda item: (
            min(sequence_by_id[value] for value in item["source_message_ids"]),
            item["atom_id"],
        ),
    )
    if atoms != expected_atoms:
        raise MemoryAtomsError("sidecar.atoms is not in deterministic order")

    relations = sidecar["relations"]
    if not isinstance(relations, list) or len(relations) > MAX_RELATIONS:
        raise MemoryAtomsError("sidecar.relations is malformed")
    relation_ids: set[str] = set()
    for index, raw_relation in enumerate(relations):
        location = f"sidecar.relations[{index}]"
        relation = _require_exact_fields(
            raw_relation,
            {
                "relation_id",
                "from_atom_id",
                "to_atom_id",
                "relation_type",
                "source_message_ids",
            },
            location,
        )
        relation_id = relation["relation_id"]
        if (
            not isinstance(relation_id, str)
            or re.fullmatch(r"relation-[0-9a-f]{32}", relation_id) is None
        ):
            raise MemoryAtomsError(f"{location}.relation_id is malformed")
        if relation_id in relation_ids:
            raise MemoryAtomsError(f"duplicate sidecar relation ID: {relation_id}")
        relation_ids.add(relation_id)
        from_id = relation["from_atom_id"]
        to_id = relation["to_atom_id"]
        if from_id not in atom_by_id or to_id not in atom_by_id or from_id == to_id:
            raise MemoryAtomsError(f"{location} has invalid atom references")
        if relation["relation_type"] not in RELATION_TYPES:
            raise MemoryAtomsError(f"{location}.relation_type is unsupported")
        relation_source_ids = set(atom_by_id[from_id]["source_message_ids"]) | set(
            atom_by_id[to_id]["source_message_ids"]
        )
        _require_source_ids(
            relation["source_message_ids"],
            relation_source_ids,
            f"{location}.source_message_ids",
        )
        if relation["source_message_ids"] != sorted(
            relation["source_message_ids"], key=sequence_by_id.__getitem__
        ):
            raise MemoryAtomsError(f"{location}.source_message_ids is not source ordered")
        expected_relation_id = "relation-" + canonical_sha256(
            {
                "source_sha256": source["source_sha256"],
                **{key: item for key, item in relation.items() if key != "relation_id"},
            }
        )[:32]
        if relation_id != expected_relation_id:
            raise MemoryAtomsError(
                f"{location}.relation_id does not match its contents"
            )
    if relations != sorted(relations, key=lambda item: item["relation_id"]):
        raise MemoryAtomsError("sidecar.relations is not in deterministic order")

    scenes = sidecar["scenes"]
    if not isinstance(scenes, list) or len(scenes) > MAX_ATOMS:
        raise MemoryAtomsError("sidecar.scenes is malformed")
    scene_ids: set[str] = set()
    scene_atom_ids: set[str] = set()
    for index, raw_scene in enumerate(scenes):
        location = f"sidecar.scenes[{index}]"
        scene = _require_exact_fields(
            raw_scene,
            {
                "scene_id",
                "scope",
                "atom_ids",
                "source_message_ids",
                "source_sequence_start",
                "source_sequence_end",
            },
            location,
        )
        scene_id = scene["scene_id"]
        if not isinstance(scene_id, str) or re.fullmatch(r"scene-[0-9a-f]{32}", scene_id) is None:
            raise MemoryAtomsError(f"{location}.scene_id is malformed")
        if scene_id in scene_ids:
            raise MemoryAtomsError(f"duplicate sidecar scene ID: {scene_id}")
        scene_ids.add(scene_id)
        _require_string(scene["scope"], f"{location}.scope", maximum=MAX_SCOPE_CHARACTERS)
        atom_ids = scene["atom_ids"]
        if (
            not isinstance(atom_ids, list)
            or not atom_ids
            or len(atom_ids) != len(set(atom_ids))
            or not set(atom_ids).issubset(atom_by_id)
        ):
            raise MemoryAtomsError(f"{location}.atom_ids is malformed")
        if any(atom_by_id[atom_id]["scope"] != scene["scope"] for atom_id in atom_ids):
            raise MemoryAtomsError(f"{location} mixes atom scopes")
        expected_scene_atom_ids = [
            atom["atom_id"] for atom in atoms if atom["scope"] == scene["scope"]
        ]
        if atom_ids != expected_scene_atom_ids:
            raise MemoryAtomsError(f"{location}.atom_ids is not in deterministic order")
        if scene_atom_ids.intersection(atom_ids):
            raise MemoryAtomsError(f"{location} repeats atoms from another scene")
        scene_atom_ids.update(atom_ids)
        scene_source_ids = _require_source_ids(
            scene["source_message_ids"], allowed_source_ids, f"{location}.source_message_ids"
        )
        expected_scene_source_ids = sorted(
            {
                source_id
                for atom_id in atom_ids
                for source_id in atom_by_id[atom_id]["source_message_ids"]
            },
            key=sequence_by_id.__getitem__,
        )
        if scene_source_ids != expected_scene_source_ids:
            raise MemoryAtomsError(f"{location}.source_message_ids disagrees with its atoms")
        start = scene["source_sequence_start"]
        end = scene["source_sequence_end"]
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            raise MemoryAtomsError(f"{location} has malformed source sequence bounds")
        if (
            start != min(sequence_by_id[item] for item in scene_source_ids)
            or end != max(sequence_by_id[item] for item in scene_source_ids)
        ):
            raise MemoryAtomsError(f"{location} source sequence bounds disagree with source")
        expected_scene_id = "scene-" + canonical_sha256(
            {
                "source_sha256": source["source_sha256"],
                "scope": scene["scope"],
                "atom_ids": atom_ids,
                "source_message_ids": scene_source_ids,
            }
        )[:32]
        if scene_id != expected_scene_id:
            raise MemoryAtomsError(f"{location}.scene_id does not match its contents")
    if scene_atom_ids != set(atom_by_id):
        raise MemoryAtomsError("sidecar scenes do not cover every atom exactly once")
    if scenes != sorted(
        scenes, key=lambda item: (item["source_sequence_start"], item["scene_id"])
    ):
        raise MemoryAtomsError("sidecar.scenes is not in deterministic order")

    metrics = _require_exact_fields(
        sidecar["metrics"],
        {
            "source_message_count",
            "cited_source_message_count",
            "atom_count",
            "relation_count",
            "scene_count",
        },
        "sidecar.metrics",
    )
    cited_ids = {source_id for atom in atoms for source_id in atom["source_message_ids"]}
    expected_metrics = {
        "source_message_count": len(source_ids),
        "cited_source_message_count": len(cited_ids),
        "atom_count": len(atoms),
        "relation_count": len(relations),
        "scene_count": len(scenes),
    }
    if metrics != expected_metrics:
        raise MemoryAtomsError("sidecar.metrics does not match sidecar contents")
    claimed = sidecar["projection_sha256"]
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise MemoryAtomsError("sidecar.projection_sha256 must be a lowercase SHA-256")
    actual = canonical_sha256(
        {key: value for key, value in sidecar.items() if key != "projection_sha256"}
    )
    if claimed != actual:
        raise MemoryAtomsError("sidecar projection SHA-256 mismatch")
    return sidecar


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def persist_sidecar(
    value: dict[str, Any], output_directory: Path, archive_root: Path
) -> tuple[Path, str]:
    value = validate_sidecar(value)
    output_directory = Path(output_directory).expanduser().resolve()
    archive_root = Path(archive_root).expanduser().resolve()
    if _is_relative_to(output_directory, archive_root):
        raise MemoryAtomsError("sidecar output directory must be outside the archive root")
    if any(part.lower() in LIVE_ARCHIVE_PARTS for part in output_directory.parts):
        raise MemoryAtomsError(
            "sidecar output directory resembles a live archive directory"
        )
    source = value["source"]
    safe_job_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source["job_id"]).strip("._")
    if not safe_job_id:
        safe_job_id = "job"
    safe_job_id = safe_job_id[:120]
    destination = (
        output_directory
        / FORMAT
        / f"{safe_job_id}-{source['source_sha256'][:16]}.json"
    )
    payload = canonical_json_bytes(value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
            return destination, "created"
        except FileExistsError:
            if destination.read_bytes() == payload:
                return destination, "existing-identical"
            raise MemoryAtomsError("sidecar destination exists with different bytes")
        except OSError as exc:
            raise MemoryAtomsError(
                f"sidecar filesystem cannot perform an atomic create: {exc}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def compare(summary: Any, sidecar: Any, summary_bytes: int, sidecar_bytes: int) -> dict[str, Any]:
    summary = _require_exact_fields(
        summary,
        {
            "topics",
            "established_conclusions",
            "open_questions",
            "concepts",
            "policy_events",
        },
        "summary",
    )
    sidecar = validate_sidecar(sidecar)
    list_fields = [
        "topics",
        "established_conclusions",
        "open_questions",
        "concepts",
        "policy_events",
    ]
    for field in list_fields:
        if not isinstance(summary.get(field), list):
            raise MemoryAtomsError(f"summary.{field} must be an array")
    summary_items = sum(len(summary[field]) for field in list_fields)
    traceable_summary_items = sum(
        1
        for event in summary["policy_events"]
        if isinstance(event, dict)
        and isinstance(event.get("source_message_ids"), list)
        and bool(event["source_message_ids"])
    )
    atom_source_ids = {
        source_id
        for atom in sidecar["atoms"]
        for source_id in atom["source_message_ids"]
    }
    return {
        "format": "memory-wuxian-memory-atoms-ab-report-v1",
        "source_sha256": sidecar["source"]["source_sha256"],
        "summary_v1": {
            "bytes": summary_bytes,
            "item_count": summary_items,
            "source_traceable_item_count": traceable_summary_items,
        },
        "memory_atoms_v1": {
            "bytes": sidecar_bytes,
            "atom_count": len(sidecar["atoms"]),
            "relation_count": len(sidecar["relations"]),
            "scene_count": len(sidecar["scenes"]),
            "source_traceable_atom_count": len(sidecar["atoms"]),
            "cited_source_message_count": len(atom_source_ids),
        },
        "interpretation_limits": [
            "Counts and byte sizes do not prove semantic quality.",
            "Human review is required before enabling memory-atoms-v1 in retrieval.",
        ],
    }


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--job", required=True)
    validate_parser.add_argument("--candidate", required=True)
    project_parser = commands.add_parser("project")
    project_parser.add_argument("--job", required=True)
    project_parser.add_argument("--candidate", required=True)
    project_parser.add_argument("--output-dir", required=True)
    project_parser.add_argument("--archive-root", required=True)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--summary", required=True)
    compare_parser.add_argument("--atoms", required=True)
    args = parser.parse_args()
    try:
        if args.command in {"validate", "project"}:
            job = read_json(Path(args.job), MAX_JOB_BYTES)
            candidate = read_json(Path(args.candidate), MAX_CANDIDATE_BYTES)
            projected = project(job, candidate)
            if args.command == "validate":
                _print(
                    {
                        "status": "valid",
                        "projection_sha256": projected["projection_sha256"],
                        "metrics": projected["metrics"],
                    }
                )
            else:
                destination, status = persist_sidecar(
                    projected, Path(args.output_dir), Path(args.archive_root)
                )
                _print(
                    {
                        "status": status,
                        "path": str(destination),
                        "projection_sha256": projected["projection_sha256"],
                    }
                )
        else:
            summary_path = Path(args.summary)
            atoms_path = Path(args.atoms)
            _print(
                compare(
                    read_json(summary_path, MAX_CANDIDATE_BYTES),
                    read_json(atoms_path, MAX_SIDECAR_BYTES),
                    summary_path.stat().st_size,
                    atoms_path.stat().st_size,
                )
            )
        return 0
    except (MemoryAtomsError, OSError) as exc:
        print(f"memory-wuxian memory atoms: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
