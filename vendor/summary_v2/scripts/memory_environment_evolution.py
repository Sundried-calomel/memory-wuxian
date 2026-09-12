"""Immutable, transport-neutral product evolution record envelopes."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from memory_environment import EnvironmentRegistry, atomic_write_json, canonical_bytes
from memory_federation import FederationManager, safe_node_id
from platform_lock import exclusive_lock


RECORD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
SCHEMA_ID = "work-system-governor/product-evolution-v1"
MAX_RECORD_BYTES = 4 * 1024 * 1024


class ProductEvolutionStore:
    """Store local evolution records and enumerate read-only peer replicas."""

    def __init__(self, store_or_root: Any):
        if hasattr(store_or_root, "root") and hasattr(store_or_root, "config"):
            store = store_or_root
        else:
            store = type(
                "_ProductEvolutionMemoryStore",
                (),
                {"root": Path(store_or_root), "config": {}},
            )()
        self.registry = EnvironmentRegistry(store.root)
        self.federation = FederationManager(store)
        self.root = self.registry.root / "product-evolution"
        self.local_root = self.root / "local"
        self.lock_path = self.registry.locks_dir / "environment-product-evolution.lock"

    def init(self) -> Dict[str, Any]:
        self.registry.init()
        self.local_root.mkdir(parents=True, exist_ok=True)
        return {"status": "initialized", "root": str(self.root)}

    def local_node_id(self) -> str:
        return safe_node_id(self.federation.node()["node_id"])

    def record(self, value: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("product evolution record must be an object")
        record_id = value.get("record_id")
        if not isinstance(record_id, str) or not RECORD_ID_RE.fullmatch(record_id):
            raise ValueError("product evolution record_id is invalid")
        origin = safe_node_id(str(value.get("origin_node_id", "")))
        if origin != self.local_node_id():
            raise ValueError("product evolution origin must equal the local node")
        if value.get("schema_version") != 1:
            raise ValueError("product evolution schema_version is unsupported")
        content = canonical_bytes(value)
        if len(content) > MAX_RECORD_BYTES:
            raise ValueError("product evolution record exceeds size limit")
        digest = hashlib.sha256(content).hexdigest()
        envelope = {
            "format": "memory-wuxian-product-evolution-v1",
            "schema_id": SCHEMA_ID,
            "record_id": record_id,
            "origin_node_id": origin,
            "content_sha256": digest,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        existing = sorted(self.local_root.glob(f"{record_id}-*.json"))
        if existing:
            current = json.loads(existing[0].read_text(encoding="utf-8"))
            if current == envelope:
                return {
                    "status": "no-change",
                    "record_id": record_id,
                    "content_sha256": digest,
                    "path": str(existing[0]),
                }
            raise ValueError("product evolution record ID already has different content")
        result = {
            "status": "preview",
            "record_id": record_id,
            "content_sha256": digest,
            "remediation_implied": False,
            "governance_acceptance_implied": False,
        }
        if not apply:
            return result
        self.init()
        path = self.local_root / f"{record_id}-{digest}.json"
        with exclusive_lock(self.lock_path):
            if list(self.local_root.glob(f"{record_id}-*.json")):
                raise ValueError("product evolution record appeared before apply")
            atomic_write_json(path, envelope)
        return {**result, "status": "recorded", "path": str(path)}

    def local_events(self) -> List[Dict[str, Any]]:
        if not self.local_root.is_dir():
            return []
        events = []
        for path in sorted(self.local_root.glob("*.json")):
            envelope = self.validate_envelope(
                json.loads(path.read_text(encoding="utf-8")),
                expected_origin=self.local_node_id(),
            )
            events.append({
                "source_event_id": (
                    f"product-evolution:{envelope['record_id']}:"
                    f"{envelope['content_sha256']}"
                ),
                "record_id": envelope["record_id"],
                "payload": envelope,
            })
        return events

    @staticmethod
    def validate_envelope(
        envelope: Dict[str, Any], *, expected_origin: str | None = None
    ) -> Dict[str, Any]:
        required = {
            "format", "schema_id", "record_id", "origin_node_id",
            "content_sha256", "content_base64",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise ValueError("product evolution envelope fields are invalid")
        if envelope["format"] != "memory-wuxian-product-evolution-v1":
            raise ValueError("product evolution envelope format is unsupported")
        if envelope["schema_id"] != SCHEMA_ID:
            raise ValueError("product evolution schema identity is unsupported")
        if not RECORD_ID_RE.fullmatch(str(envelope["record_id"])):
            raise ValueError("product evolution envelope ID is invalid")
        origin = safe_node_id(str(envelope["origin_node_id"]))
        if expected_origin and origin != safe_node_id(expected_origin):
            raise ValueError("product evolution envelope origin mismatch")
        try:
            content = base64.b64decode(envelope["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("product evolution content encoding is invalid") from error
        if len(content) > MAX_RECORD_BYTES:
            raise ValueError("product evolution record exceeds size limit")
        if hashlib.sha256(content).hexdigest() != envelope["content_sha256"]:
            raise ValueError("product evolution content hash mismatch")
        record = json.loads(content)
        if record.get("record_id") != envelope["record_id"]:
            raise ValueError("product evolution identity mismatch")
        if safe_node_id(str(record.get("origin_node_id", ""))) != origin:
            raise ValueError("product evolution content origin mismatch")
        return json.loads(json.dumps(envelope))

    def list(self) -> Dict[str, Any]:
        local = [item["payload"] for item in self.local_events()]
        remote = []
        replicas = self.registry.root / "replicas" / "peers"
        if replicas.is_dir():
            for path in sorted(replicas.glob("*/product-evolution/*.json")):
                remote.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "status": "listed",
            "local": local,
            "remote": remote,
            "automatic_remediation": False,
            "automatic_governance_acceptance": False,
        }
