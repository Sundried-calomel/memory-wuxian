"""Shared mechanics for immutable Environment record envelopes."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import Any, Dict, List, Mapping

from memory_environment import EnvironmentRegistry, atomic_write_json, canonical_bytes
from memory_federation import FederationManager, safe_node_id
from platform_lock import exclusive_lock


@dataclass(frozen=True)
class ImmutableRecordMessages:
    object_required: str
    identity_invalid: str
    local_origin_required: str
    schema_unsupported: str
    size_exceeded: str
    identity_conflict: str
    appeared_before_apply: str
    envelope_fields_invalid: str
    envelope_format_unsupported: str
    schema_identity_unsupported: str
    envelope_identity_invalid: str
    envelope_origin_mismatch: str
    content_encoding_invalid: str
    content_hash_mismatch: str
    content_identity_mismatch: str
    content_origin_mismatch: str


@dataclass(frozen=True)
class ImmutableRecordContract:
    format_name: str
    schema_id: str
    identity_field: str
    identity_pattern: Pattern[str]
    maximum_bytes: int
    collection_directory: str
    lock_filename: str
    source_event_prefix: str
    messages: ImmutableRecordMessages


class ImmutableRecordStore:
    """Own canonical encoding, immutable persistence, and envelope validation."""

    def __init__(self, store_or_root: Any, contract: ImmutableRecordContract):
        if hasattr(store_or_root, "root") and hasattr(store_or_root, "config"):
            store = store_or_root
        else:
            store = type(
                "_ImmutableRecordMemoryStore",
                (),
                {"root": Path(store_or_root), "config": {}},
            )()
        self.contract = contract
        self.registry = EnvironmentRegistry(store.root)
        self.federation = FederationManager(store)
        self.root = self.registry.root / contract.collection_directory
        self.local_root = self.root / "local"
        self.lock_path = self.registry.locks_dir / contract.lock_filename

    def init(self) -> Dict[str, Any]:
        self.registry.init()
        self.local_root.mkdir(parents=True, exist_ok=True)
        return {"status": "initialized", "root": str(self.root)}

    def local_node_id(self) -> str:
        return safe_node_id(self.federation.node()["node_id"])

    def put(
        self,
        value: Dict[str, Any],
        *,
        apply: bool = False,
        preview_fields: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        contract = self.contract
        messages = contract.messages
        if not isinstance(value, dict):
            raise ValueError(messages.object_required)
        identity = value.get(contract.identity_field)
        if (
            not isinstance(identity, str)
            or not contract.identity_pattern.fullmatch(identity)
        ):
            raise ValueError(messages.identity_invalid)
        origin = safe_node_id(str(value.get("origin_node_id", "")))
        if origin != self.local_node_id():
            raise ValueError(messages.local_origin_required)
        if value.get("schema_version") != 1:
            raise ValueError(messages.schema_unsupported)
        content = canonical_bytes(value)
        if len(content) > contract.maximum_bytes:
            raise ValueError(messages.size_exceeded)
        digest = hashlib.sha256(content).hexdigest()
        envelope = {
            "format": contract.format_name,
            "schema_id": contract.schema_id,
            contract.identity_field: identity,
            "origin_node_id": origin,
            "content_sha256": digest,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        existing = sorted(self.local_root.glob(f"{identity}-*.json"))
        if existing:
            current = json.loads(existing[0].read_text(encoding="utf-8"))
            if current == envelope:
                return {
                    "status": "no-change",
                    contract.identity_field: identity,
                    "content_sha256": digest,
                    "path": str(existing[0]),
                }
            raise ValueError(messages.identity_conflict)
        result = {
            "status": "preview",
            contract.identity_field: identity,
            "content_sha256": digest,
            **dict(preview_fields or {}),
        }
        if not apply:
            return result
        self.init()
        path = self.local_root / f"{identity}-{digest}.json"
        with exclusive_lock(self.lock_path):
            conflicts = sorted(self.local_root.glob(f"{identity}-*.json"))
            if conflicts:
                raise ValueError(messages.appeared_before_apply)
            atomic_write_json(path, envelope)
        return {**result, "status": "recorded", "path": str(path)}

    def local_events(self) -> List[Dict[str, Any]]:
        if not self.local_root.is_dir():
            return []
        events = []
        identity_field = self.contract.identity_field
        for path in sorted(self.local_root.glob("*.json")):
            envelope = self.validate_envelope(
                json.loads(path.read_text(encoding="utf-8")),
                expected_origin=self.local_node_id(),
            )
            identity = envelope[identity_field]
            events.append(
                {
                    "source_event_id": (
                        f"{self.contract.source_event_prefix}:{identity}:"
                        f"{envelope['content_sha256']}"
                    ),
                    identity_field: identity,
                    "payload": envelope,
                }
            )
        return events

    def remote_envelopes(self) -> List[Dict[str, Any]]:
        remote = []
        replicas = self.registry.root / "replicas" / "peers"
        if replicas.is_dir():
            pattern = f"*/{self.contract.collection_directory}/*.json"
            for path in sorted(replicas.glob(pattern)):
                remote.append(json.loads(path.read_text(encoding="utf-8")))
        return remote

    def validate_envelope(
        self, envelope: Dict[str, Any], *, expected_origin: str | None = None
    ) -> Dict[str, Any]:
        return validate_immutable_envelope(
            envelope, self.contract, expected_origin=expected_origin
        )


def validate_immutable_envelope(
    envelope: Dict[str, Any],
    contract: ImmutableRecordContract,
    *,
    expected_origin: str | None = None,
) -> Dict[str, Any]:
    messages = contract.messages
    required = {
        "format",
        "schema_id",
        contract.identity_field,
        "origin_node_id",
        "content_sha256",
        "content_base64",
    }
    if not isinstance(envelope, dict) or set(envelope) != required:
        raise ValueError(messages.envelope_fields_invalid)
    if envelope["format"] != contract.format_name:
        raise ValueError(messages.envelope_format_unsupported)
    if envelope["schema_id"] != contract.schema_id:
        raise ValueError(messages.schema_identity_unsupported)
    if not contract.identity_pattern.fullmatch(str(envelope[contract.identity_field])):
        raise ValueError(messages.envelope_identity_invalid)
    origin = safe_node_id(str(envelope["origin_node_id"]))
    if expected_origin and origin != safe_node_id(expected_origin):
        raise ValueError(messages.envelope_origin_mismatch)
    try:
        content = base64.b64decode(envelope["content_base64"], validate=True)
    except Exception as error:
        raise ValueError(messages.content_encoding_invalid) from error
    if len(content) > contract.maximum_bytes:
        raise ValueError(messages.size_exceeded)
    if hashlib.sha256(content).hexdigest() != envelope["content_sha256"]:
        raise ValueError(messages.content_hash_mismatch)
    record = json.loads(content)
    if record.get(contract.identity_field) != envelope[contract.identity_field]:
        raise ValueError(messages.content_identity_mismatch)
    if safe_node_id(str(record.get("origin_node_id", ""))) != origin:
        raise ValueError(messages.content_origin_mismatch)
    return json.loads(json.dumps(envelope))
