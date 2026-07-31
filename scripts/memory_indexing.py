#!/usr/bin/env python3
"""Memory Plane ownership for deterministic index reconstruction."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable


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
