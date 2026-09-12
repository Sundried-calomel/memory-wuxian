#!/usr/bin/env python3
"""Resumable exact-byte transfer between local shadow content stores."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from memory_content_store import ContentStore, sha256_bytes
from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json, read_canonical_json


DOMAINS = {"archive", "environment"}


def stable_stream_id(domain: str, source_id: str, manifest_id: str, target_id: str) -> str:
    if domain not in DOMAINS or not target_id:
        raise ValueError("Invalid transfer domain or target identity")
    value = f"{domain}\0{source_id}\0{manifest_id}\0{target_id}".encode("utf-8")
    return "stream-" + hashlib.sha256(value).hexdigest()


class ResumableTransfer:
    def __init__(self, source: ContentStore, target: ContentStore, domain: str, target_id: str):
        if domain not in DOMAINS or not target_id:
            raise ValueError("Invalid transfer boundary")
        self.source = source
        self.target = target
        self.domain = domain
        self.target_id = target_id

    def _checkpoint_path(self, stream_id: str) -> Path:
        return self.target.root / "transfers" / self.domain / stream_id / "checkpoint.json"

    def _new_checkpoint(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        stream_id = stable_stream_id(
            self.domain, manifest["source_id"], manifest["manifest_id"], self.target_id
        )
        return {
            "format": "memory-wuxian-transfer-checkpoint-v1",
            "stream_id": stream_id,
            "domain": self.domain,
            "source_id": manifest["source_id"],
            "target_id": self.target_id,
            "manifest_id": manifest["manifest_id"],
            "next_index": 0,
            "accepted_sha256": [],
            "status": "receiving",
        }

    def checkpoint(self, manifest_id: str) -> Dict[str, Any]:
        manifest = self.source.load_manifest(manifest_id)
        expected = self._new_checkpoint(manifest)
        path = self._checkpoint_path(expected["stream_id"])
        if not path.exists():
            return expected
        value = read_canonical_json(path)
        if not isinstance(value, dict) or set(value) != set(expected):
            raise ValueError("Transfer checkpoint has an invalid closed field set")
        for key in ("format", "stream_id", "domain", "source_id", "target_id", "manifest_id"):
            if value[key] != expected[key]:
                raise ValueError("Transfer checkpoint identity mismatch")
        if (
            type(value["next_index"]) is not int
            or value["next_index"] < 0
            or not isinstance(value["accepted_sha256"], list)
            or value["next_index"] != len(value["accepted_sha256"])
            or value["status"] not in {"receiving", "completed"}
        ):
            raise ValueError("Transfer checkpoint range is inconsistent")
        expected_prefix = [
            entry["sha256"] for entry in manifest["entries"][: value["next_index"]]
        ]
        if value["accepted_sha256"] != expected_prefix:
            raise ValueError("Transfer checkpoint hashes conflict with the manifest prefix")
        if value["status"] == "completed" and value["next_index"] != len(manifest["entries"]):
            raise ValueError("Completed checkpoint does not cover the manifest")
        return value

    def _transfer_unlocked(self, manifest_id: str, *, start: int, count: int) -> Dict[str, Any]:
        if start < 0 or count < 1 or count > 1000:
            raise ValueError("Invalid bounded transfer range")
        manifest = self.source.load_manifest(manifest_id)
        checkpoint = self.checkpoint(manifest_id)
        entries = manifest["entries"]
        end = min(start + count, len(entries))
        if start >= len(entries):
            raise ValueError("Transfer range starts beyond the manifest")
        if start > checkpoint["next_index"]:
            raise ValueError(f"missing segment: expected index {checkpoint['next_index']}, received {start}")
        if start < checkpoint["next_index"]:
            if end > checkpoint["next_index"]:
                raise ValueError("overlapping segment crosses the accepted checkpoint")
            expected = checkpoint["accepted_sha256"][start:end]
            received = [entry["sha256"] for entry in entries[start:end]]
            if expected != received:
                raise ValueError("replayed segment conflicts with accepted hashes")
            return {"status": "duplicate-replay", "checkpoint": checkpoint, "transferred": 0}
        accepted = list(checkpoint["accepted_sha256"])
        for entry in entries[start:end]:
            source_path = self.source.object_path(entry["sha256"])
            data = source_path.read_bytes()
            if len(data) != entry["byte_length"] or sha256_bytes(data) != entry["sha256"]:
                raise ValueError(f"corrupt segment from source {manifest['source_id']}: {entry['path']}")
            self.target._write_object(data)
            accepted.append(entry["sha256"])
        checkpoint["accepted_sha256"] = accepted
        checkpoint["next_index"] = end
        if end == len(entries):
            target_manifest = self.target.manifest_path(manifest_id)
            if target_manifest.exists() and read_canonical_json(target_manifest) != manifest:
                raise ValueError(
                    f"manifest conflict for source {manifest['source_id']} and target {self.target_id}"
                )
            atomic_write_canonical_json(target_manifest, manifest)
            if self.target.verify(manifest_id)["status"] != "verified":
                raise RuntimeError("Transferred manifest failed exact-byte verification")
            checkpoint["status"] = "completed"
        atomic_write_canonical_json(self._checkpoint_path(checkpoint["stream_id"]), checkpoint)
        return {"status": checkpoint["status"], "checkpoint": checkpoint, "transferred": end - start}

    def transfer(self, manifest_id: str, *, start: int, count: int) -> Dict[str, Any]:
        with exclusive_lock(self.target.archive_root / ".locks" / "content-transfer.lock"):
            return self._transfer_unlocked(manifest_id, start=start, count=count)

    def preview(self, manifest_id: str, *, start: int, count: int) -> Dict[str, Any]:
        if start < 0 or count < 1 or count > 1000:
            raise ValueError("Invalid bounded transfer range")
        manifest = self.source.load_manifest(manifest_id)
        checkpoint = self.checkpoint(manifest_id)
        end = min(start + count, len(manifest["entries"]))
        if start >= len(manifest["entries"]):
            raise ValueError("Transfer range starts beyond the manifest")
        return {
            "status": "preview",
            "applied": False,
            "stream_id": checkpoint["stream_id"],
            "expected_start": checkpoint["next_index"],
            "requested_start": start,
            "requested_end": end,
            "would_complete": end == len(manifest["entries"]),
        }
