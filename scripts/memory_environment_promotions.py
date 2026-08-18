"""Reviewed project-to-global capability promotion for Memory無限 2.0."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_environment import (
    EnvironmentRegistry,
    _strict_keys,
    atomic_write_json,
    canonical_bytes,
    now_iso,
    read_json,
)
from platform_lock import exclusive_lock


PROMOTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
CLASSIFICATIONS = {
    "duplicate",
    "extension",
    "new-global-capability",
    "mixed",
    "project-only",
    "conflict",
}
REVIEW_STATES = {
    "discovered",
    "pending-review",
    "deprojectizing",
    "validating",
    "promotable",
    "accepted",
    "project-only",
    "rejected",
    "conflict",
}
TRANSITIONS = {
    None: {"discovered"},
    "discovered": {"pending-review", "project-only", "rejected", "conflict"},
    "pending-review": {
        "deprojectizing",
        "validating",
        "project-only",
        "rejected",
        "conflict",
    },
    "deprojectizing": {"validating", "project-only", "rejected", "conflict"},
    "validating": {"promotable", "project-only", "rejected", "conflict"},
    "promotable": {"accepted", "rejected", "conflict"},
    "accepted": set(),
    "project-only": set(),
    "rejected": set(),
    "conflict": set(),
}
REQUIRED_VALIDATIONS = {
    "source-project-regression",
    "unrelated-project-or-generic-fixture",
    "macos",
    "windows",
    "missing-adapter",
    "invalid-input",
    "project-specific-data-scan",
}


def _require_string(value: Any, label: str, *, nullable: bool = False) -> Optional[str]:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected non-empty string")
    return value


class PromotionStore:
    """Persist immutable promotion-review events without changing Skill ownership."""

    REQUIRED_FIELDS = {
        "schema_version",
        "promotion_id",
        "source_project_id",
        "source_skill_id",
        "source_capability",
        "classification",
        "proposed_global_owner",
        "interface_contract",
        "retained_project_adapter",
        "provenance",
        "validation_matrix",
        "review_state",
        "approval",
    }

    def __init__(self, archive_root: Path | str):
        self.registry = EnvironmentRegistry(archive_root)
        self.root = self.registry.promotions_dir
        self.derived_path = self.registry.derived_dir / "promotions-current.json"
        self.lock_path = self.registry.locks_dir / "environment-promotions.lock"

    def init(self) -> Dict[str, Any]:
        self.registry.init()
        self.root.mkdir(parents=True, exist_ok=True)
        return {"status": "initialized", "root": str(self.root)}

    def propose(self, record: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        normalized = self._validate_record(record)
        if normalized["review_state"] != "discovered":
            raise ValueError("new promotion must start in discovered")
        if normalized["approval"]["approved"]:
            raise ValueError("discovered promotion cannot already be approved")
        return self._write_transition(normalized, previous=None, apply=apply)

    def transition(
        self, promotion_id: str, record: Dict[str, Any], *, apply: bool = False
    ) -> Dict[str, Any]:
        current = self.current(promotion_id)
        normalized = self._validate_record(record)
        if normalized["promotion_id"] != promotion_id:
            raise ValueError("promotion_id cannot change")
        self._validate_identity_unchanged(current, normalized)
        return self._write_transition(normalized, previous=current, apply=apply)

    def current(self, promotion_id: str) -> Dict[str, Any]:
        self._validate_promotion_id(promotion_id)
        events = self._events(promotion_id)
        if not events:
            raise KeyError(promotion_id)
        return events[-1]["record"]

    def history(self, promotion_id: str) -> List[Dict[str, Any]]:
        self._validate_promotion_id(promotion_id)
        return self._events(promotion_id)

    def list(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        records = []
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            try:
                records.append(self.current(directory.name))
            except (KeyError, ValueError):
                continue
        return records

    def validate(self) -> Dict[str, Any]:
        errors = []
        count = 0
        if self.root.exists():
            for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
                previous = None
                for event in self._events(directory.name):
                    count += 1
                    record = event["record"]
                    try:
                        self._validate_record(record)
                        self._validate_transition(previous, record)
                        if previous is not None:
                            self._validate_identity_unchanged(previous, record)
                        expected = self._event_hash(record, event["previous_event_sha256"])
                        if expected != event["event_sha256"]:
                            raise ValueError("event hash mismatch")
                        previous = record
                    except ValueError as error:
                        errors.append(f"{event['path']}: {error}")
        return {
            "status": "valid" if not errors else "invalid",
            "promotion_events": count,
            "promotions": len(self.list()),
            "errors": errors,
        }

    def _write_transition(
        self,
        record: Dict[str, Any],
        *,
        previous: Optional[Dict[str, Any]],
        apply: bool,
    ) -> Dict[str, Any]:
        self._validate_transition(previous, record)
        previous_hash = None
        if previous is not None:
            previous_hash = self._events(record["promotion_id"])[-1]["event_sha256"]
        event_hash = self._event_hash(record, previous_hash)
        result = {
            "status": "preview",
            "promotion_id": record["promotion_id"],
            "from_state": previous["review_state"] if previous else None,
            "to_state": record["review_state"],
            "event_sha256": event_hash,
        }
        if not apply:
            return result

        self.init()
        with exclusive_lock(self.lock_path):
            latest = None
            events = self._events(record["promotion_id"])
            if events:
                latest = events[-1]["record"]
            if previous is None and latest is not None:
                raise ValueError("promotion already exists")
            if previous is not None:
                if latest is None:
                    raise ValueError("promotion disappeared before apply")
                if canonical_bytes(latest) != canonical_bytes(previous):
                    raise ValueError("stale promotion review state")
            sequence = len(events) + 1
            event = {
                "schema_version": 1,
                "event_sequence": sequence,
                "previous_event_sha256": previous_hash,
                "event_sha256": event_hash,
                "created_at": now_iso(),
                "record": record,
            }
            event_dir = self.root / record["promotion_id"]
            event_dir.mkdir(parents=True, exist_ok=True)
            event_path = event_dir / f"{sequence:06d}-{event_hash}.json"
            if event_path.exists():
                raise ValueError("promotion event already exists")
            atomic_write_json(event_path, event)
            self._rebuild_derived()
        return {**result, "status": "recorded", "event_path": str(event_path)}

    def _events(self, promotion_id: str) -> List[Dict[str, Any]]:
        directory = self.root / promotion_id
        if not directory.is_dir():
            return []
        events = []
        expected_sequence = 1
        previous_hash = None
        for path in sorted(directory.glob("*.json")):
            value = read_json(path)
            _strict_keys(
                value,
                {
                    "schema_version",
                    "event_sequence",
                    "previous_event_sha256",
                    "event_sha256",
                    "created_at",
                    "record",
                },
                {
                    "schema_version",
                    "event_sequence",
                    "previous_event_sha256",
                    "event_sha256",
                    "created_at",
                    "record",
                },
                "promotion event",
            )
            if value["schema_version"] != 1:
                raise ValueError("unsupported promotion event schema")
            if value["event_sequence"] != expected_sequence:
                raise ValueError("non-contiguous promotion event sequence")
            if value["previous_event_sha256"] != previous_hash:
                raise ValueError("broken promotion predecessor chain")
            value["path"] = str(path)
            events.append(value)
            expected_sequence += 1
            previous_hash = value["event_sha256"]
        return events

    def _rebuild_derived(self) -> None:
        atomic_write_json(
            self.derived_path,
            {
                "schema_version": 1,
                "generated_at": now_iso(),
                "promotions": self.list(),
            },
        )

    @staticmethod
    def _event_hash(record: Dict[str, Any], previous_hash: Optional[str]) -> str:
        return hashlib.sha256(
            canonical_bytes(
                {"record": record, "previous_event_sha256": previous_hash}
            )
        ).hexdigest()

    def _validate_record(self, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("promotion record: expected object")
        _strict_keys(value, self.REQUIRED_FIELDS, self.REQUIRED_FIELDS, "promotion")
        if value["schema_version"] != 1:
            raise ValueError("promotion: unsupported schema_version")
        self._validate_promotion_id(value["promotion_id"])
        for field in (
            "source_project_id",
            "source_skill_id",
            "source_capability",
        ):
            _require_string(value[field], field)
        if value["classification"] not in CLASSIFICATIONS:
            raise ValueError("classification: unsupported value")
        _require_string(
            value["proposed_global_owner"],
            "proposed_global_owner",
            nullable=True,
        )
        for field in (
            "interface_contract",
            "retained_project_adapter",
            "provenance",
        ):
            if not isinstance(value[field], dict):
                raise ValueError(f"{field}: expected object")
        if not isinstance(value["validation_matrix"], list) or not value[
            "validation_matrix"
        ]:
            raise ValueError("validation_matrix: expected non-empty array")
        self._validate_matrix(value["validation_matrix"])
        if value["review_state"] not in REVIEW_STATES:
            raise ValueError("review_state: unsupported value")
        self._validate_approval(value["approval"])
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def _validate_matrix(matrix: List[Any]) -> None:
        names = set()
        for index, item in enumerate(matrix):
            if not isinstance(item, dict):
                raise ValueError(f"validation_matrix[{index}]: expected object")
            _strict_keys(
                item,
                {"name", "status", "evidence"},
                {"name", "status", "evidence"},
                f"validation_matrix[{index}]",
            )
            name = _require_string(item["name"], f"validation_matrix[{index}].name")
            if name in names:
                raise ValueError(f"validation_matrix: duplicate check {name}")
            names.add(name)
            if item["status"] not in {"pending", "pass", "fail"}:
                raise ValueError(f"validation_matrix[{index}].status: unsupported")
            if item["evidence"] is not None:
                _require_string(
                    item["evidence"], f"validation_matrix[{index}].evidence"
                )

    @staticmethod
    def _validate_approval(approval: Any) -> None:
        if not isinstance(approval, dict):
            raise ValueError("approval: expected object")
        _strict_keys(
            approval,
            {"required", "approved", "approved_at", "evidence"},
            {"required", "approved"},
            "approval",
        )
        if approval["required"] is not True:
            raise ValueError("approval.required must be true")
        if type(approval["approved"]) is not bool:
            raise ValueError("approval.approved must be boolean")
        for optional in ("approved_at", "evidence"):
            if optional in approval and approval[optional] is not None:
                _require_string(approval[optional], f"approval.{optional}")

    def _validate_transition(
        self,
        previous: Optional[Dict[str, Any]],
        record: Dict[str, Any],
    ) -> None:
        old_state = previous["review_state"] if previous else None
        new_state = record["review_state"]
        if new_state not in TRANSITIONS[old_state]:
            raise ValueError(f"invalid review transition: {old_state} -> {new_state}")
        if record["approval"]["approved"] and new_state != "accepted":
            raise ValueError("approval may be true only for accepted state")
        if new_state == "accepted":
            self._validate_acceptance(record)
        elif new_state == "promotable":
            self._validate_validation_gate(record)
        if new_state == "project-only" and record["classification"] != "project-only":
            raise ValueError("project-only state requires project-only classification")
        if new_state == "conflict" and record["classification"] != "conflict":
            raise ValueError("conflict state requires conflict classification")

    def _validate_acceptance(self, record: Dict[str, Any]) -> None:
        self._validate_validation_gate(record)
        if not record["proposed_global_owner"]:
            raise ValueError("accepted promotion requires proposed_global_owner")
        approval = record["approval"]
        if not approval["approved"]:
            raise ValueError("accepted promotion requires explicit approval")
        if not approval.get("approved_at") or not approval.get("evidence"):
            raise ValueError("accepted promotion requires approval time and evidence")

    @staticmethod
    def _validate_validation_gate(record: Dict[str, Any]) -> None:
        checks = {item["name"]: item for item in record["validation_matrix"]}
        missing = REQUIRED_VALIDATIONS - set(checks)
        if missing:
            raise ValueError(f"promotion validation missing: {sorted(missing)}")
        failed = sorted(
            name
            for name in REQUIRED_VALIDATIONS
            if checks[name]["status"] != "pass" or not checks[name]["evidence"]
        )
        if failed:
            raise ValueError(f"promotion validation not passed: {failed}")

    @staticmethod
    def _validate_identity_unchanged(
        previous: Dict[str, Any], record: Dict[str, Any]
    ) -> None:
        for field in (
            "promotion_id",
            "source_project_id",
            "source_skill_id",
            "source_capability",
        ):
            if previous[field] != record[field]:
                raise ValueError(f"{field} cannot change during review")

    @staticmethod
    def _validate_promotion_id(value: Any) -> None:
        if not isinstance(value, str) or not PROMOTION_ID_RE.fullmatch(value):
            raise ValueError("promotion_id: invalid")

