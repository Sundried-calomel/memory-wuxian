#!/usr/bin/env python3
"""Memory Plane ownership for deterministic index reconstruction."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from platform_transaction import (
    INDEX_GENERATION_FORMAT,
    INDEX_GENERATION_FORMAT_VERSION,
    PlatformTransactionError,
    atomic_write_canonical_json,
    canonical_json_bytes,
    index_generation_id,
    read_canonical_json,
    read_index_generation,
    source_manifest_sha256,
    write_index_generation,
)


GENERATION_FORMAT_VERSION = INDEX_GENERATION_FORMAT_VERSION
GENERATION_BUILDER = "memory-indexing-v2.6"
GENERATION_MANIFEST = "manifest.json"
ACTIVE_GENERATION_POINTER = "active-generation.json"
ACTIVE_POINTER_FORMAT = "memory-wuxian-active-index-generation-v1"


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw_record_sha256(record: Dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"_path", "content_sha256"}
    }
    return _canonical_sha256(payload)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(f"Source path escapes archive root: {path}") from exc


def _generation_root(store: Any) -> Path:
    return store.index_dir / "generations"


def _pointer_path(store: Any) -> Path:
    return store.index_dir / ACTIVE_GENERATION_POINTER


def _write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    _atomic_write_text(path, text)


def rebuild_indexes(store: Any, apply: bool) -> Dict[str, Any]:
    """Rebuild the existing index layout without changing its public contract."""
    store.init()
    raw_records = store.read_all_raw()
    summaries = store.summary_records_from_files()
    summaries_by_id = {summary["summary_id"]: summary for summary in summaries}
    integrity_issues = []
    for record in raw_records:
        stored_digest = record.get("content_sha256")
        if stored_digest and stored_digest != _raw_record_sha256(record):
            integrity_issues.append(
                f"raw content SHA-256 mismatch: {record['message_id']}"
            )
    for summary in summaries:
        actual_source_sha256 = store.actual_summary_source_sha256(
            summary, raw_records, summaries_by_id
        )
        if (
            summary.get("source_sha256")
            and summary["source_sha256"] != actual_source_sha256
        ):
            integrity_issues.append(
                f"summary source SHA-256 mismatch: {summary['summary_id']}"
            )
        if not summary.get("source_sha256"):
            summary["source_sha256"] = actual_source_sha256

    try:
        existing_summary_index = store.summary_records()
    except ValueError:
        existing_summary_index = []
    for existing in existing_summary_index:
        summary = summaries_by_id.get(existing["summary_id"])
        expected_digest = existing.get("summary_sha256")
        if summary and expected_digest and expected_digest != summary["summary_sha256"]:
            integrity_issues.append(
                f"summary SHA-256 mismatch: {existing['summary_id']}"
            )
    integrity_issues = list(dict.fromkeys(integrity_issues))
    if apply and integrity_issues:
        raise RuntimeError(
            "Refusing to rebuild indexes over integrity failures: "
            + "; ".join(integrity_issues)
        )

    conversations = []
    for record in raw_records:
        index_record = {
            key: value
            for key, value in record.items()
            if key not in {"text", "_path"}
        }
        index_record["content_sha256"] = (
            record.get("content_sha256") or _raw_record_sha256(record)
        )
        index_record["path"] = record["_path"]
        index_record["conversation_path"] = store.relative(
            store.conversation_transcript_path(str(record["conversation_id"]))
        )
        conversations.append(index_record)

    registry = []
    concepts = []
    policies = []
    timeline_lines = ["# Timeline Index", ""]
    concept_lines = ["# Concept Index", ""]
    for summary in summaries:
        source_signature = (
            "children:" + ",".join(summary.get("source_summaries", []))
            if int(summary["level"]) > 1
            else f"messages:{summary.get('source_start')}-{summary.get('source_end')}"
        )
        registry.append(
            {
                "event": "created",
                "summary_id": summary["summary_id"],
                "level": summary["level"],
                "conversation_id": summary.get("conversation_id"),
                "path": summary["path"],
                "source_signature": source_signature,
                "source_sha256": summary.get("source_sha256"),
                "summary_sha256": summary["summary_sha256"],
                "timestamp": summary.get("created_at"),
            }
        )
        for child_id in summary.get("source_summaries", []):
            registry.append(
                {
                    "event": "grouped",
                    "child_summary_id": child_id,
                    "parent_summary_id": summary["summary_id"],
                    "timestamp": summary.get("created_at"),
                }
            )
        date = str(summary.get("start_time") or "unknown").split("T", 1)[0]
        topics = ", ".join(summary["topics"]) or "No topics recorded"
        timeline_lines.extend(
            [
                f"## {date}",
                "",
                f"- Summary: `{summary['summary_id']}`",
                f"- Level: `{summary['level']}`",
                f"- Time range: `{summary.get('start_time')}` to `{summary.get('end_time')}`",
                f"- Topics: {topics}",
                f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                "",
            ]
        )
        for concept in summary["concepts"]:
            concepts.append(
                {
                    "event": "appearance",
                    "concept": concept,
                    "normalized": concept.casefold(),
                    "conversation_id": summary.get("conversation_id"),
                    "summary_id": summary["summary_id"],
                    "summary_level": summary["level"],
                    "start_time": summary.get("start_time"),
                    "end_time": summary.get("end_time"),
                    "source_start": summary.get("source_start"),
                    "source_end": summary.get("source_end"),
                    "source_start_sequence": summary.get("source_start_sequence"),
                    "source_end_sequence": summary.get("source_end_sequence"),
                    "source_files": summary.get("source_files", []),
                }
            )
            concept_lines.extend(
                [
                    f"## {concept}",
                    "",
                    f"- Summary: `{summary['summary_id']}`",
                    f"- First indexed time in this entry: `{summary.get('start_time')}`",
                    f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                    "",
                ]
            )
        policies.extend(store.policy_event_records(summary))

    targets = [
        store.index_dir / "conversations.jsonl",
        store.index_dir / "summaries.jsonl",
        store.index_dir / "concepts.jsonl",
        store.policy_index_path,
        store.index_dir / "timeline.md",
        store.index_dir / "concepts.md",
        store.current_policy_path,
        store.summaries_dir / "registry.jsonl",
        store.index_dir / "by-conversation",
    ]
    backup = None
    if apply:
        backup = store.backup_derived_files("index-rebuild", targets)
        _write_jsonl(store.index_dir / "conversations.jsonl", conversations)
        _write_jsonl(store.index_dir / "summaries.jsonl", summaries)
        _write_jsonl(store.index_dir / "concepts.jsonl", concepts)
        _write_jsonl(store.policy_index_path, policies)
        _atomic_write_text(
            store.index_dir / "timeline.md",
            "\n".join(timeline_lines).rstrip() + "\n",
        )
        _atomic_write_text(
            store.index_dir / "concepts.md",
            "\n".join(concept_lines).rstrip() + "\n",
        )
        _atomic_write_text(
            store.current_policy_path,
            store.render_current_policy_view(policies),
        )
        _write_jsonl(store.summaries_dir / "registry.jsonl", registry)
        by_conversation_root = store.index_dir / "by-conversation"
        if by_conversation_root.exists():
            shutil.rmtree(by_conversation_root)
        by_conversation_root.mkdir(parents=True, exist_ok=True)
        conversation_ids = sorted(
            {str(record["conversation_id"]) for record in conversations}
        )
        for conversation_id in conversation_ids:
            directory = store.ensure_conversation_index_files(conversation_id)
            message_records = [
                record
                for record in conversations
                if record.get("conversation_id") == conversation_id
            ]
            summary_records = [
                summary
                for summary in summaries
                if summary.get("conversation_id") == conversation_id
            ]
            concept_records = [
                concept
                for concept in concepts
                if concept.get("conversation_id") == conversation_id
            ]
            policy_records = [
                policy
                for policy in policies
                if policy.get("conversation_id") == conversation_id
            ]
            _write_jsonl(directory / "messages.jsonl", message_records)
            _write_jsonl(directory / "summaries.jsonl", summary_records)
            _write_jsonl(directory / "concepts.jsonl", concept_records)
            _write_jsonl(directory / "policies.jsonl", policy_records)

            message_timeline = [
                "# Conversation Timeline",
                "",
                f"- Conversation ID: `{conversation_id}`",
                "",
            ]
            for record in message_records:
                source = record.get("source") or {}
                phase = source.get("phase") or record.get("speaker")
                message_timeline.append(
                    f"- `{record['timestamp']}` | sequence `{record['sequence']}` | "
                    f"`{phase}` | round `{record.get('round_number', 0)}` | "
                    f"`{record['message_id']}`"
                )
            _atomic_write_text(
                directory / "timeline.md",
                "\n".join(message_timeline).rstrip() + "\n",
            )

            summary_timeline = [
                "# Conversation Summary Timeline",
                "",
                f"- Conversation ID: `{conversation_id}`",
                "",
            ]
            conversation_concepts = [
                "# Conversation Concept Index",
                "",
                f"- Conversation ID: `{conversation_id}`",
                "",
            ]
            for summary in summary_records:
                topics = ", ".join(summary["topics"]) or "No topics recorded"
                summary_timeline.extend(
                    [
                        f"## {str(summary.get('start_time') or 'unknown').split('T', 1)[0]}",
                        "",
                        f"- Summary: `{summary['summary_id']}`",
                        f"- Level: `{summary['level']}`",
                        f"- Time range: `{summary.get('start_time')}` to `{summary.get('end_time')}`",
                        f"- Topics: {topics}",
                        f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                        "",
                    ]
                )
                for concept in summary["concepts"]:
                    conversation_concepts.extend(
                        [
                            f"## {concept}",
                            "",
                            f"- Summary: `{summary['summary_id']}`",
                            f"- First indexed time in this entry: `{summary.get('start_time')}`",
                            f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                            "",
                        ]
                    )
            _atomic_write_text(
                directory / "summary-timeline.md",
                "\n".join(summary_timeline).rstrip() + "\n",
            )
            _atomic_write_text(
                directory / "concepts.md",
                "\n".join(conversation_concepts).rstrip() + "\n",
            )
    return {
        "mode": "apply" if apply else "preview",
        "changed": apply,
        "backup": str(backup) if backup else None,
        "raw_messages": len(conversations),
        "summaries": len(summaries),
        "concept_entries": len(concepts),
        "policy_events": len(policies),
        "registry_entries": len(registry),
        "integrity_issues": integrity_issues,
        "can_apply": not integrity_issues,
    }


def compute_source_manifest(store: Any) -> Dict[str, Any]:
    """Return an exact, verified manifest of raw and summary source bytes."""
    root = store.root.resolve()
    raw_records = store.read_all_raw()
    summaries = store.summary_records_from_files()
    summaries_by_id = {item["summary_id"]: item for item in summaries}
    raw_record_paths = set()
    for record in raw_records:
        relative = Path(str(record.get("_path", ""))).as_posix()
        if not relative:
            raise RuntimeError("Raw record is missing its source path")
        stored_digest = record.get("content_sha256")
        actual_digest = _raw_record_sha256(record)
        if stored_digest and stored_digest != actual_digest:
            raise RuntimeError(
                f"Raw content SHA-256 mismatch: {record.get('message_id')}"
            )
        raw_record_paths.add(relative)

    entries: List[Dict[str, Any]] = []
    raw_paths = sorted(store.raw_dir.rglob("*.md"))
    known_raw_paths = {_safe_relative(root, path) for path in raw_paths}
    unexplained = sorted(raw_record_paths - known_raw_paths)
    if unexplained:
        raise RuntimeError(f"Raw source files are missing: {unexplained}")
    for path in raw_paths:
        relative = _safe_relative(root, path)
        value = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "byte_length": len(value),
                "sha256": _sha256_bytes(value),
            }
        )

    summary_paths = sorted(store.summaries_dir.glob("level-*/*.md"))
    parsed_paths = {Path(str(item["path"])).as_posix() for item in summaries}
    known_summary_paths = {_safe_relative(root, path) for path in summary_paths}
    if parsed_paths != known_summary_paths:
        missing = sorted(known_summary_paths - parsed_paths)
        unexplained = sorted(parsed_paths - known_summary_paths)
        raise RuntimeError(
            "Summary source inventory mismatch: "
            f"unparsed={missing}, missing={unexplained}"
        )
    for summary in summaries:
        relative = Path(str(summary["path"])).as_posix()
        path = root / Path(relative)
        if not path.is_file():
            raise RuntimeError(f"Summary source file is missing: {relative}")
        value = path.read_bytes()
        if _sha256_bytes(value) != summary["summary_sha256"]:
            raise RuntimeError(f"Summary SHA-256 mismatch: {summary['summary_id']}")
        actual_source_sha256 = store.actual_summary_source_sha256(
            summary, raw_records, summaries_by_id
        )
        if actual_source_sha256 is None:
            raise RuntimeError(
                f"Summary source is incomplete: {summary['summary_id']}"
            )
        stored_source_sha256 = summary.get("source_sha256")
        if stored_source_sha256 and stored_source_sha256 != actual_source_sha256:
            raise RuntimeError(
                f"Summary source SHA-256 mismatch: {summary['summary_id']}"
            )
        entries.append(
            {
                "path": relative,
                "byte_length": len(value),
                "sha256": _sha256_bytes(value),
            }
        )

    exact_entries = sorted(entries, key=lambda item: item["path"])
    return {
        "entries": exact_entries,
        "sha256": source_manifest_sha256(exact_entries),
    }


def _verify_source_manifest(store: Any, manifest: Dict[str, Any]) -> None:
    for source in manifest["entries"]:
        relative = Path(str(source.get("path", "")))
        path = (store.root / relative).resolve()
        _safe_relative(store.root, path)
        if not path.is_file():
            raise RuntimeError(f"Generation source is missing: {relative.as_posix()}")
        value = path.read_bytes()
        if len(value) != source.get("byte_length"):
            raise RuntimeError(
                f"Generation source byte length mismatch: {relative.as_posix()}"
            )
        if _sha256_bytes(value) != source.get("sha256"):
            raise RuntimeError(
                f"Generation source SHA-256 mismatch: {relative.as_posix()}"
            )


def _legacy_generation_files(root: Path) -> List[Path]:
    fixed = [
        root / "indexes" / "conversations.jsonl",
        root / "indexes" / "summaries.jsonl",
        root / "indexes" / "concepts.jsonl",
        root / "indexes" / "policies.jsonl",
        root / "indexes" / "timeline.md",
        root / "indexes" / "concepts.md",
        root / "indexes" / "policies-current.md",
        root / "summaries" / "registry.jsonl",
    ]
    return fixed + sorted((root / "indexes" / "by-conversation").rglob("*"))


def _render_shadow_files(store: Any, source_manifest: Dict[str, Any]) -> Dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="memory-index-generation-") as temporary:
        shadow_root = Path(temporary) / "archive"
        for source in source_manifest["entries"]:
            relative = Path(source["path"])
            destination = shadow_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((store.root / relative).read_bytes())
        shadow_store = store.__class__(shadow_root, store.config)
        rebuild_indexes(shadow_store, True)
        files: Dict[str, bytes] = {}
        for path in _legacy_generation_files(shadow_root):
            if path.is_file():
                relative = path.relative_to(shadow_root).as_posix()
                files[relative] = path.read_bytes()
        return files


def _file_manifest(files: Dict[str, bytes]) -> List[Dict[str, Any]]:
    return [
        {
            "path": path,
            "byte_length": len(value),
            "sha256": _sha256_bytes(value),
        }
        for path, value in sorted(files.items())
    ]


def build_shadow_generation(store: Any) -> Dict[str, Any]:
    """Build one immutable generation without changing active index files."""
    source_manifest = compute_source_manifest(store)
    files = _render_shadow_files(store, source_manifest)
    file_manifest = _file_manifest(files)
    pointer = None
    if _pointer_path(store).is_file():
        pointer = _read_generation_pointer(_pointer_path(store))
    manifest = {
        "format": INDEX_GENERATION_FORMAT,
        "format_version": GENERATION_FORMAT_VERSION,
        "builder": GENERATION_BUILDER,
        "source_manifest": source_manifest,
        "files": file_manifest,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "previous_generation_id": (pointer or {}).get("active_generation_id"),
        "status": "complete",
    }
    generation_id = index_generation_id(manifest)
    manifest["generation_id"] = generation_id
    generations = _generation_root(store)
    generation = generations / generation_id
    if generation.exists():
        status = inspect_generation_status(store, generation_id, verify_sources=True)
        return {**status, "created": False}

    generations.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{generation_id}.building-", dir=str(generations))
    )
    try:
        for relative, value in sorted(files.items()):
            _atomic_write_bytes(temporary / "payload" / relative, value)
        write_index_generation(temporary / GENERATION_MANIFEST, manifest)
        os.replace(temporary, generation)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    status = inspect_generation_status(store, generation_id, verify_sources=True)
    return {**status, "created": True}


def inspect_generation_status(
    store: Any,
    generation_id: Optional[str] = None,
    *,
    verify_sources: bool = False,
) -> Dict[str, Any]:
    """Verify one generation, or report the active pointer when no ID is given."""
    pointer = None
    pointer_path = _pointer_path(store)
    if pointer_path.exists():
        try:
            pointer = _read_generation_pointer(pointer_path)
        except (OSError, PlatformTransactionError, RuntimeError) as exc:
            raise RuntimeError("Active generation pointer is unreadable") from exc
    selected = generation_id or (pointer or {}).get("active_generation_id")
    if not selected:
        return {
            "status": "inactive",
            "generation_id": None,
            "active_generation_id": None,
            "previous_generation_id": None,
        }
    if not isinstance(selected, str) or not selected.startswith("idxgen-"):
        raise RuntimeError("Invalid generation identity")
    generation = _generation_root(store) / selected
    manifest_path = generation / GENERATION_MANIFEST
    if not manifest_path.is_file():
        raise RuntimeError(f"Generation is incomplete: {selected}")
    try:
        manifest = read_index_generation(manifest_path)
    except (OSError, PlatformTransactionError) as exc:
        raise RuntimeError(f"Generation manifest is unreadable: {selected}") from exc
    if manifest["generation_id"] != selected:
        raise RuntimeError(f"Generation identity mismatch: {selected}")
    expected_paths = set()
    for item in manifest.get("files", []):
        relative = Path(str(item.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RuntimeError(f"Unsafe generation payload path: {relative}")
        expected_paths.add(relative.as_posix())
        path = generation / "payload" / relative
        if not path.is_file():
            raise RuntimeError(f"Generation payload is missing: {relative.as_posix()}")
        value = path.read_bytes()
        if len(value) != item.get("byte_length"):
            raise RuntimeError(
                f"Generation payload byte length mismatch: {relative.as_posix()}"
            )
        if _sha256_bytes(value) != item.get("sha256"):
            raise RuntimeError(
                f"Generation payload SHA-256 mismatch: {relative.as_posix()}"
            )
    actual_paths = {
        path.relative_to(generation / "payload").as_posix()
        for path in (generation / "payload").rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise RuntimeError(
            "Generation contains unexplained payload files: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - expected_paths)}"
        )
    if verify_sources:
        _verify_source_manifest(store, manifest["source_manifest"])
    return {
        "status": "complete",
        "generation_id": selected,
        "active_generation_id": (pointer or {}).get("active_generation_id"),
        "previous_generation_id": (pointer or {}).get("previous_generation_id"),
        "source_manifest_sha256": manifest["source_manifest"]["sha256"],
        "files": len(expected_paths),
        "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
    }


def _write_generation_pointer(
    store: Any, active_generation_id: str, previous_generation_id: Optional[str]
) -> None:
    payload = {
        "format_version": GENERATION_FORMAT_VERSION,
        "format": ACTIVE_POINTER_FORMAT,
        "active_generation_id": active_generation_id,
        "previous_generation_id": previous_generation_id,
    }
    atomic_write_canonical_json(_pointer_path(store), payload)


def _read_generation_pointer(path: Path) -> Dict[str, Any]:
    pointer = read_canonical_json(path)
    expected_fields = {
        "format_version",
        "format",
        "active_generation_id",
        "previous_generation_id",
    }
    if not isinstance(pointer, dict) or set(pointer) != expected_fields:
        raise RuntimeError("Active generation pointer has an invalid field set")
    if pointer["format_version"] != GENERATION_FORMAT_VERSION:
        raise RuntimeError("Active generation pointer format version is unsupported")
    if pointer["format"] != ACTIVE_POINTER_FORMAT:
        raise RuntimeError("Active generation pointer format is unsupported")
    for field in ("active_generation_id", "previous_generation_id"):
        value = pointer[field]
        if value is not None and (
            not isinstance(value, str) or not value.startswith("idxgen-")
        ):
            raise RuntimeError(f"Active generation pointer has invalid {field}")
    return pointer


def activate_generation(store: Any, generation_id: str) -> Dict[str, Any]:
    """Atomically select a complete generation whose sources still match."""
    inspect_generation_status(store, generation_id, verify_sources=True)
    pointer_path = _pointer_path(store)
    current = None
    if pointer_path.exists():
        try:
            current = _read_generation_pointer(pointer_path)
        except (OSError, PlatformTransactionError, RuntimeError) as exc:
            raise RuntimeError("Active generation pointer is unreadable") from exc
    previous_active = (current or {}).get("active_generation_id")
    if previous_active == generation_id:
        return inspect_generation_status(store, generation_id)
    if previous_active:
        inspect_generation_status(store, previous_active)
    _write_generation_pointer(store, generation_id, previous_active)
    return inspect_generation_status(store, generation_id)


def rollback_generation(store: Any) -> Dict[str, Any]:
    """Swap active and previous pointers without reading or rebuilding sources."""
    pointer_path = _pointer_path(store)
    if not pointer_path.is_file():
        raise RuntimeError("No active generation pointer exists")
    try:
        pointer = _read_generation_pointer(pointer_path)
    except (OSError, PlatformTransactionError, RuntimeError) as exc:
        raise RuntimeError("Active generation pointer is unreadable") from exc
    active = pointer.get("active_generation_id")
    previous = pointer.get("previous_generation_id")
    if not active or not previous:
        raise RuntimeError("No previous generation is available for rollback")
    inspect_generation_status(store, active)
    inspect_generation_status(store, previous)
    _write_generation_pointer(store, previous, active)
    return inspect_generation_status(store, previous)
