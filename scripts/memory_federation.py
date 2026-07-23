#!/usr/bin/env python3
"""Federated replica exchange for Memory Wuxian archives."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROTOCOL_VERSION = 1
BUNDLE_FORMAT = "memory-wuxian-delta-v1"
NODE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
AGE_PUBLIC_KEY_PATTERN = re.compile(r"^age1[0-9a-z]{50,80}$")
SIGNING_PUBLIC_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,128}$")
KEY_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{16,64}$")
MAX_BUNDLE_FILES = 4
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACTS = 100_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
MAX_EXPORT_PAYLOAD_BYTES = MAX_BUNDLE_BYTES - MAX_MANIFEST_BYTES
SSH_CONNECT_TIMEOUT_SECONDS = 15
SSH_COMMAND_TIMEOUT_SECONDS = 600


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def raw_record_sha256(record: Dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"_path", "content_sha256"}
    }
    return canonical_sha256(payload)


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not path.exists() and default is not None:
        return dict(default)
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return result


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_bytes(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n",
    )


def atomic_write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    atomic_write_bytes(
        path,
        b"".join(canonical_bytes(record) + b"\n" for record in records),
    )


def safe_node_id(value: str) -> str:
    normalized = value.strip().lower()
    if not NODE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Node IDs must contain 3-64 lowercase ASCII letters, digits, or hyphens"
        )
    return normalized


def safe_relative_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.drive:
        raise ValueError(f"Unsafe bundle path: {value}")
    return candidate


class FederationManager:
    """Manage one local node and read-only replicas from other nodes."""

    def __init__(self, store: Any):
        self.store = store
        self.root = store.root
        self.metadata_root = self.root / "federation"
        self.node_path = self.metadata_root / "node.json"
        self.export_state_path = self.metadata_root / "export-state.json"
        self.export_ledger_path = self.metadata_root / "export-ledger.jsonl"
        self.peers_dir = self.metadata_root / "peers"
        self.sync_log_path = self.metadata_root / "sync-log.jsonl"
        configured = (
            store.config.get("federation", {}).get("replica_directory")
            if isinstance(store.config.get("federation"), dict)
            else None
        )
        self.replica_root = (
            Path(str(configured)).expanduser()
            if configured
            else self.root.parent / f"{self.root.name}-federation-cache"
        ).resolve()
        resolved_root = self.root.resolve()
        if (
            self.replica_root == resolved_root
            or self.replica_root.is_relative_to(resolved_root)
            or resolved_root.is_relative_to(self.replica_root)
        ):
            raise ValueError(
                "Federation replica_directory must be separate from the primary archive"
            )
        self.global_index_dir = self.replica_root / "global-index"

    def init_layout(self) -> None:
        for directory in (
            self.metadata_root,
            self.peers_dir,
            self.replica_root / "peers",
            self.global_index_dir,
            self.root / ".locks",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.export_state_path.exists():
            atomic_write_json(
                self.export_state_path,
                {
                    "format_version": 1,
                    "next_event_sequence": 1,
                    "artifacts": {},
                },
            )
        if not self.export_ledger_path.exists():
            atomic_write_bytes(self.export_ledger_path, b"")
        if not self.sync_log_path.exists():
            atomic_write_bytes(self.sync_log_path, b"")

    def init_node(
        self,
        display_name: Optional[str] = None,
        requested_node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.init_layout()
        if self.node_path.exists():
            return {"status": "exists", "node": read_json(self.node_path)}
        node_id = safe_node_id(
            requested_node_id or f"mw-{uuid.uuid4().hex[:20]}"
        )
        node = {
            "format_version": 1,
            "protocol_version": PROTOCOL_VERSION,
            "node_id": node_id,
            "display_name": (display_name or platform.node() or node_id).strip(),
            "created_at": now_iso(),
            "replica_root": str(self.replica_root),
        }
        atomic_write_json(self.node_path, node)
        return {"status": "created", "node": node}

    def node(self) -> Dict[str, Any]:
        if not self.node_path.exists():
            raise ValueError("Federation node is not initialized; run init-node first")
        node = read_json(self.node_path)
        safe_node_id(str(node.get("node_id", "")))
        if int(node.get("protocol_version", 0)) != PROTOCOL_VERSION:
            raise ValueError("Unsupported local federation protocol version")
        return node

    def peer_path(self, node_id: str) -> Path:
        return self.peers_dir / f"{safe_node_id(node_id)}.json"

    def add_peer(
        self,
        node_id: str,
        display_name: Optional[str] = None,
        host: Optional[str] = None,
        port: int = 22,
        remote_root: Optional[str] = None,
        remote_config: Optional[str] = None,
        remote_cli: Optional[str] = None,
        remote_python: str = "python3",
        remote_shell: str = "posix",
    ) -> Dict[str, Any]:
        self.init_layout()
        local_node_id = self.node()["node_id"]
        node_id = safe_node_id(node_id)
        if node_id == local_node_id:
            raise ValueError("A node cannot be added as its own peer")
        if remote_shell not in {"posix", "powershell"}:
            raise ValueError("remote_shell must be posix or powershell")
        if host and (host.startswith("-") or any(character in host for character in "\r\n")):
            raise ValueError("SSH host must not begin with '-' or contain line breaks")
        if not 1 <= int(port) <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        path = self.peer_path(node_id)
        existing = read_json(path) if path.exists() else {}
        peer = {
            "format_version": 1,
            "node_id": node_id,
            "display_name": display_name or existing.get("display_name") or node_id,
            "trusted": True,
            "transport": {
                "type": "ssh" if host else "offline",
                "host": host,
                "port": int(port),
                "remote_root": remote_root,
                "remote_config": remote_config,
                "remote_cli": remote_cli,
                "remote_python": remote_python,
                "remote_shell": remote_shell,
            },
            "cloud_identity": existing.get("cloud_identity"),
        }
        atomic_write_json(path, peer)
        return {"status": "registered", "peer": peer}

    def set_peer_cloud_identity(
        self,
        node_id: str,
        encryption_public_key: str,
        signing_public_key: str,
        fingerprint: str,
    ) -> Dict[str, Any]:
        path = self.peer_path(node_id)
        if not path.exists():
            raise ValueError(
                f"Unknown peer: {safe_node_id(node_id)}; register it with add-peer first"
            )
        encryption_public_key = encryption_public_key.strip()
        signing_public_key = signing_public_key.strip()
        fingerprint = fingerprint.strip().lower()
        if not AGE_PUBLIC_KEY_PATTERN.fullmatch(encryption_public_key):
            raise ValueError("Invalid age encryption public key")
        if not SIGNING_PUBLIC_KEY_PATTERN.fullmatch(signing_public_key):
            raise ValueError("Invalid Ed25519 signing public key")
        if not KEY_FINGERPRINT_PATTERN.fullmatch(fingerprint):
            raise ValueError("Invalid cloud identity fingerprint")
        peer = read_json(path)
        if not peer.get("trusted"):
            raise ValueError(f"Peer {safe_node_id(node_id)} is revoked")
        peer["cloud_identity"] = {
            "format_version": 1,
            "encryption_public_key": encryption_public_key,
            "signing_public_key": signing_public_key,
            "fingerprint": fingerprint,
        }
        atomic_write_json(path, peer)
        return {"status": "cloud-identity-registered", "peer": peer}

    def revoke_peer(self, node_id: str) -> Dict[str, Any]:
        path = self.peer_path(node_id)
        if not path.exists():
            raise ValueError(f"Unknown peer: {node_id}")
        peer = read_json(path)
        peer["trusted"] = False
        atomic_write_json(path, peer)
        self.log_sync("peer-revoked", node_id, {})
        return {"status": "revoked", "peer": peer}

    def peers(self) -> List[Dict[str, Any]]:
        self.init_layout()
        return [read_json(path) for path in sorted(self.peers_dir.glob("*.json"))]

    def local_artifacts(self) -> Dict[str, Dict[str, Any]]:
        artifacts: Dict[str, Dict[str, Any]] = {}
        for record in self.store.read_all_raw():
            stored_hash = record.get("content_sha256")
            actual_hash = raw_record_sha256(record)
            if stored_hash and stored_hash != actual_hash:
                raise ValueError(
                    f"Raw source hash mismatch for {record.get('message_id')}"
                )
            clean_record = {key: value for key, value in record.items() if key != "_path"}
            clean_record["content_sha256"] = actual_hash
            artifact_id = f"raw:{record['message_id']}"
            artifacts[artifact_id] = {
                "artifact_type": "raw",
                "artifact_id": artifact_id,
                "sha256": canonical_sha256(clean_record),
                "payload": clean_record,
            }
        for summary in self.store.summary_records():
            path = self.root / str(summary["path"])
            content = path.read_text(encoding="utf-8")
            payload = {
                "record": summary,
                "content": content,
            }
            artifact_id = f"summary:{summary['summary_id']}"
            artifacts[artifact_id] = {
                "artifact_type": "summary",
                "artifact_id": artifact_id,
                "sha256": canonical_sha256(payload),
                "payload": payload,
            }
        for entry in read_jsonl(self.store.title_index_path):
            digest = canonical_sha256(entry)
            artifact_id = f"title:{digest}"
            artifacts[artifact_id] = {
                "artifact_type": "title",
                "artifact_id": artifact_id,
                "sha256": digest,
                "payload": entry,
            }
        return artifacts

    def refresh_export_ledger(self) -> Dict[str, Dict[str, Any]]:
        self.init_layout()
        artifacts = self.local_artifacts()
        ledger = read_jsonl(self.export_ledger_path)
        known: Dict[str, Dict[str, Any]] = {}
        for expected_sequence, event in enumerate(ledger, 1):
            event_sequence = int(event.get("event_sequence", 0))
            artifact_id = str(event.get("artifact_id", ""))
            if event_sequence != expected_sequence:
                raise ValueError("Export ledger event sequence is not contiguous")
            if not artifact_id or artifact_id in known:
                raise ValueError("Export ledger contains a duplicate or empty artifact ID")
            known[artifact_id] = {
                "event_sequence": event_sequence,
                "artifact_type": event.get("artifact_type"),
                "sha256": event.get("sha256"),
            }
        next_sequence = len(ledger) + 1
        changed = False
        for artifact_id in sorted(artifacts):
            artifact = artifacts[artifact_id]
            if artifact_id in known:
                if known[artifact_id]["sha256"] != artifact["sha256"]:
                    raise ValueError(
                        f"Immutable local artifact changed: {artifact_id}"
                    )
                continue
            event = {
                "event_sequence": next_sequence,
                "artifact_type": artifact["artifact_type"],
                "artifact_id": artifact_id,
                "sha256": artifact["sha256"],
            }
            ledger.append(event)
            known[artifact_id] = {
                "event_sequence": next_sequence,
                "artifact_type": artifact["artifact_type"],
                "sha256": artifact["sha256"],
            }
            next_sequence += 1
            changed = True
        if changed:
            atomic_write_jsonl(self.export_ledger_path, ledger)
        expected_state = {
            "format_version": 1,
            "next_event_sequence": next_sequence,
            "artifacts": known,
        }
        current_state = read_json(self.export_state_path, {})
        if current_state != expected_state:
            atomic_write_json(self.export_state_path, expected_state)
        return artifacts

    def export_delta(
        self,
        output: Path,
        after_event_sequence: int = 0,
        target_node_id: Optional[str] = None,
        previous_bundle_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        node = self.node()
        artifacts = self.refresh_export_ledger()
        if int(after_event_sequence) < 0:
            raise ValueError("after_event_sequence must not be negative")
        latest_event_sequence = max(
            (
                int(event["event_sequence"])
                for event in read_jsonl(self.export_ledger_path)
            ),
            default=0,
        )
        if int(after_event_sequence) > latest_event_sequence:
            raise ValueError(
                f"Export cursor {after_event_sequence} is ahead of local event "
                f"sequence {latest_event_sequence}"
            )
        if int(after_event_sequence) > 0:
            if not previous_bundle_sha256 or not re.fullmatch(
                r"[0-9a-f]{64}", previous_bundle_sha256
            ):
                raise ValueError(
                    "A 64-character --previous-bundle-sha256 is required for "
                    "noninitial delta exports"
                )
        elif previous_bundle_sha256:
            raise ValueError(
                "An initial delta export must not declare a previous bundle"
            )
        pending_ledger = [
            event
            for event in read_jsonl(self.export_ledger_path)
            if int(event["event_sequence"]) > int(after_event_sequence)
        ]
        if not pending_ledger:
            return {
                "status": "no-change",
                "origin_node_id": node["node_id"],
                "after_event_sequence": int(after_event_sequence),
            }
        payload_records = []
        payload_size = 0
        for event in pending_ledger:
            artifact = artifacts.get(str(event["artifact_id"]))
            if artifact is None:
                raise ValueError(
                    f"Export artifact disappeared after ledger assignment: {event['artifact_id']}"
                )
            payload_record = {**event, "payload": artifact["payload"]}
            encoded = canonical_bytes(payload_record) + b"\n"
            if len(encoded) > MAX_EXPORT_PAYLOAD_BYTES:
                raise ValueError(
                    f"Artifact exceeds the federation bundle size limit: "
                    f"{event['artifact_id']}"
                )
            if (
                len(payload_records) >= MAX_ARTIFACTS
                or payload_size + len(encoded) > MAX_EXPORT_PAYLOAD_BYTES
            ):
                break
            payload_records.append(payload_record)
            payload_size += len(encoded)
        payload_bytes = b"".join(canonical_bytes(item) + b"\n" for item in payload_records)
        ledger = pending_ledger[:len(payload_records)]
        manifest_base = {
            "format": BUNDLE_FORMAT,
            "protocol_version": PROTOCOL_VERSION,
            "minimum_protocol_version": 1,
            "origin_node_id": node["node_id"],
            "target_node_id": safe_node_id(target_node_id) if target_node_id else None,
            "base_event_sequence": int(after_event_sequence),
            "previous_bundle_sha256": previous_bundle_sha256,
            "from_event_sequence": int(ledger[0]["event_sequence"]),
            "to_event_sequence": int(ledger[-1]["event_sequence"]),
            "artifact_count": len(payload_records),
            "payload_path": "payload/artifacts.jsonl",
            "payload_bytes": len(payload_bytes),
            "payload_sha256": bytes_sha256(payload_bytes),
        }
        manifest = {
            **manifest_base,
            "bundle_id": f"mwb-{canonical_sha256(manifest_base)[:32]}",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            suffix=".part",
            dir=str(output.parent),
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ).encode("utf-8")
                    + b"\n",
                )
                archive.writestr("payload/artifacts.jsonl", payload_bytes)
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())
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
            "has_more": int(manifest["to_event_sequence"]) < latest_event_sequence,
            "sha256": bytes_sha256(output.read_bytes()),
        }

    def read_bundle_manifest(self, bundle: Path) -> Dict[str, Any]:
        if not bundle.is_file():
            raise ValueError(f"Bundle does not exist: {bundle}")
        with zipfile.ZipFile(bundle, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_BUNDLE_FILES:
                raise ValueError("Bundle contains too many files")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Bundle contains duplicate paths")
            for name in names:
                safe_relative_path(name)
            if set(names) != {"manifest.json", "payload/artifacts.jsonl"}:
                raise ValueError("Bundle contains unexpected files")
            total_size = sum(info.file_size for info in infos)
            if total_size > MAX_BUNDLE_BYTES:
                raise ValueError("Bundle exceeds the uncompressed size limit")
            for info in infos:
                if (
                    info.file_size > MAX_MANIFEST_BYTES
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("Bundle contains an excessive compression ratio")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("Bundle manifest exceeds the size limit")
            manifest = json.loads(archive.read("manifest.json"))
        return manifest

    def read_bundle(
        self,
        bundle: Path,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        manifest = self.read_bundle_manifest(bundle)
        with zipfile.ZipFile(bundle, "r") as archive:
            payload_bytes = archive.read("payload/artifacts.jsonl")
        if manifest.get("format") != BUNDLE_FORMAT:
            raise ValueError("Unsupported bundle format")
        if int(manifest.get("protocol_version", 0)) != PROTOCOL_VERSION:
            raise ValueError("Unsupported bundle protocol version")
        origin_node_id = safe_node_id(str(manifest.get("origin_node_id", "")))
        target_node_id = manifest.get("target_node_id")
        if target_node_id:
            safe_node_id(str(target_node_id))
        if int(manifest.get("payload_bytes", -1)) != len(payload_bytes):
            raise ValueError("Bundle payload size mismatch")
        if manifest.get("payload_sha256") != bytes_sha256(payload_bytes):
            raise ValueError("Bundle payload SHA-256 mismatch")
        manifest_base = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        expected_bundle_id = f"mwb-{canonical_sha256(manifest_base)[:32]}"
        if manifest.get("bundle_id") != expected_bundle_id:
            raise ValueError("Bundle ID does not match its manifest")
        records = []
        for line_number, line in enumerate(payload_bytes.splitlines(), 1):
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid bundle payload JSONL at line {line_number}: {exc}"
                ) from exc
        if len(records) > MAX_ARTIFACTS:
            raise ValueError("Bundle contains too many artifacts")
        if len(records) != int(manifest.get("artifact_count", -1)):
            raise ValueError("Bundle artifact count mismatch")
        from_sequence = int(manifest["from_event_sequence"])
        to_sequence = int(manifest["to_event_sequence"])
        base_sequence = int(manifest["base_event_sequence"])
        if from_sequence < 1 or to_sequence < from_sequence:
            raise ValueError("Bundle event sequence range is invalid")
        if to_sequence - from_sequence + 1 != len(records):
            raise ValueError("Bundle event sequence range does not match its artifacts")
        if any(
            int(record["event_sequence"]) != from_sequence + offset
            for offset, record in enumerate(records)
        ):
            raise ValueError("Bundle event sequence is not contiguous")
        if base_sequence < 0 or base_sequence + 1 != from_sequence:
            raise ValueError("Bundle base sequence does not match its first event")
        seen_ids = set()
        for record in records:
            artifact_id = str(record.get("artifact_id", ""))
            if not artifact_id or artifact_id in seen_ids:
                raise ValueError("Bundle contains duplicate or empty artifact IDs")
            seen_ids.add(artifact_id)
            if record.get("artifact_type") not in {"raw", "summary", "title"}:
                raise ValueError(f"Unsupported artifact type: {record.get('artifact_type')}")
            if record.get("sha256") != canonical_sha256(record.get("payload")):
                raise ValueError(f"Artifact SHA-256 mismatch: {artifact_id}")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"Artifact payload must be an object: {artifact_id}")
            artifact_type = str(record["artifact_type"])
            if artifact_type == "raw":
                expected_artifact_id = f"raw:{payload.get('message_id', '')}"
            elif artifact_type == "summary":
                summary_record = payload.get("record")
                if not isinstance(summary_record, dict):
                    raise ValueError("Summary artifact is missing its record")
                expected_artifact_id = (
                    f"summary:{summary_record.get('summary_id', '')}"
                )
            else:
                expected_artifact_id = f"title:{canonical_sha256(payload)}"
            if artifact_id != expected_artifact_id:
                raise ValueError(
                    f"Artifact ID does not match its payload: {artifact_id}"
                )
        manifest["origin_node_id"] = origin_node_id
        return manifest, records

    def inspect_bundle(self, bundle: Path) -> Dict[str, Any]:
        manifest, records = self.read_bundle(bundle)
        counts: Dict[str, int] = {}
        for record in records:
            key = str(record["artifact_type"])
            counts[key] = counts.get(key, 0) + 1
        return {
            "status": "valid",
            "bundle": str(bundle),
            "bundle_sha256": bytes_sha256(bundle.read_bytes()),
            "manifest": manifest,
            "artifact_counts": counts,
        }

    def replica_peer_root(self, node_id: str) -> Path:
        return self.replica_root / "peers" / safe_node_id(node_id)

    def replica_state(self, node_id: str) -> Dict[str, Any]:
        return read_json(
            self.replica_peer_root(node_id) / "replica-state.json",
            {
                "format_version": 1,
                "origin_node_id": safe_node_id(node_id),
                "last_event_sequence": 0,
                "last_bundle_id": None,
                "last_bundle_sha256": None,
                "last_sync_at": None,
                "artifact_hashes": {},
            },
        )

    def import_delta(
        self,
        bundle: Path,
        expected_node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.init_layout()
        local_node = self.node()
        preflight_manifest = self.read_bundle_manifest(bundle)
        preflight_origin = safe_node_id(
            str(preflight_manifest.get("origin_node_id", ""))
        )
        if expected_node_id and preflight_origin != safe_node_id(expected_node_id):
            raise ValueError(
                f"Bundle origin {preflight_origin} does not match expected peer "
                f"{safe_node_id(expected_node_id)}"
            )
        preflight_target = preflight_manifest.get("target_node_id")
        if preflight_target and safe_node_id(str(preflight_target)) != local_node["node_id"]:
            raise ValueError("Bundle is addressed to another node")
        preflight_peer_path = self.peer_path(preflight_origin)
        if not preflight_peer_path.exists():
            raise ValueError(
                f"Peer {preflight_origin} is not trusted; register it with add-peer"
            )
        if not read_json(preflight_peer_path).get("trusted"):
            raise ValueError(f"Peer {preflight_origin} is revoked")
        manifest, artifacts = self.read_bundle(bundle)
        origin_node_id = manifest["origin_node_id"]
        if origin_node_id == local_node["node_id"]:
            raise ValueError("Refusing to import this node's own bundle")
        if expected_node_id and origin_node_id != safe_node_id(expected_node_id):
            raise ValueError(
                f"Bundle origin {origin_node_id} does not match expected peer "
                f"{safe_node_id(expected_node_id)}"
            )
        target = manifest.get("target_node_id")
        if target and target != local_node["node_id"]:
            raise ValueError("Bundle is addressed to another node")
        peer_path = self.peer_path(origin_node_id)
        if not peer_path.exists():
            raise ValueError(
                f"Peer {origin_node_id} is not trusted; register it with add-peer"
            )
        peer = read_json(peer_path)
        if not peer.get("trusted"):
            raise ValueError(f"Peer {origin_node_id} is revoked")
        peer_root = self.replica_peer_root(origin_node_id)
        receipts_dir = peer_root / "receipts"
        receipt_path = receipts_dir / f"{manifest['bundle_id']}.json"
        transaction_path = peer_root / ".importing.json"
        state = self.replica_state(origin_node_id)
        if transaction_path.exists():
            transaction = read_json(transaction_path)
            if transaction.get("bundle_id") != manifest["bundle_id"]:
                raise RuntimeError(
                    f"Replica has an incomplete import for "
                    f"{transaction.get('bundle_id')}; retry that bundle first"
                )
            if (
                state.get("last_bundle_id") == manifest["bundle_id"]
                and int(state.get("last_event_sequence", 0))
                == int(manifest["to_event_sequence"])
            ):
                receipts_dir.mkdir(parents=True, exist_ok=True)
                if not receipt_path.exists():
                    atomic_write_json(
                        receipt_path,
                        {
                            "format_version": 1,
                            "received_at": state.get("last_sync_at") or now_iso(),
                            "bundle_sha256": bytes_sha256(bundle.read_bytes()),
                            "manifest": manifest,
                        },
                    )
                transaction_path.unlink(missing_ok=True)
                self.rebuild_global_indexes()
                return {
                    "status": "recovered",
                    "bundle_id": manifest["bundle_id"],
                    "origin_node_id": origin_node_id,
                    "to_event_sequence": manifest["to_event_sequence"],
                }
        if receipt_path.exists():
            transaction_path.unlink(missing_ok=True)
            self.rebuild_global_indexes()
            return {
                "status": "no-change",
                "reason": "bundle-already-imported",
                "bundle_id": manifest["bundle_id"],
                "origin_node_id": origin_node_id,
            }
        last_sequence = int(state.get("last_event_sequence", 0))
        if int(manifest["base_event_sequence"]) != last_sequence:
            relation = (
                "overlap"
                if int(manifest["base_event_sequence"]) < last_sequence
                else "gap"
            )
            raise ValueError(
                f"Bundle sequence {relation}: expected base {last_sequence}, "
                f"received {manifest['base_event_sequence']}"
            )
        previous_bundle_sha256 = manifest.get("previous_bundle_sha256")
        if last_sequence == 0:
            if previous_bundle_sha256 is not None:
                raise ValueError("Initial bundle unexpectedly declares a predecessor")
        elif previous_bundle_sha256 != state.get("last_bundle_sha256"):
            raise ValueError(
                "Bundle predecessor SHA-256 does not match the imported chain"
            )
        raw_path = peer_root / "raw-records.jsonl"
        titles_path = peer_root / "conversation-titles.jsonl"
        existing_raw = read_jsonl(raw_path)
        existing_titles = read_jsonl(titles_path)
        raw_by_id = {str(record["message_id"]): record for record in existing_raw}
        title_hashes = {canonical_sha256(record) for record in existing_titles}
        next_raw = list(existing_raw)
        next_titles = list(existing_titles)
        summary_writes: List[Tuple[Path, bytes]] = []
        artifact_hashes = dict(state.get("artifact_hashes", {}))
        for artifact in artifacts:
            artifact_id = str(artifact["artifact_id"])
            artifact_hash = str(artifact["sha256"])
            previous_hash = artifact_hashes.get(artifact_id)
            if previous_hash and previous_hash != artifact_hash:
                raise ValueError(f"Replica artifact conflict: {artifact_id}")
            payload = artifact["payload"]
            artifact_type = artifact["artifact_type"]
            if artifact_type == "raw":
                message_id = str(payload.get("message_id", ""))
                if payload.get("content_sha256") != raw_record_sha256(payload):
                    raise ValueError(f"Remote raw record hash mismatch: {message_id}")
                existing = raw_by_id.get(message_id)
                if existing and canonical_sha256(existing) != canonical_sha256(payload):
                    raise ValueError(f"Remote message ID conflict: {message_id}")
                if not existing:
                    raw_by_id[message_id] = payload
                    next_raw.append(payload)
            elif artifact_type == "summary":
                record = payload.get("record") or {}
                summary_id = str(record.get("summary_id", ""))
                if not summary_id:
                    raise ValueError("Remote summary is missing summary_id")
                safe_summary_name = hashlib.sha256(
                    summary_id.encode("utf-8")
                ).hexdigest()
                destination = (
                    peer_root / "summaries" / f"summary-{safe_summary_name}.json"
                )
                content = (
                    json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8")
                if destination.exists() and destination.read_bytes() != content:
                    raise ValueError(f"Remote summary conflict: {summary_id}")
                if not destination.exists():
                    summary_writes.append((destination, content))
            elif artifact_type == "title":
                title_hash = canonical_sha256(payload)
                if title_hash not in title_hashes:
                    title_hashes.add(title_hash)
                    next_titles.append(payload)
            artifact_hashes[artifact_id] = artifact_hash
        next_raw.sort(key=lambda record: int(record.get("sequence", 0)))
        next_state = {
            **state,
            "last_event_sequence": int(manifest["to_event_sequence"]),
            "last_bundle_id": manifest["bundle_id"],
            "last_bundle_sha256": bytes_sha256(bundle.read_bytes()),
            "last_sync_at": now_iso(),
            "artifact_hashes": artifact_hashes,
        }
        atomic_write_json(
            transaction_path,
            {
                "format_version": 1,
                "bundle_id": manifest["bundle_id"],
                "origin_node_id": origin_node_id,
                "started_at": now_iso(),
            },
        )
        committed = False
        try:
            atomic_write_jsonl(raw_path, next_raw)
            atomic_write_jsonl(titles_path, next_titles)
            for destination, content in summary_writes:
                if destination.exists():
                    continue
                atomic_write_bytes(destination, content)
            atomic_write_json(peer_root / "replica-state.json", next_state)
            receipts_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                receipt_path,
                {
                    "format_version": 1,
                    "received_at": next_state["last_sync_at"],
                    "bundle_sha256": bytes_sha256(bundle.read_bytes()),
                    "manifest": manifest,
                },
            )
            committed = True
        finally:
            if committed:
                transaction_path.unlink(missing_ok=True)
        self.rebuild_global_indexes()
        self.log_sync(
            "imported",
            origin_node_id,
            {
                "bundle_id": manifest["bundle_id"],
                "from_event_sequence": manifest["from_event_sequence"],
                "to_event_sequence": manifest["to_event_sequence"],
                "artifact_count": len(artifacts),
            },
        )
        return {
            "status": "imported",
            "bundle_id": manifest["bundle_id"],
            "origin_node_id": origin_node_id,
            "from_event_sequence": manifest["from_event_sequence"],
            "to_event_sequence": manifest["to_event_sequence"],
            "artifact_count": len(artifacts),
            "replica_root": str(peer_root),
        }

    def rebuild_global_indexes(self) -> Dict[str, Any]:
        self.init_layout()
        nodes = []
        messages = []
        summaries = []
        concepts = []
        conversations: Dict[str, Dict[str, Any]] = {}
        for peer in self.peers():
            node_id = safe_node_id(str(peer["node_id"]))
            peer_root = self.replica_peer_root(node_id)
            if (peer_root / ".importing.json").exists():
                raise RuntimeError(
                    f"Replica import is incomplete for {node_id}; retry the bundle"
                )
            state = self.replica_state(node_id)
            nodes.append(
                {
                    "node_id": node_id,
                    "display_name": peer.get("display_name") or node_id,
                    "trusted": bool(peer.get("trusted")),
                    "last_event_sequence": int(state.get("last_event_sequence", 0)),
                    "last_sync_at": state.get("last_sync_at"),
                }
            )
            for record in read_jsonl(peer_root / "raw-records.jsonl"):
                qualified_conversation_id = (
                    f"{node_id}:{record.get('conversation_id')}"
                )
                messages.append(
                    {
                        "origin_node_id": node_id,
                        "qualified_message_id": f"{node_id}:{record.get('message_id')}",
                        "qualified_conversation_id": qualified_conversation_id,
                        "message_id": record.get("message_id"),
                        "conversation_id": record.get("conversation_id"),
                        "sequence": record.get("sequence"),
                        "timestamp": record.get("timestamp"),
                        "speaker": record.get("speaker"),
                        "text": record.get("text"),
                        "content_sha256": record.get("content_sha256"),
                    }
                )
                conversation = conversations.setdefault(
                    qualified_conversation_id,
                    {
                        "origin_node_id": node_id,
                        "qualified_conversation_id": qualified_conversation_id,
                        "conversation_id": record.get("conversation_id"),
                        "message_count": 0,
                        "first_timestamp": record.get("timestamp"),
                        "last_timestamp": record.get("timestamp"),
                    },
                )
                conversation["message_count"] += 1
                conversation["last_timestamp"] = record.get("timestamp")
            for path in sorted((peer_root / "summaries").glob("*.json")):
                payload = read_json(path)
                record = payload.get("record") or {}
                summaries.append(
                    {
                        **record,
                        "origin_node_id": node_id,
                        "qualified_summary_id": f"{node_id}:{record.get('summary_id')}",
                        "qualified_conversation_id": (
                            f"{node_id}:{record.get('conversation_id')}"
                            if record.get("conversation_id")
                            else None
                        ),
                        "replica_path": str(path),
                    }
                )
                for concept in record.get("concepts", []):
                    concepts.append(
                        {
                            "origin_node_id": node_id,
                            "qualified_summary_id": (
                                f"{node_id}:{record.get('summary_id')}"
                            ),
                            "qualified_conversation_id": (
                                f"{node_id}:{record.get('conversation_id')}"
                                if record.get("conversation_id")
                                else None
                            ),
                            "concept": concept,
                            "normalized": str(concept).casefold(),
                        }
                    )
        messages.sort(
            key=lambda record: (
                str(record.get("timestamp", "")),
                str(record.get("origin_node_id", "")),
                int(record.get("sequence") or 0),
            )
        )
        summaries.sort(
            key=lambda record: (
                str(record.get("origin_node_id", "")),
                int(record.get("level") or 0),
                str(record.get("summary_id", "")),
            )
        )
        atomic_write_jsonl(self.global_index_dir / "nodes.jsonl", nodes)
        atomic_write_jsonl(
            self.global_index_dir / "conversations.jsonl",
            conversations.values(),
        )
        atomic_write_jsonl(self.global_index_dir / "messages.jsonl", messages)
        atomic_write_jsonl(self.global_index_dir / "summaries.jsonl", summaries)
        atomic_write_jsonl(self.global_index_dir / "concepts.jsonl", concepts)
        return {
            "status": "rebuilt",
            "nodes": len(nodes),
            "conversations": len(conversations),
            "messages": len(messages),
            "summaries": len(summaries),
            "concepts": len(concepts),
            "global_index_root": str(self.global_index_dir),
        }

    def retrieve_global(
        self,
        query: str,
        node_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        query_normalized = self.store.normalize_search_text(query)
        if not query_normalized:
            raise ValueError("Query must not be empty")
        terms = self.store.search_terms(query)
        selected_node = safe_node_id(node_id) if node_id else None
        local_node_id = self.node()["node_id"]
        candidates: List[Dict[str, Any]] = []
        if selected_node in {None, local_node_id}:
            for record in self.store.read_all_raw():
                candidates.append(
                    {
                        **record,
                        "origin_node_id": local_node_id,
                        "qualified_message_id": f"{local_node_id}:{record['message_id']}",
                        "source_scope": "local-authority",
                    }
                )
        if selected_node != local_node_id:
            for record in read_jsonl(self.global_index_dir / "messages.jsonl"):
                if selected_node and record.get("origin_node_id") != selected_node:
                    continue
                candidates.append({**record, "source_scope": "remote-replica"})
        ranked = self.store.ranked_search(
            candidates,
            query_normalized,
            terms,
            lambda record: str(record.get("text", "")),
        )
        maximum = int(
            self.store.config.get("retrieval", {}).get(
                "maximum_initial_candidates", 10
            )
        )
        matches = self.store.strongest_matches(ranked, len(terms), maximum)
        verified_matches = []
        remote_records_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for match in matches:
            record = match["record"]
            if record.get("source_scope") == "local-authority":
                verified_matches.append(match)
                continue
            origin_node_id = safe_node_id(str(record["origin_node_id"]))
            if (
                self.replica_peer_root(origin_node_id) / ".importing.json"
            ).exists():
                raise RuntimeError(
                    f"Replica import is incomplete for {origin_node_id}; retry the bundle"
                )
            if origin_node_id not in remote_records_cache:
                remote_records_cache[origin_node_id] = {
                    str(item["message_id"]): item
                    for item in read_jsonl(
                        self.replica_peer_root(origin_node_id) / "raw-records.jsonl"
                    )
                }
            source_record = remote_records_cache[origin_node_id].get(
                str(record.get("message_id"))
            )
            if source_record is None:
                raise ValueError(
                    f"Federated index points to a missing replica record: "
                    f"{record.get('qualified_message_id')}"
                )
            if source_record.get("content_sha256") != raw_record_sha256(source_record):
                raise ValueError(
                    f"Replica source hash mismatch: {record.get('qualified_message_id')}"
                )
            if (
                source_record.get("content_sha256") != record.get("content_sha256")
                or source_record.get("text") != record.get("text")
            ):
                raise ValueError(
                    f"Federated index drift: {record.get('qualified_message_id')}"
                )
            verified_matches.append(
                {
                    **match,
                    "record": {
                        **source_record,
                        "origin_node_id": origin_node_id,
                        "qualified_message_id": record["qualified_message_id"],
                        "source_scope": "remote-replica",
                    },
                }
            )
        matches = verified_matches
        lines = [
            "# Memory無限 Federated Retrieval",
            "",
            f"- Query: {query}",
            f"- Node scope: `{selected_node or 'all'}`",
            f"- Confidence: `{'verified' if matches else 'unverified'}`",
            "",
        ]
        if matches:
            lines.extend(["## Verified Sources", ""])
            for match in matches:
                record = match["record"]
                lines.extend(
                    [
                        f"### {record['qualified_message_id']} ({record.get('speaker')})",
                        "",
                        f"- Origin node: `{record['origin_node_id']}`",
                        f"- Conversation: `{record.get('conversation_id')}`",
                        f"- Timestamp: `{record.get('timestamp')}`",
                        f"- Source scope: `{record.get('source_scope')}`",
                        f"- Content SHA-256: `{record.get('content_sha256')}`",
                        "",
                        str(record.get("text", "")),
                        "",
                    ]
                )
        else:
            lines.append("No persisted source matched the query.")
        output = "\n".join(lines).rstrip() + "\n"
        metadata = {
            "query": query,
            "node_scope": selected_node,
            "verification": "verified" if matches else "unverified",
            "matches": [
                {
                    "origin_node_id": item["record"]["origin_node_id"],
                    "qualified_message_id": item["record"]["qualified_message_id"],
                    "score": round(float(item["score"]), 6),
                    "matched_terms": item["matched_terms"],
                }
                for item in matches
            ],
        }
        return output, metadata

    def status(self) -> Dict[str, Any]:
        node = read_json(self.node_path) if self.node_path.exists() else None
        devices = []
        peer_paths = (
            sorted(self.peers_dir.glob("*.json"))
            if self.peers_dir.exists()
            else []
        )
        for peer in (read_json(path) for path in peer_paths):
            node_id = str(peer["node_id"])
            state = self.replica_state(node_id)
            peer_root = self.replica_peer_root(node_id)
            replica_bytes = sum(
                path.stat().st_size
                for path in peer_root.rglob("*")
                if path.is_file()
            ) if peer_root.exists() else 0
            devices.append(
                {
                    "node_id": node_id,
                    "display_name": peer.get("display_name") or node_id,
                    "trusted": bool(peer.get("trusted")),
                    "transport": (peer.get("transport") or {}).get("type", "offline"),
                    "cloud_identity": peer.get("cloud_identity"),
                    "last_event_sequence": int(state.get("last_event_sequence", 0)),
                    "last_sync_at": state.get("last_sync_at"),
                    "last_bundle_id": state.get("last_bundle_id"),
                    "last_bundle_sha256": state.get("last_bundle_sha256"),
                    "replica_bytes": replica_bytes,
                }
            )
        return {
            "enabled": bool(node),
            "protocol_version": PROTOCOL_VERSION,
            "node": node,
            "replica_root": str(self.replica_root),
            "devices": devices,
            "recent_sync": (
                read_jsonl(self.sync_log_path)[-20:]
                if self.sync_log_path.exists()
                else []
            ),
        }

    def log_sync(
        self,
        event: str,
        node_id: str,
        details: Dict[str, Any],
    ) -> None:
        self.init_layout()
        record = {
            "timestamp": now_iso(),
            "event": event,
            "node_id": safe_node_id(node_id),
            **details,
        }
        with self.sync_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def sync_peer(self, node_id: str) -> Dict[str, Any]:
        node_id = safe_node_id(node_id)
        peer_path = self.peer_path(node_id)
        if not peer_path.exists():
            raise ValueError(f"Unknown peer: {node_id}")
        peer = read_json(peer_path)
        if not peer.get("trusted"):
            raise ValueError(f"Peer {node_id} is revoked")
        transport = peer.get("transport") or {}
        if transport.get("type") != "ssh":
            raise ValueError(f"Peer {node_id} does not use the SSH transport")
        required = ("host", "remote_root", "remote_config", "remote_cli")
        missing = [key for key in required if not transport.get(key)]
        if missing:
            raise ValueError(
                f"Peer SSH configuration is incomplete: {', '.join(missing)}"
            )
        state = self.replica_state(node_id)
        after = int(state.get("last_event_sequence", 0))
        remote_arguments = [
            str(transport.get("remote_python") or "python3"),
            str(transport["remote_cli"]),
            "--root",
            str(transport["remote_root"]),
            "--config",
            str(transport["remote_config"]),
            "export-delta",
            "--after-event-sequence",
            str(after),
            "--previous-bundle-sha256",
            str(state.get("last_bundle_sha256") or ""),
            "--target-node-id",
            self.node()["node_id"],
            "--output",
            "-",
        ]
        if transport.get("remote_shell", "posix") == "powershell":
            def quote_powershell(value: str) -> str:
                return "'" + value.replace("'", "''") + "'"

            remote_command = "& " + " ".join(
                quote_powershell(value) for value in remote_arguments
            )
        else:
            remote_command = " ".join(shlex.quote(value) for value in remote_arguments)
        ssh_command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-p",
            str(int(transport.get("port") or 22)),
            str(transport["host"]),
            remote_command,
        ]
        self.log_sync("sync-started", node_id, {"after_event_sequence": after})
        try:
            completed = subprocess.run(
                ssh_command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=SSH_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            self.log_sync(
                "sync-failed",
                node_id,
                {
                    "after_event_sequence": after,
                    "error": f"SSH synchronization timed out after "
                    f"{SSH_COMMAND_TIMEOUT_SECONDS} seconds",
                },
            )
            raise RuntimeError(
                f"SSH synchronization timed out after "
                f"{SSH_COMMAND_TIMEOUT_SECONDS} seconds"
            ) from exc
        if completed.returncode != 0:
            error = completed.stderr.decode("utf-8", errors="replace").strip()
            self.log_sync(
                "sync-failed",
                node_id,
                {"after_event_sequence": after, "error": error},
            )
            raise RuntimeError(f"SSH peer export failed: {error}")
        if not completed.stdout.startswith(b"PK"):
            text = completed.stdout.decode("utf-8", errors="replace").strip()
            try:
                response = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "SSH peer returned neither a bundle nor valid JSON"
                ) from exc
            if response.get("status") == "no-change":
                self.log_sync("sync-no-change", node_id, {"after_event_sequence": after})
                return response
            raise RuntimeError(f"Unexpected SSH peer response: {response}")
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-peer-") as directory:
            bundle = Path(directory) / f"{node_id}.mwxb"
            bundle.write_bytes(completed.stdout)
            result = self.import_delta(bundle, expected_node_id=node_id)
        return result
