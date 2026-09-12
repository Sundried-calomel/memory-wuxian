"""Deterministic three-way conflict governance for Memory無限 Environment."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from memory_environment import EnvironmentRegistry, atomic_write_json, now_iso, read_json
from platform_lock import exclusive_lock


REVISION_RE = re.compile(r"^rev:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONFLICT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
OBJECT_CLASSES = {
    "global-rule",
    "project-rule",
    "global-skill",
    "project-skill",
    "global-runtime-contract",
}
CONFLICT_KINDS = {
    "same-managed-block",
    "delete-modify",
    "divergent-skill-code",
    "unregistered-local-change",
    "project-identity-ambiguity",
    "platform-incompatible",
    "permission-expansion",
    "network-expansion",
    "base-mismatch",
    "manual-review",
}
REVIEW_FLAGS = (
    ("permission_expansion", "permission-expansion"),
    ("network_expansion", "network-expansion"),
    ("project_identity_ambiguous", "project-identity-ambiguity"),
    ("platform_incompatible", "platform-incompatible"),
    ("unregistered_local_change", "unregistered-local-change"),
)


def _strict_keys(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    required: Iterable[str],
    label: str,
) -> None:
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f"{label}: unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label}: missing fields: {sorted(missing)}")


def _optional_id(value: Any, label: str, pattern: re.Pattern[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label}: invalid")
    return value


def _blocks(value: Any, label: str) -> Set[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected array")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label}: expected non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label}: duplicate block")
    return set(value)


class EnvironmentConflictStore:
    """Classify safe convergence and persist only review-required conflicts."""

    ASSESS_FIELDS = {
        "artifact_id",
        "object_class",
        "base_revision_id",
        "local_revision_id",
        "remote_revision_id",
        "base_content_sha256",
        "local_content_sha256",
        "remote_content_sha256",
        "local_changed_blocks",
        "remote_changed_blocks",
        "local_deleted",
        "remote_deleted",
        "unregistered_local_change",
        "project_identity_ambiguous",
        "platform_incompatible",
        "permission_expansion",
        "network_expansion",
    }

    def __init__(self, archive_root: Path | str):
        self.registry = EnvironmentRegistry(archive_root)
        self.root = self.registry.conflicts_dir
        self.lock_path = self.registry.locks_dir / "environment-conflicts.lock"

    def assess(self, request: Mapping[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        value = self._validate_assessment(request)
        decision = self._classify(value)
        if decision["decision"] != "queue-conflict":
            return {"status": "preview", **decision}
        record = self._pending_record(value, decision["conflict_kind"])
        result = {
            "status": "preview",
            "decision": "queue-conflict",
            "conflict": record,
        }
        if not apply:
            return result
        self.registry.init()
        with exclusive_lock(self.lock_path):
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{record['conflict_id']}-000001.json"
            if path.exists():
                raise ValueError("conflict event already exists")
            atomic_write_json(path, record)
        return {**result, "status": "queued", "path": str(path)}

    def resolve(
        self,
        conflict_id: str,
        *,
        action: str,
        evidence: str,
        reviewer: str,
        apply: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(conflict_id, str) or not CONFLICT_ID_RE.fullmatch(conflict_id):
            raise ValueError("conflict_id: invalid")
        if action not in {"take-local", "take-remote", "manual-merge", "reject-remote"}:
            raise ValueError("unsupported conflict resolution action")
        if not isinstance(evidence, str) or not evidence:
            raise ValueError("resolution evidence is required")
        if not isinstance(reviewer, str) or not reviewer:
            raise ValueError("resolution reviewer is required")
        current = self.current(conflict_id)
        if current["status"] != "pending-review":
            raise ValueError("conflict is not pending review")
        record = {
            **current,
            "event_sequence": current["event_sequence"] + 1,
            "status": "resolved" if action != "reject-remote" else "rejected",
            "resolution": {
                "action": action,
                "evidence": evidence,
                "reviewer": reviewer,
                "resolved_at": now_iso(),
            },
            "created_at": now_iso(),
        }
        self._validate_record(record)
        result = {"status": "preview", "conflict": record}
        if not apply:
            return result
        with exclusive_lock(self.lock_path):
            latest = self.current(conflict_id)
            if latest["event_sequence"] != current["event_sequence"]:
                raise ValueError("stale conflict review state")
            path = self.root / (
                f"{conflict_id}-{record['event_sequence']:06d}.json"
            )
            if path.exists():
                raise ValueError("conflict resolution event already exists")
            atomic_write_json(path, record)
        return {**result, "status": "resolved", "path": str(path)}

    def current(self, conflict_id: str) -> Dict[str, Any]:
        events = self.history(conflict_id)
        if not events:
            raise KeyError(conflict_id)
        return events[-1]

    def history(self, conflict_id: str) -> List[Dict[str, Any]]:
        if not isinstance(conflict_id, str) or not CONFLICT_ID_RE.fullmatch(conflict_id):
            raise ValueError("conflict_id: invalid")
        if not self.root.exists():
            return []
        events = []
        for path in sorted(self.root.glob(f"{conflict_id}-*.json")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("conflict event path is unsafe")
            record = read_json(path)
            self._validate_record(record)
            if record["conflict_id"] != conflict_id:
                raise ValueError("conflict event identity mismatch")
            if record["event_sequence"] != len(events) + 1:
                raise ValueError("conflict event sequence is not contiguous")
            events.append(record)
        return events

    def list(self, *, pending_only: bool = False) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        ids = set()
        for path in self.root.glob("*.json"):
            record = read_json(path)
            self._validate_record(record)
            ids.add(record["conflict_id"])
        records = [self.current(conflict_id) for conflict_id in sorted(ids)]
        if pending_only:
            records = [item for item in records if item["status"] == "pending-review"]
        return records

    def _classify(self, value: Dict[str, Any]) -> Dict[str, Any]:
        for field, conflict_kind in REVIEW_FLAGS:
            if value[field]:
                return {"decision": "queue-conflict", "conflict_kind": conflict_kind}

        local_hash = value["local_content_sha256"]
        remote_hash = value["remote_content_sha256"]
        base_hash = value["base_content_sha256"]
        if local_hash == remote_hash:
            return {"decision": "no-change", "reason": "local equals remote"}
        if value["local_deleted"] != value["remote_deleted"]:
            unchanged_side = remote_hash == base_hash or local_hash == base_hash
            if not unchanged_side:
                return {
                    "decision": "queue-conflict",
                    "conflict_kind": "delete-modify",
                }
        if remote_hash == base_hash:
            return {"decision": "keep-local", "reason": "remote equals base"}
        if local_hash == base_hash:
            return {"decision": "take-remote", "reason": "local equals base"}
        if value["base_revision_id"] is None or base_hash is None:
            return {"decision": "queue-conflict", "conflict_kind": "base-mismatch"}
        if value["object_class"].endswith("-skill"):
            return {
                "decision": "queue-conflict",
                "conflict_kind": "divergent-skill-code",
            }
        overlap = (
            value["local_changed_blocks"] & value["remote_changed_blocks"]
        )
        if overlap:
            return {
                "decision": "queue-conflict",
                "conflict_kind": "same-managed-block",
            }
        if value["local_changed_blocks"] and value["remote_changed_blocks"]:
            return {
                "decision": "merge-managed-blocks",
                "reason": "changed managed blocks are structurally disjoint",
                "local_blocks": sorted(value["local_changed_blocks"]),
                "remote_blocks": sorted(value["remote_changed_blocks"]),
            }
        return {"decision": "queue-conflict", "conflict_kind": "manual-review"}

    def _validate_assessment(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ValueError("conflict assessment: expected object")
        _strict_keys(request, self.ASSESS_FIELDS, self.ASSESS_FIELDS, "assessment")
        artifact_id = request["artifact_id"]
        if not isinstance(artifact_id, str) or len(artifact_id) < 3:
            raise ValueError("artifact_id: invalid")
        if request["object_class"] not in OBJECT_CLASSES:
            raise ValueError("object_class: invalid")
        value = {
            **dict(request),
            "base_revision_id": _optional_id(
                request["base_revision_id"], "base_revision_id", REVISION_RE
            ),
            "local_revision_id": _optional_id(
                request["local_revision_id"], "local_revision_id", REVISION_RE
            ),
            "remote_revision_id": _optional_id(
                request["remote_revision_id"], "remote_revision_id", REVISION_RE
            ),
            "base_content_sha256": _optional_id(
                request["base_content_sha256"], "base_content_sha256", SHA256_RE
            ),
            "local_content_sha256": _optional_id(
                request["local_content_sha256"], "local_content_sha256", SHA256_RE
            ),
            "remote_content_sha256": _optional_id(
                request["remote_content_sha256"], "remote_content_sha256", SHA256_RE
            ),
            "local_changed_blocks": _blocks(
                request["local_changed_blocks"], "local_changed_blocks"
            ),
            "remote_changed_blocks": _blocks(
                request["remote_changed_blocks"], "remote_changed_blocks"
            ),
        }
        for field in (
            "local_deleted",
            "remote_deleted",
            "unregistered_local_change",
            "project_identity_ambiguous",
            "platform_incompatible",
            "permission_expansion",
            "network_expansion",
        ):
            if type(request[field]) is not bool:
                raise ValueError(f"{field}: expected boolean")
        if value["local_deleted"] != (value["local_content_sha256"] is None):
            raise ValueError("local deletion flag/hash mismatch")
        if value["remote_deleted"] != (value["remote_content_sha256"] is None):
            raise ValueError("remote deletion flag/hash mismatch")
        return value

    def _pending_record(
        self, value: Dict[str, Any], conflict_kind: str
    ) -> Dict[str, Any]:
        record = {
            "schema_version": 1,
            "conflict_id": f"conflict-{uuid.uuid4().hex}",
            "event_sequence": 1,
            "artifact_id": value["artifact_id"],
            "object_class": value["object_class"],
            "base_revision_id": value["base_revision_id"],
            "local_revision_id": value["local_revision_id"],
            "remote_revision_id": value["remote_revision_id"],
            "base_content_sha256": value["base_content_sha256"],
            "local_content_sha256": value["local_content_sha256"],
            "remote_content_sha256": value["remote_content_sha256"],
            "conflict_kind": conflict_kind,
            "decision": "queue-conflict",
            "status": "pending-review",
            "details": {
                "local_changed_blocks": sorted(value["local_changed_blocks"]),
                "remote_changed_blocks": sorted(value["remote_changed_blocks"]),
                "modification_time_used": False,
            },
            "resolution": None,
            "created_at": now_iso(),
        }
        self._validate_record(record)
        return record

    @staticmethod
    def _validate_record(value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("conflict record: expected object")
        fields = {
            "schema_version",
            "conflict_id",
            "event_sequence",
            "artifact_id",
            "object_class",
            "base_revision_id",
            "local_revision_id",
            "remote_revision_id",
            "base_content_sha256",
            "local_content_sha256",
            "remote_content_sha256",
            "conflict_kind",
            "decision",
            "status",
            "details",
            "resolution",
            "created_at",
        }
        _strict_keys(value, fields, fields, "conflict record")
        if value["schema_version"] != 1:
            raise ValueError("unsupported conflict schema")
        if not CONFLICT_ID_RE.fullmatch(value["conflict_id"]):
            raise ValueError("conflict_id: invalid")
        if type(value["event_sequence"]) is not int or value["event_sequence"] < 1:
            raise ValueError("event_sequence: invalid")
        if value["object_class"] not in OBJECT_CLASSES:
            raise ValueError("object_class: invalid")
        if value["conflict_kind"] not in CONFLICT_KINDS:
            raise ValueError("conflict_kind: invalid")
        if value["decision"] != "queue-conflict":
            raise ValueError("conflict decision must queue review")
        if value["status"] not in {"pending-review", "resolved", "rejected"}:
            raise ValueError("conflict status: invalid")
        if not isinstance(value["details"], dict):
            raise ValueError("conflict details: invalid")
        if value["status"] == "pending-review" and value["resolution"] is not None:
            raise ValueError("pending conflict cannot have resolution")
        if value["status"] != "pending-review" and not isinstance(
            value["resolution"], dict
        ):
            raise ValueError("terminal conflict requires resolution")
