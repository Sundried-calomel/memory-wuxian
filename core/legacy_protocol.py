"""Bounded, integrity-only readers for explicitly selected legacy evidence bundles.

These parsers do not authenticate a writer. SHA-256 fields establish internal
consistency only; network envelopes must first pass legacy_crypto's real
decrypt/signature helper through LegacyBridge.convert_envelopes.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any


ARCHIVE_FORMAT = "memory-wuxian-delta-v1"
ENVIRONMENT_FORMAT = "memory-wuxian-environment-bundle-v1"
ATTACHMENT_FORMAT = "memory-wuxian-project-attachment-bundle-v1"
MAX_ARCHIVE_BYTES, MAX_ENV_BYTES, MAX_ATTACHMENT_BYTES = 512 * 1024 * 1024, 64 * 1024 * 1024, 32 * 1024 * 1024
MAX_MANIFEST_BYTES, MAX_ARCHIVE_ARTIFACTS = 1024 * 1024, 100_000
MAX_ENV_ARTIFACTS, MAX_ATTACHMENT_EVENTS = 256, 4
MAX_ARCHIVE_RATIO, MAX_ENV_RATIO, MAX_ATTACHMENT_RATIO = 1000, 200, 1000
MAX_ATTACHMENT_CHUNK = 4 * 1024 * 1024
MAX_ATTACHMENT_FILE = 256 * 1024 * 1024
MAX_ATTACHMENT_GENERATION = 1024 * 1024 * 1024
MAX_SKILL_PACKAGE = 64 * 1024 * 1024


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {value}")))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha(_canonical(value))


def _manifest_id(manifest: dict) -> str:
    return "mwb-" + _canonical_sha({key: value for key, value in manifest.items() if key != "bundle_id"})[:32]


def _node(value, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", value):
        raise ValueError("invalid legacy node identity")
    return value


def _safe_path(value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("legacy ZIP path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("legacy ZIP path is not normalized and relative")
    return path.as_posix()


def _zip_payload(data: bytes, *, expected_names: set[str], total_limit: int,
                 payload_limit: int, ratio_limit: int):
    if not isinstance(data, bytes) or not data or len(data) > total_limit:
        raise ValueError("legacy bundle is empty or exceeds its compressed-size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("legacy bundle is not a readable ZIP") from exc
    with archive:
        infos = archive.infolist()
        names = [_safe_path(item.filename) for item in infos]
        if len(infos) != len(expected_names) or len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError("legacy bundle ZIP inventory is unexpected")
        inventory = {item.filename: item for item in infos}
        uncompressed_total = 0
        for info in infos:
            if info.is_dir() or info.flag_bits & 0x1 or info.file_size < 0 or info.compress_size < 0:
                raise ValueError("legacy bundle contains a directory, encrypted member, or invalid size")
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ValueError("legacy bundle contains a symbolic-link member")
            uncompressed_total += info.file_size
            if info.filename == "manifest.json" and info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("legacy manifest exceeds its size limit")
            if info.filename != "manifest.json" and info.file_size > payload_limit:
                raise ValueError("legacy payload exceeds its size limit")
            if info.file_size and not info.compress_size:
                raise ValueError("legacy ZIP compression metadata is invalid")
            if info.compress_size and info.file_size / info.compress_size > ratio_limit:
                raise ValueError("legacy ZIP compression ratio exceeds limit")
        if uncompressed_total > total_limit:
            raise ValueError("legacy ZIP uncompressed size exceeds limit")
        manifest = _json(archive.read("manifest.json"))
        payload_name = next(name for name in expected_names if name != "manifest.json")
        payload = archive.read(payload_name)
    if not isinstance(manifest, dict):
        raise ValueError("legacy manifest must be an object")
    return manifest, payload


def _verify_manifest_payload(manifest: dict, payload: bytes):
    if type(manifest.get("payload_bytes")) is not int or manifest["payload_bytes"] != len(payload):
        raise ValueError("legacy payload length mismatch")
    if manifest.get("payload_sha256") != _sha(payload):
        raise ValueError("legacy payload SHA-256 mismatch")
    if manifest.get("bundle_id") != _manifest_id(manifest):
        raise ValueError("legacy manifest bundle_id mismatch")


def _jsonl(payload: bytes, count: int):
    if type(count) is not int or count < 0:
        raise ValueError("legacy artifact count is invalid")
    lines = [line for line in payload.splitlines() if line]
    if len(lines) != count:
        raise ValueError("legacy artifact count mismatch")
    records = [_json(line) for line in lines]
    if any(not isinstance(item, dict) for item in records):
        raise ValueError("legacy event rows must be objects")
    return records


def _archive_records(manifest, payload):
    if manifest.get("format") != ARCHIVE_FORMAT:
        raise ValueError("unsupported archive bundle format")
    protocol = manifest.get("protocol_version")
    if type(protocol) is not int or protocol not in {1, 2, 3}:
        raise ValueError("unsupported archive protocol version")
    origin = _node(manifest.get("origin_node_id"))
    target = _node(manifest.get("target_node_id"), optional=True)
    _verify_manifest_payload(manifest, payload)
    count = manifest.get("artifact_count")
    if type(count) is not int or not 0 <= count <= MAX_ARCHIVE_ARTIFACTS:
        raise ValueError("archive artifact count exceeds limit")
    records = _jsonl(payload, count)
    start, end, base = (manifest.get("from_event_sequence"), manifest.get("to_event_sequence"), manifest.get("base_event_sequence"))
    if any(type(value) is not int for value in (start, end, base)) or start < 1 or end < start or base < 0 or base + 1 != start or end - start + 1 != count:
        raise ValueError("archive sequence range is invalid")
    predecessor = manifest.get("previous_bundle_sha256")
    if (base == 0 and predecessor is not None) or (base > 0 and not re.fullmatch(r"[0-9a-f]{64}", str(predecessor or ""))):
        raise ValueError("archive predecessor metadata is invalid")
    seen = set()
    for offset, record in enumerate(records):
        if set(record) != {"event_sequence", "artifact_type", "artifact_id", "sha256", "payload"} or type(record["event_sequence"]) is not int or record["event_sequence"] != start + offset:
            raise ValueError("archive event fields or sequence are invalid")
        artifact_id, kind, item = record["artifact_id"], record["artifact_type"], record["payload"]
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in seen or not isinstance(item, dict):
            raise ValueError("archive artifact identity or payload is invalid")
        seen.add(artifact_id)
        if record["sha256"] != _canonical_sha(item):
            raise ValueError("archive artifact payload hash mismatch")
        if kind == "raw":
            expected = f"raw:{item.get('message_id', '')}"
        elif kind == "summary":
            summary = item.get("record")
            if not isinstance(summary, dict):
                raise ValueError("summary artifact record is missing")
            expected = f"summary:{summary.get('summary_id', '')}"
        elif kind == "title":
            expected = f"title:{_canonical_sha(item)}"
        elif kind == "token-usage" and protocol >= 2:
            if item.get("measurement") != "codex-reported-model-usage" or not item.get("session_id") or not isinstance(item.get("daily_usage"), dict):
                raise ValueError("token-usage artifact shape is invalid")
            session_digest = hashlib.sha256(str(item["session_id"]).encode()).hexdigest()[:24]
            expected = f"token-usage:{session_digest}:{_canonical_sha(item)}"
        elif kind == "summary-v2" and protocol >= 3:
            completion = item.get("completion")
            if not isinstance(completion, dict) or not isinstance(completion.get("bundle"), dict):
                raise ValueError("Summary V2 completion is invalid")
            try:
                sj = base64.b64decode(item["summary_json_base64"], validate=True)
                sm = base64.b64decode(item["summary_markdown_base64"], validate=True)
                sidecar = _json(sj)
            except (KeyError, ValueError) as exc:
                raise ValueError("Summary V2 content is invalid") from exc
            bundle = completion["bundle"]
            if (_sha(sj) != bundle.get("summary_json_sha256") or _sha(sm) != bundle.get("summary_markdown_sha256")
                or sidecar.get("summary_v2_id") != bundle.get("summary_v2_id")
                or sidecar.get("parallel_summary_id") != completion.get("target_summary_id")
                or sidecar.get("conversation_id") != completion.get("conversation_id")
                or sidecar.get("summary_level") != completion.get("summary_level")):
                raise ValueError("Summary V2 bundle bytes do not match completion")
            expected = f"summary-v2:{completion.get('target_summary_id', '')}"
        else:
            raise ValueError(f"unsupported archive artifact type: {kind}")
        if artifact_id != expected:
            raise ValueError("archive artifact id does not match payload")
    return {"kind": "archive", "origin_node_id": origin, "target_node_id": target,
            "stream_id": "archive-v1", "bundle_id": manifest["bundle_id"],
            "from_event_sequence": start, "to_event_sequence": end,
            "base_event_sequence": base, "previous_bundle_sha256": manifest.get("previous_bundle_sha256"),
            "events": records}


def _validate_attachment_manifest(value):
    required = {"format", "schema_version", "generation_id", "project_id", "title", "conversation_ids", "files", "total_bytes", "chunk_bytes"}
    if not isinstance(value, dict) or set(value) != required or value.get("format") != "memory-wuxian-project-attachment-manifest-v1" or value.get("schema_version") != 1:
        raise ValueError("project attachment manifest fields/format are invalid")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", str(value["project_id"])) or not isinstance(value["title"], str) or not value["title"].strip() or len(value["title"]) > 240:
        raise ValueError("project attachment project or title is invalid")
    conversations = value["conversation_ids"]
    if not isinstance(conversations, list) or len(conversations) > 32 or conversations != sorted(set(conversations)):
        raise ValueError("project attachment conversation list is invalid")
    if any(not isinstance(cid, str) or not re.fullmatch(r"[a-z0-9._-]+:[^\r\n]{1,240}", cid) for cid in conversations):
        raise ValueError("project attachment conversation id is invalid")
    files = value["files"]
    if not isinstance(files, list) or not files or len(files) > 256:
        raise ValueError("project attachment file list is invalid")
    total, previous = 0, None
    suffixes = {".pdf", ".pptx", ".docx", ".xlsx", ".xls", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp", ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv"}
    roles = {"source-paper", "supplement", "presentation", "report", "figure", "table", "other-deliverable", "project-rule", "status", "next-plan", "decision", "qa", "daily-report", "weekly-report", "phase-report", "template", "artifact-index", "other-evidence"}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "role", "byte_length", "sha256", "chunks"}:
            raise ValueError("project attachment file fields are invalid")
        path = _safe_path(item["path"])
        if path != item["path"] or (previous is not None and path <= previous) or PurePosixPath(path).suffix.lower() not in suffixes or item["role"] not in roles:
            raise ValueError("project attachment file ordering/type is invalid")
        previous = path
        length = item["byte_length"]
        if type(length) is not int or not 0 <= length <= MAX_ATTACHMENT_FILE or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
            raise ValueError("project attachment file metadata is invalid")
        chunks = item["chunks"]
        if not isinstance(chunks, list) or (length and not chunks):
            raise ValueError("project attachment chunks are missing")
        offset = 0
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict) or set(chunk) != {"index", "offset", "byte_length", "sha256"}:
                raise ValueError("project attachment chunk descriptor is invalid")
            clen = chunk["byte_length"]
            if type(chunk["index"]) is not int or chunk["index"] != index or type(chunk["offset"]) is not int or chunk["offset"] != offset or type(clen) is not int or not 0 < clen <= MAX_ATTACHMENT_CHUNK or not re.fullmatch(r"[0-9a-f]{64}", str(chunk["sha256"])):
                raise ValueError("project attachment chunk descriptor is invalid")
            offset += clen
        if offset != length:
            raise ValueError("project attachment chunks do not cover file")
        total += length
    if total != value["total_bytes"] or total > MAX_ATTACHMENT_GENERATION or value["chunk_bytes"] != MAX_ATTACHMENT_CHUNK:
        raise ValueError("project attachment generation size/chunk configuration is invalid")
    identity = {key: item for key, item in value.items() if key != "generation_id"}
    if value["generation_id"] != "project-attachment:" + _canonical_sha(identity):
        raise ValueError("project attachment generation identity mismatch")


def _environment_records(manifest, payload):
    if manifest.get("format") != ENVIRONMENT_FORMAT or manifest.get("stream_id") != "environment-v1" or type(manifest.get("protocol_version")) is not int or manifest.get("protocol_version") != 1:
        raise ValueError("unsupported Environment bundle format/protocol")
    origin, target = _node(manifest.get("origin_node_id")), _node(manifest.get("target_node_id"), optional=True)
    _verify_manifest_payload(manifest, payload)
    count = manifest.get("artifact_count")
    if type(count) is not int or not 0 <= count <= MAX_ENV_ARTIFACTS:
        raise ValueError("Environment artifact count exceeds limit")
    records = _jsonl(payload, count)
    start, end, base = manifest.get("from_event_sequence"), manifest.get("to_event_sequence"), manifest.get("base_event_sequence")
    if any(type(value) is not int for value in (start, end, base)) or start < 1 or end - start + 1 != count or base < 0 or base + 1 != start:
        raise ValueError("Environment sequence range is invalid")
    predecessor = manifest.get("previous_bundle_sha256")
    if (base == 0 and predecessor is not None) or (base > 0 and not re.fullmatch(r"[0-9a-f]{64}", str(predecessor or ""))):
        raise ValueError("Environment predecessor metadata is invalid")
    for offset, event in enumerate(records):
        if type(event.get("event_sequence")) is not int or event.get("event_sequence") != start + offset or not isinstance(event.get("source_event_id"), str):
            raise ValueError("Environment event sequence/source identity is invalid")
        kind = event.get("event_kind")
        if kind == "personal-environment-profile":
            expected = {"event_sequence", "source_event_id", "event_kind", "profile_id", "payload_sha256", "payload"}
            p = event.get("payload")
            if set(event) != expected or not isinstance(p, dict) or not isinstance(p.get("profile"), dict) or event["profile_id"] != p["profile"].get("profile_id") or event["payload_sha256"] != _canonical_sha(p):
                raise ValueError("Environment profile event integrity/identity mismatch")
        elif kind == "project-registration":
            expected = {"event_sequence", "source_event_id", "event_kind", "project_id", "payload_sha256", "payload"}
            p = event.get("payload")
            if set(event) != expected or not isinstance(p, dict) or set(p) != {"project"} or not isinstance(p["project"], dict) or event["project_id"] != p["project"].get("project_id") or event["payload_sha256"] != _canonical_sha(p):
                raise ValueError("Environment project registration event mismatch")
        elif kind in {None, "artifact-revision"}:
            expected = {"event_sequence", "source_event_id", "artifact_id", "revision_id", "payload_sha256", "payload"}
            if kind is not None:
                expected.add("event_kind")
            p = event.get("payload")
            if set(event) != expected or not isinstance(p, dict) or set(p) != {"artifact", "revision", "content_base64", "package_attachment"} or event["payload_sha256"] != _canonical_sha(p):
                raise ValueError("Environment artifact revision fields/hash are invalid")
            artifact, revision = p["artifact"], p["revision"]
            if not isinstance(artifact, dict) or not isinstance(revision, dict) or event["artifact_id"] != artifact.get("artifact_id") or event["revision_id"] != revision.get("revision_id") or revision.get("artifact_id") != artifact.get("artifact_id"):
                raise ValueError("Environment artifact/revision identity mismatch")
            try:
                content = base64.b64decode(p["content_base64"], validate=True)
            except Exception as exc:
                raise ValueError("Environment content base64 is invalid") from exc
            if _sha(content) != revision.get("content_sha256"):
                raise ValueError("Environment content hash mismatch")
            attachment = p["package_attachment"]
            if artifact.get("object_class", "").endswith("-skill"):
                if not isinstance(attachment, dict) or set(attachment) != {"package_sha256", "content_base64"}:
                    raise ValueError("Skill package attachment is invalid")
                package = base64.b64decode(attachment["content_base64"], validate=True)
                if len(package) > MAX_SKILL_PACKAGE or _sha(package) != attachment["package_sha256"]:
                    raise ValueError("Skill package bytes/hash invalid")
            elif attachment is not None:
                raise ValueError("non-Skill revision carries a package attachment")
        else:
            raise ValueError(f"unsupported Environment event kind: {kind}")
    return {"kind": "environment", "origin_node_id": origin, "target_node_id": target,
            "stream_id": "environment-v1", "bundle_id": manifest["bundle_id"],
            "from_event_sequence": start, "to_event_sequence": end,
            "base_event_sequence": base, "previous_bundle_sha256": manifest.get("previous_bundle_sha256"),
            "events": records}


def _attachment_records(manifest, payload):
    required = {"format", "protocol_version", "stream_id", "origin_node_id", "target_node_id", "base_event_sequence", "previous_bundle_sha256", "from_event_sequence", "to_event_sequence", "artifact_count", "payload_path", "payload_bytes", "payload_sha256", "bundle_id"}
    if set(manifest) != required or manifest.get("format") != ATTACHMENT_FORMAT or manifest.get("protocol_version") != 1 or manifest.get("stream_id") != "project-attachment-v1":
        raise ValueError("project attachment bundle manifest fields/format are unsupported")
    origin, target = _node(manifest["origin_node_id"]), _node(manifest["target_node_id"])
    _verify_manifest_payload(manifest, payload)
    if manifest.get("payload_path") != "payload/project-attachments.jsonl":
        raise ValueError("project attachment payload path mismatch")
    count, first, last, base = (manifest.get("artifact_count"), manifest.get("from_event_sequence"), manifest.get("to_event_sequence"), manifest.get("base_event_sequence"))
    if any(type(v) is not int for v in (count, first, last, base)) or not 0 < count <= MAX_ATTACHMENT_EVENTS or base < 0 or first != base + 1 or last - first + 1 != count:
        raise ValueError("project attachment sequence range invalid")
    predecessor = manifest["previous_bundle_sha256"]
    if (base == 0 and predecessor is not None) or (base > 0 and not re.fullmatch(r"[0-9a-f]{64}", str(predecessor or ""))):
        raise ValueError("project attachment predecessor invalid")
    if manifest["bundle_id"] != _manifest_id(manifest):
        raise ValueError("project attachment bundle identity mismatch")
    events = _jsonl(payload, count)
    for offset, event in enumerate(events):
        if set(event) != {"event_sequence", "source_event_id", "event_kind", "payload", "payload_sha256"} or event["event_sequence"] != first + offset or event["payload_sha256"] != _canonical_sha(event["payload"]):
            raise ValueError("project attachment event shape/hash/sequence invalid")
        item = event["payload"]
        if event["event_kind"] == "project-attachment-chunk":
            if not isinstance(item, dict) or set(item) != {"sha256", "byte_length", "content_base64"}:
                raise ValueError("project attachment chunk event fields invalid")
            chunk = base64.b64decode(item["content_base64"], validate=True)
            if not 0 < len(chunk) <= MAX_ATTACHMENT_CHUNK or len(chunk) != item["byte_length"] or _sha(chunk) != item["sha256"] or event["source_event_id"] != f"project-attachment-chunk:{item['sha256']}":
                raise ValueError("project attachment chunk bytes/hash mismatch")
        elif event["event_kind"] == "project-attachment-manifest":
            _validate_attachment_manifest(item)
            if event["source_event_id"] != item["generation_id"]:
                raise ValueError("project attachment manifest event identity mismatch")
        else:
            raise ValueError("unsupported project attachment event kind")
    return {"kind": "project-attachment", "origin_node_id": origin, "target_node_id": target,
            "stream_id": "project-attachment-v1", "bundle_id": manifest["bundle_id"],
            "from_event_sequence": first, "to_event_sequence": last,
            "base_event_sequence": base, "previous_bundle_sha256": predecessor,
            "events": events}


def parse_local_bundle(data: bytes, expected_kind: str | None = None) -> dict:
    """Parse an explicitly supplied local legacy ZIP; hashes do not authenticate origin."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("explicit local migration input must be nonempty bytes")
    # Probe only the bounded manifest inventory first; format selects the exact stream reader.
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("legacy bundle exceeds maximum compressed size")
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            names = {_safe_path(item.filename) for item in infos}
            if "manifest.json" not in names or len(infos) > 4:
                raise ValueError("legacy bundle inventory is invalid")
            info = archive.getinfo("manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("legacy manifest exceeds size limit")
            manifest = _json(archive.read("manifest.json"))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("legacy bundle is not a readable ZIP") from exc
    fmt = manifest.get("format") if isinstance(manifest, dict) else None
    if fmt == ARCHIVE_FORMAT:
        kind = "archive"
        manifest, payload = _zip_payload(data, expected_names={"manifest.json", "payload/artifacts.jsonl"}, total_limit=MAX_ARCHIVE_BYTES, payload_limit=MAX_ARCHIVE_BYTES, ratio_limit=MAX_ARCHIVE_RATIO)
        result = _archive_records(manifest, payload)
    elif fmt == ENVIRONMENT_FORMAT:
        kind = "environment"
        manifest, payload = _zip_payload(data, expected_names={"manifest.json", "payload/environment.jsonl"}, total_limit=MAX_ENV_BYTES, payload_limit=MAX_ENV_BYTES, ratio_limit=MAX_ENV_RATIO)
        result = _environment_records(manifest, payload)
    elif fmt == ATTACHMENT_FORMAT:
        kind = "project-attachment"
        manifest, payload = _zip_payload(data, expected_names={"manifest.json", "payload/project-attachments.jsonl"}, total_limit=MAX_ATTACHMENT_BYTES, payload_limit=MAX_ATTACHMENT_BYTES, ratio_limit=MAX_ATTACHMENT_RATIO)
        result = _attachment_records(manifest, payload)
    else:
        raise ValueError("unknown legacy bundle format")
    if expected_kind is not None and expected_kind != kind:
        raise ValueError("legacy bundle kind does not match requested migration type")
    return {"schema": "legacy-normalized-v1", "integrity": "self-consistency-verified", "authenticated": False, **result}


def _package_path(value):
    path = _safe_path(value)
    if any(part.endswith((" ", ".")) or part.split(".")[0].casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))} for part in PurePosixPath(path).parts):
        raise ValueError("unsafe Skill package path")
    return path


def skill_attachment_files(attachment: dict, *, expected_revision: str) -> dict[str, bytes]:
    """Verify package ZIP layout and declared file hashes, returning bytes without extraction."""
    if not isinstance(attachment, dict) or set(attachment) != {"package_sha256", "content_base64"}:
        raise ValueError("Skill package attachment fields are invalid")
    package = base64.b64decode(attachment["content_base64"], validate=True)
    if len(package) > MAX_SKILL_PACKAGE or _sha(package) != attachment["package_sha256"]:
        raise ValueError("Skill package attachment size/hash mismatch")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Skill attachment is not a ZIP package") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 4096:
            raise ValueError("Skill package entry count exceeds limit")
        entries, folded, total = {}, set(), 0
        for info in infos:
            total += info.file_size
            if (info.file_size > MAX_SKILL_PACKAGE or total > MAX_SKILL_PACKAGE
                    or (info.file_size and not info.compress_size)
                    or (info.compress_size and info.file_size / info.compress_size > 200)):
                raise ValueError("Skill package expansion exceeds limit")
            name = info.filename.rstrip("/") if info.is_dir() else info.filename
            if not name:
                continue
            name = _package_path(name)
            key = name.casefold()
            if name in entries or key in folded or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("duplicate, case-colliding or linked Skill package entry")
            entries[name] = info
            folded.add(key)
        manifest_info = entries.get("skill-package-manifest.json")
        if manifest_info is None or manifest_info.is_dir() or manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("Skill package manifest is missing or oversized")
        manifest = _json(archive.read(manifest_info))
        required = {"schema_version", "skill_id", "version", "scope", "project_id", "source_revision", "files", "supported_platforms", "runtime_requirements", "network_access", "persistent_components", "rollback"}
        if not isinstance(manifest, dict) or set(manifest) - (required | {"checks"}) or not required.issubset(manifest) or manifest["schema_version"] != 1:
            raise ValueError("Skill package manifest fields are invalid")
        if manifest["source_revision"] != expected_revision or not re.fullmatch(r"rev:[0-9a-f]{64}", str(expected_revision)):
            raise ValueError("Skill package source revision mismatch")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", str(manifest["skill_id"])):
            raise ValueError("Skill package skill_id is invalid")
        if manifest["scope"] not in {"global", "project"} or (manifest["scope"] == "global" and manifest["project_id"] is not None) or (manifest["scope"] == "project" and (not isinstance(manifest["project_id"], str) or len(manifest["project_id"]) < 3)):
            raise ValueError("Skill package scope/project identity is invalid")
        declarations = manifest["files"]
        if not isinstance(declarations, list) or not declarations:
            raise ValueError("Skill package file declarations are invalid")
        declared = {}
        for item in declarations:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256", "executable"}:
                raise ValueError("Skill package file declaration is invalid")
            path = _package_path(item["path"])
            if path in declared or type(item["size"]) is not int or item["size"] < 0 or type(item["executable"]) is not bool or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
                raise ValueError("Skill package file declaration values are invalid")
            declared[path] = item
        if "SKILL.md" not in declared:
            raise ValueError("Skill package must declare SKILL.md")
        if not isinstance(manifest["supported_platforms"], list) or not manifest["supported_platforms"] or any(p not in {"windows", "macos", "linux"} for p in manifest["supported_platforms"]):
            raise ValueError("Skill package supported platforms are invalid")
        if not isinstance(manifest["runtime_requirements"], dict) or not isinstance(manifest["network_access"], dict) or not isinstance(manifest["persistent_components"], list) or not isinstance(manifest["rollback"], dict):
            raise ValueError("Skill package contract fields are invalid")
        actual_files = {name for name, info in entries.items() if not info.is_dir() and name != "skill-package-manifest.json"}
        if actual_files != set(declared):
            raise ValueError("Skill ZIP files do not match manifest declarations")
        output = {}
        for path, declaration in declared.items():
            data = archive.read(entries[path])
            if len(data) != declaration["size"] or _sha(data) != declaration["sha256"]:
                raise ValueError(f"Skill package file integrity mismatch: {path}")
            output[path] = data
    return output


def reconstruct_project_attachment_files(events: list[dict], *, generation_id: str | None = None,
                                          max_total_bytes: int = 64 * 1024 * 1024) -> dict:
    """Rebuild one attachment generation from its selected chunk and manifest events."""
    chunks, manifests = {}, []
    for event in events:
        kind, payload = event.get("event_kind"), event.get("payload")
        if kind == "project-attachment-chunk":
            data = base64.b64decode(payload["content_base64"], validate=True)
            if len(data) != payload.get("byte_length") or _sha(data) != payload.get("sha256"):
                raise ValueError("project attachment chunk content mismatch")
            prior = chunks.get(payload["sha256"])
            if prior is not None and prior != data:
                raise ValueError("project attachment duplicate chunk conflict")
            chunks[payload["sha256"]] = data
        elif kind == "project-attachment-manifest":
            _validate_attachment_manifest(payload)
            manifests.append(payload)
    selected = [item for item in manifests if generation_id is None or item["generation_id"] == generation_id]
    if len(selected) != 1:
        raise ValueError("requested project attachment generation is missing or ambiguous")
    manifest = selected[0]
    if type(max_total_bytes) is not int or not 0 <= max_total_bytes <= MAX_ATTACHMENT_GENERATION or manifest["total_bytes"] > max_total_bytes:
        raise ValueError("project attachment generation exceeds reconstruction memory bound")
    files = {}
    for item in manifest["files"]:
        content_parts = []
        for descriptor in item["chunks"]:
            chunk = chunks.get(descriptor["sha256"])
            if chunk is None or len(chunk) != descriptor["byte_length"]:
                raise ValueError("project attachment generation is missing a required chunk")
            content_parts.append(chunk)
        data = b"".join(content_parts)
        if len(data) != item["byte_length"] or _sha(data) != item["sha256"]:
            raise ValueError("reconstructed project attachment file hash mismatch")
        files[item["path"]] = data
    return {"manifest": manifest, "files": files}


def extract_environment_skill_files(events: list[dict], *, artifact_id: str, revision_id: str) -> dict[str, bytes]:
    """Return declared Skill package files for one explicit Environment artifact revision."""
    matches = []
    for event in events:
        payload = event.get("payload")
        if event.get("event_kind") not in {None, "artifact-revision"} or not isinstance(payload, dict):
            continue
        artifact, revision = payload.get("artifact"), payload.get("revision")
        if (isinstance(artifact, dict) and isinstance(revision, dict)
                and artifact.get("artifact_id") == artifact_id
                and revision.get("revision_id") == revision_id):
            matches.append((artifact, revision, payload.get("package_attachment")))
    if len(matches) != 1:
        raise ValueError("selected Skill artifact/revision is missing or ambiguous")
    artifact, revision, attachment = matches[0]
    if not str(artifact.get("object_class", "")).endswith("-skill"):
        raise ValueError("selected artifact is not a Skill")
    return skill_attachment_files(attachment, expected_revision=revision_id)


def environment_files(event: dict) -> dict[str, bytes]:
    """Materialize exactly one verified Environment artifact revision, without applying it."""
    if not isinstance(event, dict):
        raise ValueError("Environment event must be an object")
    payload = event.get("payload", event)
    if not isinstance(payload, dict) or not isinstance(payload.get("artifact"), dict) or not isinstance(payload.get("revision"), dict):
        raise ValueError("selected Environment event is not an artifact revision")
    if "payload_sha256" in event and event["payload_sha256"] != _canonical_sha(payload):
        raise ValueError("Environment event payload hash mismatch")
    artifact, revision = payload["artifact"], payload["revision"]
    if artifact.get("artifact_id") != revision.get("artifact_id") or not revision.get("revision_id"):
        raise ValueError("Environment artifact/revision binding is invalid")
    if artifact.get("object_class", "").endswith("-skill"):
        return skill_attachment_files(payload.get("package_attachment"), expected_revision=revision["revision_id"])
    try:
        content = base64.b64decode(payload["content_base64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise ValueError("Environment revision content encoding is invalid") from exc
    if _sha(content) != revision.get("content_sha256"):
        raise ValueError("Environment revision content hash mismatch")
    return {"content.bin": content}


def attachment_files(events_or_bundle, generation_id: str | None = None,
                     *, max_total_bytes: int = 64 * 1024 * 1024) -> dict[str, bytes]:
    """Select and reconstruct one project attachment generation from validated event rows.

    Pass all chunk/manifest events in the selected origin stream when chunks span bundles.
    The generation may be selected by the argument or by bundle.manifest.
    """
    events = events_or_bundle
    if isinstance(events_or_bundle, dict):
        events = events_or_bundle.get("events", [events_or_bundle])
        generation_id = generation_id or events_or_bundle.get("generation_id")
        candidate = events_or_bundle.get("manifest")
        if isinstance(candidate, dict):
            generation_id = generation_id or candidate.get("generation_id")
    result = reconstruct_project_attachment_files(
        events, generation_id=generation_id, max_total_bytes=max_total_bytes)
    return result["files"]
