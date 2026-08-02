#!/usr/bin/env python3
"""Immutable project evidence packages and their independent cloud stream."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from memory_environment import EnvironmentRegistry, canonical_bytes
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


PACKAGE_FORMAT = "memory-wuxian-project-evidence-package-v1"
BUNDLE_FORMAT = "memory-wuxian-project-evidence-bundle-v1"
PROTOCOL_VERSION = 1
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
GENERATION_ID_RE = re.compile(r"^project-evidence:[0-9a-f]{64}$")
ALLOWED_ROLES = {
    "project-rule",
    "status",
    "next-plan",
    "decision",
    "qa",
    "daily-report",
    "weekly-report",
    "phase-report",
    "template",
    "figure",
    "table",
    "artifact-index",
    "other-evidence",
}
ALLOWED_SUFFIXES = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv",
    ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".pptx", ".docx",
}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv"}
MAX_FILES = 256
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_EVENTS_PER_BUNDLE = 32
MAX_QUERY_RESULTS = 50
MAX_QUERY_EXCERPT_CHARACTERS = 2000
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
)


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("project evidence paths must be normalized and relative")
    return path.as_posix()


def _generation_identity(package: Dict[str, Any]) -> str:
    identity = {key: value for key, value in package.items() if key != "generation_id"}
    return "project-evidence:" + hashlib.sha256(canonical_bytes(identity)).hexdigest()


class ProjectEvidenceStore:
    """Build explicit bounded packages and keep peer copies read-only."""

    def __init__(self, store_or_root: Any):
        if hasattr(store_or_root, "root"):
            self.archive_root = Path(store_or_root.root)
            self.store = (
                store_or_root
                if hasattr(store_or_root, "config")
                else type("_Store", (), {"root": self.archive_root, "config": {}})()
            )
        else:
            self.archive_root = Path(store_or_root)
            self.store = type("_Store", (), {"root": self.archive_root, "config": {}})()
        self.environment = EnvironmentRegistry(self.archive_root)
        self.root = self.environment.root / "project-evidence"
        self.local_root = self.root / "local"
        self.replica_root = self.root / "replicas" / "peers"
        self.owner_root = self.root / "owners"
        self.lock_path = self.environment.locks_dir / "project-evidence.lock"

    def init(self) -> None:
        self.environment.init()
        self.local_root.mkdir(parents=True, exist_ok=True)
        self.replica_root.mkdir(parents=True, exist_ok=True)
        self.owner_root.mkdir(parents=True, exist_ok=True)

    def _owner_path(self, project_id: str) -> Path:
        if not PROJECT_ID_RE.fullmatch(str(project_id)):
            raise ValueError("project evidence project_id is invalid")
        return self.owner_root / f"{project_id}.json"

    def register_owner(self, spec: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        """Register one explicit device-local source selection without exporting its path."""
        package = self._package_from_spec(spec)
        project_id = package["project_id"]
        owner = {
            "format": "memory-wuxian-project-evidence-owner-v1",
            "schema_version": 1,
            "project_id": project_id,
            "title": package["title"],
            "source_root": str(Path(str(spec["source_root"])).expanduser().resolve()),
            "files": sorted(spec["files"], key=lambda item: item["path"]),
            "enabled": True,
        }
        path = self._owner_path(project_id)
        current = read_json(path) if path.exists() else None
        result = {
            "status": "no-change" if current == owner else "preview",
            "project_id": project_id,
            "selected_files": len(owner["files"]),
            "source_root_exported": False,
            "automatic_workspace_scan": False,
        }
        if current == owner or not apply:
            return result
        self.init()
        with exclusive_lock(self.lock_path):
            atomic_write_json(path, owner)
        return {**result, "status": "recorded", "path": str(path)}

    def _local_heads(self, project_id: str) -> List[Dict[str, Any]]:
        packages = [item["payload"] for item in self.local_events() if item["project_id"] == project_id]
        predecessors = {
            item["predecessor_generation_id"]
            for item in packages
            if item["predecessor_generation_id"] is not None
        }
        return [item for item in packages if item["generation_id"] not in predecessors]

    @staticmethod
    def _entry_identity(package: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {key: entry[key] for key in ("path", "role", "byte_length", "sha256")}
            for entry in package["entries"]
        ]

    def refresh_owner(self, project_id: str, *, apply: bool = False) -> Dict[str, Any]:
        """Create one successor only when the explicit stable selection changed."""
        owner_path = self._owner_path(project_id)
        if not owner_path.exists():
            raise ValueError("project evidence owner is not registered")
        owner = read_json(owner_path)
        if owner.get("format") != "memory-wuxian-project-evidence-owner-v1" or not owner.get("enabled"):
            raise ValueError("project evidence owner is disabled or invalid")
        heads = self._local_heads(project_id)
        if len(heads) > 1:
            raise ValueError("project evidence owner has conflicting local heads")
        predecessor = heads[0]["generation_id"] if heads else None
        spec = {
            "schema_version": 1,
            "project_id": owner["project_id"],
            "title": owner["title"],
            "source_root": owner["source_root"],
            "files": owner["files"],
        }
        before = {
            item["path"]: (Path(owner["source_root"]) / item["path"]).stat().st_mtime_ns
            for item in owner["files"]
        }
        candidate = self._package_from_spec(spec)
        after = {
            item["path"]: (Path(owner["source_root"]) / item["path"]).stat().st_mtime_ns
            for item in owner["files"]
        }
        if before != after:
            raise ValueError("project evidence source changed during refresh")
        candidate_entries = {entry["path"]: entry for entry in candidate["entries"]}
        for item in owner["files"]:
            content = (Path(owner["source_root"]) / item["path"]).read_bytes()
            entry = candidate_entries[item["path"]]
            if len(content) != entry["byte_length"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("project evidence source changed during refresh")
        if heads and self._entry_identity(candidate) == self._entry_identity(heads[0]):
            return {
                "status": "no-change",
                "project_id": project_id,
                "generation_id": predecessor,
                "persistent_mutations": 0,
            }
        if predecessor is not None:
            spec["predecessor_generation_id"] = predecessor
        result = self.build(spec, apply=apply)
        return {**result, "owner_refresh": True}

    def owner_status(self) -> Dict[str, Any]:
        owners = []
        if self.owner_root.is_dir():
            for path in sorted(self.owner_root.glob("*.json")):
                owner = read_json(path)
                heads = self._local_heads(str(owner.get("project_id", "")))
                owners.append({
                    "project_id": owner.get("project_id"),
                    "title": owner.get("title"),
                    "enabled": bool(owner.get("enabled")),
                    "selected_files": len(owner.get("files", [])),
                    "current_generation_id": heads[0]["generation_id"] if len(heads) == 1 else None,
                    "attention": "conflicting-heads" if len(heads) > 1 else None,
                })
        return {"status": "ok", "owners": owners, "count": len(owners)}

    def refresh_owners(self, *, maximum_owners: int = 20, apply: bool = False) -> Dict[str, Any]:
        if maximum_owners < 1 or maximum_owners > 100:
            raise ValueError("maximum_owners must be between 1 and 100")
        project_ids = [path.stem for path in sorted(self.owner_root.glob("*.json"))]
        selected = project_ids[:maximum_owners]
        results = []
        for project_id in selected:
            try:
                results.append(self.refresh_owner(project_id, apply=apply))
            except Exception as exc:
                results.append({
                    "status": "attention",
                    "project_id": project_id,
                    "error": str(exc).replace("\r", " ").replace("\n", " ")[:500],
                })
        return {
            "status": "attention" if any(item["status"] == "attention" for item in results) else "ok",
            "processed": len(selected),
            "remaining": max(0, len(project_ids) - len(selected)),
            "created": sum(item["status"] == "recorded" for item in results),
            "unchanged": sum(item["status"] == "no-change" for item in results),
            "results": results,
        }

    @staticmethod
    def validate_package(package: Dict[str, Any]) -> Dict[str, Any]:
        required = {
            "format", "schema_version", "generation_id", "project_id", "title",
            "predecessor_generation_id", "entries",
        }
        if not isinstance(package, dict) or set(package) != required:
            raise ValueError("project evidence package fields are invalid")
        if package["format"] != PACKAGE_FORMAT or package["schema_version"] != 1:
            raise ValueError("project evidence package format is unsupported")
        if not PROJECT_ID_RE.fullmatch(str(package["project_id"])):
            raise ValueError("project evidence project_id is invalid")
        if not isinstance(package["title"], str) or not package["title"].strip() or len(package["title"]) > 240:
            raise ValueError("project evidence title is invalid")
        predecessor = package["predecessor_generation_id"]
        if predecessor is not None and not GENERATION_ID_RE.fullmatch(str(predecessor)):
            raise ValueError("project evidence predecessor is invalid")
        entries = package["entries"]
        if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
            raise ValueError("project evidence entries are missing or exceed the limit")
        previous = None
        total = 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "path", "role", "byte_length", "sha256", "content_base64"
            }:
                raise ValueError("project evidence entry fields are invalid")
            path = _safe_relative_path(entry["path"])
            if path != entry["path"] or (previous is not None and path <= previous):
                raise ValueError("project evidence entries must be strictly ordered")
            previous = path
            if entry["role"] not in ALLOWED_ROLES:
                raise ValueError("project evidence role is unsupported")
            if Path(path).suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError("project evidence file type is unsupported")
            try:
                content = base64.b64decode(entry["content_base64"], validate=True)
            except Exception as error:
                raise ValueError("project evidence content encoding is invalid") from error
            if len(content) != entry["byte_length"] or len(content) > MAX_FILE_BYTES:
                raise ValueError("project evidence byte length is invalid")
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("project evidence content hash mismatch")
            total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("project evidence package exceeds the byte limit")
        if package["generation_id"] != _generation_identity(package):
            raise ValueError("project evidence generation identity mismatch")
        return json.loads(json.dumps(package))

    def _package_from_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        required = {"schema_version", "project_id", "title", "source_root", "files"}
        optional = {"predecessor_generation_id"}
        if not isinstance(spec, dict) or not required.issubset(spec) or set(spec) - required - optional:
            raise ValueError("project evidence specification fields are invalid")
        if spec["schema_version"] != 1 or not PROJECT_ID_RE.fullmatch(str(spec["project_id"])):
            raise ValueError("project evidence specification identity is invalid")
        source_root = Path(str(spec["source_root"])).expanduser().resolve()
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("project evidence source_root is not a regular directory")
        files = spec["files"]
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise ValueError("project evidence files are missing or exceed the limit")
        selected: Dict[str, str] = {}
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "role"}:
                raise ValueError("project evidence file selection is invalid")
            relative = _safe_relative_path(item["path"])
            if relative in selected:
                raise ValueError("project evidence file is selected more than once")
            if item["role"] not in ALLOWED_ROLES:
                raise ValueError("project evidence role is unsupported")
            selected[relative] = item["role"]
        entries = []
        total = 0
        for relative in sorted(selected):
            path = source_root.joinpath(*PurePosixPath(relative).parts)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source_root):
                raise ValueError(f"project evidence source is unsafe: {relative}")
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError(f"project evidence file type is unsupported: {relative}")
            content = path.read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise ValueError(f"project evidence file exceeds the byte limit: {relative}")
            if path.suffix.lower() in TEXT_SUFFIXES and any(pattern.search(content) for pattern in SECRET_PATTERNS):
                raise ValueError(f"project evidence contains a probable secret: {relative}")
            total += len(content)
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("project evidence package exceeds the byte limit")
            entries.append({
                "path": relative,
                "role": selected[relative],
                "byte_length": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
            })
        package = {
            "format": PACKAGE_FORMAT,
            "schema_version": 1,
            "generation_id": "",
            "project_id": str(spec["project_id"]),
            "title": str(spec["title"]).strip(),
            "predecessor_generation_id": spec.get("predecessor_generation_id"),
            "entries": entries,
        }
        package["generation_id"] = _generation_identity(package)
        return self.validate_package(package)

    def build(self, spec: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        package = self._package_from_spec(spec)
        predecessor = package["predecessor_generation_id"]
        if predecessor is not None:
            predecessor_digest = predecessor.split(":", 1)[1]
            predecessor_path = self.local_root / package["project_id"] / f"{predecessor_digest}.json"
            if not predecessor_path.exists():
                raise ValueError("project evidence predecessor is not a local generation for this project")
        digest = package["generation_id"].split(":", 1)[1]
        path = self.local_root / package["project_id"] / f"{digest}.json"
        result = {
            "status": "preview",
            "generation_id": package["generation_id"],
            "project_id": package["project_id"],
            "entries": len(package["entries"]),
            "bytes": sum(item["byte_length"] for item in package["entries"]),
            "automatic_activation": False,
            "source_root_persisted": False,
        }
        if path.exists():
            if read_json(path) != package:
                raise ValueError("project evidence generation conflicts with existing content")
            return {**result, "status": "no-change", "path": str(path)}
        if not apply:
            return result
        self.init()
        with exclusive_lock(self.lock_path):
            if path.exists():
                if read_json(path) != package:
                    raise ValueError("project evidence generation appeared with different content")
                return {**result, "status": "no-change", "path": str(path)}
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, package)
        return {**result, "status": "recorded", "path": str(path)}

    def local_events(self) -> List[Dict[str, Any]]:
        events = []
        if not self.local_root.is_dir():
            return events
        for path in sorted(self.local_root.glob("*/*.json")):
            package = self.validate_package(read_json(path))
            events.append({
                "source_event_id": package["generation_id"],
                "generation_id": package["generation_id"],
                "project_id": package["project_id"],
                "payload": package,
            })
        return events

    def list(self, project_id: Optional[str] = None) -> Dict[str, Any]:
        if project_id is not None and not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project evidence project_id is invalid")
        local = [item["payload"] for item in self.local_events()]
        remote = []
        if self.replica_root.is_dir():
            for path in sorted(self.replica_root.glob("*/*/*.json")):
                record = read_json(path)
                package = self.validate_package(record["package"])
                remote.append({
                    "origin_node_id": record["origin_node_id"],
                    "event_sequence": record["event_sequence"],
                    "package": package,
                })
        if project_id is not None:
            local = [item for item in local if item["project_id"] == project_id]
            remote = [item for item in remote if item["package"]["project_id"] == project_id]
        return {"status": "listed", "local": local, "remote": remote, "automatic_activation": False}

    def query(
        self,
        query: str,
        *,
        project_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Dict[str, Any]:
        needle = str(query).strip().casefold()
        if not needle:
            raise ValueError("project evidence query is empty")
        if role is not None and role not in ALLOWED_ROLES:
            raise ValueError("project evidence role is unsupported")
        inventory = self.list(project_id)
        candidates = [
            ("local", None, package)
            for package in inventory["local"]
        ] + [
            ("remote", record["origin_node_id"], record["package"])
            for record in inventory["remote"]
        ]
        successors: Dict[str, List[str]] = {}
        for _authority, _origin, package in candidates:
            predecessor = package["predecessor_generation_id"]
            if predecessor is not None:
                successors.setdefault(predecessor, []).append(package["generation_id"])
        candidates.sort(
            key=lambda item: (
                item[2]["generation_id"] in successors,
                item[2]["project_id"],
                item[2]["generation_id"],
            )
        )
        results = []
        for authority, origin, package in candidates:
            for entry in package["entries"]:
                if role is not None and entry["role"] != role:
                    continue
                content = base64.b64decode(entry["content_base64"], validate=True)
                text_content = ""
                if Path(entry["path"]).suffix.lower() in TEXT_SUFFIXES:
                    text_content = content.decode("utf-8", errors="replace")
                haystack = "\n".join((
                    package["project_id"], package["title"], entry["path"],
                    entry["role"], text_content,
                )).casefold()
                if needle not in haystack:
                    continue
                results.append({
                    "authority": authority,
                    "origin_node_id": origin,
                    "project_id": package["project_id"],
                    "title": package["title"],
                    "generation_id": package["generation_id"],
                    "predecessor_generation_id": package["predecessor_generation_id"],
                    "is_current_head": package["generation_id"] not in successors,
                    "successor_generation_ids": sorted(successors.get(package["generation_id"], [])),
                    "path": entry["path"],
                    "role": entry["role"],
                    "byte_length": entry["byte_length"],
                    "sha256": entry["sha256"],
                    "text_excerpt": text_content[:MAX_QUERY_EXCERPT_CHARACTERS],
                    "excerpt_truncated": len(text_content) > MAX_QUERY_EXCERPT_CHARACTERS,
                })
                if len(results) >= MAX_QUERY_RESULTS:
                    return {"status": "matched", "results": results, "truncated": True}
        return {"status": "matched", "results": results, "truncated": False}

    def reconstruct(self, generation_id: str, destination: Path, *, apply: bool = False) -> Dict[str, Any]:
        if not GENERATION_ID_RE.fullmatch(generation_id):
            raise ValueError("project evidence generation_id is invalid")
        digest = generation_id.split(":", 1)[1]
        candidates = list(self.local_root.glob(f"*/{digest}.json"))
        candidates += list(self.replica_root.glob(f"*/*/{digest}.json"))
        if len(candidates) != 1:
            raise ValueError("project evidence generation is missing or ambiguous")
        record = read_json(candidates[0])
        package = self.validate_package(record.get("package", record))
        target_root = Path(destination).expanduser().resolve()
        writes, conflicts = [], []
        planned_writes: List[tuple[Path, bytes]] = []
        for entry in package["entries"]:
            target = target_root.joinpath(*PurePosixPath(entry["path"]).parts)
            content = base64.b64decode(entry["content_base64"], validate=True)
            if target.exists():
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                    conflicts.append(entry["path"])
            else:
                writes.append(entry["path"])
                planned_writes.append((target, content))
        if conflicts:
            return {"status": "conflict", "applied": False, "writes": writes, "conflicts": conflicts}
        if apply:
            for target, content in planned_writes:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, content)
        return {"status": "completed" if apply else "preview", "applied": apply, "writes": writes, "conflicts": []}


class ProjectEvidenceExchangeManager:
    """Independent project-evidence-v1 stream; old clients safely ignore it."""

    requires_authenticated_transport = True

    def __init__(self, store: Any):
        self.store = store
        self.root = Path(store.root)
        self.evidence = ProjectEvidenceStore(store)
        self.federation = FederationManager(self.evidence.store)
        self.metadata_root = self.evidence.root / "exchange"
        self.replica_root = self.evidence.replica_root
        self.export_ledger_path = self.metadata_root / "export-ledger.jsonl"
        self.export_state_path = self.metadata_root / "export-state.json"
        self.sync_log_path = self.metadata_root / "sync-log.jsonl"
        self.exchange_lock_path = self.evidence.environment.locks_dir / "project-evidence-exchange.lock"

    def init_layout(self) -> None:
        self.evidence.init()
        self.metadata_root.mkdir(parents=True, exist_ok=True)
        if not self.export_ledger_path.exists():
            atomic_write_bytes(self.export_ledger_path, b"")
        if not self.export_state_path.exists():
            atomic_write_json(self.export_state_path, {"format_version": 1, "next_event_sequence": 1, "source_events": {}})
        if not self.sync_log_path.exists():
            atomic_write_bytes(self.sync_log_path, b"")

    def node(self) -> Dict[str, Any]:
        return self.federation.node()

    def peers(self) -> List[Dict[str, Any]]:
        return self.federation.peers()

    def exchange_observation(self, _timestamp: float) -> Dict[str, Any]:
        events = self.evidence.local_events()
        marker = {"size": sum(len(canonical_bytes(item["payload"])) for item in events), "mtime_ns": 0}
        return {"completed_rounds": len(events), "raw_today": marker, "summary_registry": marker, "title_index": marker}

    def log_sync(self, event: str, node_id: str, details: Dict[str, Any]) -> None:
        self.init_layout()
        with self.sync_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"timestamp": now_iso(), "event": event, "node_id": safe_node_id(node_id), **details}, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def refresh_export_ledger(self) -> List[Dict[str, Any]]:
        self.init_layout()
        ledger = read_jsonl(self.export_ledger_path)
        known = {item["source_event_id"] for item in ledger}
        next_sequence = max((int(item["event_sequence"]) for item in ledger), default=0) + 1
        for event in self.evidence.local_events():
            if event["source_event_id"] in known:
                continue
            payload = event["payload"]
            ledger.append({
                "event_sequence": next_sequence,
                "source_event_id": event["source_event_id"],
                "event_kind": "project-evidence-package",
                "generation_id": event["generation_id"],
                "project_id": event["project_id"],
                "payload_sha256": canonical_sha256(payload),
                "payload": payload,
            })
            known.add(event["source_event_id"])
            next_sequence += 1
        atomic_write_jsonl(self.export_ledger_path, ledger)
        atomic_write_json(self.export_state_path, {"format_version": 1, "next_event_sequence": next_sequence, "source_events": {item: True for item in sorted(known)}})
        return ledger

    def export_delta(self, output: Path, after_event_sequence: int = 0, target_node_id: Optional[str] = None, previous_bundle_sha256: Optional[str] = None) -> Dict[str, Any]:
        ledger = self.refresh_export_ledger()
        latest = max((int(item["event_sequence"]) for item in ledger), default=0)
        if after_event_sequence < 0 or after_event_sequence > latest:
            raise ValueError("project evidence export cursor is invalid")
        if after_event_sequence and not re.fullmatch(r"[0-9a-f]{64}", str(previous_bundle_sha256 or "")):
            raise ValueError("project evidence predecessor hash is required")
        if not after_event_sequence and previous_bundle_sha256 is not None:
            raise ValueError("initial project evidence export cannot name a predecessor")
        pending = [item for item in ledger if int(item["event_sequence"]) > after_event_sequence][:MAX_EVENTS_PER_BUNDLE]
        if not pending:
            return {"status": "no-change", "after_event_sequence": after_event_sequence}
        payload = b"".join(canonical_bytes(item) + b"\n" for item in pending)
        if len(payload) > MAX_BUNDLE_BYTES:
            raise ValueError("project evidence bundle exceeds the byte limit")
        manifest_base = {
            "format": BUNDLE_FORMAT,
            "protocol_version": PROTOCOL_VERSION,
            "stream_id": "project-evidence-v1",
            "origin_node_id": self.node()["node_id"],
            "target_node_id": safe_node_id(target_node_id) if target_node_id else None,
            "base_event_sequence": after_event_sequence,
            "previous_bundle_sha256": previous_bundle_sha256,
            "from_event_sequence": int(pending[0]["event_sequence"]),
            "to_event_sequence": int(pending[-1]["event_sequence"]),
            "artifact_count": len(pending),
            "payload_path": "payload/project-evidence.jsonl",
            "payload_bytes": len(payload),
            "payload_sha256": bytes_sha256(payload),
        }
        manifest = {**manifest_base, "bundle_id": "mwb-" + canonical_sha256(manifest_base)[:32]}
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", canonical_bytes(manifest) + b"\n")
            archive.writestr(manifest["payload_path"], payload)
        return {"status": "exported", **manifest, "output": str(output), "bundle_sha256": bytes_sha256(output.read_bytes())}

    def read_bundle_manifest(self, bundle: Path) -> Dict[str, Any]:
        with zipfile.ZipFile(bundle, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        required = {
            "format", "protocol_version", "stream_id", "origin_node_id",
            "target_node_id", "base_event_sequence", "previous_bundle_sha256",
            "from_event_sequence", "to_event_sequence", "artifact_count",
            "payload_path", "payload_bytes", "payload_sha256", "bundle_id",
        }
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ValueError("project evidence bundle manifest fields are invalid")
        if manifest["format"] != BUNDLE_FORMAT or manifest["stream_id"] != "project-evidence-v1" or manifest["protocol_version"] != PROTOCOL_VERSION:
            raise ValueError("project evidence bundle format is unsupported")
        safe_node_id(str(manifest["origin_node_id"]))
        if manifest["target_node_id"] is not None:
            safe_node_id(str(manifest["target_node_id"]))
        base = int(manifest["base_event_sequence"])
        first = int(manifest["from_event_sequence"])
        last = int(manifest["to_event_sequence"])
        count = int(manifest["artifact_count"])
        if base < 0 or first != base + 1 or last < first or last - first + 1 != count or count > MAX_EVENTS_PER_BUNDLE:
            raise ValueError("project evidence bundle sequence manifest is invalid")
        predecessor = manifest["previous_bundle_sha256"]
        if (base == 0 and predecessor is not None) or (base > 0 and not re.fullmatch(r"[0-9a-f]{64}", str(predecessor or ""))):
            raise ValueError("project evidence bundle predecessor is invalid")
        if manifest["payload_path"] != "payload/project-evidence.jsonl":
            raise ValueError("project evidence bundle payload path is invalid")
        if not 0 <= int(manifest["payload_bytes"]) <= MAX_BUNDLE_BYTES or not re.fullmatch(r"[0-9a-f]{64}", str(manifest["payload_sha256"])):
            raise ValueError("project evidence bundle payload metadata is invalid")
        identity = {key: value for key, value in manifest.items() if key != "bundle_id"}
        if manifest["bundle_id"] != "mwb-" + canonical_sha256(identity)[:32]:
            raise ValueError("project evidence bundle identity mismatch")
        return manifest

    def replica_state(self, node_id: str) -> Dict[str, Any]:
        path = self.replica_root / safe_node_id(node_id) / "state.json"
        if not path.exists():
            return {"last_event_sequence": 0, "last_bundle_id": None, "last_bundle_sha256": None}
        return read_json(path)

    def import_delta(self, *_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        raise TypeError("project evidence import requires authenticated transport")

    def _import_authenticated_delta(self, bundle: Path, *, expected_node_id: str, authenticated_open_result: Any) -> Dict[str, Any]:
        origin, target, payload_sha256 = authenticated_open_result.consume_environment_binding()
        expected = safe_node_id(expected_node_id)
        if origin != expected or target != safe_node_id(self.node()["node_id"]):
            raise ValueError("project evidence authenticated transport binding mismatch")
        if payload_sha256 != bytes_sha256(bundle.read_bytes()):
            raise ValueError("project evidence authenticated payload hash mismatch")
        manifest = self.read_bundle_manifest(bundle)
        if safe_node_id(manifest["origin_node_id"]) != origin or safe_node_id(manifest["target_node_id"]) != target:
            raise ValueError("project evidence bundle target binding mismatch")
        state = self.replica_state(origin)
        expected_sequence = int(state["last_event_sequence"]) + 1
        if int(manifest["from_event_sequence"]) != expected_sequence:
            raise ValueError("project evidence bundle sequence is not contiguous")
        if int(state["last_event_sequence"]):
            if manifest["previous_bundle_sha256"] != state["last_bundle_sha256"]:
                raise ValueError("project evidence bundle predecessor hash mismatch")
        elif manifest["previous_bundle_sha256"] is not None:
            raise ValueError("initial project evidence bundle names a predecessor")
        with zipfile.ZipFile(bundle, "r") as archive:
            payload = archive.read(manifest["payload_path"])
        if len(payload) != manifest["payload_bytes"] or bytes_sha256(payload) != manifest["payload_sha256"]:
            raise ValueError("project evidence bundle payload integrity failed")
        records = [json.loads(line) for line in payload.splitlines() if line.strip()]
        expected_sequences = list(range(
            int(manifest["from_event_sequence"]),
            int(manifest["to_event_sequence"]) + 1,
        ))
        actual_sequences = [int(record.get("event_sequence", -1)) for record in records]
        if (
            len(records) != int(manifest["artifact_count"])
            or actual_sequences != expected_sequences
        ):
            raise ValueError("project evidence bundle event range is invalid")
        peer_root = self.replica_root / origin
        planned_records = []
        for record in records:
            required = {"event_sequence", "source_event_id", "event_kind", "generation_id", "project_id", "payload_sha256", "payload"}
            if set(record) != required or record["event_kind"] != "project-evidence-package":
                raise ValueError("project evidence event fields are invalid")
            package = self.evidence.validate_package(record["payload"])
            if record["generation_id"] != package["generation_id"] or record["project_id"] != package["project_id"] or record["payload_sha256"] != canonical_sha256(package):
                raise ValueError("project evidence event identity mismatch")
            digest = package["generation_id"].split(":", 1)[1]
            path = peer_root / package["project_id"] / f"{digest}.json"
            value = {"schema_version": 1, "origin_node_id": origin, "event_sequence": record["event_sequence"], "received_bundle_id": manifest["bundle_id"], "automatic_activation": False, "package": package}
            if path.exists() and read_json(path) != value:
                raise ValueError("project evidence replica conflicts with existing content")
            planned_records.append((path, value))
        for path, value in planned_records:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, value)
        state = {"last_event_sequence": int(manifest["to_event_sequence"]), "last_bundle_id": manifest["bundle_id"], "last_bundle_sha256": bytes_sha256(bundle.read_bytes()), "last_sync_at": now_iso()}
        atomic_write_json(peer_root / "state.json", state)
        return {"status": "imported", "origin_node_id": origin, "imported": len(records), **state}

    def status(self) -> Dict[str, Any]:
        ledger = read_jsonl(self.export_ledger_path) if self.export_ledger_path.exists() else []
        federation_status = self.federation.status()
        return {
            "status": "ok",
            "stream_id": "project-evidence-v1",
            "local_packages": len(self.evidence.local_events()),
            "local_event_sequence": max((int(item["event_sequence"]) for item in ledger), default=0),
            "peers": [
                {"node_id": peer["node_id"], "replica": self.replica_state(peer["node_id"])}
                for peer in federation_status.get("devices", [])
                if peer.get("trusted")
            ],
        }
