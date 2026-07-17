#!/usr/bin/env python3
"""Deterministic file operations for the Memory無限 skill."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_ROOT / "config.yaml"
RAW_MARKER = "<!-- memory-wuxian-record -->"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return {}
    if value.startswith(('"', "'")) and value.endswith(('"', "'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def load_simple_yaml(path: Path) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    stack: List[Tuple[int, Dict[str, Any]]] = [(-1, data)]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, separator, value = raw_line.strip().partition(":")
        if not separator:
            raise ValueError(f"Unsupported config line: {raw_line}")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = parse_scalar(value)
        parent[key] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return data


def nested_get(data: Dict[str, Any], keys: Sequence[str], default: Any) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_text_exact(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    append_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    atomic_write_text(path, text)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def yaml_list(values: Iterable[str], indent: int = 2) -> str:
    prefix = " " * indent
    values = list(values)
    if not values:
        return "[]"
    return "\n" + "\n".join(f"{prefix}- {json.dumps(value, ensure_ascii=False)}" for value in values)


def markdown_bullets(values: Iterable[str]) -> str:
    values = list(values)
    if not values:
        return "- None recorded."
    return "\n".join(f"- {value}" for value in values)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raw_record_sha256(record: Dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"_path", "content_sha256"}
    }
    return canonical_sha256(payload)


def raw_source_sha256(records: Iterable[Dict[str, Any]]) -> str:
    payload = [
        {
            "sequence": int(record["sequence"]),
            "message_id": record["message_id"],
            "content_sha256": raw_record_sha256(record),
        }
        for record in sorted(records, key=lambda item: int(item["sequence"]))
    ]
    return canonical_sha256(payload)


def parse_frontmatter_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"", "[]"}:
        return [] if value == "[]" else None
    if value in {"null", "None"}:
        return None
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_summary_markdown(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError(f"Summary frontmatter missing: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"Summary frontmatter is not closed: {path}") from exc
    metadata: Dict[str, Any] = {}
    current_list: Optional[str] = None
    for line in lines[1:end]:
        if line.startswith("  - ") and current_list:
            metadata[current_list].append(parse_frontmatter_scalar(line[4:]))
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        parsed = parse_frontmatter_scalar(value)
        metadata[key] = parsed
        current_list = key if parsed is None else None
        if current_list:
            metadata[current_list] = []

    sections: Dict[str, List[str]] = {
        "Topics": [],
        "Established Conclusions": [],
        "Open Questions": [],
        "Concepts": [],
    }
    current_section: Optional[str] = None
    for line in lines[end + 1 :]:
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        if current_section in sections and line.startswith("- "):
            value = line[2:].strip()
            if value != "None recorded.":
                sections[current_section].append(value)
    return {
        **metadata,
        "topics": sections["Topics"],
        "established_conclusions": sections["Established Conclusions"],
        "open_questions": sections["Open Questions"],
        "concepts": sections["Concepts"],
    }


SECRET_PATTERNS = [
    re.compile(r"(?i)(password\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)(\S+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(AKI[A-Z0-9]{13,})\b"),
]


def redact_secrets(text: str) -> Tuple[str, bool]:
    redacted = text
    changed = False
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted, count = pattern.subn(r"\1[REDACTED]", redacted)
        else:
            redacted, count = pattern.subn("[REDACTED]", redacted)
        changed = changed or count > 0
    return redacted, changed


class MemoryStore:
    def __init__(self, root: Path, config: Dict[str, Any]):
        self.root = root.resolve()
        self.config = config
        self.state_path = self.root / "state.json"
        self.raw_dir = self.root / "raw"
        self.conversation_dir = self.root / "conversations"
        self.summaries_dir = self.root / "summaries"
        self.index_dir = self.root / "indexes"
        self.retrieval_dir = self.root / "retrieval"
        self.pending_dir = self.root / "pending"
        self.archive_dir = self.root / "archive"
        self.locks_dir = self.root / ".locks"
        self.imports_dir = self.root / "imports"
        self.codex_import_dir = self.imports_dir / "codex"

    @property
    def level_1_trigger(self) -> int:
        return int(nested_get(self.config, ["summaries", "level_1_trigger_rounds"], 20))

    @property
    def higher_trigger(self) -> int:
        return int(nested_get(self.config, ["summaries", "higher_level_trigger_count"], 10))

    @property
    def maximum_depth(self) -> int:
        return int(nested_get(self.config, ["summaries", "maximum_summary_depth"], 8))

    def initial_state(self) -> Dict[str, Any]:
        return {
            "format_version": 1,
            "total_messages": 0,
            "completed_rounds": 0,
            "last_summarized_round": 0,
            "last_summarized_rounds": {},
            "last_raw_message_id": None,
            "pending_round": None,
            "pending_rounds": {},
            "next_round_number": 1,
            "completed_rounds_out_of_order": [],
            "next_job_id": 1,
            "next_summary_ids": {str(level): 1 for level in range(1, self.maximum_depth + 1)},
            "last_successful_memory_update": None,
        }

    def init(self) -> Dict[str, Any]:
        directories = [
            self.raw_dir,
            self.conversation_dir,
            self.summaries_dir,
            self.index_dir,
            self.index_dir / "by-conversation",
            self.retrieval_dir,
            self.pending_dir,
            self.archive_dir,
            self.locks_dir,
            self.codex_import_dir,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        (self.summaries_dir / "level-1").mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            atomic_write_json(self.state_path, self.initial_state())
        initial_files = {
            self.root / "README.md": (
                "# Memory無限 Archive\n\n"
                "Raw history is authoritative. Summaries and indexes provide retrieval routes.\n\n"
                "Use `heartbeat --check-only` for a read-only integrity check. Preview "
                "`rebuild-state` or `rebuild-indexes` before applying recovery. Reconstruction "
                "may replace derived files after archiving them, but it never edits raw messages "
                "or summary files.\n"
            ),
            self.conversation_dir / "README.md": (
                "# Per-Conversation Archives\n\n"
                "Each Markdown file contains the complete visible transcript for exactly one "
                "conversation. These files are deterministic views of the immutable records "
                "under `raw/`.\n"
            ),
            self.index_dir / "timeline.md": "# Timeline Index\n",
            self.index_dir / "concepts.md": "# Concept Index\n",
            self.index_dir / "conversations.jsonl": "",
            self.index_dir / "summaries.jsonl": "",
            self.index_dir / "concepts.jsonl": "",
            self.summaries_dir / "registry.jsonl": "",
            self.retrieval_dir / "retrieval-log.jsonl": "",
            self.pending_dir / "failed-jobs.jsonl": "",
        }
        for path, content in initial_files.items():
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        unsummarized = self.pending_dir / "unsummarized.json"
        if not unsummarized.exists():
            atomic_write_json(unsummarized, {"format_version": 1, "pending_jobs": []})
        return self.load_state()

    def load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            self.init()
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: Dict[str, Any]) -> None:
        state["last_successful_memory_update"] = now_iso()
        atomic_write_json(self.state_path, state)

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def raw_path_for_timestamp(self, timestamp: str) -> Path:
        parsed = dt.datetime.fromisoformat(timestamp)
        return self.raw_dir / f"{parsed.year:04d}" / f"{parsed.month:02d}" / f"{parsed.date().isoformat()}.md"

    def ensure_raw_header(self, path: Path, timestamp: str) -> None:
        if path.exists():
            return
        parsed = dt.datetime.fromisoformat(timestamp)
        timezone = parsed.tzname() or str(parsed.utcoffset() or "local")
        header = (
            "---\n"
            "record_type: raw_conversation\n"
            f"date: {json.dumps(parsed.date().isoformat())}\n"
            f"timezone: {json.dumps(timezone)}\n"
            "format_version: 1\n"
            "---\n\n"
            f"# Raw Conversation {parsed.date().isoformat()}\n\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")

    def conversation_transcript_path(self, conversation_id: str) -> Path:
        codex_match = re.fullmatch(r"codex:([A-Za-z0-9-]+)", conversation_id)
        if codex_match:
            filename = f"codex-{codex_match.group(1)}.md"
        else:
            digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
            filename = f"conversation-{digest}.md"
        return self.conversation_dir / filename

    def conversation_index_dir(self, conversation_id: str) -> Path:
        return self.index_dir / "by-conversation" / self.conversation_transcript_path(
            conversation_id
        ).stem

    def ensure_conversation_index_files(self, conversation_id: str) -> Path:
        directory = self.conversation_index_dir(conversation_id)
        directory.mkdir(parents=True, exist_ok=True)
        initial_files = {
            directory / "messages.jsonl": "",
            directory / "summaries.jsonl": "",
            directory / "concepts.jsonl": "",
            directory / "timeline.md": (
                "# Conversation Timeline\n\n"
                f"- Conversation ID: `{conversation_id}`\n"
            ),
            directory / "summary-timeline.md": (
                "# Conversation Summary Timeline\n\n"
                f"- Conversation ID: `{conversation_id}`\n"
            ),
            directory / "concepts.md": (
                "# Conversation Concept Index\n\n"
                f"- Conversation ID: `{conversation_id}`\n"
            ),
        }
        for path, content in initial_files.items():
            if not path.exists():
                atomic_write_text(path, content)
        return directory

    def append_conversation_message_index(
        self,
        index_record: Dict[str, Any],
    ) -> None:
        conversation_id = str(index_record["conversation_id"])
        directory = self.ensure_conversation_index_files(conversation_id)
        append_jsonl(directory / "messages.jsonl", index_record)
        source = index_record.get("source") or {}
        phase = source.get("phase") or index_record.get("speaker")
        append_text(
            directory / "timeline.md",
            (
                f"\n- `{index_record['timestamp']}` | sequence "
                f"`{index_record['sequence']}` | `{phase}` | round "
                f"`{index_record.get('round_number', 0)}` | "
                f"`{index_record['message_id']}`\n"
            ),
        )

    def conversation_transcript_header(self, conversation_id: str) -> str:
        return (
            "---\n"
            "record_type: conversation_transcript\n"
            f"conversation_id: {json.dumps(conversation_id, ensure_ascii=False)}\n"
            "format_version: 1\n"
            "---\n\n"
            f"# Conversation {conversation_id}\n\n"
            "This file contains user messages and user-visible assistant text only. "
            "The fenced JSON record preserves the exact stored text and source metadata.\n\n"
        )

    def conversation_transcript_block(self, record: Dict[str, Any]) -> str:
        stored_record = {key: value for key, value in record.items() if key != "_path"}
        source = stored_record.get("source") or {}
        phase = source.get("phase")
        phase_label = f" / {phase}" if phase else ""
        return (
            f"{RAW_MARKER}\n"
            "```json\n"
            f"{json.dumps(stored_record, ensure_ascii=False, separators=(',', ':'))}\n"
            "```\n\n"
            f"## {stored_record['speaker']}{phase_label}\n\n"
            f"- Timestamp: `{stored_record['timestamp']}`\n"
            f"- Message ID: `{stored_record['message_id']}`\n\n"
            f"{stored_record['text']}\n\n"
        )

    def append_conversation_transcript(self, record: Dict[str, Any]) -> Path:
        conversation_id = str(record["conversation_id"])
        path = self.conversation_transcript_path(conversation_id)
        lock_name = f"conversation-{hashlib.sha256(conversation_id.encode('utf-8')).hexdigest()[:16]}.lock"
        with exclusive_lock(self.locks_dir / lock_name):
            if not path.exists():
                atomic_write_text(path, self.conversation_transcript_header(conversation_id))
            append_text(path, self.conversation_transcript_block(record))
        return path

    def render_conversation_transcript(
        self,
        conversation_id: str,
        records: Iterable[Dict[str, Any]],
    ) -> str:
        ordered = sorted(records, key=lambda item: int(item["sequence"]))
        return self.conversation_transcript_header(conversation_id) + "".join(
            self.conversation_transcript_block(record) for record in ordered
        )

    def recover_round_tracking(
        self,
        raw_records: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        records = sorted(raw_records, key=lambda item: int(item["sequence"]))
        rounds: Dict[int, List[Dict[str, Any]]] = {}
        for record in records:
            number = int(record.get("round_number", 0))
            if number > 0:
                rounds.setdefault(number, []).append(record)

        completed_numbers = set()
        for number, round_records in rounds.items():
            user_conversations = {
                str(record["conversation_id"])
                for record in round_records
                if record["speaker"] == "user"
            }
            final_conversations = {
                str(record["conversation_id"])
                for record in round_records
                if record["speaker"] == "assistant"
                and bool(record.get("completes_round", True))
            }
            conversation_scoped = any(
                record.get("round_scope") == "conversation"
                for record in round_records
            )
            if (
                user_conversations & final_conversations
                if conversation_scoped
                else user_conversations and final_conversations
            ):
                completed_numbers.add(number)

        completed_high_watermark = 0
        while completed_high_watermark + 1 in completed_numbers:
            completed_high_watermark += 1

        pending_rounds: Dict[str, Dict[str, Any]] = {}
        for record in records:
            number = int(record.get("round_number", 0))
            if number <= completed_high_watermark:
                continue
            conversation_id = str(record["conversation_id"])
            if record["speaker"] == "user":
                pending = pending_rounds.get(conversation_id)
                if pending is None or int(pending["number"]) != number:
                    pending = {
                        "number": number,
                        "first_user_message_id": record["message_id"],
                        "latest_user_message_id": record["message_id"],
                    }
                else:
                    pending["latest_user_message_id"] = record["message_id"]
                pending_rounds[conversation_id] = pending
            elif (
                record["speaker"] == "assistant"
                and bool(record.get("completes_round", True))
                and conversation_id in pending_rounds
                and int(pending_rounds[conversation_id]["number"]) == number
            ):
                pending_rounds.pop(conversation_id, None)

        out_of_order = sorted(
            number
            for number in completed_numbers
            if number > completed_high_watermark
        )
        allocated_rounds = [
            int(pending["number"])
            for pending in pending_rounds.values()
        ] + out_of_order
        return {
            "completed_rounds": completed_high_watermark,
            "completed_rounds_out_of_order": out_of_order,
            "pending_rounds": pending_rounds,
            "next_round_number": max(
                [completed_high_watermark, *allocated_rounds],
                default=0,
            ) + 1,
        }

    def append_message(
        self,
        speaker: str,
        text: str,
        timestamp: Optional[str],
        conversation_id: str,
        message_id: Optional[str],
        reply_to: Optional[str],
        allow_secrets: bool,
        complete_round: bool = True,
        source: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.init()
        timestamp = timestamp or now_iso()
        dt.datetime.fromisoformat(timestamp)
        with exclusive_lock(self.locks_dir / "state.lock"):
            state = self.load_state()
            raw_records = self.read_all_raw()
            recovered_rounds = self.recover_round_tracking(raw_records)
            if not isinstance(state.get("pending_rounds"), dict):
                state["pending_rounds"] = recovered_rounds["pending_rounds"]
            state["next_round_number"] = max(
                int(state.get("next_round_number", 1)),
                int(recovered_rounds["next_round_number"]),
            )
            state["completed_rounds_out_of_order"] = sorted({
                int(number)
                for number in state.get(
                    "completed_rounds_out_of_order",
                    recovered_rounds["completed_rounds_out_of_order"],
                )
                if int(number) > int(state["completed_rounds"])
            })
            state["pending_round"] = None
            sequence = int(state["total_messages"]) + 1
            pending_rounds = dict(state["pending_rounds"])
            pending = pending_rounds.get(conversation_id)
            if speaker == "user":
                if pending is None:
                    pending = {
                        "number": int(state["next_round_number"]),
                        "first_user_message_id": None,
                        "latest_user_message_id": None,
                    }
                    state["next_round_number"] = int(state["next_round_number"]) + 1
                round_number = int(pending["number"])
            elif speaker == "assistant" and pending is not None:
                round_number = int(pending["number"])
            else:
                round_number = 0

            suffix = {"user": "u", "assistant": "a", "system": "s", "tool": "t"}[speaker]
            message_id = message_id or f"msg-{sequence:06d}-{suffix}"
            stored_text = text
            was_redacted = False
            redact_enabled = bool(nested_get(self.config, ["safety", "redact_secrets"], True))
            if redact_enabled and not allow_secrets:
                stored_text, was_redacted = redact_secrets(stored_text)

            existing = next(
                (record for record in raw_records if record.get("message_id") == message_id),
                None,
            )
            if existing is not None:
                same_source = (
                    existing.get("speaker") == speaker
                    and existing.get("text") == stored_text
                    and existing.get("conversation_id") == conversation_id
                    and existing.get("timestamp") == timestamp
                    and existing.get("source") == source
                )
                if not same_source:
                    raise ValueError(f"Message ID already exists with different content: {message_id}")
                transcript_path = self.conversation_transcript_path(conversation_id)
                transcript_ids = {
                    record.get("message_id")
                    for record in self.read_raw_file(transcript_path)
                }
                transcript_repaired = message_id not in transcript_ids
                if transcript_repaired:
                    self.append_conversation_transcript(existing)
                return {
                    "status": "duplicate",
                    "message_id": message_id,
                    "sequence": existing["sequence"],
                    "path": existing.get("_path"),
                    "conversation_path": self.relative(transcript_path),
                    "transcript_repaired": transcript_repaired,
                    "text_redacted": bool(existing.get("redacted")),
                }

            if reply_to is None and speaker == "assistant" and pending is not None:
                reply_to = pending.get("latest_user_message_id")
            record = {
                "record_type": "raw_message",
                "sequence": sequence,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "timestamp": timestamp,
                "speaker": speaker,
                "round_number": round_number,
                "round_scope": "conversation",
                "reply_to": reply_to,
                "text": stored_text,
                "redacted": was_redacted,
                "completes_round": bool(
                    speaker == "assistant" and complete_round and pending is not None
                ),
            }
            if source is not None:
                record["source"] = source
            record["content_sha256"] = raw_record_sha256(record)
            raw_path = self.raw_path_for_timestamp(timestamp)
            with exclusive_lock(self.locks_dir / f"raw-{raw_path.stem}.lock"):
                self.ensure_raw_header(raw_path, timestamp)
                block = f"{RAW_MARKER}\n```json\n{json.dumps(record, ensure_ascii=False, separators=(',', ':'))}\n```\n\n"
                append_text(raw_path, block)

            transcript_path = self.append_conversation_transcript(record)

            index_record = {key: value for key, value in record.items() if key != "text"}
            index_record["path"] = self.relative(raw_path)
            index_record["conversation_path"] = self.relative(transcript_path)
            append_jsonl(self.index_dir / "conversations.jsonl", index_record)
            self.append_conversation_message_index(index_record)

            state["total_messages"] = sequence
            state["last_raw_message_id"] = message_id
            if speaker == "user":
                if pending.get("first_user_message_id") is None:
                    pending["first_user_message_id"] = message_id
                pending["latest_user_message_id"] = message_id
                pending_rounds[conversation_id] = pending
            elif speaker == "assistant" and pending is not None and complete_round:
                completed = int(state["completed_rounds"])
                out_of_order = {
                    int(number)
                    for number in state["completed_rounds_out_of_order"]
                    if int(number) > completed
                }
                if round_number == completed + 1:
                    completed = round_number
                    while completed + 1 in out_of_order:
                        out_of_order.remove(completed + 1)
                        completed += 1
                elif round_number > completed + 1:
                    out_of_order.add(round_number)
                state["completed_rounds"] = completed
                state["completed_rounds_out_of_order"] = sorted(out_of_order)
                pending_rounds.pop(conversation_id, None)
            state["pending_rounds"] = pending_rounds
            self.save_state(state)
        return {**index_record, "status": "appended", "text_redacted": was_redacted}

    def configured_backup_root(self) -> Optional[Path]:
        if not bool(nested_get(self.config, ["backup", "enabled"], False)):
            return None
        configured = str(nested_get(self.config, ["backup", "directory"], "")).strip()
        if not configured:
            raise ValueError("backup.enabled requires backup.directory")
        path = Path(configured).expanduser().resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return path
        raise ValueError("Backup directory must be outside the memory archive root")

    @property
    def backup_retention_count(self) -> int:
        count = int(nested_get(self.config, ["backup", "retention_count"], 1))
        if count < 1:
            raise ValueError("backup.retention_count must be at least 1")
        return count

    @property
    def workspace_backup_retention_count(self) -> int:
        count = int(nested_get(self.config, ["backup", "workspace_retention_count"], 1))
        if count < 1:
            raise ValueError("backup.workspace_retention_count must be at least 1")
        return count

    def prune_backup_snapshots(self, backup_root: Path, keep: Iterable[Path]) -> List[str]:
        keep_paths = {path.resolve() for path in keep}
        snapshot_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}(?:_\d{6})?)?$"
        )
        snapshots = sorted(
            path
            for path in backup_root.iterdir()
            if path.is_dir() and snapshot_pattern.fullmatch(path.name)
        )
        retained = set(snapshots[-self.backup_retention_count :]) | keep_paths
        removed = []
        for path in snapshots:
            if path.resolve() in retained:
                continue
            shutil.rmtree(path)
            removed.append(path.name)
        return removed

    def create_backup_snapshot(
        self,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Path]:
        backup_root = self.configured_backup_root()
        if backup_root is None:
            return None
        backup_root.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.locks_dir / "desktop-backup.lock"):
            stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S_%f")
            final_path = backup_root / stamp
            temporary = backup_root / f".{stamp}.tmp-{os.getpid()}"
            if temporary.exists() or final_path.exists():
                raise RuntimeError(f"Backup destination already exists: {final_path}")
            shutil.copytree(
                self.root,
                temporary,
                ignore=shutil.ignore_patterns(".locks", ".DS_Store"),
            )
            copied_files = []
            for path in sorted(temporary.rglob("*")):
                if path.is_file():
                    copied_files.append({
                        "path": str(path.relative_to(temporary)),
                        "sha256": file_sha256(path),
                        "bytes": path.stat().st_size,
                    })
            manifest = {
                "format_version": 1,
                "created_at": now_iso(),
                "source_root": str(self.root),
                "reason": reason,
                "metadata": metadata or {},
                "state": self.load_state(),
                "files": copied_files,
            }
            atomic_write_json(temporary / "backup-manifest.json", manifest)
            os.replace(temporary, final_path)
            append_jsonl(
                backup_root / "backup-log.jsonl",
                {
                    "created_at": manifest["created_at"],
                    "snapshot": final_path.name,
                    "reason": reason,
                    "source_root": str(self.root),
                    "file_count": len(copied_files),
                    "total_messages": manifest["state"].get("total_messages"),
                    "completed_rounds": manifest["state"].get("completed_rounds"),
                    "metadata": metadata or {},
                },
            )
            self.prune_backup_snapshots(backup_root, [final_path])
        return final_path

    def codex_session_metadata(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                if payload.get("type") != "session_meta":
                    continue
                metadata = payload.get("payload") or {}
                identifier = metadata.get("id") or metadata.get("session_id")
                if identifier:
                    return {
                        "session_id": str(identifier),
                        "source": metadata.get("source"),
                        "parent_thread_id": metadata.get("parent_thread_id"),
                        "is_subagent": isinstance(metadata.get("source"), dict)
                        and "subagent" in metadata.get("source", {}),
                    }
        raise ValueError(f"Codex session metadata is missing an ID: {path}")

    def codex_cursor_path(self, session_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        return self.codex_import_dir / f"{safe_id}.json"

    def sync_codex_file(self, source_path: Path) -> Dict[str, Any]:
        source_path = source_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Codex session does not exist: {source_path}")
        session_metadata = self.codex_session_metadata(source_path)
        session_id = session_metadata["session_id"]
        cursor_path = self.codex_cursor_path(session_id)
        cursor = json.loads(cursor_path.read_text(encoding="utf-8")) if cursor_path.exists() else {}
        last_line = int(cursor.get("last_line", 0))
        imported = 0
        duplicates = 0
        repaired_transcripts = 0
        total_lines = 0
        visible_events = 0

        if session_metadata["is_subagent"]:
            total_lines = sum(1 for _ in source_path.open("r", encoding="utf-8"))
            atomic_write_json(
                cursor_path,
                {
                    "format_version": 1,
                    "session_id": session_id,
                    "source_path": str(source_path),
                    "last_line": total_lines,
                    "excluded_reason": "subagent-session",
                    "updated_at": now_iso(),
                },
            )
            return {
                "session_id": session_id,
                "source_path": str(source_path),
                "last_line": total_lines,
                "visible_events": 0,
                "imported_messages": 0,
                "duplicate_messages": 0,
                "repaired_transcripts": 0,
                "excluded_reason": "subagent-session",
            }

        with source_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                total_lines = line_number
                if line_number <= last_line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid Codex JSONL at {source_path}:{line_number}: {exc}") from exc
                if event.get("type") != "event_msg":
                    continue
                payload = event.get("payload") or {}
                event_type = payload.get("type")
                phase = payload.get("phase")
                if event_type == "user_message":
                    speaker = "user"
                    text = payload.get("message")
                    complete_round = False
                    phase = "user"
                elif event_type == "agent_message" and phase in {"commentary", "final_answer"}:
                    speaker = "assistant"
                    text = payload.get("message")
                    complete_round = phase == "final_answer"
                else:
                    continue
                if not isinstance(text, str) or not text:
                    continue
                visible_events += 1
                timestamp = str(event.get("timestamp") or now_iso())
                if timestamp.endswith("Z"):
                    timestamp = timestamp[:-1] + "+00:00"
                suffix = "u" if speaker == "user" else "a"
                message_id = f"codex-{session_id}-{line_number:08d}-{suffix}"
                result = self.append_message(
                    speaker=speaker,
                    text=text,
                    timestamp=timestamp,
                    conversation_id=f"codex:{session_id}",
                    message_id=message_id,
                    reply_to=None,
                    allow_secrets=False,
                    complete_round=complete_round,
                    source={
                        "kind": "codex-rollout-jsonl",
                        "session_id": session_id,
                        "path": str(source_path),
                        "line": line_number,
                        "phase": phase,
                    },
                )
                if result.get("status") == "duplicate":
                    duplicates += 1
                    if result.get("transcript_repaired"):
                        repaired_transcripts += 1
                else:
                    imported += 1

        if total_lines < last_line:
            raise ValueError(
                f"Codex session was truncated below its saved cursor: {source_path} "
                f"({total_lines} < {last_line})"
            )
        atomic_write_json(
            cursor_path,
            {
                "format_version": 1,
                "session_id": session_id,
                "source_path": str(source_path),
                "last_line": total_lines,
                "source_size": source_path.stat().st_size,
                "source_mtime": dt.datetime.fromtimestamp(
                    source_path.stat().st_mtime,
                    tz=dt.timezone.utc,
                ).isoformat(),
                "updated_at": now_iso(),
            },
        )
        return {
            "session_id": session_id,
            "source_path": str(source_path),
            "last_line": total_lines,
            "visible_events": visible_events,
            "imported_messages": imported,
            "duplicate_messages": duplicates,
            "repaired_transcripts": repaired_transcripts,
        }

    def sync_codex(
        self,
        session_files: Sequence[Path],
        sessions_root: Optional[Path],
        since: Optional[str],
    ) -> Dict[str, Any]:
        self.init()
        candidates = {path.expanduser().resolve() for path in session_files}
        since_timestamp: Optional[float] = None
        if since:
            parsed_since = dt.datetime.fromisoformat(since[:-1] + "+00:00" if since.endswith("Z") else since)
            since_timestamp = parsed_since.timestamp()
        if sessions_root is not None:
            root = sessions_root.expanduser().resolve()
            if not root.exists():
                raise FileNotFoundError(f"Codex sessions root does not exist: {root}")
            for path in root.rglob("rollout-*.jsonl"):
                if since_timestamp is None or path.stat().st_mtime >= since_timestamp:
                    candidates.add(path.resolve())
        results = [self.sync_codex_file(path) for path in sorted(candidates)]
        imported = sum(int(item["imported_messages"]) for item in results)
        repaired_transcripts = sum(int(item["repaired_transcripts"]) for item in results)
        created_job = None
        if imported:
            job = self.make_summary_job()
            created_job = str(job) if job else None
        return {
            "status": "synced",
            "sessions": results,
            "session_count": len(results),
            "imported_messages": imported,
            "duplicate_messages": sum(int(item["duplicate_messages"]) for item in results),
            "repaired_transcripts": repaired_transcripts,
            "created_summary_job": created_job,
        }

    def read_raw_file(self, path: Path) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if not path.exists():
            return records
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line == RAW_MARKER and index + 3 < len(lines) and lines[index + 1] == "```json":
                try:
                    records.append(json.loads(lines[index + 2]))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid raw record in {path}:{index + 3}: {exc}") from exc
        return records

    def read_all_raw(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for path in sorted(self.raw_dir.rglob("*.md")):
            for record in self.read_raw_file(path):
                record["_path"] = self.relative(path)
                records.append(record)
        return sorted(records, key=lambda record: int(record["sequence"]))

    def pending_jobs(self) -> List[Dict[str, Any]]:
        jobs = []
        for path in sorted(self.pending_dir.glob("job-*.json")):
            job = json.loads(path.read_text(encoding="utf-8"))
            job["_path"] = str(path)
            jobs.append(job)
        return jobs

    def summary_registry(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.summaries_dir / "registry.jsonl")

    def summary_records(self) -> List[Dict[str, Any]]:
        return [entry for entry in read_jsonl(self.index_dir / "summaries.jsonl") if entry.get("event") == "created"]

    def completed_rounds_by_conversation(
        self,
        raw_records: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, List[List[Dict[str, Any]]]]:
        records = list(raw_records) if raw_records is not None else self.read_all_raw()
        grouped: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for record in records:
            round_number = int(record.get("round_number", 0))
            if round_number <= 0:
                continue
            key = (str(record["conversation_id"]), round_number)
            grouped.setdefault(key, []).append(record)

        completed: Dict[str, List[List[Dict[str, Any]]]] = {}
        for (conversation_id, _), round_records in grouped.items():
            ordered = sorted(round_records, key=lambda item: int(item["sequence"]))
            has_user = any(record.get("speaker") == "user" for record in ordered)
            has_final = any(
                record.get("speaker") == "assistant"
                and bool(record.get("completes_round", True))
                for record in ordered
            )
            if has_user and has_final:
                completed.setdefault(conversation_id, []).append(ordered)
        for conversation_rounds in completed.values():
            conversation_rounds.sort(key=lambda items: int(items[0]["sequence"]))
        return completed

    def make_summary_job(self) -> Optional[Path]:
        self.init()
        with exclusive_lock(self.locks_dir / "summary-jobs.lock"):
            state = self.load_state()
            existing = self.pending_jobs()
            raw_records = self.read_all_raw()
            completed = self.completed_rounds_by_conversation(raw_records)
            summarized = {
                str(key): int(value)
                for key, value in state.get("last_summarized_rounds", {}).items()
            }
            for summary in self.summary_records():
                if int(summary.get("level", 0)) != 1 or not summary.get("conversation_id"):
                    continue
                conversation_id = str(summary["conversation_id"])
                summarized[conversation_id] = max(
                    summarized.get(conversation_id, 0),
                    int(summary.get("source_round_end", 0)),
                )
            assigned = dict(summarized)
            for job in existing:
                if int(job.get("summary_level", 0)) != 1 or not job.get("conversation_id"):
                    continue
                conversation_id = str(job["conversation_id"])
                assigned[conversation_id] = max(
                    assigned.get(conversation_id, 0),
                    int(job.get("source_round_end", 0)),
                )

            conversation_order = sorted(
                completed,
                key=lambda conversation_id: int(completed[conversation_id][0][0]["sequence"]),
            )
            for conversation_id in conversation_order:
                last_assigned_round = assigned.get(conversation_id, 0)
                eligible_rounds = [
                    round_records
                    for round_records in completed[conversation_id]
                    if int(round_records[0]["round_number"]) > last_assigned_round
                ]
                if len(eligible_rounds) < self.level_1_trigger:
                    continue
                selected_rounds = eligible_rounds[: self.level_1_trigger]
                start_round = int(selected_rounds[0][0]["round_number"])
                end_round = int(selected_rounds[-1][0]["round_number"])
                records = [
                    record
                    for round_records in selected_rounds
                    for record in round_records
                ]
                records.sort(key=lambda record: int(record["sequence"]))
                signature = (
                    f"conversation:{conversation_id}:rounds:{start_round}-{end_round}"
                )
                job = self.build_level_1_job(
                    state,
                    records,
                    start_round,
                    end_round,
                    signature,
                    conversation_id,
                )
                return self.persist_job(state, job)

            grouped_children = {
                entry["child_summary_id"]
                for entry in self.summary_registry()
                if entry.get("event") == "grouped"
            }
            summaries = self.summary_records()
            for level in range(1, self.maximum_depth):
                conversation_ids = sorted({
                    str(entry.get("conversation_id"))
                    for entry in summaries
                    if int(entry["level"]) == level and entry.get("conversation_id")
                })
                for conversation_id in conversation_ids:
                    candidates = [
                        entry for entry in summaries
                        if int(entry["level"]) == level
                        and entry["summary_id"] not in grouped_children
                        and entry.get("conversation_id") == conversation_id
                    ]
                    candidates.sort(key=lambda entry: entry["summary_id"])
                    if len(candidates) < self.higher_trigger:
                        continue
                    children = candidates[: self.higher_trigger]
                    signature = (
                        f"conversation:{conversation_id}:children:"
                        + ",".join(entry["summary_id"] for entry in children)
                    )
                    match = next(
                        (job for job in existing if job.get("source_signature") == signature),
                        None,
                    )
                    if match:
                        return Path(match["_path"])
                    job = self.build_parent_job(
                        state,
                        level + 1,
                        children,
                        signature,
                        conversation_id,
                    )
                    return self.persist_job(state, job)
            return None

    def build_level_1_job(
        self,
        state: Dict[str, Any],
        records: List[Dict[str, Any]],
        start_round: int,
        end_round: int,
        signature: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        source_files = list(dict.fromkeys(record["_path"] for record in records))
        summary_number = int(state["next_summary_ids"]["1"])
        timestamps = [dt.datetime.fromisoformat(str(record["timestamp"])) for record in records]
        return {
            "format_version": 1,
            "job_id": f"job-{int(state['next_job_id']):06d}",
            "target_summary_id": f"L1-{summary_number:06d}",
            "summary_level": 1,
            "conversation_id": conversation_id,
            "created_at": now_iso(),
            "source_signature": signature,
            "source_round_start": start_round,
            "source_round_end": end_round,
            "source_start": records[0]["message_id"],
            "source_end": records[-1]["message_id"],
            "source_start_sequence": records[0]["sequence"],
            "source_end_sequence": records[-1]["sequence"],
            "start_time": min(timestamps).isoformat(),
            "end_time": max(timestamps).isoformat(),
            "source_files": source_files,
            "source_message_ids": [record["message_id"] for record in records],
            "source_sha256": raw_source_sha256(records),
            "source_records": [{key: value for key, value in record.items() if key != "_path"} for record in records],
            "required_result_keys": ["topics", "established_conclusions", "open_questions", "concepts"],
        }

    def build_parent_job(
        self,
        state: Dict[str, Any],
        target_level: int,
        children: List[Dict[str, Any]],
        signature: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        summary_number = int(state["next_summary_ids"][str(target_level)])
        child_payload = []
        child_digests = []
        for child in children:
            child_path = self.root / child["path"]
            child_digest = file_sha256(child_path)
            child_digests.append({"summary_id": child["summary_id"], "summary_sha256": child_digest})
            child_payload.append({
                "summary_id": child["summary_id"],
                "metadata": child,
                "content": child_path.read_text(encoding="utf-8"),
                "summary_sha256": child_digest,
            })
        return {
            "format_version": 1,
            "job_id": f"job-{int(state['next_job_id']):06d}",
            "target_summary_id": f"L{target_level}-{summary_number:06d}",
            "summary_level": target_level,
            "conversation_id": conversation_id,
            "created_at": now_iso(),
            "source_signature": signature,
            "source_summaries": [child["summary_id"] for child in children],
            "source_start": children[0].get("source_start"),
            "source_end": children[-1].get("source_end"),
            "source_start_sequence": children[0].get("source_start_sequence"),
            "source_end_sequence": children[-1].get("source_end_sequence"),
            "start_time": min(child["start_time"] for child in children),
            "end_time": max(child["end_time"] for child in children),
            "source_files": list(dict.fromkeys(path for child in children for path in child.get("source_files", []))),
            "source_sha256": canonical_sha256(child_digests),
            "source_summary_payload": child_payload,
            "required_result_keys": ["topics", "established_conclusions", "open_questions", "concepts"],
        }

    def persist_job(self, state: Dict[str, Any], job: Dict[str, Any]) -> Path:
        path = self.pending_dir / f"{job['job_id']}.json"
        atomic_write_json(path, job)
        state["next_job_id"] = int(state["next_job_id"]) + 1
        level = str(int(job["summary_level"]))
        state["next_summary_ids"][level] = int(state["next_summary_ids"][level]) + 1
        self.save_state(state)
        self.refresh_unsummarized_registry()
        return path

    def refresh_unsummarized_registry(self) -> None:
        jobs = []
        for job in self.pending_jobs():
            jobs.append({key: value for key, value in job.items() if key not in {"_path", "source_records", "source_summary_payload"}})
        atomic_write_json(self.pending_dir / "unsummarized.json", {"format_version": 1, "pending_jobs": jobs})

    def validate_summary_payload(self, payload: Dict[str, Any], required: Iterable[str]) -> Dict[str, List[str]]:
        normalized: Dict[str, List[str]] = {}
        for key in required:
            value = payload.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Summary key {key!r} must be an array of strings")
            normalized[key] = [item.strip() for item in value if item.strip()]
        extra = set(payload) - set(required)
        if extra:
            raise ValueError(f"Unexpected summary keys: {', '.join(sorted(extra))}")
        return normalized

    def current_job_source_sha256(self, job: Dict[str, Any]) -> str:
        level = int(job["summary_level"])
        if level == 1:
            expected_ids = list(job.get("source_message_ids", []))
            raw_by_id = {
                record["message_id"]: record for record in self.read_all_raw()
            }
            if expected_ids:
                missing = [message_id for message_id in expected_ids if message_id not in raw_by_id]
                if missing:
                    raise RuntimeError("Summary source range is incomplete")
                return raw_source_sha256(raw_by_id[message_id] for message_id in expected_ids)
            start = int(job["source_start_sequence"])
            end = int(job["source_end_sequence"])
            records = [
                record
                for record in raw_by_id.values()
                if start <= int(record["sequence"]) <= end
            ]
            if not records:
                raise RuntimeError("Summary source range is incomplete")
            return raw_source_sha256(records)

        summaries = {record["summary_id"]: record for record in self.summary_records()}
        child_digests = []
        for summary_id in job.get("source_summaries", []):
            child = summaries.get(summary_id)
            if not child:
                raise RuntimeError(f"Summary source is missing from the index: {summary_id}")
            child_path = self.root / child["path"]
            if not child_path.exists():
                raise RuntimeError(f"Summary source file is missing: {child_path}")
            child_digests.append({
                "summary_id": summary_id,
                "summary_sha256": file_sha256(child_path),
            })
        return canonical_sha256(child_digests)

    def ingest_summary(self, job_path: Path, summary_json_path: Path) -> Path:
        self.init()
        job_path = job_path.resolve()
        if job_path.parent != self.pending_dir.resolve() or not job_path.exists():
            raise ValueError("Job must be an existing file in the memory pending directory")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
        summary = self.validate_summary_payload(payload, job["required_result_keys"])
        level = int(job["summary_level"])
        summary_id = job["target_summary_id"]
        level_dir = self.summaries_dir / f"level-{level}"
        level_dir.mkdir(parents=True, exist_ok=True)
        output_path = level_dir / f"{summary_id}.md"
        if output_path.exists():
            raise FileExistsError(f"Summary already exists: {output_path}")

        with exclusive_lock(self.locks_dir / "summary-ingest.lock"):
            current_source_sha256 = self.current_job_source_sha256(job)
            expected_source_sha256 = job.get("source_sha256")
            if expected_source_sha256 and current_source_sha256 != expected_source_sha256:
                raise RuntimeError(
                    "Summary source drift detected: current source SHA-256 does not match the pending job"
                )
            metadata_lines = [
                "---",
                f"summary_id: {summary_id}",
                f"summary_level: {level}",
                f"conversation_id: {json.dumps(job.get('conversation_id'), ensure_ascii=False)}",
                f"created_at: {json.dumps(now_iso())}",
                f"source_start: {json.dumps(job.get('source_start'))}",
                f"source_end: {json.dumps(job.get('source_end'))}",
                f"start_time: {json.dumps(job.get('start_time'))}",
                f"end_time: {json.dumps(job.get('end_time'))}",
                f"source_sha256: {json.dumps(current_source_sha256)}",
                f"source_files: {yaml_list(job.get('source_files', []))}",
            ]
            if level == 1:
                metadata_lines.append(f"source_rounds: {int(job['source_round_end']) - int(job['source_round_start']) + 1}")
                metadata_lines.append(f"source_round_start: {int(job['source_round_start'])}")
                metadata_lines.append(f"source_round_end: {int(job['source_round_end'])}")
                metadata_lines.append(
                    f"source_message_ids: {yaml_list(job.get('source_message_ids', []))}"
                )
            else:
                metadata_lines.append(f"source_summaries: {yaml_list(job.get('source_summaries', []))}")
            metadata_lines.extend(["format_version: 1", "---", ""])
            body = "\n".join(metadata_lines) + (
                f"\n# Level-{level} Summary {summary_id}\n\n"
                f"## Topics\n\n{markdown_bullets(summary['topics'])}\n\n"
                f"## Established Conclusions\n\n{markdown_bullets(summary['established_conclusions'])}\n\n"
                f"## Open Questions\n\n{markdown_bullets(summary['open_questions'])}\n\n"
                f"## Concepts\n\n{markdown_bullets(summary['concepts'])}\n\n"
                f"## Source References\n\n{markdown_bullets(job.get('source_files', []) or job.get('source_summaries', []))}\n"
            )
            output_path.write_text(body, encoding="utf-8")
            summary_sha256 = file_sha256(output_path)

            index_record = {
                "event": "created",
                "summary_id": summary_id,
                "level": level,
                "conversation_id": job.get("conversation_id"),
                "created_at": now_iso(),
                "start_time": job.get("start_time"),
                "end_time": job.get("end_time"),
                "source_start": job.get("source_start"),
                "source_end": job.get("source_end"),
                "source_start_sequence": job.get("source_start_sequence"),
                "source_end_sequence": job.get("source_end_sequence"),
                "source_files": job.get("source_files", []),
                "source_summaries": job.get("source_summaries", []),
                "source_message_ids": job.get("source_message_ids", []),
                "source_round_start": job.get("source_round_start"),
                "source_round_end": job.get("source_round_end"),
                "source_sha256": current_source_sha256,
                "summary_sha256": summary_sha256,
                "path": self.relative(output_path),
                **summary,
            }
            append_jsonl(self.index_dir / "summaries.jsonl", index_record)
            if index_record.get("conversation_id"):
                conversation_indexes = self.ensure_conversation_index_files(
                    str(index_record["conversation_id"])
                )
                append_jsonl(conversation_indexes / "summaries.jsonl", index_record)
            append_jsonl(self.summaries_dir / "registry.jsonl", {
                "event": "created",
                "summary_id": summary_id,
                "level": level,
                "path": self.relative(output_path),
                "source_signature": job["source_signature"],
                "source_sha256": current_source_sha256,
                "summary_sha256": summary_sha256,
                "timestamp": now_iso(),
            })
            for child_id in job.get("source_summaries", []):
                append_jsonl(self.summaries_dir / "registry.jsonl", {
                    "event": "grouped",
                    "child_summary_id": child_id,
                    "parent_summary_id": summary_id,
                    "timestamp": now_iso(),
                })
            self.update_human_indexes(index_record)
            self.update_concept_indexes(index_record)

            state = self.load_state()
            if level == 1:
                state["last_summarized_round"] = max(int(state["last_summarized_round"]), int(job["source_round_end"]))
                conversation_id = str(job["conversation_id"])
                summarized_rounds = dict(state.get("last_summarized_rounds", {}))
                summarized_rounds[conversation_id] = max(
                    int(summarized_rounds.get(conversation_id, 0)),
                    int(job["source_round_end"]),
                )
                state["last_summarized_rounds"] = summarized_rounds
            self.save_state(state)

            archived_job = self.archive_dir / f"{job['job_id']}-ingested.json"
            shutil.move(str(job_path), str(archived_job))
            self.refresh_unsummarized_registry()
        return output_path

    def update_human_indexes(self, summary: Dict[str, Any]) -> None:
        date = str(summary["start_time"]).split("T", 1)[0]
        topics = ", ".join(summary["topics"]) or "No topics recorded"
        timeline = (
            f"\n## {date}\n\n"
            f"- Summary: `{summary['summary_id']}`\n"
            f"- Level: `{summary['level']}`\n"
            f"- Time range: `{summary['start_time']}` to `{summary['end_time']}`\n"
            f"- Topics: {topics}\n"
            f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`\n"
        )
        append_text(self.index_dir / "timeline.md", timeline)
        if summary.get("conversation_id"):
            directory = self.ensure_conversation_index_files(
                str(summary["conversation_id"])
            )
            append_text(directory / "summary-timeline.md", timeline)

    def update_concept_indexes(self, summary: Dict[str, Any]) -> None:
        for concept in summary["concepts"]:
            record = {
                "event": "appearance",
                "concept": concept,
                "normalized": concept.casefold(),
                "summary_id": summary["summary_id"],
                "summary_level": summary["level"],
                "start_time": summary["start_time"],
                "end_time": summary["end_time"],
                "source_start": summary.get("source_start"),
                "source_end": summary.get("source_end"),
                "source_start_sequence": summary.get("source_start_sequence"),
                "source_end_sequence": summary.get("source_end_sequence"),
                "source_files": summary.get("source_files", []),
            }
            append_jsonl(self.index_dir / "concepts.jsonl", record)
            append_text(
                self.index_dir / "concepts.md",
                f"\n## {concept}\n\n- Summary: `{summary['summary_id']}`\n- First indexed time in this entry: `{summary['start_time']}`\n- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`\n",
            )
            if summary.get("conversation_id"):
                directory = self.ensure_conversation_index_files(
                    str(summary["conversation_id"])
                )
                conversation_record = {
                    **record,
                    "conversation_id": summary["conversation_id"],
                }
                append_jsonl(directory / "concepts.jsonl", conversation_record)
                append_text(
                    directory / "concepts.md",
                    f"\n## {concept}\n\n- Summary: `{summary['summary_id']}`\n"
                    f"- First indexed time in this entry: `{summary['start_time']}`\n"
                    f"- Source: `{summary.get('source_start')}` through "
                    f"`{summary.get('source_end')}`\n",
                )

    def summary_records_from_files(self) -> List[Dict[str, Any]]:
        raw_records = self.read_all_raw()
        raw_by_id = {record["message_id"]: record for record in raw_records}
        records = []
        for path in sorted(self.summaries_dir.glob("level-*/*.md")):
            parsed = parse_summary_markdown(path)
            source_start = parsed.get("source_start")
            source_end = parsed.get("source_end")
            start_record = raw_by_id.get(source_start)
            end_record = raw_by_id.get(source_end)
            conversation_id = parsed.get("conversation_id")
            if not conversation_id and start_record and end_record:
                if start_record.get("conversation_id") == end_record.get("conversation_id"):
                    conversation_id = start_record.get("conversation_id")
            records.append({
                "event": "created",
                "summary_id": parsed["summary_id"],
                "level": int(parsed["summary_level"]),
                "conversation_id": conversation_id,
                "created_at": parsed.get("created_at"),
                "start_time": parsed.get("start_time") or (start_record or {}).get("timestamp"),
                "end_time": parsed.get("end_time") or (end_record or {}).get("timestamp"),
                "source_start": source_start,
                "source_end": source_end,
                "source_start_sequence": (start_record or {}).get("sequence"),
                "source_end_sequence": (end_record or {}).get("sequence"),
                "source_files": parsed.get("source_files") or [],
                "source_summaries": parsed.get("source_summaries") or [],
                "source_message_ids": parsed.get("source_message_ids") or [],
                "source_round_start": parsed.get("source_round_start"),
                "source_round_end": parsed.get("source_round_end"),
                "source_sha256": parsed.get("source_sha256"),
                "summary_sha256": file_sha256(path),
                "path": self.relative(path),
                "topics": parsed["topics"],
                "established_conclusions": parsed["established_conclusions"],
                "open_questions": parsed["open_questions"],
                "concepts": parsed["concepts"],
            })
        return sorted(records, key=lambda record: (int(record["level"]), record["summary_id"]))

    def actual_summary_source_sha256(
        self,
        summary: Dict[str, Any],
        raw_records: Optional[List[Dict[str, Any]]] = None,
        summaries_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        if int(summary["level"]) == 1:
            source_message_ids = list(summary.get("source_message_ids", []))
            raw_records = raw_records if raw_records is not None else self.read_all_raw()
            if source_message_ids:
                raw_by_id = {record["message_id"]: record for record in raw_records}
                if any(message_id not in raw_by_id for message_id in source_message_ids):
                    return None
                return raw_source_sha256(
                    raw_by_id[message_id] for message_id in source_message_ids
                )
            start = summary.get("source_start_sequence")
            end = summary.get("source_end_sequence")
            if start is None or end is None:
                return None
            selected = [
                record
                for record in raw_records
                if int(start) <= int(record["sequence"]) <= int(end)
            ]
            if not selected:
                return None
            return raw_source_sha256(selected)

        summaries_by_id = summaries_by_id or {
            record["summary_id"]: record for record in self.summary_records_from_files()
        }
        child_digests = []
        for child_id in summary.get("source_summaries", []):
            child = summaries_by_id.get(child_id)
            if not child:
                return None
            child_path = self.root / child["path"]
            if not child_path.exists():
                return None
            child_digests.append({
                "summary_id": child_id,
                "summary_sha256": file_sha256(child_path),
            })
        return canonical_sha256(child_digests)

    def backup_derived_files(self, label: str, paths: Iterable[Path]) -> Path:
        stamp = dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.archive_dir / f"{label}-{stamp}"
        for path in paths:
            if not path.exists():
                continue
            destination = backup_dir / self.relative(path)
            if path.is_dir():
                shutil.copytree(path, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        self.prune_workspace_backups(keep=[backup_dir])
        return backup_dir

    def prune_workspace_backups(self, keep: Iterable[Path]) -> List[str]:
        keep_paths = {path.resolve() for path in keep if path.exists()}
        backup_pattern = re.compile(
            r"^(?:state|conversation|index)-rebuild-\d{8}_\d{6}_\d{6}$"
        )
        backups = sorted(
            (
                path
                for path in self.archive_dir.iterdir()
                if path.is_dir() and backup_pattern.fullmatch(path.name)
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        retained = set(backups[-self.workspace_backup_retention_count :]) | keep_paths
        removed = []
        for path in backups:
            if path.resolve() in retained:
                continue
            shutil.rmtree(path)
            removed.append(path.name)
        return removed

    def build_recovered_state(self) -> Dict[str, Any]:
        raw_records = self.read_all_raw()
        round_tracking = self.recover_round_tracking(raw_records)

        summaries = self.summary_records_from_files()
        raw_by_id = {record["message_id"]: record for record in raw_records}
        summarized_rounds = [
            int(raw_by_id[summary["source_end"]]["round_number"])
            for summary in summaries
            if int(summary["level"]) == 1 and summary.get("source_end") in raw_by_id
        ]
        last_summarized_rounds: Dict[str, int] = {}
        for summary in summaries:
            if int(summary["level"]) != 1 or not summary.get("conversation_id"):
                continue
            conversation_id = str(summary["conversation_id"])
            source_round_end = summary.get("source_round_end")
            if source_round_end is None:
                continue
            last_summarized_rounds[conversation_id] = max(
                last_summarized_rounds.get(conversation_id, 0),
                int(source_round_end),
            )
        next_summary_ids = {str(level): 1 for level in range(1, self.maximum_depth + 1)}
        for summary in summaries:
            match = re.fullmatch(r"L(\d+)-(\d+)", summary["summary_id"])
            if match:
                level, number = int(match.group(1)), int(match.group(2))
                next_summary_ids[str(level)] = max(next_summary_ids.get(str(level), 1), number + 1)

        for job in self.pending_jobs():
            match = re.fullmatch(r"L(\d+)-(\d+)", str(job.get("target_summary_id", "")))
            if match:
                level, number = int(match.group(1)), int(match.group(2))
                next_summary_ids[str(level)] = max(
                    next_summary_ids.get(str(level), 1),
                    number + 1,
                )

        job_numbers = []
        for path in list(self.pending_dir.glob("job-*.json")) + list(self.archive_dir.glob("job-*-ingested.json")):
            match = re.match(r"job-(\d+)", path.name)
            if match:
                job_numbers.append(int(match.group(1)))
        return {
            "format_version": 1,
            "total_messages": max((int(record["sequence"]) for record in raw_records), default=0),
            "completed_rounds": round_tracking["completed_rounds"],
            "last_summarized_round": max(summarized_rounds, default=0),
            "last_summarized_rounds": last_summarized_rounds,
            "last_raw_message_id": raw_records[-1]["message_id"] if raw_records else None,
            "pending_round": None,
            "pending_rounds": round_tracking["pending_rounds"],
            "next_round_number": round_tracking["next_round_number"],
            "completed_rounds_out_of_order": round_tracking[
                "completed_rounds_out_of_order"
            ],
            "next_job_id": max(job_numbers, default=0) + 1,
            "next_summary_ids": next_summary_ids,
            "last_successful_memory_update": None,
        }

    def rebuild_state(self, apply: bool) -> Dict[str, Any]:
        self.init()
        current = self.load_state()
        recovered = self.build_recovered_state()
        compared_keys = [
            "total_messages",
            "completed_rounds",
            "last_summarized_round",
            "last_summarized_rounds",
            "last_raw_message_id",
            "pending_round",
            "pending_rounds",
            "next_round_number",
            "completed_rounds_out_of_order",
            "next_job_id",
            "next_summary_ids",
        ]
        differences = {
            key: {"current": current.get(key), "recovered": recovered.get(key)}
            for key in compared_keys
            if current.get(key) != recovered.get(key)
        }
        backup = None
        if apply and differences:
            backup = self.backup_derived_files("state-rebuild", [self.state_path])
            self.save_state(recovered)
        return {
            "mode": "apply" if apply else "preview",
            "changed": bool(apply and differences),
            "differences": differences,
            "backup": str(backup) if backup else None,
            "recovered_state": recovered,
        }

    def expected_conversation_transcripts(
        self,
        raw_records: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[Path, str]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        records = list(raw_records) if raw_records is not None else self.read_all_raw()
        for record in records:
            grouped.setdefault(str(record["conversation_id"]), []).append(record)
        return {
            self.conversation_transcript_path(conversation_id): self.render_conversation_transcript(
                conversation_id,
                conversation_records,
            )
            for conversation_id, conversation_records in grouped.items()
        }

    def rebuild_conversations(self, apply: bool) -> Dict[str, Any]:
        self.init()
        raw_records = self.read_all_raw()
        integrity_issues = []
        for record in raw_records:
            stored_digest = record.get("content_sha256")
            if stored_digest and stored_digest != raw_record_sha256(record):
                integrity_issues.append(f"raw content SHA-256 mismatch: {record['message_id']}")
        if apply and integrity_issues:
            raise RuntimeError(
                "Refusing to rebuild conversation transcripts over integrity failures: "
                + "; ".join(integrity_issues)
            )

        expected = self.expected_conversation_transcripts(raw_records)
        current_paths = {
            path for path in self.conversation_dir.glob("*.md") if path.name != "README.md"
        }
        changed_paths = sorted(
            path
            for path, content in expected.items()
            if not path.exists() or read_text_exact(path) != content
        )
        extra_paths = sorted(current_paths - set(expected))
        backup = None
        if apply and (changed_paths or extra_paths):
            backup = self.backup_derived_files(
                "conversation-rebuild",
                sorted(current_paths),
            )
            for path, content in expected.items():
                atomic_write_text(path, content)
            for path in extra_paths:
                path.unlink()
        return {
            "mode": "apply" if apply else "preview",
            "changed": bool(apply and (changed_paths or extra_paths)),
            "backup": str(backup) if backup else None,
            "conversation_count": len(expected),
            "raw_messages": len(raw_records),
            "changed_files": [self.relative(path) for path in changed_paths],
            "extra_files": [self.relative(path) for path in extra_paths],
            "integrity_issues": integrity_issues,
            "can_apply": not integrity_issues,
        }

    def rebuild_indexes(self, apply: bool) -> Dict[str, Any]:
        self.init()
        raw_records = self.read_all_raw()
        summaries = self.summary_records_from_files()
        summaries_by_id = {summary["summary_id"]: summary for summary in summaries}
        integrity_issues = []
        for record in raw_records:
            stored_digest = record.get("content_sha256")
            if stored_digest and stored_digest != raw_record_sha256(record):
                integrity_issues.append(f"raw content SHA-256 mismatch: {record['message_id']}")
        for summary in summaries:
            actual_source_sha256 = self.actual_summary_source_sha256(
                summary, raw_records, summaries_by_id
            )
            if summary.get("source_sha256") and summary["source_sha256"] != actual_source_sha256:
                integrity_issues.append(f"summary source SHA-256 mismatch: {summary['summary_id']}")
            if not summary.get("source_sha256"):
                summary["source_sha256"] = actual_source_sha256

        try:
            existing_summary_index = self.summary_records()
        except ValueError:
            existing_summary_index = []
        for existing in existing_summary_index:
            summary = summaries_by_id.get(existing["summary_id"])
            expected_digest = existing.get("summary_sha256")
            if summary and expected_digest and expected_digest != summary["summary_sha256"]:
                integrity_issues.append(f"summary SHA-256 mismatch: {existing['summary_id']}")
        integrity_issues = list(dict.fromkeys(integrity_issues))
        if apply and integrity_issues:
            raise RuntimeError(
                "Refusing to rebuild indexes over integrity failures: " + "; ".join(integrity_issues)
            )

        conversations = []
        for record in raw_records:
            index_record = {
                key: value
                for key, value in record.items()
                if key not in {"text", "_path"}
            }
            index_record["content_sha256"] = record.get("content_sha256") or raw_record_sha256(record)
            index_record["path"] = record["_path"]
            index_record["conversation_path"] = self.relative(
                self.conversation_transcript_path(str(record["conversation_id"]))
            )
            conversations.append(index_record)

        registry = []
        concepts = []
        timeline_lines = ["# Timeline Index", ""]
        concept_lines = ["# Concept Index", ""]
        for summary in summaries:
            source_signature = (
                "children:" + ",".join(summary.get("source_summaries", []))
                if int(summary["level"]) > 1
                else f"messages:{summary.get('source_start')}-{summary.get('source_end')}"
            )
            registry.append({
                "event": "created",
                "summary_id": summary["summary_id"],
                "level": summary["level"],
                "conversation_id": summary.get("conversation_id"),
                "path": summary["path"],
                "source_signature": source_signature,
                "source_sha256": summary.get("source_sha256"),
                "summary_sha256": summary["summary_sha256"],
                "timestamp": summary.get("created_at"),
            })
            for child_id in summary.get("source_summaries", []):
                registry.append({
                    "event": "grouped",
                    "child_summary_id": child_id,
                    "parent_summary_id": summary["summary_id"],
                    "timestamp": summary.get("created_at"),
                })
            date = str(summary.get("start_time") or "unknown").split("T", 1)[0]
            topics = ", ".join(summary["topics"]) or "No topics recorded"
            timeline_lines.extend([
                f"## {date}",
                "",
                f"- Summary: `{summary['summary_id']}`",
                f"- Level: `{summary['level']}`",
                f"- Time range: `{summary.get('start_time')}` to `{summary.get('end_time')}`",
                f"- Topics: {topics}",
                f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                "",
            ])
            for concept in summary["concepts"]:
                concepts.append({
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
                })
                concept_lines.extend([
                    f"## {concept}",
                    "",
                    f"- Summary: `{summary['summary_id']}`",
                    f"- First indexed time in this entry: `{summary.get('start_time')}`",
                    f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                    "",
                ])

        targets = [
            self.index_dir / "conversations.jsonl",
            self.index_dir / "summaries.jsonl",
            self.index_dir / "concepts.jsonl",
            self.index_dir / "timeline.md",
            self.index_dir / "concepts.md",
            self.summaries_dir / "registry.jsonl",
            self.index_dir / "by-conversation",
        ]
        backup = None
        if apply:
            backup = self.backup_derived_files("index-rebuild", targets)
            write_jsonl(self.index_dir / "conversations.jsonl", conversations)
            write_jsonl(self.index_dir / "summaries.jsonl", summaries)
            write_jsonl(self.index_dir / "concepts.jsonl", concepts)
            atomic_write_text(self.index_dir / "timeline.md", "\n".join(timeline_lines).rstrip() + "\n")
            atomic_write_text(self.index_dir / "concepts.md", "\n".join(concept_lines).rstrip() + "\n")
            write_jsonl(self.summaries_dir / "registry.jsonl", registry)
            by_conversation_root = self.index_dir / "by-conversation"
            if by_conversation_root.exists():
                shutil.rmtree(by_conversation_root)
            by_conversation_root.mkdir(parents=True, exist_ok=True)
            conversation_ids = sorted({
                str(record["conversation_id"]) for record in conversations
            })
            for conversation_id in conversation_ids:
                directory = self.ensure_conversation_index_files(conversation_id)
                message_records = [
                    record for record in conversations
                    if record.get("conversation_id") == conversation_id
                ]
                summary_records = [
                    summary for summary in summaries
                    if summary.get("conversation_id") == conversation_id
                ]
                concept_records = [
                    concept for concept in concepts
                    if concept.get("conversation_id") == conversation_id
                ]
                write_jsonl(directory / "messages.jsonl", message_records)
                write_jsonl(directory / "summaries.jsonl", summary_records)
                write_jsonl(directory / "concepts.jsonl", concept_records)

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
                atomic_write_text(
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
                    summary_timeline.extend([
                        f"## {str(summary.get('start_time') or 'unknown').split('T', 1)[0]}",
                        "",
                        f"- Summary: `{summary['summary_id']}`",
                        f"- Level: `{summary['level']}`",
                        f"- Time range: `{summary.get('start_time')}` to `{summary.get('end_time')}`",
                        f"- Topics: {topics}",
                        f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                        "",
                    ])
                    for concept in summary["concepts"]:
                        conversation_concepts.extend([
                            f"## {concept}",
                            "",
                            f"- Summary: `{summary['summary_id']}`",
                            f"- First indexed time in this entry: `{summary.get('start_time')}`",
                            f"- Source: `{summary.get('source_start')}` through `{summary.get('source_end')}`",
                            "",
                        ])
                atomic_write_text(
                    directory / "summary-timeline.md",
                    "\n".join(summary_timeline).rstrip() + "\n",
                )
                atomic_write_text(
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
            "registry_entries": len(registry),
            "integrity_issues": integrity_issues,
            "can_apply": not integrity_issues,
        }

    @staticmethod
    def overlapping_ranges(records: Iterable[Dict[str, Any]], label: str) -> List[str]:
        by_scope: Dict[Tuple[int, str], List[Tuple[int, int, str]]] = {}
        for record in records:
            start = record.get("source_start_sequence")
            end = record.get("source_end_sequence")
            if start is None or end is None:
                continue
            level = int(record.get("level", record.get("summary_level", 1)))
            conversation_id = str(record.get("conversation_id") or "legacy-global")
            identifier = record.get("summary_id", record.get("job_id", "unknown"))
            by_scope.setdefault((level, conversation_id), []).append(
                (int(start), int(end), identifier)
            )
        overlaps = []
        for (level, conversation_id), ranges in by_scope.items():
            ranges.sort()
            for previous, current in zip(ranges, ranges[1:]):
                if current[0] <= previous[1]:
                    overlaps.append(
                        f"{label} level {level} overlap for {conversation_id}: "
                        f"{previous[2]} and {current[2]}"
                    )
        return overlaps

    def audit(self) -> Dict[str, Any]:
        self.init()
        repairable_issues = []
        integrity_issues = []
        warnings = []
        missing_sources = []
        raw_records = self.read_all_raw()

        expected_transcripts = self.expected_conversation_transcripts(raw_records)
        current_transcript_paths = {
            path for path in self.conversation_dir.glob("*.md") if path.name != "README.md"
        }
        transcript_mismatch = current_transcript_paths != set(expected_transcripts)
        if not transcript_mismatch:
            transcript_mismatch = any(
                read_text_exact(path) != content
                for path, content in expected_transcripts.items()
            )
        if transcript_mismatch:
            repairable_issues.append("conversation transcripts differ from raw records")

        sequences = [int(record["sequence"]) for record in raw_records]
        if len(sequences) != len(set(sequences)):
            integrity_issues.append("duplicate raw message sequences")
        if sequences and sequences != list(range(min(sequences), max(sequences) + 1)):
            integrity_issues.append("raw message sequence gap")
        legacy_raw = 0
        for record in raw_records:
            stored = record.get("content_sha256")
            if stored is None:
                legacy_raw += 1
            elif stored != raw_record_sha256(record):
                integrity_issues.append(f"raw content SHA-256 mismatch: {record['message_id']}")
        if legacy_raw:
            warnings.append(f"legacy raw records without content SHA-256={legacy_raw}")

        message_owners = {
            record["message_id"]: record["conversation_id"]
            for record in raw_records
        }
        legacy_cross_replies = 0
        for record in raw_records:
            reply_to = record.get("reply_to")
            reply_owner = message_owners.get(reply_to)
            if reply_owner is None or reply_owner == record["conversation_id"]:
                continue
            if record.get("round_scope") == "conversation":
                integrity_issues.append(
                    "conversation-scoped reply crosses conversations: "
                    f"{record['message_id']} -> {reply_to}"
                )
            else:
                legacy_cross_replies += 1
        if legacy_cross_replies:
            warnings.append(
                f"legacy cross-conversation reply links={legacy_cross_replies}"
            )

        try:
            conversation_index = read_jsonl(self.index_dir / "conversations.jsonl")
        except ValueError as exc:
            conversation_index = []
            repairable_issues.append(str(exc))
        if len(conversation_index) != len(raw_records):
            repairable_issues.append(
                f"conversation index records={len(conversation_index)} but raw records={len(raw_records)}"
            )
        indexed_ids = [record.get("message_id") for record in conversation_index]
        raw_ids = [record.get("message_id") for record in raw_records]
        if indexed_ids != raw_ids:
            repairable_issues.append("conversation index order or message IDs differ from raw records")

        try:
            summary_index = [
                entry
                for entry in read_jsonl(self.index_dir / "summaries.jsonl")
                if entry.get("event") == "created"
            ]
        except ValueError as exc:
            summary_index = []
            repairable_issues.append(str(exc))
        try:
            summary_files = self.summary_records_from_files()
        except ValueError as exc:
            summary_files = []
            integrity_issues.append(str(exc))
        indexed_summary_ids = {record["summary_id"] for record in summary_index}
        file_summary_ids = {record["summary_id"] for record in summary_files}
        if indexed_summary_ids != file_summary_ids:
            repairable_issues.append("summary index IDs differ from persisted summary files")

        expected_concept_entries = sum(len(summary["concepts"]) for summary in summary_files)
        try:
            concept_index = read_jsonl(self.index_dir / "concepts.jsonl")
        except ValueError as exc:
            concept_index = []
            repairable_issues.append(str(exc))
        if len(concept_index) != expected_concept_entries:
            repairable_issues.append(
                f"concept index records={len(concept_index)} but expected={expected_concept_entries}"
            )
        for human_index in (self.index_dir / "timeline.md", self.index_dir / "concepts.md"):
            if not human_index.exists():
                repairable_issues.append(f"human index missing: {self.relative(human_index)}")

        summaries_by_file_id = {record["summary_id"]: record for record in summary_files}
        raw_by_id = {record["message_id"]: record for record in raw_records}
        for summary in summary_index:
            path = self.root / summary["path"]
            if not path.exists():
                missing_sources.append({"summary_id": summary["summary_id"], "source": summary["path"]})
                integrity_issues.append(f"summary file missing: {summary['summary_id']}")
                continue
            expected_summary_sha = summary.get("summary_sha256")
            if expected_summary_sha and expected_summary_sha != file_sha256(path):
                integrity_issues.append(f"summary SHA-256 mismatch: {summary['summary_id']}")
            elif not expected_summary_sha:
                warnings.append(f"legacy summary without summary SHA-256: {summary['summary_id']}")
            for source in summary.get("source_files", []):
                if not (self.root / source).exists():
                    missing_sources.append({"summary_id": summary["summary_id"], "source": source})
                    integrity_issues.append(
                        f"summary source file missing: {summary['summary_id']} -> {source}"
                    )
            actual_source_sha = self.actual_summary_source_sha256(
                summary, raw_records, summaries_by_file_id
            )
            expected_source_sha = summary.get("source_sha256")
            if expected_source_sha and actual_source_sha != expected_source_sha:
                integrity_issues.append(f"summary source SHA-256 mismatch: {summary['summary_id']}")
            elif not expected_source_sha:
                warnings.append(f"legacy summary without source SHA-256: {summary['summary_id']}")
            if int(summary["level"]) == 1:
                if summary.get("source_start") not in raw_by_id or summary.get("source_end") not in raw_by_id:
                    integrity_issues.append(f"summary raw boundary missing: {summary['summary_id']}")

        integrity_issues.extend(self.overlapping_ranges(summary_index, "summary"))
        try:
            jobs = self.pending_jobs()
        except (ValueError, json.JSONDecodeError) as exc:
            jobs = []
            integrity_issues.append(f"pending summary job is unreadable: {exc}")
        integrity_issues.extend(self.overlapping_ranges(jobs, "pending job"))
        signatures = [job.get("source_signature") for job in jobs]
        duplicate_signatures = sorted({signature for signature in signatures if signature and signatures.count(signature) > 1})
        if duplicate_signatures:
            integrity_issues.append(f"duplicate pending source assignments={duplicate_signatures}")

        try:
            failed_jobs = read_jsonl(self.pending_dir / "failed-jobs.jsonl")
        except ValueError as exc:
            failed_jobs = []
            integrity_issues.append(f"failed-job log is unreadable: {exc}")
        if failed_jobs:
            warnings.append(f"failed jobs awaiting review={len(failed_jobs)}")

        expected_state = self.build_recovered_state()
        current_state = self.load_state()
        state_keys = [
            "total_messages",
            "completed_rounds",
            "last_summarized_round",
            "last_raw_message_id",
            "pending_round",
            "pending_rounds",
            "next_round_number",
            "completed_rounds_out_of_order",
            "next_job_id",
            "next_summary_ids",
        ]
        state_differences = {
            key: {"current": current_state.get(key), "recovered": expected_state.get(key)}
            for key in state_keys
            if current_state.get(key) != expected_state.get(key)
        }
        if state_differences:
            repairable_issues.append(f"state differences={sorted(state_differences)}")

        try:
            registry = self.summary_registry()
        except ValueError as exc:
            registry = []
            repairable_issues.append(str(exc))
        created_registry_ids = {
            entry["summary_id"] for entry in registry if entry.get("event") == "created"
        }
        if created_registry_ids != file_summary_ids:
            repairable_issues.append("summary registry IDs differ from persisted summary files")

        all_issues = integrity_issues + repairable_issues
        return {
            "status": "ok" if not all_issues else "attention",
            "integrity_issues": integrity_issues,
            "repairable_issues": repairable_issues,
            "warnings": warnings,
            "missing_sources": missing_sources,
            "state_differences": state_differences,
            "pending_jobs": len(jobs),
            "failed_jobs": len(failed_jobs),
        }

    def retrieve(self, query: str) -> Tuple[str, Dict[str, Any]]:
        self.init()
        query_folded = query.casefold().strip()
        if not query_folded:
            raise ValueError("Query must not be empty")
        concept_hits = [
            record for record in read_jsonl(self.index_dir / "concepts.jsonl")
            if query_folded in record.get("normalized", "") or record.get("normalized", "") in query_folded
        ]
        summaries = self.summary_records()
        summary_hits = []
        hit_ids = {record["summary_id"] for record in concept_hits}
        for summary in summaries:
            searchable = "\n".join(
                item
                for key in ("topics", "established_conclusions", "open_questions", "concepts")
                for item in summary.get(key, [])
            ).casefold()
            if summary["summary_id"] in hit_ids or query_folded in searchable:
                summary_hits.append(summary)

        all_raw = self.read_all_raw()
        candidate_sequences = set()
        for summary in summary_hits:
            start = summary.get("source_start_sequence")
            end = summary.get("source_end_sequence")
            if start is not None and end is not None:
                candidate_sequences.update(range(int(start), int(end) + 1))
        candidate_raw = [record for record in all_raw if int(record["sequence"]) in candidate_sequences]
        search_pool = candidate_raw or all_raw
        matching_indexes = [index for index, record in enumerate(search_pool) if query_folded in record["text"].casefold()]
        before = int(nested_get(self.config, ["retrieval", "context_messages_before"], 3))
        after = int(nested_get(self.config, ["retrieval", "context_messages_after"], 3))
        selected: List[Dict[str, Any]] = []
        if matching_indexes:
            selected_indexes = set()
            for index in matching_indexes:
                selected_indexes.update(range(max(0, index - before), min(len(search_pool), index + after + 1)))
            selected = [search_pool[index] for index in sorted(selected_indexes)]
        elif candidate_raw:
            limit = int(nested_get(self.config, ["retrieval", "maximum_initial_candidates"], 10))
            selected = candidate_raw[:limit]

        if selected:
            confidence = "verified"
        elif summary_hits and min(int(summary["level"]) for summary in summary_hits) == 1:
            confidence = "summary-supported"
        elif summary_hits or concept_hits:
            confidence = "index-only"
        else:
            confidence = "unverified"

        lines = [
            "# Memory無限 Retrieval",
            "",
            f"- Query: {query}",
            f"- Confidence: `{confidence}`",
            f"- Matched summaries: {', '.join(summary['summary_id'] for summary in summary_hits) or 'None'}",
            "",
        ]
        if selected:
            lines.extend(["## Verified Raw Context", ""])
            for record in selected:
                lines.extend([
                    f"### {record['message_id']} ({record['speaker']})",
                    "",
                    f"- Timestamp: `{record['timestamp']}`",
                    f"- Raw file: `{record['_path']}`",
                    "",
                    record["text"],
                    "",
                ])
        elif summary_hits:
            lines.extend(["## Summary Routes", ""])
            for summary in summary_hits:
                lines.append(f"- `{summary['summary_id']}`: `{summary['path']}`")
        else:
            lines.append("No persisted source matched the query.")
        output = "\n".join(lines).rstrip() + "\n"
        metadata = {
            "timestamp": now_iso(),
            "query": query,
            "matched_concepts": [record["concept"] for record in concept_hits],
            "summaries": [summary["summary_id"] for summary in summary_hits],
            "raw_files": list(dict.fromkeys(record["_path"] for record in selected)),
            "message_range": f"{selected[0]['message_id']}..{selected[-1]['message_id']}" if selected else None,
            "verification": confidence,
        }
        (self.retrieval_dir / "last-query.md").write_text(output, encoding="utf-8")
        if bool(nested_get(self.config, ["retrieval", "log_queries"], True)):
            append_jsonl(self.retrieval_dir / "retrieval-log.jsonl", metadata)
        return output, metadata

    def status(self) -> Dict[str, Any]:
        self.init()
        state = self.load_state()
        summaries = self.summary_records()
        grouped = [entry for entry in self.summary_registry() if entry.get("event") == "grouped"]
        return {
            **state,
            "root": str(self.root),
            "conversation_archives": len(
                [path for path in self.conversation_dir.glob("*.md") if path.name != "README.md"]
            ),
            "pending_summary_jobs": len(self.pending_jobs()),
            "summary_counts": {
                str(level): sum(1 for summary in summaries if int(summary["level"]) == level)
                for level in range(1, self.maximum_depth + 1)
            },
            "grouped_child_summaries": len(grouped),
            "unsummarized_completed_rounds": int(state["completed_rounds"]) - int(state["last_summarized_round"]),
        }

    def heartbeat(self, create_jobs: bool, repair: bool = False) -> Dict[str, Any]:
        self.init()
        before = self.audit()
        repairs = []
        if repair and not before["integrity_issues"]:
            needs_conversation_rebuild = any(
                "conversation transcript" in issue
                for issue in before["repairable_issues"]
            )
            needs_index_rebuild = any(
                "index" in issue or "registry" in issue
                for issue in before["repairable_issues"]
            )
            if needs_conversation_rebuild:
                repairs.append({"conversations": self.rebuild_conversations(apply=True)})
            if needs_index_rebuild:
                repairs.append({"indexes": self.rebuild_indexes(apply=True)})
            if before["state_differences"] or needs_index_rebuild:
                repairs.append({"state": self.rebuild_state(apply=True)})
        after = self.audit() if repairs else before

        created_job = None
        if create_jobs and after["status"] == "ok":
            path = self.make_summary_job()
            created_job = str(path) if path else None
        issues = after["integrity_issues"] + after["repairable_issues"]
        return {
            "status": after["status"],
            "timestamp": now_iso(),
            "mode": "repair" if repair else ("check-only" if not create_jobs else "maintenance"),
            "issues": issues,
            "integrity_issues": after["integrity_issues"],
            "repairable_issues": after["repairable_issues"],
            "warnings": after["warnings"],
            "missing_sources": after["missing_sources"],
            "failed_jobs": after["failed_jobs"],
            "pending_jobs": len(self.pending_jobs()),
            "created_job": created_job,
            "repairs": repairs,
        }


def resolve_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config does not exist: {path}")
    return load_simple_yaml(path)


def resolve_root(root_argument: Optional[str], config: Dict[str, Any]) -> Path:
    if root_argument:
        return Path(root_argument).expanduser()
    configured = Path(str(nested_get(config, ["memory", "root_directory"], "./memory")))
    return configured if configured.is_absolute() else SKILL_ROOT / configured


def read_message_text(args: argparse.Namespace) -> str:
    provided = sum(value is not None for value in (args.text, args.text_file))
    if provided > 1:
        raise ValueError("Use only one of --text or --text-file")
    if args.text is not None:
        return args.text
    if args.text_file is not None:
        return Path(args.text_file).read_text(encoding="utf-8")
    if sys.stdin.isatty():
        raise ValueError("Provide --text, --text-file, or pipe message text on stdin")
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memory無限 persistent conversation memory CLI")
    parser.add_argument("--root", help="Memory archive root; defaults to config.yaml")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Configuration YAML path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize an archive without overwriting existing records")
    append_parser = subparsers.add_parser("append", help="Append one exact dialogue message")
    append_parser.add_argument("--speaker", required=True, choices=["user", "assistant", "system", "tool"])
    append_parser.add_argument("--text")
    append_parser.add_argument("--text-file")
    append_parser.add_argument("--timestamp", help="ISO-8601 timestamp with timezone")
    append_parser.add_argument("--conversation-id", default="default")
    append_parser.add_argument("--message-id")
    append_parser.add_argument("--reply-to")
    append_parser.add_argument("--allow-secrets", action="store_true", help="Disable configured secret redaction for this message")
    append_parser.add_argument(
        "--nonfinal-assistant",
        action="store_true",
        help="Store a visible assistant update without completing the dialogue round",
    )

    sync_parser = subparsers.add_parser(
        "sync-codex",
        help="Incrementally import visible messages from Codex rollout JSONL files",
    )
    sync_parser.add_argument(
        "--session-file",
        action="append",
        default=[],
        help="Specific Codex rollout JSONL file; may be supplied more than once",
    )
    sync_parser.add_argument(
        "--sessions-root",
        help="Recursively scan a Codex sessions directory for rollout JSONL files",
    )
    sync_parser.add_argument(
        "--since",
        help="When scanning --sessions-root, include files modified at or after this ISO-8601 time",
    )

    subparsers.add_parser("status", help="Print archive counters and pending work")
    backup_parser = subparsers.add_parser(
        "backup",
        help="Create one verified external snapshot and prune older snapshots",
    )
    backup_parser.add_argument("--reason", default="manual-backup")
    subparsers.add_parser("make-summary-job", help="Create the next due deterministic summary job")
    ingest_parser = subparsers.add_parser("ingest-summary", help="Validate and persist an Agent-generated summary")
    ingest_parser.add_argument("--job", required=True)
    ingest_parser.add_argument("--summary-json", required=True)
    retrieve_parser = subparsers.add_parser("retrieve", help="Search indexes and verify against raw history")
    retrieve_parser.add_argument("--query", required=True)
    rebuild_state_parser = subparsers.add_parser("rebuild-state", help="Preview or apply state reconstruction from persisted files")
    rebuild_state_parser.add_argument("--apply", action="store_true", help="Back up and replace state.json")
    rebuild_conversations_parser = subparsers.add_parser(
        "rebuild-conversations",
        help="Preview or rebuild one complete transcript per conversation",
    )
    rebuild_conversations_parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and replace derived per-conversation transcripts",
    )
    rebuild_indexes_parser = subparsers.add_parser("rebuild-indexes", help="Preview or apply derived-index reconstruction")
    rebuild_indexes_parser.add_argument("--apply", action="store_true", help="Back up and replace derived indexes")
    heartbeat_parser = subparsers.add_parser("heartbeat", help="Validate archive state and recover due work")
    heartbeat_parser.add_argument("--no-create-jobs", action="store_true")
    heartbeat_parser.add_argument("--check-only", action="store_true", help="Validate without creating jobs or repairing files")
    heartbeat_parser.add_argument("--repair", action="store_true", help="Back up and rebuild repairable state or index inconsistencies")
    return parser


def dispatch_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    store: MemoryStore,
) -> int:
    if args.command == "init":
        store.init()
        result: Any = {"status": "initialized", "root": str(store.root)}
    elif args.command == "append":
        result = store.append_message(
            args.speaker,
            read_message_text(args),
            args.timestamp,
            args.conversation_id,
            args.message_id,
            args.reply_to,
            args.allow_secrets,
            complete_round=not args.nonfinal_assistant,
        )
        if result.get("status") == "appended" or result.get("transcript_repaired"):
            backup = store.create_backup_snapshot(
                "append-message",
                {"message_id": result.get("message_id")},
            )
            result["backup"] = str(backup) if backup else None
    elif args.command == "sync-codex":
        if not args.session_file and not args.sessions_root:
            raise ValueError("Provide --session-file or --sessions-root")
        result = store.sync_codex(
            [Path(path) for path in args.session_file],
            Path(args.sessions_root) if args.sessions_root else None,
            args.since,
        )
        if result["imported_messages"] or result["repaired_transcripts"]:
            backup = store.create_backup_snapshot(
                "codex-sync",
                {
                    "imported_messages": result["imported_messages"],
                    "repaired_transcripts": result["repaired_transcripts"],
                    "session_ids": [item["session_id"] for item in result["sessions"]],
                },
            )
            result["backup"] = str(backup) if backup else None
        else:
            result["backup"] = None
    elif args.command == "status":
        result = store.status()
    elif args.command == "backup":
        backup = store.create_backup_snapshot(args.reason)
        result = {
            "status": "created" if backup else "disabled",
            "backup": str(backup) if backup else None,
            "retention_count": store.backup_retention_count if backup else None,
        }
    elif args.command == "make-summary-job":
        path = store.make_summary_job()
        result = {"status": "created" if path else "not-due", "job": str(path) if path else None}
        if path:
            backup = store.create_backup_snapshot("summary-job-created", {"job": str(path)})
            result["backup"] = str(backup) if backup else None
    elif args.command == "ingest-summary":
        path = store.ingest_summary(Path(args.job), Path(args.summary_json))
        result = {"status": "ingested", "summary": str(path)}
        backup = store.create_backup_snapshot("summary-ingested", {"summary": str(path)})
        result["backup"] = str(backup) if backup else None
    elif args.command == "retrieve":
        output, _ = store.retrieve(args.query)
        print(output, end="")
        return 0
    elif args.command == "rebuild-state":
        result = store.rebuild_state(args.apply)
        if args.apply and result.get("changed"):
            backup = store.create_backup_snapshot("state-rebuilt")
            result["desktop_backup"] = str(backup) if backup else None
    elif args.command == "rebuild-conversations":
        result = store.rebuild_conversations(args.apply)
        if args.apply and result.get("changed"):
            backup = store.create_backup_snapshot("conversation-transcripts-rebuilt")
            result["desktop_backup"] = str(backup) if backup else None
    elif args.command == "rebuild-indexes":
        result = store.rebuild_indexes(args.apply)
        if args.apply and result.get("changed"):
            backup = store.create_backup_snapshot("indexes-rebuilt")
            result["desktop_backup"] = str(backup) if backup else None
    elif args.command == "heartbeat":
        if args.check_only and args.repair:
            raise ValueError("--check-only and --repair cannot be used together")
        create_jobs = not (args.no_create_jobs or args.check_only)
        result = store.heartbeat(create_jobs, repair=args.repair)
        if result.get("created_job") or result.get("repairs"):
            backup = store.create_backup_snapshot(
                "heartbeat-maintenance",
                {
                    "created_job": result.get("created_job"),
                    "repair_count": len(result.get("repairs", [])),
                },
            )
            result["backup"] = str(backup) if backup else None
    else:
        parser.error(f"Unknown command: {args.command}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = resolve_config(Path(args.config))
        store = MemoryStore(resolve_root(args.root, config), config)
        with exclusive_lock(store.root / ".locks" / "archive.lock"):
            return dispatch_command(args, parser, store)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"memory-wuxian: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
