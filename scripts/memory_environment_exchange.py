"""Independent environment-v1 exchange stream for Memory無限 2.0."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory_environment import EnvironmentRegistry, canonical_bytes, read_json
from memory_environment_governance import GovernanceProposalStore
from memory_environment_skills import MANIFEST_NAME, skill_package_contract_bytes
from memory_federation import (
    FederationManager,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    bytes_sha256,
    canonical_sha256,
    now_iso,
    read_jsonl,
    safe_node_id,
    safe_relative_path,
)


ENVIRONMENT_BUNDLE_FORMAT = "memory-wuxian-environment-bundle-v1"
ENVIRONMENT_PROTOCOL_VERSION = 1
MAX_ARTIFACTS = 256
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_FILES = 2
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 200


def _file_marker(path: Path) -> Dict[str, int]:
    try:
        stat = path.stat()
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    except OSError:
        return {"size": 0, "mtime_ns": 0}


class EnvironmentExchangeManager:
    """Stage authenticated peer Environment revisions without auto-installing them."""

    def __init__(self, store: Any):
        self.store = store
        self.root = store.root
        self.registry = EnvironmentRegistry(self.root)
        self.federation = FederationManager(store)
        self.governance = GovernanceProposalStore(store)
        self.metadata_root = self.registry.root / "exchange"
        self.export_state_path = self.metadata_root / "export-state.json"
        self.export_ledger_path = self.metadata_root / "export-ledger.jsonl"
        self.sync_log_path = self.metadata_root / "sync-log.jsonl"
        self.replica_root = self.registry.root / "replicas"
        self.exchange_lock_path = (
            self.registry.locks_dir / "environment-exchange.lock"
        )

    def init_layout(self) -> None:
        self.registry.init()
        self.governance.init()
        for directory in (
            self.metadata_root,
            self.replica_root / "peers",
            self.registry.staging_dir / "incoming",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.export_state_path.exists():
            atomic_write_json(
                self.export_state_path,
                {
                    "format_version": 1,
                    "next_event_sequence": 1,
                    "source_events": {},
                },
            )
        if not self.export_ledger_path.exists():
            atomic_write_bytes(self.export_ledger_path, b"")
        if not self.sync_log_path.exists():
            atomic_write_bytes(self.sync_log_path, b"")

    def node(self) -> Dict[str, Any]:
        return self.federation.node()

    def peers(self) -> List[Dict[str, Any]]:
        return self.federation.peers()

    def status(self) -> Dict[str, Any]:
        base = self.federation.status()
        base.update(
            {
                "stream_id": "environment-v1",
                "local_event_sequence": max(
                    (
                        int(item["event_sequence"])
                        for item in read_jsonl(self.export_ledger_path)
                    ),
                    default=0,
                ),
            }
        )
        return base

    def log_sync(self, event: str, node_id: str, details: Dict[str, Any]) -> None:
        self.init_layout()
        rows = read_jsonl(self.sync_log_path)
        rows.append(
            {
                "timestamp": now_iso(),
                "event": event,
                "node_id": safe_node_id(node_id),
                "details": details,
            }
        )
        atomic_write_jsonl(self.sync_log_path, rows)

    def exchange_observation(self, _timestamp: float) -> Dict[str, Any]:
        registry = (
            read_json(self.registry.registry_path)
            if self.registry.registry_path.exists()
            else {"events": []}
        )
        return {
            "completed_rounds": len(registry.get("events") or []),
            "raw_today": _file_marker(self.registry.registry_path),
            "summary_registry": _file_marker(self.export_ledger_path),
            "title_index": _file_marker(self.registry.state_path),
            "governance_proposals": {
                "count": len(self.governance.local_events()),
                "directory": _file_marker(self.governance.local_root),
            },
        }

    def refresh_export_ledger(self) -> List[Dict[str, Any]]:
        self.init_layout()
        local_node_id = self.node()["node_id"]
        state = read_json(self.export_state_path)
        known = dict(state.get("source_events") or {})
        ledger = read_jsonl(self.export_ledger_path)
        next_sequence = int(state.get("next_event_sequence", 1))
        registry = self.registry._read_registry()
        for event in registry["events"]:
            if event.get("operation") != "artifact-revision":
                continue
            source_event_id = str(event["event_id"])
            if source_event_id in known:
                continue
            artifact = self.registry._read_relative_json(
                event["artifact_path"], "artifact_path"
            )
            revision = self.registry._read_relative_json(
                event["revision_path"], "revision_path"
            )
            if revision["origin_node_id"] != local_node_id:
                continue
            if self._superseded_skill_without_package(
                registry, artifact, revision
            ):
                continue
            self.registry._verify_object(revision)
            object_path = self.registry._resolve_relative(
                revision["object_path"], "object_path"
            )
            content = object_path.read_bytes()
            package_attachment = self._skill_package_attachment(
                artifact, revision, content
            )
            payload = {
                "artifact": artifact,
                "revision": revision,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "package_attachment": package_attachment,
            }
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "artifact-revision",
                    "artifact_id": artifact["artifact_id"],
                    "revision_id": revision["revision_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        for event in self.governance.local_events():
            source_event_id = event["source_event_id"]
            if source_event_id in known:
                continue
            payload = event["payload"]
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "governance-proposal",
                    "proposal_id": event["proposal_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        atomic_write_jsonl(self.export_ledger_path, ledger)
        atomic_write_json(
            self.export_state_path,
            {
                "format_version": 1,
                "next_event_sequence": next_sequence,
                "source_events": known,
            },
        )
        return ledger

    def _superseded_skill_without_package(
        self,
        registry: Dict[str, Any],
        artifact: Dict[str, Any],
        revision: Dict[str, Any],
    ) -> bool:
        """Skip only replaced local Skill candidates that never passed package verification."""

        if not artifact["object_class"].endswith("-skill"):
            return False
        current = registry["current_artifacts"].get(artifact["artifact_id"])
        if (
            not isinstance(current, dict)
            or current.get("revision_id") == revision["revision_id"]
        ):
            return False
        reference_path = (
            self.registry.root
            / "packages"
            / "by-revision"
            / f"{revision['revision_id'].split(':', 1)[1]}.json"
        )
        return not reference_path.is_file() or reference_path.is_symlink()

    def export_delta(
        self,
        output: Path,
        after_event_sequence: int = 0,
        target_node_id: Optional[str] = None,
        previous_bundle_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        node = self.node()
        ledger = self.refresh_export_ledger()
        after = int(after_event_sequence)
        latest = max((int(item["event_sequence"]) for item in ledger), default=0)
        if after < 0 or after > latest:
            raise ValueError("environment export cursor is invalid")
        if after and not (
            isinstance(previous_bundle_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", previous_bundle_sha256)
        ):
            raise ValueError("noninitial environment export requires predecessor hash")
        if not after and previous_bundle_sha256 is not None:
            raise ValueError("initial environment export cannot name a predecessor")
        pending = [item for item in ledger if int(item["event_sequence"]) > after]
        if not pending:
            return {
                "status": "no-change",
                "origin_node_id": node["node_id"],
                "after_event_sequence": after,
            }
        selected = []
        size = 0
        for item in pending:
            encoded = canonical_bytes(item) + b"\n"
            if len(encoded) > MAX_PAYLOAD_BYTES:
                raise ValueError("environment artifact exceeds bundle limit")
            if len(selected) >= MAX_ARTIFACTS or size + len(encoded) > MAX_PAYLOAD_BYTES:
                break
            selected.append(item)
            size += len(encoded)
        payload = b"".join(canonical_bytes(item) + b"\n" for item in selected)
        manifest_base = {
            "format": ENVIRONMENT_BUNDLE_FORMAT,
            "protocol_version": ENVIRONMENT_PROTOCOL_VERSION,
            "stream_id": "environment-v1",
            "origin_node_id": node["node_id"],
            "target_node_id": (
                safe_node_id(target_node_id) if target_node_id else None
            ),
            "base_event_sequence": after,
            "previous_bundle_sha256": previous_bundle_sha256,
            "from_event_sequence": int(selected[0]["event_sequence"]),
            "to_event_sequence": int(selected[-1]["event_sequence"]),
            "artifact_count": len(selected),
            "payload_path": "payload/environment.jsonl",
            "payload_bytes": len(payload),
            "payload_sha256": bytes_sha256(payload),
        }
        manifest = {
            **manifest_base,
            "bundle_id": f"mwb-{canonical_sha256(manifest_base)[:32]}",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".part", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        manifest, ensure_ascii=False, indent=2, sort_keys=True
                    ).encode("utf-8")
                    + b"\n",
                )
                archive.writestr("payload/environment.jsonl", payload)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "status": "created",
            "bundle": str(output),
            "bundle_id": manifest["bundle_id"],
            "origin_node_id": node["node_id"],
            "from_event_sequence": manifest["from_event_sequence"],
            "to_event_sequence": manifest["to_event_sequence"],
            "artifact_count": manifest["artifact_count"],
            "has_more": manifest["to_event_sequence"] < latest,
            "sha256": bytes_sha256(output.read_bytes()),
        }

    def read_bundle_manifest(self, bundle: Path) -> Dict[str, Any]:
        if not bundle.is_file():
            raise ValueError("environment bundle does not exist")
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_BUNDLE_FILES or len(names) != len(set(names)):
                raise ValueError("environment bundle file inventory is invalid")
            for name in names:
                safe_relative_path(name)
            if set(names) != {"manifest.json", "payload/environment.jsonl"}:
                raise ValueError("environment bundle contains unexpected files")
            inventory = {item.filename: item for item in infos}
            manifest_info = inventory["manifest.json"]
            payload_info = inventory["payload/environment.jsonl"]
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("environment bundle manifest exceeds size limit")
            if payload_info.file_size > MAX_PAYLOAD_BYTES:
                raise ValueError("environment bundle payload exceeds size limit")
            for info in infos:
                if info.file_size and info.compress_size == 0:
                    raise ValueError("environment bundle compression metadata is invalid")
                if (
                    info.compress_size
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("environment bundle compression ratio exceeds limit")
            manifest = json.loads(archive.read("manifest.json"))
        return manifest

    def read_bundle(
        self, bundle: Path
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        manifest = self.read_bundle_manifest(bundle)
        with zipfile.ZipFile(bundle) as archive:
            payload = archive.read("payload/environment.jsonl")
        if manifest.get("format") != ENVIRONMENT_BUNDLE_FORMAT:
            raise ValueError("unsupported environment bundle format")
        if manifest.get("stream_id") != "environment-v1":
            raise ValueError("environment bundle stream mismatch")
        if int(manifest.get("protocol_version", 0)) != ENVIRONMENT_PROTOCOL_VERSION:
            raise ValueError("unsupported environment protocol")
        if int(manifest.get("payload_bytes", -1)) != len(payload):
            raise ValueError("environment payload size mismatch")
        if manifest.get("payload_sha256") != bytes_sha256(payload):
            raise ValueError("environment payload hash mismatch")
        base_manifest = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        if manifest.get("bundle_id") != (
            f"mwb-{canonical_sha256(base_manifest)[:32]}"
        ):
            raise ValueError("environment bundle ID mismatch")
        records = [json.loads(line) for line in payload.splitlines() if line]
        if len(records) != int(manifest["artifact_count"]):
            raise ValueError("environment artifact count mismatch")
        start = int(manifest["from_event_sequence"])
        if any(
            int(item.get("event_sequence", -1)) != start + offset
            for offset, item in enumerate(records)
        ):
            raise ValueError("environment event sequence is not contiguous")
        if int(manifest["base_event_sequence"]) + 1 != start:
            raise ValueError("environment bundle base sequence mismatch")
        for item in records:
            self._validate_payload_record(item)
        return manifest, records

    def replica_state(self, node_id: str) -> Dict[str, Any]:
        peer_root = self._peer_root(node_id)
        path = peer_root / "replica-state.json"
        if path.exists():
            return read_json(path)
        return {
                "format_version": 1,
                "stream_id": "environment-v1",
                "origin_node_id": safe_node_id(node_id),
                "last_event_sequence": 0,
                "last_bundle_id": None,
                "last_bundle_sha256": None,
                "last_sync_at": None,
            }

    def import_delta(
        self, bundle: Path, expected_node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        self.init_layout()
        local_node = self.node()
        manifest, records = self.read_bundle(bundle)
        origin = safe_node_id(str(manifest["origin_node_id"]))
        if origin == local_node["node_id"]:
            raise ValueError("refusing own environment bundle")
        if expected_node_id and origin != safe_node_id(expected_node_id):
            raise ValueError("environment bundle origin mismatch")
        target = manifest.get("target_node_id")
        if target and safe_node_id(str(target)) != local_node["node_id"]:
            raise ValueError("environment bundle targets another node")
        peers = {item["node_id"]: item for item in self.peers()}
        if origin not in peers or not peers[origin].get("trusted"):
            raise ValueError("environment bundle peer is not trusted")
        state = self.replica_state(origin)
        bundle_hash = bytes_sha256(bundle.read_bytes())
        receipt_path = (
            self._peer_root(origin) / "receipts" / f"{manifest['bundle_id']}.json"
        )
        if receipt_path.exists():
            return {
                "status": "no-change",
                "bundle_id": manifest["bundle_id"],
                "origin_node_id": origin,
                "to_event_sequence": manifest["to_event_sequence"],
            }
        expected_sequence = int(state["last_event_sequence"]) + 1
        if int(manifest["from_event_sequence"]) != expected_sequence:
            raise ValueError("environment replica sequence gap")
        if state["last_event_sequence"]:
            if manifest["previous_bundle_sha256"] != state["last_bundle_sha256"]:
                raise ValueError("environment predecessor bundle mismatch")
        elif manifest["previous_bundle_sha256"] is not None:
            raise ValueError("initial environment import names a predecessor")
        incoming = self.registry.staging_dir / "incoming" / origin
        incoming.mkdir(parents=True, exist_ok=True)
        proposal_replica_root = (
            self._peer_root(origin) / "governance-proposals"
        )
        staged_artifacts = 0
        staged_governance_proposals = 0
        for item in records:
            if item.get("event_kind") == "governance-proposal":
                envelope = self.governance.validate_envelope(
                    item["payload"], expected_origin=origin
                )
                proposal_replica_root.mkdir(parents=True, exist_ok=True)
                proposal_id = envelope["proposal_id"]
                digest = envelope["content_sha256"]
                conflicts = sorted(
                    proposal_replica_root.glob(f"{proposal_id}-*.json")
                )
                proposal_record = {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": item["event_sequence"],
                    "proposal": envelope,
                    "received_bundle_id": manifest["bundle_id"],
                    "automatic_acceptance": False,
                }
                if conflicts:
                    if (
                        len(conflicts) != 1
                        or read_json(conflicts[0]) != proposal_record
                    ):
                        raise ValueError(
                            "peer governance proposal ID conflicts with existing content"
                        )
                else:
                    atomic_write_json(
                        proposal_replica_root
                        / f"{proposal_id}-{digest}.json",
                        proposal_record,
                    )
                staged_governance_proposals += 1
                continue
            payload = item["payload"]
            content = base64.b64decode(payload["content_base64"], validate=True)
            revision = payload["revision"]
            if bytes_sha256(content) != revision["content_sha256"]:
                raise ValueError("staged Environment object hash mismatch")
            stage_record = {
                "schema_version": 1,
                "stream_id": "environment-v1",
                "origin_node_id": origin,
                "event_sequence": item["event_sequence"],
                "artifact": payload["artifact"],
                "revision": revision,
                "content_base64": payload["content_base64"],
                "package_attachment": payload["package_attachment"],
                "received_bundle_id": manifest["bundle_id"],
            }
            stage_path = incoming / (
                f"{int(item['event_sequence']):020d}-"
                f"{canonical_sha256(item['artifact_id'])[:16]}.json"
            )
            if stage_path.exists() and read_json(stage_path) != stage_record:
                raise ValueError("staged Environment event conflicts with existing event")
            atomic_write_json(stage_path, stage_record)
            staged_artifacts += 1
        peer_root = self._peer_root(origin)
        peer_root.mkdir(parents=True, exist_ok=True)
        new_state = {
            "format_version": 1,
            "stream_id": "environment-v1",
            "origin_node_id": origin,
            "last_event_sequence": int(manifest["to_event_sequence"]),
            "last_bundle_id": manifest["bundle_id"],
            "last_bundle_sha256": bundle_hash,
            "last_sync_at": now_iso(),
        }
        atomic_write_json(peer_root / "replica-state.json", new_state)
        atomic_write_json(
            receipt_path,
            {
                "format_version": 1,
                "stream_id": "environment-v1",
                "bundle_sha256": bundle_hash,
                "manifest": manifest,
                "received_at": new_state["last_sync_at"],
            },
        )
        return {
            "status": "imported",
            "bundle_id": manifest["bundle_id"],
            "origin_node_id": origin,
            "to_event_sequence": manifest["to_event_sequence"],
            "staged_artifacts": staged_artifacts,
            "staged_governance_proposals": staged_governance_proposals,
        }

    def _peer_root(self, node_id: str) -> Path:
        return self.replica_root / "peers" / safe_node_id(node_id)

    def _validate_payload_record(self, item: Dict[str, Any]) -> None:
        legacy_required = {
            "event_sequence",
            "source_event_id",
            "artifact_id",
            "revision_id",
            "payload_sha256",
            "payload",
        }
        if not isinstance(item, dict):
            raise ValueError("environment payload event fields are invalid")
        event_kind = item.get("event_kind")
        if event_kind == "governance-proposal":
            required = {
                "event_sequence",
                "source_event_id",
                "event_kind",
                "proposal_id",
                "payload_sha256",
                "payload",
            }
            if set(item) != required:
                raise ValueError("governance proposal event fields are invalid")
            payload = self.governance.validate_envelope(item["payload"])
            if item["proposal_id"] != payload["proposal_id"]:
                raise ValueError("governance proposal event identity mismatch")
            if item["payload_sha256"] != canonical_sha256(payload):
                raise ValueError("governance proposal event hash mismatch")
            return
        if event_kind == "artifact-revision":
            required = legacy_required | {"event_kind"}
        elif event_kind is None:
            required = legacy_required
        else:
            raise ValueError("environment payload event kind is unsupported")
        if set(item) != required:
            raise ValueError("environment payload event fields are invalid")
        payload = item["payload"]
        if item["payload_sha256"] != canonical_sha256(payload):
            raise ValueError("environment payload event hash mismatch")
        if not isinstance(payload, dict) or set(payload) != {
            "artifact",
            "revision",
            "content_base64",
            "package_attachment",
        }:
            raise ValueError("environment payload is invalid")
        artifact = self.registry._validate_artifact(payload["artifact"])
        revision = self.registry._validate_revision(payload["revision"])
        if artifact["artifact_id"] != item["artifact_id"]:
            raise ValueError("environment artifact identity mismatch")
        if revision["revision_id"] != item["revision_id"]:
            raise ValueError("environment revision identity mismatch")
        if revision["artifact_id"] != artifact["artifact_id"]:
            raise ValueError("environment revision artifact mismatch")
        try:
            content = base64.b64decode(payload["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("environment object base64 is invalid") from error
        if bytes_sha256(content) != revision["content_sha256"]:
            raise ValueError("environment object content hash mismatch")
        attachment = payload["package_attachment"]
        if artifact["object_class"].endswith("-skill"):
            self._validate_skill_attachment(attachment, revision, content)
        elif attachment is not None:
            raise ValueError("rule payload cannot carry a Skill package attachment")

    def _skill_package_attachment(
        self,
        artifact: Dict[str, Any],
        revision: Dict[str, Any],
        contract: bytes,
    ) -> Optional[Dict[str, str]]:
        if not artifact["object_class"].endswith("-skill"):
            return None
        reference_path = (
            self.registry.root
            / "packages"
            / "by-revision"
            / f"{revision['revision_id'].split(':', 1)[1]}.json"
        )
        if not reference_path.is_file() or reference_path.is_symlink():
            raise ValueError(
                "registered Skill revision has no verified package attachment"
            )
        reference = read_json(reference_path)
        required = {
            "schema_version",
            "artifact_id",
            "revision_id",
            "package_sha256",
            "package_path",
            "package_contract_sha256",
            "verified_at",
        }
        if not isinstance(reference, dict) or set(reference) != required:
            raise ValueError("Skill package reference is invalid")
        if (
            reference["artifact_id"] != artifact["artifact_id"]
            or reference["revision_id"] != revision["revision_id"]
        ):
            raise ValueError("Skill package reference identity mismatch")
        if reference["package_contract_sha256"] != bytes_sha256(contract):
            raise ValueError("Skill package reference contract hash mismatch")
        expected_package_path = (
            f"packages/sha256/{reference['package_sha256'][:2]}/"
            f"{reference['package_sha256'][2:]}"
        )
        if reference["package_path"] != expected_package_path:
            raise ValueError("Skill package reference path is not content-addressed")
        package_path = self.registry._resolve_relative(
            reference["package_path"], "package_path"
        )
        package = package_path.read_bytes()
        if bytes_sha256(package) != reference["package_sha256"]:
            raise ValueError("Skill package attachment hash mismatch")
        attachment = {
            "package_sha256": reference["package_sha256"],
            "content_base64": base64.b64encode(package).decode("ascii"),
        }
        self._validate_skill_attachment(attachment, revision, contract)
        return attachment

    @staticmethod
    def _validate_skill_attachment(
        attachment: Any,
        revision: Dict[str, Any],
        contract: bytes,
    ) -> None:
        if not isinstance(attachment, dict) or set(attachment) != {
            "package_sha256",
            "content_base64",
        }:
            raise ValueError("Skill package attachment is invalid")
        try:
            package = base64.b64decode(attachment["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("Skill package attachment base64 is invalid") from error
        if not isinstance(attachment["package_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", attachment["package_sha256"]
        ):
            raise ValueError("Skill package attachment hash is invalid")
        if bytes_sha256(package) != attachment["package_sha256"]:
            raise ValueError("Skill package attachment hash mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                manifest = json.loads(archive.read(MANIFEST_NAME))
        except (KeyError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise ValueError("Skill package attachment ZIP is invalid") from error
        if manifest.get("source_revision") != revision["revision_id"]:
            raise ValueError("Skill package attachment revision mismatch")
        if skill_package_contract_bytes(manifest) != contract:
            raise ValueError("Skill package attachment contract mismatch")
