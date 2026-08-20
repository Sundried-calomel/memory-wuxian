#!/usr/bin/env python3
"""Exact-byte project attachments and their independent encrypted stream."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from memory_environment import EnvironmentRegistry, canonical_bytes
from memory_exchange_contract import (
    ExchangeStreamFacade,
    ExchangeStreamPort,
    build_bundle_manifest,
    select_jsonl_page,
    validate_authenticated_binding,
    validate_export_cursor,
    validate_strict_replica_continuity,
    verify_bundle_identity,
    verify_payload,
)
from memory_federation import (
    FederationManager,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    bytes_sha256,
    canonical_sha256,
    now_iso,
    read_json,
    read_jsonl,
    safe_node_id,
)
from platform_lock import exclusive_lock


MANIFEST_FORMAT = "memory-wuxian-project-attachment-manifest-v1"
BUNDLE_FORMAT = "memory-wuxian-project-attachment-bundle-v1"
OWNER_FORMAT = "memory-wuxian-project-attachment-owner-v1"
PROTOCOL_VERSION = 1
STREAM_ID = "project-attachment-v1"
CHUNK_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_GENERATION_BYTES = 1024 * 1024 * 1024
MAX_FILES = 256
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_EVENTS_PER_BUNDLE = 4
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
GENERATION_ID_RE = re.compile(r"^project-attachment:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONVERSATION_ID_RE = re.compile(r"^[a-z0-9._-]+:[^\r\n]{1,240}$")
ALLOWED_SUFFIXES = {
    ".pdf", ".pptx", ".docx", ".xlsx", ".xls", ".tif", ".tiff",
    ".png", ".jpg", ".jpeg", ".webp",
}
ALLOWED_ROLES = {
    "source-paper", "supplement", "presentation", "report", "figure",
    "table", "other-deliverable",
}


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("project attachment paths must be normalized and relative")
    return path.as_posix()


def _manifest_identity(manifest: Dict[str, Any]) -> str:
    identity = {key: value for key, value in manifest.items() if key != "generation_id"}
    return "project-attachment:" + hashlib.sha256(canonical_bytes(identity)).hexdigest()


class ProjectAttachmentStore:
    """Build closed attachment manifests without changing their source files."""

    def __init__(self, store_or_root: Any):
        if hasattr(store_or_root, "root"):
            self.archive_root = Path(store_or_root.root)
            self.store = store_or_root if hasattr(store_or_root, "config") else type("_Store", (), {"root": self.archive_root, "config": {}})()
        else:
            self.archive_root = Path(store_or_root)
            self.store = type("_Store", (), {"root": self.archive_root, "config": {}})()
        self.environment = EnvironmentRegistry(self.archive_root)
        self.root = self.environment.root / "project-attachments"
        self.objects_root = self.root / "objects"
        self.local_root = self.root / "local"
        self.replica_root = self.root / "replicas" / "peers"
        self.owner_root = self.root / "owners"
        self.reconstruction_root = self.root / "reconstruction-receipts"
        self.lock_path = self.environment.locks_dir / "project-attachments.lock"

    def init(self) -> None:
        self.environment.init()
        for path in (
            self.objects_root,
            self.local_root,
            self.replica_root,
            self.owner_root,
            self.reconstruction_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _object_path(self, sha256: str) -> Path:
        if not SHA256_RE.fullmatch(sha256):
            raise ValueError("project attachment object hash is invalid")
        return self.objects_root / sha256[:2] / sha256

    def _owner_path(self, project_id: str) -> Path:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project attachment project_id is invalid")
        return self.owner_root / f"{project_id}.json"

    def _owner_current_manifest(self, owner: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        generation_id = owner.get("current_generation_id")
        if generation_id is None:
            return None
        if not GENERATION_ID_RE.fullmatch(str(generation_id)):
            raise ValueError("project attachment owner current generation is invalid")
        digest = str(generation_id).split(":", 1)[1]
        path = self.local_root / str(owner.get("project_id", "")) / f"{digest}.json"
        if not path.exists():
            return None
        manifest = self.validate_manifest(read_json(path))
        if manifest["project_id"] != owner.get("project_id"):
            raise ValueError("project attachment owner current generation belongs to another project")
        return manifest

    @staticmethod
    def _source_state(spec: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        root = Path(str(spec["source_root"])).expanduser().resolve()
        state = {}
        for item in spec["files"]:
            relative = _safe_relative_path(item["path"])
            stat = root.joinpath(*PurePosixPath(relative).parts).stat()
            state[relative] = {"byte_length": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        return state

    @staticmethod
    def validate_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
        required = {
            "format", "schema_version", "generation_id", "project_id", "title",
            "conversation_ids", "files", "total_bytes", "chunk_bytes",
        }
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError("project attachment manifest fields are invalid")
        if manifest["format"] != MANIFEST_FORMAT or manifest["schema_version"] != 1:
            raise ValueError("project attachment manifest format is unsupported")
        if not PROJECT_ID_RE.fullmatch(str(manifest["project_id"])):
            raise ValueError("project attachment project_id is invalid")
        if not isinstance(manifest["title"], str) or not manifest["title"].strip() or len(manifest["title"]) > 240:
            raise ValueError("project attachment title is invalid")
        conversations = manifest["conversation_ids"]
        if not isinstance(conversations, list) or not conversations or len(conversations) > 32:
            raise ValueError("project attachment conversation_ids are invalid")
        if conversations != sorted(set(conversations)) or any(not CONVERSATION_ID_RE.fullmatch(str(item)) for item in conversations):
            raise ValueError("project attachment conversation_ids are invalid")
        files = manifest["files"]
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise ValueError("project attachment files are invalid")
        total = 0
        previous = None
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "role", "byte_length", "sha256", "chunks"}:
                raise ValueError("project attachment file fields are invalid")
            path = _safe_relative_path(item["path"])
            if path != item["path"] or (previous is not None and path <= previous):
                raise ValueError("project attachment files must be strictly ordered")
            previous = path
            if Path(path).suffix.lower() not in ALLOWED_SUFFIXES or item["role"] not in ALLOWED_ROLES:
                raise ValueError("project attachment file type or role is unsupported")
            length = int(item["byte_length"])
            if length < 1 or length > MAX_FILE_BYTES or not SHA256_RE.fullmatch(str(item["sha256"])):
                raise ValueError("project attachment file metadata is invalid")
            chunks = item["chunks"]
            if not isinstance(chunks, list) or not chunks:
                raise ValueError("project attachment chunks are missing")
            offset = 0
            for index, chunk in enumerate(chunks):
                if not isinstance(chunk, dict) or set(chunk) != {"index", "offset", "byte_length", "sha256"}:
                    raise ValueError("project attachment chunk fields are invalid")
                chunk_length = int(chunk["byte_length"])
                if chunk["index"] != index or int(chunk["offset"]) != offset or not 0 < chunk_length <= CHUNK_BYTES:
                    raise ValueError("project attachment chunk order is invalid")
                if not SHA256_RE.fullmatch(str(chunk["sha256"])):
                    raise ValueError("project attachment chunk hash is invalid")
                offset += chunk_length
            if offset != length:
                raise ValueError("project attachment chunks do not cover the file")
            total += length
        if total != int(manifest["total_bytes"]) or total > MAX_GENERATION_BYTES:
            raise ValueError("project attachment generation byte count is invalid")
        if int(manifest["chunk_bytes"]) != CHUNK_BYTES:
            raise ValueError("project attachment chunk size is unsupported")
        if manifest["generation_id"] != _manifest_identity(manifest):
            raise ValueError("project attachment generation identity mismatch")
        return json.loads(json.dumps(manifest))

    def _manifest_from_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        required = {"schema_version", "project_id", "title", "source_root", "conversation_ids", "files"}
        if not isinstance(spec, dict) or set(spec) != required or spec["schema_version"] != 1:
            raise ValueError("project attachment specification fields are invalid")
        project_id = str(spec["project_id"])
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project attachment specification identity is invalid")
        source_root = Path(str(spec["source_root"])).expanduser().resolve()
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("project attachment source_root is unsafe")
        conversations = sorted(set(str(item) for item in spec["conversation_ids"]))
        if len(conversations) != len(spec["conversation_ids"]):
            raise ValueError("project attachment conversation_ids contain duplicates")
        selected: Dict[str, str] = {}
        for item in spec["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "role"}:
                raise ValueError("project attachment file selection is invalid")
            relative = _safe_relative_path(item["path"])
            if relative in selected:
                raise ValueError("project attachment file is selected more than once")
            selected[relative] = str(item["role"])
        if not selected or len(selected) > MAX_FILES:
            raise ValueError("project attachment files are missing or exceed the limit")
        files: List[Dict[str, Any]] = []
        total = 0
        for relative in sorted(selected):
            path = source_root.joinpath(*PurePosixPath(relative).parts)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source_root):
                raise ValueError(f"project attachment source is unsafe: {relative}")
            if path.suffix.lower() not in ALLOWED_SUFFIXES or selected[relative] not in ALLOWED_ROLES:
                raise ValueError(f"project attachment file type or role is unsupported: {relative}")
            size = path.stat().st_size
            if size < 1 or size > MAX_FILE_BYTES:
                raise ValueError(f"project attachment file exceeds the byte limit: {relative}")
            file_hash = hashlib.sha256()
            chunks = []
            offset = 0
            with path.open("rb") as handle:
                while True:
                    content = handle.read(CHUNK_BYTES)
                    if not content:
                        break
                    digest = hashlib.sha256(content).hexdigest()
                    file_hash.update(content)
                    chunks.append({"index": len(chunks), "offset": offset, "byte_length": len(content), "sha256": digest})
                    offset += len(content)
            if offset != size or path.stat().st_size != size:
                raise ValueError("project attachment source changed during build")
            total += size
            if total > MAX_GENERATION_BYTES:
                raise ValueError("project attachment generation exceeds the byte limit")
            files.append({"path": relative, "role": selected[relative], "byte_length": size, "sha256": file_hash.hexdigest(), "chunks": chunks})
        manifest = {
            "format": MANIFEST_FORMAT,
            "schema_version": 1,
            "generation_id": "",
            "project_id": project_id,
            "title": str(spec["title"]).strip(),
            "conversation_ids": conversations,
            "files": files,
            "total_bytes": total,
            "chunk_bytes": CHUNK_BYTES,
        }
        manifest["generation_id"] = _manifest_identity(manifest)
        return self.validate_manifest(manifest)

    def _persist_objects_from_spec(self, spec: Dict[str, Any], manifest: Dict[str, Any]) -> None:
        source_root = Path(str(spec["source_root"])).expanduser().resolve()
        for item in manifest["files"]:
            source = source_root.joinpath(*PurePosixPath(item["path"]).parts)
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(source_root):
                raise ValueError(f"project attachment source is unsafe: {item['path']}")
            initial_size = source.stat().st_size
            file_hash = hashlib.sha256()
            with source.open("rb") as handle:
                for chunk in item["chunks"]:
                    content = handle.read(chunk["byte_length"])
                    if len(content) != chunk["byte_length"] or bytes_sha256(content) != chunk["sha256"]:
                        raise ValueError("project attachment source changed during object persistence")
                    file_hash.update(content)
                    object_path = self._object_path(chunk["sha256"])
                    if object_path.exists():
                        if bytes_sha256(object_path.read_bytes()) != chunk["sha256"]:
                            raise ValueError("project attachment object conflicts with existing content")
                    else:
                        object_path.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_bytes(object_path, content)
                if handle.read(1):
                    raise ValueError("project attachment source grew during object persistence")
            if (
                initial_size != item["byte_length"]
                or source.stat().st_size != initial_size
                or file_hash.hexdigest() != item["sha256"]
            ):
                raise ValueError("project attachment source changed during object persistence")

    def build(self, spec: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        manifest = self._manifest_from_spec(spec)
        digest = manifest["generation_id"].split(":", 1)[1]
        path = self.local_root / manifest["project_id"] / f"{digest}.json"
        unique_chunks = {
            chunk["sha256"]
            for item in manifest["files"]
            for chunk in item["chunks"]
        }
        result = {"status": "preview", "generation_id": manifest["generation_id"], "project_id": manifest["project_id"], "files": len(manifest["files"]), "bytes": manifest["total_bytes"], "chunks": sum(len(item["chunks"]) for item in manifest["files"]), "unique_chunks": len(unique_chunks), "source_root_persisted": False}
        if path.exists():
            if read_json(path) != manifest:
                raise ValueError("project attachment generation conflicts with existing content")
            return {**result, "status": "no-change", "path": str(path)}
        if not apply:
            return result
        self.init()
        with exclusive_lock(self.lock_path):
            self._persist_objects_from_spec(spec, manifest)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, manifest)
        return {**result, "status": "recorded", "path": str(path)}

    def register_owner(self, spec: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        manifest = self._manifest_from_spec(spec)
        owner = {"format": OWNER_FORMAT, "schema_version": 1, "project_id": manifest["project_id"], "title": manifest["title"], "source_root": str(Path(spec["source_root"]).expanduser().resolve()), "conversation_ids": manifest["conversation_ids"], "files": sorted(spec["files"], key=lambda item: item["path"]), "source_state": self._source_state(spec), "current_generation_id": None, "enabled": True}
        path = self._owner_path(manifest["project_id"])
        current = read_json(path) if path.exists() else None
        stable_fields = set(owner) - {"current_generation_id"}
        if isinstance(current, dict) and all(current.get(key) == owner[key] for key in stable_fields):
            current_manifest = self._owner_current_manifest(current)
            if current_manifest is not None:
                owner["current_generation_id"] = current_manifest["generation_id"]
        result = {"status": "no-change" if current == owner else "preview", "project_id": owner["project_id"], "selected_files": len(owner["files"]), "source_root_exported": False}
        if current == owner or not apply:
            return result
        self.init()
        with exclusive_lock(self.lock_path):
            atomic_write_json(path, owner)
        return {**result, "status": "recorded", "path": str(path)}

    def refresh_owner(self, project_id: str, *, apply: bool = False) -> Dict[str, Any]:
        path = self._owner_path(project_id)
        if not path.exists():
            raise ValueError("project attachment owner is not registered")
        owner = read_json(path)
        if owner.get("format") != OWNER_FORMAT or not owner.get("enabled"):
            raise ValueError("project attachment owner is disabled or invalid")
        spec = {"schema_version": 1, "project_id": owner["project_id"], "title": owner["title"], "source_root": owner["source_root"], "conversation_ids": owner["conversation_ids"], "files": owner["files"]}
        current_state = self._source_state(spec)
        current_manifest = self._owner_current_manifest(owner)
        if current_state == owner.get("source_state") and current_manifest is not None:
            return {"status": "no-change", "project_id": project_id, "generation_id": current_manifest["generation_id"], "persistent_mutations": 0, "owner_refresh": True}
        result = self.build(spec, apply=apply)
        if apply and result["status"] in {"recorded", "no-change"}:
            owner["source_state"] = current_state
            owner["current_generation_id"] = result["generation_id"]
            with exclusive_lock(self.lock_path):
                atomic_write_json(path, owner)
        return {**result, "owner_refresh": True}

    def owner_status(self) -> Dict[str, Any]:
        owners = []
        for path in sorted(self.owner_root.glob("*.json")) if self.owner_root.is_dir() else []:
            owner = read_json(path)
            current_manifest = self._owner_current_manifest(owner)
            owners.append({"project_id": owner.get("project_id"), "title": owner.get("title"), "enabled": bool(owner.get("enabled")), "selected_files": len(owner.get("files", [])), "current_generation_id": current_manifest["generation_id"] if current_manifest else None})
        return {"status": "ok", "owners": owners, "count": len(owners)}

    def refresh_owners(self, *, maximum_owners: int = 20, apply: bool = False) -> Dict[str, Any]:
        if maximum_owners < 1 or maximum_owners > 100:
            raise ValueError("maximum_owners must be between 1 and 100")
        project_ids = [path.stem for path in sorted(self.owner_root.glob("*.json"))]
        results = []
        for project_id in project_ids[:maximum_owners]:
            try:
                results.append(self.refresh_owner(project_id, apply=apply))
            except Exception as error:
                results.append({"status": "attention", "project_id": project_id, "error": str(error).replace("\r", " ").replace("\n", " ")[:500]})
        return {"status": "attention" if any(item["status"] == "attention" for item in results) else "ok", "processed": len(results), "remaining": max(0, len(project_ids) - len(results)), "created": sum(item["status"] == "recorded" for item in results), "unchanged": sum(item["status"] == "no-change" for item in results), "results": results}

    def manifests(self, *, remote: bool = False) -> List[Dict[str, Any]]:
        results = []
        pattern = "*/*/*.json" if remote else "*/*.json"
        root = self.replica_root if remote else self.local_root
        for path in sorted(root.glob(pattern)) if root.is_dir() else []:
            record = read_json(path)
            results.append(self.validate_manifest(record.get("manifest", record)))
        return results

    def local_events(self) -> List[Dict[str, Any]]:
        manifests = self.manifests()
        hashes = sorted({chunk["sha256"] for manifest in manifests for item in manifest["files"] for chunk in item["chunks"]})
        events = []
        for sha256 in hashes:
            content = self._object_path(sha256).read_bytes()
            if len(content) > CHUNK_BYTES or bytes_sha256(content) != sha256:
                raise ValueError("project attachment local object integrity failed")
            events.append({"source_event_id": f"project-attachment-chunk:{sha256}", "event_kind": "project-attachment-chunk", "payload": {"sha256": sha256, "byte_length": len(content), "content_base64": base64.b64encode(content).decode("ascii")}})
        for manifest in manifests:
            events.append({"source_event_id": manifest["generation_id"], "event_kind": "project-attachment-manifest", "payload": manifest})
        return events

    def status(self) -> Dict[str, Any]:
        manifests = self.manifests()
        receipts = list(self.reconstruction_root.glob("*.json")) if self.reconstruction_root.is_dir() else []
        return {
            "status": "ok",
            "stream_id": STREAM_ID,
            "local_manifests": len(manifests),
            "local_files": sum(len(item["files"]) for item in manifests),
            "local_bytes": sum(int(item["total_bytes"]) for item in manifests),
            "verified_reconstructions": len(receipts),
        }

    @staticmethod
    def _validate_reconstruction_receipt(
        receipt: Dict[str, Any], generation_id: str, files: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"format", "generation_id", "verified_at", "files"}
            or receipt.get("format") != "memory-wuxian-project-attachment-reconstruction-v1"
            or receipt.get("generation_id") != generation_id
            or receipt.get("files") != files
            or not isinstance(receipt.get("verified_at"), str)
            or not receipt["verified_at"]
        ):
            raise ValueError("project attachment reconstruction receipt conflicts with verified evidence")
        return receipt

    def reconstruct(self, generation_id: str, destination: Path, *, apply: bool = False) -> Dict[str, Any]:
        if not GENERATION_ID_RE.fullmatch(generation_id):
            raise ValueError("project attachment generation_id is invalid")
        digest = generation_id.split(":", 1)[1]
        candidates = list(self.local_root.glob(f"*/{digest}.json")) + list(self.replica_root.glob(f"*/*/{digest}.json"))
        if len(candidates) != 1:
            raise ValueError("project attachment generation is missing or ambiguous")
        manifest = self.validate_manifest(read_json(candidates[0]).get("manifest", read_json(candidates[0])))
        receipt_files = [
            {
                "path": item["path"],
                "byte_length": item["byte_length"],
                "sha256": item["sha256"],
            }
            for item in manifest["files"]
        ]
        receipt_path = self.reconstruction_root / f"{digest}.json"
        if apply and receipt_path.exists():
            self._validate_reconstruction_receipt(
                read_json(receipt_path), generation_id, receipt_files
            )
        target_root = Path(destination).expanduser().resolve()
        writes, conflicts, missing = [], [], []
        planned = []
        for item in manifest["files"]:
            file_hash = hashlib.sha256()
            content_parts = []
            for chunk in item["chunks"]:
                object_path = self._object_path(chunk["sha256"])
                if not object_path.exists():
                    missing.append(chunk["sha256"])
                    continue
                content = object_path.read_bytes()
                if len(content) != chunk["byte_length"] or bytes_sha256(content) != chunk["sha256"]:
                    raise ValueError("project attachment reconstruction chunk integrity failed")
                content_parts.append(content)
                file_hash.update(content)
            if missing:
                continue
            content = b"".join(content_parts)
            if len(content) != item["byte_length"] or file_hash.hexdigest() != item["sha256"]:
                raise ValueError("project attachment reconstructed file integrity failed")
            target = target_root.joinpath(*PurePosixPath(item["path"]).parts)
            if target.exists() and (not target.is_file() or bytes_sha256(target.read_bytes()) != item["sha256"]):
                conflicts.append(item["path"])
            elif not target.exists():
                writes.append(item["path"])
                planned.append((target, content))
        if missing:
            return {"status": "incomplete", "applied": False, "writes": [], "conflicts": [], "missing_chunks": sorted(set(missing))}
        if conflicts:
            return {"status": "conflict", "applied": False, "writes": writes, "conflicts": conflicts, "missing_chunks": []}
        if apply:
            for target, content in planned:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, content)
            receipt = {
                "format": "memory-wuxian-project-attachment-reconstruction-v1",
                "generation_id": generation_id,
                "verified_at": now_iso(),
                "files": receipt_files,
            }
            self.init()
            with exclusive_lock(self.lock_path):
                if receipt_path.exists():
                    self._validate_reconstruction_receipt(
                        read_json(receipt_path), generation_id, receipt_files
                    )
                else:
                    atomic_write_json(receipt_path, receipt)
        return {"status": "completed" if apply else "preview", "applied": apply, "writes": writes, "conflicts": [], "missing_chunks": []}


class ProjectAttachmentExchangeManager(ExchangeStreamFacade):
    """Independent project-attachment-v1 stream; old clients safely ignore it."""

    requires_authenticated_transport = True

    def __init__(self, store: Any):
        self.store = store
        self.root = Path(store.root)
        self.attachments = ProjectAttachmentStore(store)
        self.federation = FederationManager(self.attachments.store)
        self.metadata_root = self.attachments.root / "exchange"
        self.replica_root = self.attachments.replica_root
        self.export_ledger_path = self.metadata_root / "export-ledger.jsonl"
        self.export_state_path = self.metadata_root / "export-state.json"
        self.sync_log_path = self.metadata_root / "sync-log.jsonl"
        self.exchange_lock_path = self.attachments.environment.locks_dir / "project-attachment-exchange.lock"

    def init_layout(self) -> None:
        self.attachments.init()
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        if not self.export_ledger_path.exists():
            atomic_write_bytes(self.export_ledger_path, b"")
        if not self.export_state_path.exists():
            atomic_write_json(self.export_state_path, {"format_version": 1, "next_event_sequence": 1, "source_events": {}})
        if not self.sync_log_path.exists():
            atomic_write_bytes(self.sync_log_path, b"")

    def exchange_port(self) -> ExchangeStreamPort:
        return ExchangeStreamPort(
            store=self.store,
            root=self.root,
            metadata_root=self.metadata_root,
            replica_root=self.replica_root,
            exchange_lock_path=self.exchange_lock_path,
            requires_authenticated_transport=True,
            node=self.node,
            peers=self.peers,
            status=self.status,
            exchange_observation=self.exchange_observation,
            replica_state=self.replica_state,
            read_bundle_manifest=self.read_bundle_manifest,
            export_delta=self.export_delta,
            import_delta=self.import_delta,
            import_authenticated_delta=self._import_authenticated_delta,
            log_sync=self.log_sync,
        )

    def node(self) -> Dict[str, Any]: return self.federation.node()
    def peers(self) -> List[Dict[str, Any]]: return self.federation.peers()

    def exchange_observation(self, _timestamp: float) -> Dict[str, Any]:
        events = self.attachments.local_events()
        marker = {"size": sum(len(canonical_bytes(item["payload"])) for item in events), "mtime_ns": 0}
        return {"completed_rounds": len(events), "raw_today": marker, "summary_registry": marker, "title_index": marker}

    def log_sync(self, event: str, node_id: str, details: Dict[str, Any]) -> None:
        self.init_layout()
        with self.sync_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"timestamp": now_iso(), "event": event, "node_id": safe_node_id(node_id), **details}, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def refresh_export_ledger(self) -> List[Dict[str, Any]]:
        self.init_layout()
        ledger = read_jsonl(self.export_ledger_path)
        known = {item["source_event_id"] for item in ledger}
        next_sequence = max((int(item["event_sequence"]) for item in ledger), default=0) + 1
        for event in self.attachments.local_events():
            if event["source_event_id"] in known: continue
            record = {"event_sequence": next_sequence, **event, "payload_sha256": canonical_sha256(event["payload"])}
            ledger.append(record); known.add(event["source_event_id"]); next_sequence += 1
        atomic_write_jsonl(self.export_ledger_path, ledger)
        atomic_write_json(self.export_state_path, {"format_version": 1, "next_event_sequence": next_sequence, "source_events": {item: True for item in sorted(known)}})
        return ledger

    def export_delta(self, output: Path, after_event_sequence: int = 0, target_node_id: Optional[str] = None, previous_bundle_sha256: Optional[str] = None) -> Dict[str, Any]:
        ledger = self.refresh_export_ledger()
        latest = max((int(item["event_sequence"]) for item in ledger), default=0)
        validate_export_cursor(
            after_event_sequence,
            latest,
            previous_bundle_sha256,
            cursor_error=lambda _after, _latest: "project attachment export cursor is invalid",
            predecessor_error="project attachment predecessor hash is required",
            initial_predecessor_error="initial project attachment export cannot name a predecessor",
            predecessor_is_valid=lambda value: bool(SHA256_RE.fullmatch(str(value or ""))),
            initial_predecessor_is_declared=lambda value: value is not None,
        )
        pending, payload = select_jsonl_page(
            (entry for entry in ledger if int(entry["event_sequence"]) > after_event_sequence),
            encode=lambda item: canonical_bytes(item) + b"\n",
            maximum_items=MAX_EVENTS_PER_BUNDLE,
            maximum_bytes=MAX_BUNDLE_BYTES,
            oversized_item_error=lambda _item: "project attachment event exceeds the bundle limit",
        )
        if not pending: return {"status": "no-change", "after_event_sequence": after_event_sequence}
        base = {"format": BUNDLE_FORMAT, "protocol_version": PROTOCOL_VERSION, "stream_id": STREAM_ID, "origin_node_id": self.node()["node_id"], "target_node_id": safe_node_id(target_node_id) if target_node_id else None, "base_event_sequence": after_event_sequence, "previous_bundle_sha256": previous_bundle_sha256, "from_event_sequence": int(pending[0]["event_sequence"]), "to_event_sequence": int(pending[-1]["event_sequence"]), "artifact_count": len(pending), "payload_path": "payload/project-attachments.jsonl", "payload_bytes": len(payload), "payload_sha256": bytes_sha256(payload)}
        manifest = build_bundle_manifest(base, canonical_sha256=canonical_sha256)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", canonical_bytes(manifest) + b"\n"); archive.writestr(manifest["payload_path"], payload)
        digest = bytes_sha256(output.read_bytes())
        return {"status": "exported", **manifest, "output": str(output), "sha256": digest, "bundle_sha256": digest}

    def read_bundle_manifest(self, bundle: Path) -> Dict[str, Any]:
        with zipfile.ZipFile(bundle, "r") as archive: manifest = json.loads(archive.read("manifest.json"))
        required = {"format", "protocol_version", "stream_id", "origin_node_id", "target_node_id", "base_event_sequence", "previous_bundle_sha256", "from_event_sequence", "to_event_sequence", "artifact_count", "payload_path", "payload_bytes", "payload_sha256", "bundle_id"}
        if not isinstance(manifest, dict) or set(manifest) != required or manifest["format"] != BUNDLE_FORMAT or manifest["stream_id"] != STREAM_ID or manifest["protocol_version"] != PROTOCOL_VERSION: raise ValueError("project attachment bundle format is unsupported")
        safe_node_id(str(manifest["origin_node_id"])); safe_node_id(str(manifest["target_node_id"]))
        base, first, last, count = map(int, (manifest["base_event_sequence"], manifest["from_event_sequence"], manifest["to_event_sequence"], manifest["artifact_count"]))
        if base < 0 or first != base + 1 or last - first + 1 != count or not 0 < count <= MAX_EVENTS_PER_BUNDLE: raise ValueError("project attachment bundle sequence is invalid")
        predecessor = manifest["previous_bundle_sha256"]
        if (base == 0 and predecessor is not None) or (base > 0 and not SHA256_RE.fullmatch(str(predecessor or ""))): raise ValueError("project attachment bundle predecessor is invalid")
        if manifest["payload_path"] != "payload/project-attachments.jsonl" or not 0 < int(manifest["payload_bytes"]) <= MAX_BUNDLE_BYTES or not SHA256_RE.fullmatch(str(manifest["payload_sha256"])): raise ValueError("project attachment bundle payload metadata is invalid")
        verify_bundle_identity(
            manifest,
            canonical_sha256=canonical_sha256,
            error="project attachment bundle identity mismatch",
        )
        return manifest

    def replica_state(self, node_id: str) -> Dict[str, Any]:
        path = self.replica_root / safe_node_id(node_id) / "state.json"
        return read_json(path) if path.exists() else {"last_event_sequence": 0, "last_bundle_id": None, "last_bundle_sha256": None}

    def import_delta(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]: raise TypeError("project attachment import requires authenticated transport")

    def _import_authenticated_delta(self, bundle: Path, *, expected_node_id: str, authenticated_open_result: Any) -> Dict[str, Any]:
        origin, target, payload_sha256 = validate_authenticated_binding(
            authenticated_open_result.consume_environment_binding(),
            expected_origin=safe_node_id(expected_node_id),
            expected_target=safe_node_id(self.node()["node_id"]),
            expected_payload_sha256=bytes_sha256(bundle.read_bytes()),
            identity_error="project attachment authenticated transport binding mismatch",
        )
        manifest = self.read_bundle_manifest(bundle)
        if safe_node_id(manifest["origin_node_id"]) != origin or safe_node_id(manifest["target_node_id"]) != target: raise ValueError("project attachment bundle target binding mismatch")
        state = self.replica_state(origin)
        validate_strict_replica_continuity(
            manifest,
            state,
            manifest_sequence_field="from_event_sequence",
            state_offset=1,
            sequence_error=lambda _expected, _actual: "project attachment bundle sequence is not contiguous",
            predecessor_error="project attachment bundle predecessor hash mismatch",
        )
        with zipfile.ZipFile(bundle, "r") as archive: payload = archive.read(manifest["payload_path"])
        verify_payload(
            manifest,
            payload,
            bytes_sha256=bytes_sha256,
            size_error="project attachment bundle payload integrity failed",
            hash_error="project attachment bundle payload integrity failed",
        )
        records = [json.loads(line) for line in payload.splitlines() if line.strip()]
        expected_sequences = list(range(int(manifest["from_event_sequence"]), int(manifest["to_event_sequence"]) + 1))
        if [int(item.get("event_sequence", -1)) for item in records] != expected_sequences: raise ValueError("project attachment event range is invalid")
        peer_root = self.replica_root / origin
        planned_objects, planned_manifests = [], []
        for record in records:
            if set(record) != {"event_sequence", "source_event_id", "event_kind", "payload", "payload_sha256"} or record["payload_sha256"] != canonical_sha256(record["payload"]): raise ValueError("project attachment event fields are invalid")
            if record["event_kind"] == "project-attachment-chunk":
                value = record["payload"]
                if set(value) != {"sha256", "byte_length", "content_base64"}: raise ValueError("project attachment chunk event is invalid")
                content = base64.b64decode(value["content_base64"], validate=True)
                if not 0 < len(content) <= CHUNK_BYTES or len(content) != value["byte_length"] or bytes_sha256(content) != value["sha256"] or record["source_event_id"] != f"project-attachment-chunk:{value['sha256']}": raise ValueError("project attachment chunk event integrity failed")
                planned_objects.append((self.attachments._object_path(value["sha256"]), content))
            elif record["event_kind"] == "project-attachment-manifest":
                value = self.attachments.validate_manifest(record["payload"])
                if record["source_event_id"] != value["generation_id"]: raise ValueError("project attachment manifest event identity failed")
                digest = value["generation_id"].split(":", 1)[1]
                planned_manifests.append((peer_root / value["project_id"] / f"{digest}.json", {"schema_version": 1, "origin_node_id": origin, "event_sequence": record["event_sequence"], "received_bundle_id": manifest["bundle_id"], "automatic_activation": False, "manifest": value}))
            else: raise ValueError("project attachment event kind is unsupported")
        for path, content in planned_objects:
            if path.exists() and path.read_bytes() != content: raise ValueError("project attachment object conflicts with existing content")
        for path, value in planned_manifests:
            if path.exists() and read_json(path) != value: raise ValueError("project attachment replica conflicts with existing content")
        for path, content in planned_objects:
            if not path.exists(): path.parent.mkdir(parents=True, exist_ok=True); atomic_write_bytes(path, content)
        for path, value in planned_manifests:
            if not path.exists(): path.parent.mkdir(parents=True, exist_ok=True); atomic_write_json(path, value)
        new_state = {"last_event_sequence": int(manifest["to_event_sequence"]), "last_bundle_id": manifest["bundle_id"], "last_bundle_sha256": bytes_sha256(bundle.read_bytes()), "last_sync_at": now_iso()}
        atomic_write_json(peer_root / "state.json", new_state)
        return {"status": "imported", "origin_node_id": origin, "imported": len(records), **new_state}

    def status(self) -> Dict[str, Any]:
        ledger = read_jsonl(self.export_ledger_path) if self.export_ledger_path.exists() else []
        return {**self.attachments.status(), "local_event_sequence": max((int(item["event_sequence"]) for item in ledger), default=0), "peers": [{"node_id": peer["node_id"], "replica": self.replica_state(peer["node_id"])} for peer in self.federation.status().get("devices", []) if peer.get("trusted")]}
