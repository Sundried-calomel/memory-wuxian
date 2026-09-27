"""Authenticated, bounded core-v1 file-queue transport.

The outer envelope is the existing native memory-wuxian-envelope binary's
``bundle``/``ack`` format.  Protocol selection is inside the authenticated
JSON payload; this module does not claim compatibility with archive-v1 peers.
Queue files are an exchange mechanism only: receipt ACKs are emitted after the
configured replica index has committed the complete batch.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
import zlib
from pathlib import Path
from pathlib import PurePosixPath
from typing import Mapping

from storage import (atomic_replace_bytes, atomic_write_json, bytes_sha256,
                     canonical_json_bytes, exclusive_lock, safe_target)

FORMAT = "memory-wuxian-core-v1"
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_LOGICAL_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 500
MAX_BATCHES = 8
MAX_ENVELOPE_BYTES = 16 * 1024 * 1024
_NODE = re.compile(r"[a-z0-9][a-z0-9-]{2,63}\Z")
_GZIP_MAGIC = b"MWCORE-GZIP1\x00"


def _file_relative(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError("file path must be a safe relative POSIX path")
    path = PurePosixPath(name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (path.is_absolute() or path.as_posix() != name or
            any(part in {"", ".", ".."} or part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved
                for part in path.parts)):
        raise ValueError("unsafe relative file path")
    return name


def _json_bytes(data: bytes):
    if not isinstance(data, bytes) or len(data) > MAX_LOGICAL_BYTES:
        raise ValueError("core-v1 payload exceeds its bounded size")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("core-v1 payload is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("core-v1 payload must be an object")
    return value


def _wire_payload(data: bytes, maximum: int) -> bytes:
    if len(data) > MAX_LOGICAL_BYTES:
        raise ValueError("core-v1 logical page exceeds 64 MiB")
    if len(data) <= maximum:
        return data
    return _GZIP_MAGIC + gzip.compress(data, compresslevel=6, mtime=0)


class CoreSyncService:
    """Small peer-sync adapter. ``peer_index`` must isolate replicas by origin.

    Required config keys: exchange_root, binary, identity, local_node_id,
    peer_id, peer_encryption_public_key and peer_signing_public_key. Queue
    layout is ``<exchange_root>/core-v1/<origin>/<target>/{outbox,acks}``.
    """

    def __init__(self, store, *, exchange_root, binary, identity,
                 local_node_id, peer_id, peer_encryption_public_key,
                 peer_signing_public_key, peer_index=None, batch_records=MAX_RECORDS,
                 max_package_bytes=MAX_PAGE_BYTES, max_batches=MAX_BATCHES):
        self.store = store
        self.root = Path(exchange_root).absolute()
        self.binary = Path(binary).absolute()
        self.identity = Path(identity).absolute()
        self.local_node_id, self.peer_id = local_node_id, peer_id
        self.recipient = peer_encryption_public_key
        self.peer_signing_key = peer_signing_public_key
        self.peer_index = peer_index
        if self.peer_index is None:
            from peer_bridge import PeerIndex
            self.peer_index = PeerIndex(self.store.root)
        if not _NODE.fullmatch(str(local_node_id)) or not _NODE.fullmatch(str(peer_id)) or local_node_id == peer_id:
            raise ValueError("distinct valid local and peer node IDs are required")
        if not self.binary.is_absolute() or not self.identity.is_absolute():
            raise ValueError("explicit native helper and identity paths are required")
        if not isinstance(self.recipient, str) or not self.recipient or not isinstance(self.peer_signing_key, str) or not self.peer_signing_key:
            raise ValueError("explicit paired-peer encryption and signing keys are required")
        if type(batch_records) is not int or not 1 <= batch_records <= MAX_RECORDS:
            raise ValueError("batch_records must be between 1 and 500")
        if type(max_package_bytes) is not int or not 1024 <= max_package_bytes <= MAX_PAGE_BYTES:
            raise ValueError("max_package_bytes is outside the supported bound")
        if type(max_batches) is not int or not 1 <= max_batches <= MAX_BATCHES:
            raise ValueError("max_batches must be between 1 and 8")
        self.batch_records, self.max_package_bytes, self.max_batches = batch_records, max_package_bytes, max_batches
        self.state_path = safe_target(self.store.root, "core-sync/" + bytes_sha256(self.peer_id.encode()) + ".json")
        self.state_lock = self.state_path.with_suffix(".lock")
        self.outbox = safe_target(self.root, f"core-v1/{self.local_node_id}/{self.peer_id}/outbox")
        self.inbox = safe_target(self.root, f"core-v1/{self.peer_id}/{self.local_node_id}/outbox")
        # ACKs live beside the package they acknowledge, so both paired
        # processes resolve the same shared directory for this direction.
        self.ack_outbox = safe_target(self.root, f"core-v1/{self.peer_id}/{self.local_node_id}/acks")
        self.ack_inbox = safe_target(self.root, f"core-v1/{self.local_node_id}/{self.peer_id}/acks")
        self.files_outbox = safe_target(self.root, f"core-v1/{self.local_node_id}/{self.peer_id}/files")
        self.files_inbox = safe_target(self.root, f"core-v1/{self.peer_id}/{self.local_node_id}/files")
        self.file_ack_outbox = safe_target(self.root, f"core-v1/{self.peer_id}/{self.local_node_id}/file-acks")
        self.file_ack_inbox = safe_target(self.root, f"core-v1/{self.local_node_id}/{self.peer_id}/file-acks")

    def _state(self):
        if not self.state_path.exists():
            return {"format": FORMAT, "peer": self.peer_id, "source_cursor": 0,
                    "published_sequence": 0, "acknowledged_sequence": 0,
                    "peer_cursor": 0, "sent_summary_ids": [], "pending": [],
                    "pending_files": [], "file_versions": {}}
        if self.state_path.stat().st_size > 1_000_000:
            raise ValueError("core sync state is oversized")
        state = json.loads(self.state_path.read_text("utf-8"))
        if state.get("format") != FORMAT or state.get("peer") != self.peer_id:
            raise ValueError("core sync state identity mismatch")
        return state

    def status(self):
        """Read local cursor metadata only; never opens queue payloads."""
        state = self._state()
        pending = bool(state.get("pending", []))
        with self.store.connection() as db:
            source_head = db.execute("SELECT COALESCE(MAX(sequence),0) FROM messages").fetchone()[0]
            excluded = self.store.excluded_conversations()
            summary_ids = [row[0] for row in db.execute("SELECT id,conversation FROM summary_index")
                           if row[1] not in excluded]
        sent_summary_set = set(state.get("sent_summary_ids", []))
        sent_summaries = len(sent_summary_set)
        pending_summaries = sum(identifier not in sent_summary_set for identifier in summary_ids)
        remaining = int(state.get("source_cursor", 0)) < source_head or pending_summaries > 0
        delivery = "awaiting-peer" if pending else ("awaiting-publication" if remaining else
                    ("peer-acknowledged" if int(state.get("published_sequence", 0)) else "idle"))
        return {"status": "configured", "delivery": delivery,
                "namespace": "core-v1", "peer": self.peer_id,
                "sent_batches": state.get("sent_batches", 0),
                "received_batches": state.get("received_batches", 0),
                "acknowledged_batches": state.get("acknowledged_batches", 0),
                "source_cursor": state.get("source_cursor", 0),
                "published_sequence": state.get("published_sequence", 0),
                "acknowledged_sequence": state.get("acknowledged_sequence", 0),
                "peer_cursor": state.get("peer_cursor", 0),
                "pending_ack": pending, "source_head": source_head,
                "export_remaining": remaining,
                "sent_summary_count": sent_summaries,
                "pending_summary_count": pending_summaries,
                "pending_file_ack_count": len(state.get("pending_files", [])),
                "published_file_count": int(state.get("published_files", 0)),
                "acknowledged_file_count": int(state.get("acknowledged_files", 0)),
                "superseded_file_count": int(state.get("superseded_files", 0))}

    def _run_helper(self, args, timeout=120):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([str(self.binary), *args], capture_output=True, text=True,
                                encoding="utf-8", timeout=timeout, check=False,
                                shell=False, **options)
        if result.returncode:
            raise ValueError("native envelope helper rejected core-v1 payload")
        try:
            meta = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise ValueError("native envelope helper returned invalid metadata") from exc
        if not isinstance(meta, dict) or meta.get("format") != "memory-wuxian-envelope-v1":
            raise ValueError("native envelope helper metadata format mismatch")
        return meta

    def _seal(self, payload: bytes, *, target, kind="bundle") -> bytes:
        if kind not in {"bundle", "ack"} or target not in {self.peer_id, self.local_node_id}:
            raise ValueError("invalid envelope kind or target")
        with tempfile.TemporaryDirectory(prefix="mw-core-seal-") as tmp:
            source, sealed = Path(tmp) / "payload.json", Path(tmp) / "sealed.mwe"
            source.write_bytes(payload)
            meta = self._run_helper(["seal", "--identity", str(self.identity),
                "--recipient", self.recipient, "--input", str(source), "--output", str(sealed),
                "--kind", kind, "--origin-node-id", self.local_node_id,
                "--target-node-id", target])
            if meta.get("kind") != kind or meta.get("origin_node_id") != self.local_node_id or meta.get("target_node_id") != target:
                raise ValueError("native helper sealed a different envelope identity")
            if not sealed.is_file() or sealed.stat().st_size > MAX_ENVELOPE_BYTES:
                raise ValueError("sealed envelope is missing or oversized")
            return sealed.read_bytes()

    def _open(self, package: bytes, *, expected_origin, expected_target, kind="bundle") -> bytes:
        if not isinstance(package, bytes) or not package or len(package) > MAX_ENVELOPE_BYTES:
            raise ValueError("invalid or oversized native envelope")
        if expected_origin != self.peer_id or expected_target != self.local_node_id or kind not in {"bundle", "ack"}:
            raise ValueError("envelope identity is not the configured peer")
        with tempfile.TemporaryDirectory(prefix="mw-core-open-") as tmp:
            source, output = Path(tmp) / "sealed.mwe", Path(tmp) / "payload.json"
            source.write_bytes(package)
            meta = self._run_helper(["open", "--identity", str(self.identity),
                "--signing-public-key=" + self.peer_signing_key, "--input", str(source),
                "--output", str(output), "--expected-kind", kind,
                "--expected-origin-node-id", expected_origin,
                "--expected-target-node-id", expected_target])
            if (meta.get("kind") != kind or meta.get("origin_node_id") != expected_origin
                    or meta.get("target_node_id") != expected_target or meta.get("output") != str(output)):
                raise ValueError("native helper authentication metadata mismatch")
            if not output.is_file() or output.stat().st_size > self.max_package_bytes:
                raise ValueError("authenticated core payload is missing or oversized")
            data = output.read_bytes()
            if meta.get("payload_length") != len(data) or meta.get("payload_sha256") != bytes_sha256(data):
                raise ValueError("native helper plaintext binding mismatch")
            if data.startswith(_GZIP_MAGIC):
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                try:
                    logical = decoder.decompress(data[len(_GZIP_MAGIC):], MAX_LOGICAL_BYTES + 1)
                    if len(logical) > MAX_LOGICAL_BYTES or decoder.unconsumed_tail:
                        raise ValueError("expanded core payload exceeds 64 MiB")
                    logical += decoder.flush()
                except zlib.error as exc:
                    raise ValueError("compressed core payload is invalid") from exc
                if (len(logical) > MAX_LOGICAL_BYTES or not decoder.eof or
                        decoder.unused_data):
                    raise ValueError("compressed core payload is truncated, oversized or has trailing data")
                data = logical
            return data

    @staticmethod
    def _files_payload(files: Mapping[str, bytes], *, origin, target, artifact_id,
                       revision_id, batch_id, publication_sequence=0):
        if not isinstance(files, Mapping) or not files or len(files) > 4096:
            raise ValueError("an explicit bounded nonempty file mapping is required")
        declarations = []
        total = 0
        for name, data in sorted(files.items()):
            name = _file_relative(name)
            if not isinstance(data, bytes):
                raise ValueError("file contents must be bytes")
            total += len(data)
            if total > MAX_PAGE_BYTES:
                raise ValueError("file package exceeds the bounded size")
            declarations.append({"path": name,
                "size": len(data), "sha256": bytes_sha256(data),
                "content": base64.b64encode(data).decode("ascii")})
        return {"format": FORMAT, "channel": "files", "origin": origin, "target": target,
                "artifact_id": artifact_id, "revision_id": revision_id,
                "batch_id": batch_id, "publication_sequence": publication_sequence,
                "files": declarations}

    def seal_files(self, files, *, target, artifact_id, revision_id, batch_id=None):
        """Create a signed/encrypted files-channel package; never applies files."""
        batch_id = batch_id or os.urandom(16).hex()
        payload = self._files_payload(files, origin=self.local_node_id, target=target,
            artifact_id=artifact_id, revision_id=revision_id, batch_id=batch_id)
        data = canonical_json_bytes(payload)
        if len(data) > self.max_package_bytes:
            raise ValueError("serialized file package exceeds configured bound")
        return self._seal(data, target=target, kind="bundle")

    def open_files(self, package: bytes, *, expected_origin, expected_target=None):
        """Authenticate and materialize file bytes. Target selection stays local."""
        target = expected_target or self.local_node_id
        value = _json_bytes(self._open(package, expected_origin=expected_origin,
                                      expected_target=target, kind="bundle"))
        if (value.get("format") != FORMAT or value.get("channel") != "files"
                or value.get("origin") != expected_origin or value.get("target") != target
                or not isinstance(value.get("artifact_id"), str)
                or not isinstance(value.get("revision_id"), str)
                or not isinstance(value.get("batch_id"), str)
                or type(value.get("publication_sequence", 0)) is not int
                or value.get("publication_sequence", 0) < 0
                or not isinstance(value.get("files"), list) or not value["files"]):
            raise ValueError("authenticated file payload schema/identity mismatch")
        files, total = {}, 0
        for item in value["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "content"}:
                raise ValueError("invalid file declaration")
            name = item["path"]
            _file_relative(name)
            if name in files or type(item["size"]) is not int or item["size"] < 0:
                raise ValueError("duplicate path or invalid file size")
            try:
                data = base64.b64decode(item["content"], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("invalid file content encoding") from exc
            total += len(data)
            if len(data) != item["size"] or bytes_sha256(data) != item["sha256"] or total > self.max_package_bytes:
                raise ValueError("file content integrity or size mismatch")
            files[name] = data
        return {"status": "verified-files", "origin": expected_origin, "target": target,
                "artifact_id": value["artifact_id"], "revision_id": value["revision_id"],
                "batch_id": value["batch_id"],
                "publication_sequence": value.get("publication_sequence", 0), "files": files}

    def publish_files(self, files, *, artifact_id, revision_id, batch_id=None):
        """Place an authenticated files package in the paired file queue."""
        if not isinstance(artifact_id, str) or not artifact_id or not isinstance(revision_id, str) or not revision_id:
            raise ValueError("explicit artifact and revision identities are required")
        payload = self._files_payload(files, origin=self.local_node_id, target=self.peer_id,
                                      artifact_id=artifact_id, revision_id=revision_id,
                                      batch_id="pending", publication_sequence=0)
        content_identity = {"artifact_id": artifact_id, "revision_id": revision_id,
                            "target": self.peer_id,
                            "files": [{"path": item["path"], "size": item["size"],
                                       "sha256": item["sha256"]} for item in payload["files"]]}
        payload_digest = bytes_sha256(canonical_json_bytes(content_identity))
        requested_batch_id = batch_id
        batch_id = batch_id or "files-" + payload_digest[:40]
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,128}", batch_id):
            raise ValueError("invalid file batch ID")
        self.files_outbox.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.state_lock):
            state = self._state()
            pending = state.setdefault("pending_files", [])
            versions = state.setdefault("file_versions", {})
            version_key = bytes_sha256(canonical_json_bytes([artifact_id, revision_id, self.peer_id]))
            previous_version = versions.get(version_key)
            if previous_version:
                if previous_version.get("content_identity_sha256") != bytes_sha256(canonical_json_bytes(content_identity)):
                    raise ValueError("immutable artifact revision was republished with different files")
                if requested_batch_id and requested_batch_id != previous_version["batch_id"]:
                    raise ValueError("immutable artifact revision is bound to a different batch ID")
                batch_id = previous_version["batch_id"]
                publication_sequence = int(previous_version.get("publication_sequence", 0))
                payload["batch_id"] = batch_id
                payload["publication_sequence"] = publication_sequence
                payload_digest = bytes_sha256(canonical_json_bytes(payload))
                path_name = previous_version.get("path", f"{publication_sequence:020d}-{batch_id}.mwe")
                path = safe_target(self.files_outbox, path_name)
                if previous_version.get("status") in {"applied", "superseded"} or path.exists():
                    return {"status": "no-change", "delivery": previous_version.get("status"),
                            "batch_id": batch_id, "path": str(path),
                            "publication_sequence": publication_sequence}
            else:
                publication_sequence = int(state.get("published_files", 0)) + 1
                payload["batch_id"] = batch_id
                payload["publication_sequence"] = publication_sequence
                payload_digest = bytes_sha256(canonical_json_bytes(payload))
                path_name = f"{publication_sequence:020d}-{batch_id}.mwe"
                path = safe_target(self.files_outbox, path_name)
            existing = next((item for item in pending if item.get("batch_id") == batch_id), None)
            if existing and (existing.get("payload_sha256") != payload_digest or
                             existing.get("artifact_id") != artifact_id or
                             existing.get("revision_id") != revision_id):
                raise ValueError("file batch ID was previously bound to different content")
            if not existing:
                entry = {"batch_id": batch_id, "path": path_name,
                         "artifact_id": artifact_id, "revision_id": revision_id,
                         "payload_sha256": payload_digest,
                         "publication_sequence": publication_sequence}
                pending.append(entry)
                versions[version_key] = {**entry, "content_identity_sha256": bytes_sha256(canonical_json_bytes(content_identity)),
                                         "status": "pending"}
                state["published_files"] = publication_sequence
                # State first: a crash before queue-file creation is recoverable
                # by retrying this stable batch identity.
                self._state_save(state)
            if not path.exists():
                package = self._seal(canonical_json_bytes(payload), target=self.peer_id, kind="bundle")
                atomic_replace_bytes(path, package)
            elif path.stat().st_size > MAX_ENVELOPE_BYTES:
                raise ValueError("existing deterministic file package is oversized")
        return {"status": "queued-files", "batch_id": batch_id, "path": str(path),
                "publication_sequence": publication_sequence,
                "sha256": bytes_sha256(path.read_bytes())}

    def receive_files(self, *, limit=32):
        """Return authenticated file packages without selecting/applying targets."""
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("file receive limit must be between 1 and 128")
        directory = safe_target(self.root, f"core-v1/{self.peer_id}/{self.local_node_id}/files")
        if not directory.exists():
            return []
        received = []
        candidates = [path for path in sorted(directory.glob("*.mwe"))
                      if not safe_target(self.file_ack_outbox, path.name).exists()]
        if len(candidates) > 512:
            raise ValueError("file queue exceeds the bounded candidate count")
        for path in candidates:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENVELOPE_BYTES:
                raise ValueError("unsafe or oversized file-channel package")
            package = path.read_bytes()
            verified = self.open_files(package, expected_origin=self.peer_id,
                                       expected_target=self.local_node_id)
            received.append({"path": str(path), "package": package, "verified": verified})
        received.sort(key=lambda item: (item["verified"].get("publication_sequence", 0),
                                        item["verified"]["batch_id"]))
        state = self._state()
        applied = state.get("applied_file_sequences", {})
        ready = []
        for item in received:
            verified = item["verified"]
            high = applied.get(verified["artifact_id"])
            sequence = verified.get("publication_sequence", 0)
            if high and sequence < high.get("sequence", 0):
                self._queue_file_ack(verified, status="superseded", queue_name=Path(item["path"]).name)
                continue
            if high and sequence == high.get("sequence", 0):
                if verified["batch_id"] != high.get("batch_id"):
                    raise ValueError("file publication sequence is bound to another batch")
                # Crash recovery: the same package was already applied, so
                # reissue its applied ACK without applying it twice.
                self._queue_file_ack(verified, status="applied", queue_name=Path(item["path"]).name)
                continue
            ready.append(item)
        return ready[:limit]

    def _queue_file_ack(self, verified, *, status, queue_name=None):
        if status not in {"applied", "superseded"}:
            raise ValueError("invalid file acknowledgement state")
        ack_payload = {"format": FORMAT, "channel": "files-ack", "origin": self.local_node_id,
                       "target": self.peer_id, "status": status,
                       "batch_id": verified["batch_id"], "artifact_id": verified["artifact_id"],
                       "revision_id": verified["revision_id"],
                       "publication_sequence": verified.get("publication_sequence", 0)}
        ack = self._seal_payload(ack_payload, target=self.peer_id, kind="ack")
        self.file_ack_outbox.mkdir(parents=True, exist_ok=True)
        ack_path = safe_target(self.file_ack_outbox, queue_name or (verified["batch_id"] + ".mwe"))
        if not ack_path.exists():
            atomic_replace_bytes(ack_path, ack)
        return {"status": f"queued-{status}-ack", "batch_id": verified["batch_id"]}

    def acknowledge_files(self, received_item):
        """Queue an authenticated ACK only after the caller's apply succeeds."""
        if not isinstance(received_item, dict) or not isinstance(received_item.get("path"), str):
            raise ValueError("a received file-queue item is required")
        path = Path(received_item["path"]).absolute()
        if path.parent != self.files_inbox or path.suffix != ".mwe" or not path.is_file() or path.is_symlink():
            raise ValueError("file acknowledgement target is outside the configured inbox")
        package = path.read_bytes()
        verified = self.open_files(package, expected_origin=self.peer_id,
                                   expected_target=self.local_node_id)
        claimed = received_item.get("verified", {})
        if (claimed.get("batch_id") != verified["batch_id"] or
                claimed.get("artifact_id") != verified["artifact_id"] or
                claimed.get("revision_id") != verified["revision_id"]):
            raise ValueError("received file acknowledgement identity changed")
        with exclusive_lock(self.state_lock):
            state = self._state()
            current = state.setdefault("applied_file_sequences", {}).get(verified["artifact_id"])
            sequence = verified.get("publication_sequence", 0)
            if current and (sequence < current.get("sequence", 0) or
                            (sequence == current.get("sequence", 0) and verified["batch_id"] != current.get("batch_id"))):
                raise ValueError("refusing to apply or acknowledge an older file publication")
            state["applied_file_sequences"][verified["artifact_id"]] = {
                "sequence": sequence, "batch_id": verified["batch_id"]}
            self._state_save(state)
        return self._queue_file_ack(verified, status="applied", queue_name=path.name)

    def _process_file_acks(self, state):
        count = 0
        if not self.file_ack_inbox.exists():
            return count
        for path in sorted(self.file_ack_inbox.glob("*.mwe")):
            value = _json_bytes(self._read_envelope_file(path, origin=self.peer_id,
                                      target=self.local_node_id, kind="ack"))
            if value.get("format") != FORMAT or value.get("channel") != "files-ack" or value.get("status") not in {"applied", "superseded"} or value.get("origin") != self.peer_id or value.get("target") != self.local_node_id:
                raise ValueError("authenticated files ACK identity mismatch")
            matches = [item for item in state.get("pending_files", [])
                       if item.get("batch_id") == value.get("batch_id") and
                       item.get("artifact_id") == value.get("artifact_id") and
                       item.get("revision_id") == value.get("revision_id") and
                       item.get("publication_sequence", 0) == value.get("publication_sequence", 0)]
            if not matches:
                continue
            state["pending_files"] = [item for item in state.get("pending_files", [])
                                       if item not in matches]
            if value["status"] == "applied":
                state["acknowledged_files"] = int(state.get("acknowledged_files", 0)) + 1
            else:
                state["superseded_files"] = int(state.get("superseded_files", 0)) + 1
            for item in matches:
                version_key = bytes_sha256(canonical_json_bytes([
                    item.get("artifact_id"), item.get("revision_id"), self.peer_id]))
                if version_key in state.get("file_versions", {}):
                    state["file_versions"][version_key]["status"] = value["status"]
            self._state_save(state)
            for item in matches:
                queued = safe_target(self.files_outbox, item["path"])
                if queued.exists():
                    queued.unlink()
            path.unlink()
            count += 1
        return count

    def _state_save(self, state):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.state_path, state)

    def _seal_payload(self, value, *, target, kind="bundle"):
        data = canonical_json_bytes(value)
        if len(data) > MAX_LOGICAL_BYTES:
            raise ValueError("core-v1 logical page exceeds 64 MiB")
        wire = _wire_payload(data, self.max_package_bytes)
        if len(wire) > self.max_package_bytes:
            raise ValueError("compressed core-v1 page exceeds configured payload bound")
        return self._seal(wire, target=target, kind=kind)

    def _read_envelope_file(self, path, *, origin, target, kind):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENVELOPE_BYTES:
            raise ValueError("unsafe, missing or oversized queue envelope")
        return self._open(path.read_bytes(), expected_origin=origin,
                          expected_target=target, kind=kind)

    def _limited_records(self, source_cursor):
        # Avoid ArchiveStore.records(), which materializes the entire 143k-row
        # tail before slicing. Fetch only this bounded source page from SQLite.
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM messages WHERE sequence>? ORDER BY sequence LIMIT ?",
                              (int(source_cursor), self.batch_records)).fetchall()
        return [self.store._read(row) for row in rows]

    def _summary_candidates(self, state, source_cursor, excluded):
        if not hasattr(self, "_summary_cache"):
            from summary import SummaryService
            self._summary_cache = SummaryService(self.store).list()
        sent = set(state.get("sent_summary_ids", []))
        candidates = [item for item in self._summary_cache
                      if item.get("id") not in sent and item.get("conversation_id") not in excluded]
        raw_ids = sorted({ref for item in candidates for ref in item.get("raw_source_ids", [])})
        raw_meta = {}
        with self.store.connection() as db:
            for start in range(0, len(raw_ids), 800):
                page = raw_ids[start:start + 800]
                if not page:
                    continue
                marks = ",".join("?" for _ in page)
                for row in db.execute(f"SELECT id,sequence,conversation FROM messages WHERE id IN ({marks})", page):
                    raw_meta[row["id"]] = (row["sequence"], row["conversation"])
        ready, known_sent = [], sent
        # Levels are ordered so a parent can be selected only after its children
        # were in a prior committed/sent page. Newly selected children become
        # available on the next bounded service call.
        for item in sorted(candidates, key=lambda x: (x.get("level", 1), x.get("id", ""))):
            refs = item.get("raw_source_ids", [])
            if not refs or any(ref not in raw_meta or raw_meta[ref][0] > source_cursor or
                               raw_meta[ref][1] in excluded for ref in refs):
                continue
            children = item.get("source_refs", []) if item.get("level", 1) > 1 else []
            if all(child in known_sent for child in children):
                ready.append(item)
                known_sent.add(item["id"])
        return ready

    def _export_one(self, state):
        source_cursor = int(state.get("source_cursor", 0))
        records = self._limited_records(source_cursor)
        excluded = self.store.excluded_conversations()
        selected = [row for row in records if row.get("conversation_id") not in excluded]
        new_source_cursor = records[-1]["sequence"] if records else source_cursor
        if records and not selected:
            # Source cursor advances over explicitly excluded records without
            # manufacturing wire events or changing any source identity.
            state["source_cursor"] = new_source_cursor
            self._state_save(state)
            return {"queued": False, "source_cursor": new_source_cursor,
                    "records": 0, "excluded": len(records), "summaries": 0}
        # A final summary-only page ensures completions produced after the last
        # raw event still reach this peer. It never consumes raw wire numbers.
        summaries = self._summary_candidates(state, new_source_cursor, excluded)
        if not records and not summaries:
            return None
        wire_from = int(state.get("published_sequence", 0)) + 1
        wire_to = wire_from + len(selected) - 1
        wrappers = [{"wire_sequence": wire_from + i, "source_sequence": row["sequence"], "record": row}
                    for i, row in enumerate(selected)]
        if not records:
            summaries = summaries[:200]
        payload = {"format": FORMAT, "channel": "archive", "origin": self.local_node_id,
                   "target": self.peer_id, "from_wire_sequence": wire_from,
                   "to_wire_sequence": wire_to, "previous_wire_sequence": wire_from - 1,
                   "from_source_sequence": source_cursor + 1,
                   "to_source_sequence": new_source_cursor,
                   "records": wrappers, "summaries": summaries}
        summary_limit = min(len(summaries), 200)
        source_page_shrunk = False
        # Shrink oversized pages automatically; cursor/wire are recalculated
        # from exactly the prefix that fits, so retrying cannot create a gap.
        while True:
            page_source_cursor = (wrappers[-1]["source_sequence"] if source_page_shrunk
                                 else new_source_cursor)
            summaries = self._summary_candidates(state, page_source_cursor, excluded)[:summary_limit]
            payload["records"] = wrappers
            payload["from_source_sequence"] = source_cursor + 1
            payload["to_source_sequence"] = page_source_cursor
            payload["to_wire_sequence"] = wire_from + len(wrappers) - 1
            payload["summaries"] = summaries
            data = canonical_json_bytes(payload)
            wire_data = _wire_payload(data, self.max_package_bytes) if len(data) <= MAX_LOGICAL_BYTES else None
            if wire_data is not None and len(wire_data) <= self.max_package_bytes:
                break
            if summaries:
                if not wrappers and summary_limit <= 1:
                    raise ValueError("single summary exceeds configured page bound")
                summary_limit = summary_limit // 2
                if wrappers:
                    continue
            elif len(wrappers) > 1:
                wrappers = wrappers[:max(1, len(wrappers) // 2)]
                source_page_shrunk = True
                continue
            elif wrappers:
                raise ValueError("single source record exceeds configured page bound")
            else:
                raise ValueError("single summary exceeds configured page bound")
        new_source_cursor = payload["to_source_sequence"]
        wire_to = payload["to_wire_sequence"]
        digest = bytes_sha256(data)
        identity = {"protocol": FORMAT, "origin": self.local_node_id, "target": self.peer_id,
                    "from_wire_sequence": wire_from, "to_wire_sequence": wire_to,
                    "payload_sha256": digest}
        if not wrappers:
            identity["summary_batch"] = int(state.get("summary_batches", 0)) + 1
        payload["batch_identity"] = identity
        sealed = self._seal_payload(payload, target=self.peer_id)
        self.outbox.mkdir(parents=True, exist_ok=True)
        # A summary-only page starts AFTER its raw sources. At the same
        # starting cursor it must precede future raw pages whose summaries
        # may depend on it; summary batches retain their publication order.
        rank = "0" if not wrappers else "1"
        order = f"-{identity['summary_batch']:020d}" if not wrappers else ""
        name = f"{wire_from:020d}-{rank}-{wire_to:020d}{order}-{digest[:16]}.mwe"
        path = safe_target(self.outbox, name)
        if path.exists():
            # Native sealing is randomized. Deterministic plaintext identity
            # names the immutable queue item; reuse its existing ciphertext.
            sealed = path.read_bytes()
            if len(sealed) > MAX_ENVELOPE_BYTES:
                raise ValueError("existing immutable queue envelope is oversized")
        else:
            atomic_replace_bytes(path, sealed)
        state["source_cursor"] = new_source_cursor
        state["published_sequence"] = wire_to
        state["sent_batches"] = int(state.get("sent_batches", 0)) + 1
        state.setdefault("pending", []).append({"path": name, "identity": identity})
        state["sent_summary_ids"] = sorted(set(state.get("sent_summary_ids", [])) | {s["id"] for s in summaries})
        if not wrappers:
            state["summary_batches"] = int(state.get("summary_batches", 0)) + 1
        self._state_save(state)
        return {"queued": True, "wire_from": wire_from, "wire_to": wire_to,
                "source_cursor": new_source_cursor, "records": len(wrappers),
                "excluded": len(records) - len(selected), "summaries": len(summaries)}

    def _process_acks(self, state):
        count = 0
        if not self.ack_inbox.exists():
            return count
        for path in sorted(self.ack_inbox.glob("*.mwe")):
            plaintext = self._read_envelope_file(path, origin=self.peer_id,
                                                 target=self.local_node_id, kind="ack")
            ack = _json_bytes(plaintext)
            if ack.get("format") != FORMAT or ack.get("channel") != "ack" or ack.get("origin") != self.peer_id or ack.get("target") != self.local_node_id:
                raise ValueError("authenticated ACK protocol identity mismatch")
            identity = ack.get("batch_identity")
            if not isinstance(identity, dict) or identity.get("origin") != self.local_node_id or identity.get("target") != self.peer_id:
                raise ValueError("ACK does not refer to this local peer stream")
            matches = [p for p in state.get("pending", []) if p.get("identity") == identity]
            if not matches:
                continue
            state["pending"] = [p for p in state["pending"] if p.get("identity") != identity]
            state["acknowledged_sequence"] = max(int(state.get("acknowledged_sequence", 0)), int(identity["to_wire_sequence"]))
            state["acknowledged_batches"] = int(state.get("acknowledged_batches", 0)) + 1
            self._state_save(state)
            for item in matches:
                queued = safe_target(self.outbox, item["path"])
                if queued.exists():
                    queued.unlink()
            path.unlink()
            count += 1
        return count

    def _accept_one(self, path, state):
        plaintext = self._read_envelope_file(path, origin=self.peer_id,
                                             target=self.local_node_id, kind="bundle")
        payload = _json_bytes(plaintext)
        if (payload.get("format") != FORMAT or payload.get("channel") != "archive"
                or payload.get("origin") != self.peer_id or payload.get("target") != self.local_node_id):
            raise ValueError("authenticated archive payload identity mismatch")
        identity = payload.get("batch_identity")
        if not isinstance(identity, dict) or identity.get("protocol") != FORMAT or identity.get("origin") != self.peer_id or identity.get("target") != self.local_node_id:
            raise ValueError("archive batch identity invalid")
        records = payload.get("records")
        wire_from, wire_to = payload.get("from_wire_sequence"), payload.get("to_wire_sequence")
        if type(wire_from) is not int or type(wire_to) is not int or type(payload.get("previous_wire_sequence")) is not int:
            raise ValueError("peer archive page wire range is malformed")
        if not isinstance(records, list) or wire_to != payload["previous_wire_sequence"] + len(records):
            raise ValueError("peer archive page wire range is inconsistent")
        if records and [x.get("wire_sequence") for x in records] != list(range(wire_from, wire_to + 1)):
            raise ValueError("peer archive page wire events are not contiguous")
        if not records and not payload.get("summaries"):
            raise ValueError("empty peer page has no summary delta")
        data_without_identity = dict(payload)
        data_without_identity.pop("batch_identity", None)
        expected_digest = bytes_sha256(canonical_json_bytes(data_without_identity))
        # Batch identity digest is over the canonical payload before the
        # identity member is attached, binding all content without recursion.
        if identity.get("payload_sha256") != expected_digest or identity.get("from_wire_sequence") != wire_from or identity.get("to_wire_sequence") != wire_to:
            raise ValueError("peer archive batch digest mismatch")
        committed = self.peer_index.has_batch(self.peer_id, identity)
        peer_state = self.peer_index.state(self.peer_id)
        previous = int(peer_state.get("last_wire_sequence", 0))
        summary_only = not records
        if not committed and not summary_only and (payload["previous_wire_sequence"] != previous or wire_from != previous + 1):
            raise ValueError("peer archive page has a sequence gap or replay")
        result = self.peer_index.ingest(self.peer_id, records, payload.get("summaries", []),
            batch_identity=identity, wire_sequence=wire_to)
        if not summary_only:
            state["peer_cursor"] = max(int(state.get("peer_cursor", 0)), wire_to)
        state["received_batches"] = int(state.get("received_batches", 0)) + (result != "no-change")
        self._state_save(state)
        ack_payload = {"format": FORMAT, "channel": "ack", "origin": self.local_node_id,
                       "target": self.peer_id, "status": "peer-imported",
                       "batch_identity": identity, "peer_wire_sequence": wire_to}
        ack = self._seal_payload(ack_payload, target=self.peer_id, kind="ack")
        self.ack_outbox.mkdir(parents=True, exist_ok=True)
        ack_path = safe_target(self.ack_outbox, path.name)
        if not ack_path.exists():
            atomic_replace_bytes(ack_path, ack)
        return result

    def sync_once(self):
        """Queue and receive at most configured pages; never invokes a scheduler."""
        self.outbox.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.state_lock):
            # Summary metadata is cached only for this bounded tick; later
            # completions must be visible to an otherwise idle raw stream.
            self.__dict__.pop("_summary_cache", None)
            state = self._state()
            ack_count = self._process_acks(state)
            file_ack_count = self._process_file_acks(state)
            received = 0
            if self.inbox.exists():
                # The sender's immutable outbox and our inbox are the same
                # shared file. Skip already-ACKed packages before the bounded
                # page slice so stale queue entries cannot starve later pages.
                candidates = [path for path in sorted(self.inbox.glob("*.mwe"))
                              if not safe_target(self.ack_outbox, path.name).exists()]
                for path in candidates[:self.max_batches]:
                    self._accept_one(path, state)
                    received += 1
            queued = []
            for _ in range(self.max_batches):
                item = self._export_one(state)
                if item is None:
                    break
                queued.append(item)
            self._state_save(state)
            return {**self.status(), "status": "ok", "last_run": {
                    "sent_batches": len(queued), "received_batches": received,
                    "acknowledged_batches": ack_count, "acknowledged_files": file_ack_count,
                    "queued": queued}}
