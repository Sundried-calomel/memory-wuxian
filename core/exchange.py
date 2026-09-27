"""Small signed exchange API for the new assembly storage contract.

This envelope is intentionally distinct from the installed Memory Wuxian protocol.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import base64
import os
from typing import Any
from pathlib import Path

from storage import atomic_replace_bytes, atomic_write_json, bytes_sha256, canonical_json_bytes, exclusive_lock

FORMAT = "assembly-six-paths.exchange.v1"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class ExchangeService:
    def __init__(self, store, key: bytes, node_id: str):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("exchange key must be explicitly configured and at least 32 bytes")
        if not node_id or not isinstance(node_id, str):
            raise ValueError("node_id is required")
        self.store, self.key, self.node_id = store, key, node_id

    def _seal(self, kind: str, payload: dict, from_sequence=None, to_sequence=None) -> bytes:
        unsigned = {"format": FORMAT, "origin": self.node_id, "kind": kind,
                    "from_sequence": from_sequence, "to_sequence": to_sequence,
                    "payload": payload, "payload_sha256": bytes_sha256(canonical_json_bytes(payload))}
        signature = hmac.new(self.key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
        package = canonical_json_bytes({**unsigned, "signature": signature})
        if len(package) > MAX_PACKAGE_BYTES:
            raise ValueError("exchange package exceeds size limit")
        return package

    def export(self, after_sequence: int = 0) -> bytes:
        if not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a nonnegative integer")
        snapshot = self.store.export_snapshot(after_sequence=after_sequence)
        records = [r for r in snapshot["records"] if int(r.get("sequence", 0)) > after_sequence]
        records.sort(key=lambda r: int(r.get("sequence", 0)))
        seq = [int(r["sequence"]) for r in records]
        if any(v < 1 for v in seq) or len(seq) != len(set(seq)):
            raise ValueError("source records have invalid or duplicate sequence numbers")
        if seq and seq != list(range(after_sequence + 1, seq[-1] + 1)):
            raise ValueError("source records do not form a contiguous exchange page")
        payload = {"records": records, "summaries": snapshot["summaries"]}
        if 'legacy_artifacts' in snapshot:
            payload['legacy_artifacts'] = snapshot['legacy_artifacts']
        for summary in payload["summaries"]:
            if not isinstance(summary, dict) or not isinstance(summary.get("id"), str):
                raise ValueError("summary record is missing its id")
        return self._seal("archive", payload, after_sequence + 1, seq[-1] if seq else after_sequence)

    def export_files(self, files: dict[str, bytes], legacy_artifacts=None) -> bytes:
        if not isinstance(files, dict) or not files:
            raise ValueError("environment file payload must be a nonempty mapping")
        entries = {}
        for name, content in files.items():
            path = Path(name)
            if not isinstance(name, str) or not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError("unsafe environment payload path")
            if not isinstance(content, bytes):
                raise ValueError("environment payload values must be exact bytes")
            entries[name] = {"base64": base64.b64encode(content).decode("ascii"),
                             "sha256": bytes_sha256(content)}
        payload = {'files':entries}
        if legacy_artifacts is not None:
            payload['legacy_artifacts'] = legacy_artifacts
        return self._seal("environment", payload)

    def receive(self, package: bytes, expected_origin: str | None = None, expected_kind: str | None = None) -> dict[str, Any]:
        if not isinstance(package, bytes) or len(package) > MAX_PACKAGE_BYTES:
            raise ValueError("invalid or oversized package")
        try:
            envelope = json.loads(package.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("package is not UTF-8 JSON") from exc
        fields = {"format", "origin", "kind", "from_sequence", "to_sequence", "payload", "payload_sha256", "signature"}
        if not isinstance(envelope, dict) or set(envelope) != fields or envelope["format"] != FORMAT:
            raise ValueError("unsupported exchange envelope")
        origin, payload = envelope["origin"], envelope["payload"]
        if not isinstance(origin, str) or not origin or (origin == self.node_id and envelope['kind'] == 'archive'):
            raise ValueError("invalid peer origin")
        if expected_origin is not None and origin != expected_origin:
            raise ValueError("unexpected peer origin")
        if not isinstance(payload, dict):
            raise ValueError("invalid exchange payload")
        digest = bytes_sha256(canonical_json_bytes(payload))
        unsigned = {k: envelope[k] for k in ("format", "origin", "kind", "from_sequence", "to_sequence", "payload", "payload_sha256")}
        expected = hmac.new(self.key, canonical_json_bytes(unsigned), hashlib.sha256).hexdigest()
        if digest != envelope["payload_sha256"] or not hmac.compare_digest(expected, str(envelope["signature"])):
            raise ValueError("exchange integrity or signature check failed")
        if expected_kind is not None and envelope['kind'] != expected_kind:
            raise ValueError('unexpected package kind')
        if envelope["kind"] == "environment":
            if 'files' not in payload or set(payload)-{'files','legacy_artifacts'} or not isinstance(payload["files"], dict):
                raise ValueError("invalid environment file payload")
            files = {}
            for name, entry in payload["files"].items():
                path = Path(name)
                if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise ValueError("unsafe environment payload path")
                if not isinstance(entry, dict) or set(entry) != {"base64", "sha256"}:
                    raise ValueError("invalid environment file entry")
                data = base64.b64decode(entry["base64"], validate=True)
                if bytes_sha256(data) != entry["sha256"]:
                    raise ValueError("environment file hash mismatch")
                files[name] = data
            return {"status": "verified-files", "origin": origin, "files": files,
                    "payload_sha256": digest, "protocol": FORMAT,
                    "legacy_artifacts":payload.get('legacy_artifacts',{})}
        if envelope["kind"] != "archive" or not {'records','summaries'} <= set(payload) or set(payload)-{'records','summaries','legacy_artifacts'}:
            raise ValueError("unsupported payload kind/schema")
        if not isinstance(payload["records"], list) or not isinstance(payload["summaries"], list):
            raise ValueError("invalid payload collections")
        start, end = envelope["from_sequence"], envelope["to_sequence"]
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start - 1:
            raise ValueError("invalid archive page interval")
        cursor_path = Path(self.store.root) / ".exchange-peer-cursors.json"
        with exclusive_lock(cursor_path.with_suffix(".lock")):
            cursors = json.loads(cursor_path.read_text("utf-8")) if cursor_path.exists() else {"schema": 1, "peers": {}}
            expected_start = int(cursors["peers"].get(origin, 0)) + 1
            if start != expected_start:
                if end < expected_start:
                    from storage import safe_target
                    replica=safe_target(self.store.root,'replicas/'+bytes_sha256(origin.encode())+'.json')
                    existing=json.loads(replica.read_text('utf-8')) if replica.exists() else {}
                    raw_by_id={r['message_id']:r for r in existing.get('records',[])}
                    summary_by_id={r['id']:r for r in existing.get('summaries',[])}
                    if (all(raw_by_id.get(r['message_id'])==r for r in payload['records'])
                        and all(summary_by_id.get(r['id'])==r for r in payload['summaries'])
                        and all(existing.get('legacy_artifacts',{}).get(k)==v for k,v in payload.get('legacy_artifacts',{}).items())):
                        return {'status':'duplicate','origin':origin,'payload_sha256':digest,'protocol':FORMAT}
                raise ValueError(f"archive page is not the next peer cursor: expected {expected_start}, got {start}")
            seq = [int(r.get("sequence", 0)) for r in payload["records"]]
            if seq != list(range(start, end + 1)):
                if not (not seq and end == start - 1):
                    raise ValueError("archive page contains a gap or sequence mismatch")
            imported = self.store.import_peer(origin, payload)
            cursors["peers"][origin] = end
            atomic_write_json(cursor_path, cursors)
        return {"status": "verified-import", "origin": origin, "payload_sha256": digest,
                "imported": imported, "protocol": FORMAT}

    def send(self, path, after_sequence: int = 0) -> dict:
        """Write a signed archive package to a caller-selected local file queue path."""
        data = self.export(after_sequence)
        atomic_replace_bytes(path, data)
        return {"path": str(path), "sha256": bytes_sha256(data), "bytes": len(data)}

    def receive_file(self, path, expected_origin: str | None = None) -> dict:
        return self.receive(Path(path).read_bytes(), expected_origin=expected_origin)


class CommandCryptoAdapter:
    """Explicit adapter for a caller-provided existing crypto command; never auto-selected."""
    def __init__(self, crypto):
        if not callable(getattr(crypto, "seal", None)) or not callable(getattr(crypto, "open", None)):
            raise TypeError("crypto adapter requires explicit seal/open implementation")
        self.crypto = crypto

    def seal(self, *args, **kwargs):
        return self.crypto.seal(*args, **kwargs)

    def open(self, *args, **kwargs):
        return self.crypto.open(*args, **kwargs)
