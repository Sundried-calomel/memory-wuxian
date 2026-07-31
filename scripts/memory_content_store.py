#!/usr/bin/env python3
"""Exact-byte shadow content store for removable archive evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional

from platform_transaction import atomic_write_canonical_json, canonical_json_bytes, read_canonical_json


MANIFEST_FORMAT = "memory-wuxian-content-manifest-v1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative_path(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Manifest paths must be normalized relative paths")
    return path.as_posix()


def manifest_identity(payload: Dict[str, Any]) -> str:
    identity = {key: value for key, value in payload.items() if key != "manifest_id"}
    return "manifest-" + sha256_bytes(canonical_json_bytes(identity))


class ContentStore:
    def __init__(self, archive_root: Path):
        self.archive_root = Path(archive_root)
        self.root = self.archive_root / "shadow-content-v1"
        self.objects = self.root / "objects" / "sha256"
        self.manifests = self.root / "manifests"

    def object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid SHA-256 object identity")
        return self.objects / digest[:2] / digest[2:]

    def manifest_path(self, manifest_id: str) -> Path:
        if not manifest_id.startswith("manifest-") or len(manifest_id) != 73:
            raise ValueError("Invalid manifest identity")
        return self.manifests / f"{manifest_id}.json"

    def _write_object(self, data: bytes) -> str:
        digest = sha256_bytes(data)
        path = self.object_path(digest)
        if path.exists():
            if path.read_bytes() != data:
                raise RuntimeError(f"Content object hash collision or corruption: {digest}")
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{digest}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                if path.read_bytes() != data:
                    raise RuntimeError(f"Content object changed during write: {digest}")
            else:
                os.replace(temporary, path)
                temporary = ""
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return digest

    def _manifest_from_source(
        self,
        source_root: Path,
        source_id: str,
        relative_paths: Iterable[str],
        *,
        write_objects: bool,
    ) -> Dict[str, Any]:
        if not source_id.strip() or len(source_id) > 256:
            raise ValueError("source_id must be a bounded stable identity")
        root = Path(source_root).resolve()
        selected = sorted({safe_relative_path(path) for path in relative_paths})
        if not selected:
            raise ValueError("At least one explicit source file is required")
        entries = []
        for relative in selected:
            path = root.joinpath(*PurePosixPath(relative).parts)
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError(f"Source is not a regular in-scope file: {relative}")
            data = path.read_bytes()
            digest = self._write_object(data) if write_objects else sha256_bytes(data)
            entries.append({"path": relative, "byte_length": len(data), "sha256": digest})
        payload = {
            "format": MANIFEST_FORMAT,
            "manifest_id": "",
            "source_id": source_id,
            "entries": entries,
        }
        payload["manifest_id"] = manifest_identity(payload)
        return payload

    def plan_manifest(
        self,
        source_root: Path,
        source_id: str,
        relative_paths: Iterable[str],
    ) -> Dict[str, Any]:
        return self._manifest_from_source(
            source_root, source_id, relative_paths, write_objects=False
        )

    def build_manifest(
        self,
        source_root: Path,
        source_id: str,
        relative_paths: Iterable[str],
    ) -> Dict[str, Any]:
        payload = self._manifest_from_source(
            source_root, source_id, relative_paths, write_objects=True
        )
        path = self.manifest_path(payload["manifest_id"])
        if path.exists():
            if read_canonical_json(path) != payload:
                raise RuntimeError("Immutable manifest identity collision")
        else:
            atomic_write_canonical_json(path, payload)
        return payload

    def load_manifest(self, manifest_id: str) -> Dict[str, Any]:
        manifest = read_canonical_json(self.manifest_path(manifest_id))
        if not isinstance(manifest, dict) or set(manifest) != {"format", "manifest_id", "source_id", "entries"}:
            raise ValueError("Content manifest has an invalid closed field set")
        if (
            manifest["format"] != MANIFEST_FORMAT
            or manifest["manifest_id"] != manifest_id
            or manifest_identity(manifest) != manifest_id
        ):
            raise ValueError("Content manifest identity mismatch")
        if not isinstance(manifest["source_id"], str) or not manifest["source_id"]:
            raise ValueError("Content manifest source identity is missing")
        if not isinstance(manifest["entries"], list) or not manifest["entries"]:
            raise ValueError("Content manifest entries are missing")
        previous = None
        for entry in manifest["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "byte_length", "sha256"}:
                raise ValueError("Content manifest entry has an invalid closed field set")
            relative = safe_relative_path(entry["path"])
            if relative != entry["path"] or (previous is not None and relative <= previous):
                raise ValueError("Content manifest entries must be strictly ordered")
            previous = relative
            if type(entry["byte_length"]) is not int or entry["byte_length"] < 0:
                raise ValueError("Content manifest byte length is invalid")
            self.object_path(entry["sha256"])
        return manifest

    def verify(self, manifest_id: str, source_root: Optional[Path] = None) -> Dict[str, Any]:
        manifest = self.load_manifest(manifest_id)
        issues = []
        root = Path(source_root).resolve() if source_root is not None else None
        for entry in manifest["entries"]:
            object_path = self.object_path(entry["sha256"])
            if not object_path.is_file():
                issues.append({"path": entry["path"], "reason": "missing-object"})
                continue
            data = object_path.read_bytes()
            if len(data) != entry["byte_length"] or sha256_bytes(data) != entry["sha256"]:
                issues.append({"path": entry["path"], "reason": "corrupt-object"})
            if root is not None:
                source = root.joinpath(*PurePosixPath(entry["path"]).parts)
                if not source.is_file():
                    issues.append({"path": entry["path"], "reason": "missing-source"})
                else:
                    source_data = source.read_bytes()
                    if len(source_data) != entry["byte_length"] or sha256_bytes(source_data) != entry["sha256"]:
                        issues.append({"path": entry["path"], "reason": "source-drift"})
        return {
            "status": "verified" if not issues else "failed",
            "manifest_id": manifest_id,
            "source_id": manifest["source_id"],
            "entries": len(manifest["entries"]),
            "issues": issues,
        }

    def reconstruct(self, manifest_id: str, destination: Path, *, apply: bool = False) -> Dict[str, Any]:
        verification = self.verify(manifest_id)
        if verification["status"] != "verified":
            raise ValueError("Cannot reconstruct an unverified shadow manifest")
        manifest = self.load_manifest(manifest_id)
        target_root = Path(destination).resolve()
        conflicts = []
        writes = []
        for entry in manifest["entries"]:
            target = target_root.joinpath(*PurePosixPath(entry["path"]).parts)
            if not target.is_relative_to(target_root):
                raise ValueError("Reconstruction target escaped destination")
            if target.exists():
                if not target.is_file() or sha256_bytes(target.read_bytes()) != entry["sha256"]:
                    conflicts.append({
                        "path": entry["path"],
                        "source_id": manifest["source_id"],
                        "reason": "destination-content-differs",
                    })
            else:
                writes.append(entry["path"])
        if conflicts:
            return {"status": "conflict", "applied": False, "conflicts": conflicts, "writes": writes}
        if apply:
            for entry in manifest["entries"]:
                target = target_root.joinpath(*PurePosixPath(entry["path"]).parts)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.object_path(entry["sha256"]), target)
                if sha256_bytes(target.read_bytes()) != entry["sha256"]:
                    raise RuntimeError(f"Reconstructed file verification failed: {entry['path']}")
        return {
            "status": "completed" if apply else "preview",
            "applied": apply,
            "manifest_id": manifest_id,
            "writes": writes,
            "conflicts": [],
        }

    def status(self) -> Dict[str, Any]:
        manifests = sorted(self.manifests.glob("manifest-*.json")) if self.manifests.exists() else []
        objects = list(self.objects.glob("*/*")) if self.objects.exists() else []
        return {
            "format": "memory-wuxian-content-store-status-v1",
            "enabled": not (self.root / "disabled.json").exists(),
            "manifest_count": len(manifests),
            "object_count": sum(path.is_file() for path in objects),
        }

    def disable(self, *, apply: bool = False) -> Dict[str, Any]:
        path = self.root / "disabled.json"
        if apply:
            atomic_write_canonical_json(path, {"format": "memory-wuxian-shadow-disabled-v1"})
        return {"status": "disabled" if apply else "preview", "applied": apply, "path": str(path)}
