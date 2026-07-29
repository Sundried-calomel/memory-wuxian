"""Immutable, transport-neutral governance proposal envelopes."""

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


PROPOSAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
SCHEMA_ID = "work-system-governor/governance-insight-v1"
MAX_PROPOSAL_BYTES = 1024 * 1024


class GovernanceProposalStore:
    """Store local proposals and enumerate read-only peer proposal replicas."""

    def __init__(self, store_or_root: Any):
        if hasattr(store_or_root, "root") and hasattr(store_or_root, "config"):
            store = store_or_root
        else:
            store = type(
                "_GovernanceProposalMemoryStore",
                (),
                {"root": Path(store_or_root), "config": {}},
            )()
        self.registry = EnvironmentRegistry(store.root)
        self.federation = FederationManager(store)
        self.root = self.registry.root / "governance-proposals"
        self.local_root = self.root / "local"
        self.lock_path = self.registry.locks_dir / "environment-governance-proposals.lock"

    def init(self) -> Dict[str, Any]:
        self.registry.init()
        self.local_root.mkdir(parents=True, exist_ok=True)
        return {"status": "initialized", "root": str(self.root)}

    def local_node_id(self) -> str:
        return safe_node_id(self.federation.node()["node_id"])

    def propose(self, proposal: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        if not isinstance(proposal, dict):
            raise ValueError("governance proposal must be an object")
        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise ValueError("governance proposal_id is invalid")
        origin = safe_node_id(str(proposal.get("origin_node_id", "")))
        if origin != self.local_node_id():
            raise ValueError("governance proposal origin must equal the local node")
        if proposal.get("schema_version") != 1:
            raise ValueError("governance proposal schema_version is unsupported")
        content = canonical_bytes(proposal)
        if len(content) > MAX_PROPOSAL_BYTES:
            raise ValueError("governance proposal exceeds size limit")
        digest = hashlib.sha256(content).hexdigest()
        envelope = {
            "format": "memory-wuxian-governance-proposal-v1",
            "schema_id": SCHEMA_ID,
            "proposal_id": proposal_id,
            "origin_node_id": origin,
            "content_sha256": digest,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        existing = sorted(self.local_root.glob(f"{proposal_id}-*.json"))
        if existing:
            current = json.loads(existing[0].read_text(encoding="utf-8"))
            if current == envelope:
                return {
                    "status": "no-change",
                    "proposal_id": proposal_id,
                    "content_sha256": digest,
                    "path": str(existing[0]),
                }
            raise ValueError("governance proposal ID already has different content")
        result = {
            "status": "preview",
            "proposal_id": proposal_id,
            "content_sha256": digest,
            "acceptance_implied": False,
        }
        if not apply:
            return result
        self.init()
        path = self.local_root / f"{proposal_id}-{digest}.json"
        with exclusive_lock(self.lock_path):
            conflicts = sorted(self.local_root.glob(f"{proposal_id}-*.json"))
            if conflicts:
                raise ValueError("governance proposal appeared before apply")
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
            events.append(
                {
                    "source_event_id": (
                        f"governance-proposal:{envelope['proposal_id']}:"
                        f"{envelope['content_sha256']}"
                    ),
                    "proposal_id": envelope["proposal_id"],
                    "payload": envelope,
                }
            )
        return events

    @staticmethod
    def validate_envelope(
        envelope: Dict[str, Any], *, expected_origin: str | None = None
    ) -> Dict[str, Any]:
        required = {
            "format", "schema_id", "proposal_id", "origin_node_id",
            "content_sha256", "content_base64",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise ValueError("governance proposal envelope fields are invalid")
        if envelope["format"] != "memory-wuxian-governance-proposal-v1":
            raise ValueError("governance proposal envelope format is unsupported")
        if envelope["schema_id"] != SCHEMA_ID:
            raise ValueError("governance proposal schema identity is unsupported")
        if not PROPOSAL_ID_RE.fullmatch(str(envelope["proposal_id"])):
            raise ValueError("governance proposal envelope ID is invalid")
        origin = safe_node_id(str(envelope["origin_node_id"]))
        if expected_origin and origin != safe_node_id(expected_origin):
            raise ValueError("governance proposal envelope origin mismatch")
        try:
            content = base64.b64decode(envelope["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("governance proposal content encoding is invalid") from error
        if len(content) > MAX_PROPOSAL_BYTES:
            raise ValueError("governance proposal exceeds size limit")
        if hashlib.sha256(content).hexdigest() != envelope["content_sha256"]:
            raise ValueError("governance proposal content hash mismatch")
        proposal = json.loads(content)
        if proposal.get("proposal_id") != envelope["proposal_id"]:
            raise ValueError("governance proposal identity mismatch")
        if safe_node_id(str(proposal.get("origin_node_id", ""))) != origin:
            raise ValueError("governance proposal content origin mismatch")
        return json.loads(json.dumps(envelope))

    def list(self) -> Dict[str, Any]:
        local = [item["payload"] for item in self.local_events()]
        remote = []
        replicas = self.registry.root / "replicas" / "peers"
        if replicas.is_dir():
            for path in sorted(replicas.glob("*/governance-proposals/*.json")):
                remote.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "status": "listed",
            "local": local,
            "remote": remote,
            "automatic_acceptance": False,
        }
