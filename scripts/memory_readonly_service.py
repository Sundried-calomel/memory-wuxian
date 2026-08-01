#!/usr/bin/env python3
"""Bounded provenance-aware read-only access to local Memory Wuxian records."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from memory_guarded_features import GuardedFeatures, SemanticIndexStaleError


MODES = {"keyword", "semantic", "hybrid"}
MAX_QUERY_CHARACTERS = 500
MAX_RESULTS = 50
MAX_SCAN_RECORDS = 200_000
MAX_RESULT_CHARACTERS = 4_000
MAX_RESPONSE_CHARACTERS = 40_000
MAX_SERIALIZED_BYTES = 58_000
MAX_ID_CHARACTERS = 512
MAX_PATH_CHARACTERS = 4_096
MAX_RAW_RECORD_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 512 * 1024 * 1024
MAX_INDEX_LINE_BYTES = 64 * 1024
MAX_RAW_FILES = 10_000
MAX_RAW_ENTRIES = 250_000


class ReadRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def bounded_string(value: Any, maximum: int, label: str) -> str:
    text = str(value or "")
    if len(text) > maximum:
        raise ReadRequestError("source-invalid", f"{label} exceeds the public response bound")
    return text


class ReadOnlyMemoryService:
    def __init__(self, store: Any):
        self.store = store

    def read_bounded_raw(self) -> list[dict[str, Any]]:
        records = []
        scanned_bytes = 0
        paths = []
        entries = 0
        archive_root = self.store.root.resolve(strict=True)
        if self.store.raw_dir.is_symlink() or (
            hasattr(self.store.raw_dir, "is_junction") and self.store.raw_dir.is_junction()
        ):
            raise ReadRequestError("source-invalid", "raw source root must not be linked")
        raw_root = self.store.raw_dir.resolve(strict=True)
        try:
            raw_root.relative_to(archive_root)
        except ValueError as exc:
            raise ReadRequestError("source-invalid", "raw source root escapes the archive") from exc
        def fail_walk(error: OSError) -> None:
            raise ReadRequestError("source-unavailable", "raw source traversal failed") from error

        for root, directories, filenames in os.walk(self.store.raw_dir, followlinks=False, onerror=fail_walk):
            directories.sort()
            filenames.sort()
            safe_directories = []
            for directory in directories:
                candidate = Path(root) / directory
                if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                    raise ReadRequestError("source-invalid", "raw source contains a linked directory")
                try:
                    candidate.resolve(strict=True).relative_to(raw_root)
                except (OSError, ValueError) as exc:
                    raise ReadRequestError("source-invalid", "raw directory escapes the archive") from exc
                safe_directories.append(directory)
            directories[:] = safe_directories
            entries += len(directories) + len(filenames)
            if entries > MAX_RAW_ENTRIES:
                raise ReadRequestError("source-too-large", "raw source exceeds the entry query bound")
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                path = Path(root) / filename
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise ReadRequestError("source-invalid", "raw source contains a linked file")
                try:
                    path.resolve(strict=True).relative_to(raw_root)
                except (OSError, ValueError) as exc:
                    raise ReadRequestError("source-invalid", "raw file escapes the archive") from exc
                paths.append(path)
                if len(paths) > MAX_RAW_FILES:
                    raise ReadRequestError("source-too-large", "raw source exceeds the file query bound")
        for path in sorted(paths):
            with path.open("rb") as handle:
                while True:
                    line = handle.readline(MAX_RAW_RECORD_BYTES + 1)
                    if not line:
                        break
                    scanned_bytes += len(line)
                    if len(line) > MAX_RAW_RECORD_BYTES or scanned_bytes > MAX_SCAN_BYTES:
                        raise ReadRequestError("source-too-large", "raw source exceeds the byte query bound")
                    if line.rstrip(b"\r\n") != b"<!-- memory-wuxian-record -->":
                        continue
                    fence = handle.readline(MAX_RAW_RECORD_BYTES + 1)
                    payload = handle.readline(MAX_RAW_RECORD_BYTES + 1)
                    closing = handle.readline(MAX_RAW_RECORD_BYTES + 1)
                    scanned_bytes += len(fence) + len(payload) + len(closing)
                    if (
                        max(len(fence), len(payload), len(closing)) > MAX_RAW_RECORD_BYTES
                        or scanned_bytes > MAX_SCAN_BYTES
                    ):
                        raise ReadRequestError("source-too-large", "raw record exceeds the byte query bound")
                    if fence.rstrip(b"\r\n") != b"```json" or closing.rstrip(b"\r\n") != b"```":
                        raise ReadRequestError("source-invalid", "raw record framing is invalid")
                    try:
                        record = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ReadRequestError("source-invalid", "raw record JSON is invalid") from exc
                    if not isinstance(record, dict):
                        raise ReadRequestError("source-invalid", "raw record must be an object")
                    required = {"sequence", "message_id", "conversation_id", "text"}
                    if (
                        not required.issubset(record)
                        or isinstance(record["sequence"], bool)
                        or not isinstance(record["sequence"], int)
                        or record["sequence"] < 1
                        or not isinstance(record["message_id"], str)
                        or not record["message_id"]
                        or not isinstance(record["conversation_id"], str)
                        or not record["conversation_id"]
                        or not isinstance(record["text"], str)
                        or ("source" in record and not isinstance(record["source"], dict))
                    ):
                        raise ReadRequestError("source-invalid", "raw record required fields are invalid")
                    record["_path"] = self.store.relative(path)
                    records.append(record)
                    if len(records) > MAX_SCAN_RECORDS:
                        raise ReadRequestError(
                            "source-too-large",
                            f"raw source exceeds the {MAX_SCAN_RECORDS}-record query bound",
                        )
        return sorted(records, key=lambda record: int(record["sequence"]))

    def index_hashes(self) -> dict[str, str]:
        path = self.store.index_dir / "conversations.jsonl"
        hashes = {}
        if not path.is_file():
            return hashes
        scanned_bytes = 0
        with path.open("rb") as handle:
            index = 0
            while True:
                line = handle.readline(MAX_INDEX_LINE_BYTES + 1)
                if not line:
                    break
                if index >= MAX_SCAN_RECORDS:
                    raise ReadRequestError("source-too-large", "conversation index exceeds the query bound")
                index += 1
                scanned_bytes += len(line)
                if len(line) > MAX_INDEX_LINE_BYTES or scanned_bytes > MAX_SCAN_BYTES:
                    raise ReadRequestError("source-too-large", "conversation index exceeds the byte query bound")
                try:
                    item = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReadRequestError("source-invalid", "conversation index JSON is invalid") from exc
                if isinstance(item, dict) and item.get("message_id") and item.get("content_sha256"):
                    hashes[str(item["message_id"])] = str(item["content_sha256"])
        return hashes

    @staticmethod
    def validate(request: Any) -> tuple[str, str, int]:
        if not isinstance(request, dict) or set(request) - {"query", "mode", "limit"}:
            raise ReadRequestError("malformed-request", "request must contain only query, mode, and limit")
        query = request.get("query")
        mode = request.get("mode", "hybrid")
        limit = request.get("limit", 20)
        if not isinstance(query, str) or not query.strip():
            raise ReadRequestError("malformed-request", "query must be a non-empty string")
        query = query.strip()
        if len(query) > MAX_QUERY_CHARACTERS:
            raise ReadRequestError("over-broad-query", f"query exceeds {MAX_QUERY_CHARACTERS} characters")
        if mode not in MODES:
            raise ReadRequestError("malformed-request", "mode must be keyword, semantic, or hybrid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise ReadRequestError("over-broad-query", f"limit must be between 1 and {MAX_RESULTS}")
        return query, mode, limit

    @staticmethod
    def from_query_parameters(parameters: Any) -> dict[str, Any]:
        if not isinstance(parameters, dict) or set(parameters) - {"q", "mode", "limit"}:
            raise ReadRequestError("malformed-request", "HTTP query contains an unknown parameter")
        if any(not isinstance(values, list) or len(values) != 1 for values in parameters.values()):
            raise ReadRequestError("malformed-request", "HTTP query parameters must occur exactly once")
        try:
            limit = int(parameters.get("limit", ["20"])[0])
        except ValueError as exc:
            raise ReadRequestError("malformed-request", "limit must be an integer") from exc
        return {
            "query": parameters.get("q", [""])[0],
            "mode": parameters.get("mode", ["hybrid"])[0],
            "limit": limit,
        }

    @staticmethod
    def from_cli_parameters(query: Any, mode: Any, limit: Any) -> dict[str, Any]:
        try:
            parsed_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ReadRequestError("malformed-request", "limit must be an integer") from exc
        return {"query": query, "mode": mode, "limit": parsed_limit}

    def query(self, request: Any) -> dict[str, Any]:
        query, mode, limit = self.validate(request)
        if not self.store.state_path.is_file() or not self.store.raw_dir.is_dir():
            raise ReadRequestError("source-unavailable", "raw memory source is unavailable")
        try:
            state = json.loads(self.store.state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("memory state must be an object")
            total_messages = int(state.get("total_messages", 0))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ReadRequestError("source-invalid", "memory state is invalid") from exc
        if total_messages > MAX_SCAN_RECORDS:
            raise ReadRequestError(
                "source-too-large",
                f"raw source exceeds the {MAX_SCAN_RECORDS}-record query bound",
            )
        try:
            raw_records = self.read_bounded_raw()
        except ReadRequestError:
            raise
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ReadRequestError("source-unavailable", "raw memory source is unavailable") from exc
        if not raw_records:
            raise ReadRequestError("source-unavailable", "raw memory source is empty")

        raw_by_id = {str(item["message_id"]): item for item in raw_records}
        try:
            index_hashes = self.index_hashes()
        except ReadRequestError:
            raise
        except (OSError, ValueError) as exc:
            raise ReadRequestError("source-unavailable", "conversation index is unavailable") from exc
        titles: dict[str, str] = {}
        for record in raw_records:
            conversation_id = bounded_string(record.get("conversation_id"), MAX_ID_CHARACTERS, "conversation_id")
            source_title = str(record.get("source", {}).get("conversation_title") or "").strip()
            if source_title:
                titles[conversation_id] = source_title
            elif conversation_id not in titles and record.get("speaker") == "user":
                titles[conversation_id] = str(record.get("text", "")).strip().replace("\n", " ")[:72]
        normalized_query = self.store.normalize_search_text(query)
        terms = [term for term in normalized_query.split() if term]
        ranked: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        if mode in {"keyword", "hybrid"}:
            for record in raw_records:
                normalized = self.store.normalize_search_text(str(record.get("text", "")))
                exact = bool(normalized_query and normalized_query in normalized)
                matched = sum(term in normalized for term in terms)
                if not exact and not matched:
                    continue
                message_id = bounded_string(record["message_id"], MAX_ID_CHARACTERS, "message_id")
                ranked[message_id] = {
                    "keyword_score": 1.0 if exact else matched / max(1, len(terms)),
                    "semantic_score": None,
                }

        semantic_provider = None
        if mode in {"semantic", "hybrid"}:
            try:
                semantic = GuardedFeatures(self.store).semantic_retrieve(
                    query,
                    max(limit * 3, 30),
                    raw_records=raw_records,
                )
            except SemanticIndexStaleError as exc:
                if mode == "semantic":
                    raise ReadRequestError(
                        "index-stale",
                        "semantic index does not cover the current raw archive",
                    ) from exc
                warnings.append("semantic-index-stale-keyword-fallback")
            except (FileNotFoundError, OSError, ValueError) as exc:
                if mode == "semantic":
                    raise ReadRequestError("index-unavailable", "semantic index is unavailable or stale") from exc
                warnings.append("semantic-index-unavailable-keyword-fallback")
            else:
                semantic_provider = semantic["provider"]
                for position, match in enumerate(semantic["matches"]):
                    message_id = bounded_string(match["message_id"], MAX_ID_CHARACTERS, "message_id")
                    item = ranked.setdefault(message_id, {"keyword_score": None, "semantic_score": None})
                    item["semantic_score"] = float(match["score"])
                    item["semantic_rank"] = position + 1

        results = []
        for message_id, scores in ranked.items():
            record = raw_by_id.get(message_id)
            if record is None:
                continue
            keyword_score = scores.get("keyword_score")
            semantic_score = scores.get("semantic_score")
            if mode == "keyword" or (mode == "hybrid" and semantic_provider is None):
                score = float(keyword_score or 0)
            elif mode == "semantic":
                score = float(semantic_score or 0)
            else:
                score = max(float(keyword_score or 0), float(semantic_score or 0))
                if keyword_score is not None and semantic_score is not None:
                    score = min(1.0, score + 0.08)
            if record.get("speaker") == "tool":
                score *= 0.72
            conversation_id = bounded_string(record.get("conversation_id"), MAX_ID_CHARACTERS, "conversation_id")
            text = str(record.get("text", ""))
            results.append({
                "message_id": message_id,
                "conversation_id": conversation_id,
                "conversation_title": bounded_string(titles.get(conversation_id, conversation_id), 256, "conversation_title"),
                "timestamp": bounded_string(record.get("timestamp"), 128, "timestamp"),
                "speaker": bounded_string(record.get("speaker"), 32, "speaker"),
                "record_type": bounded_string(record.get("record_type"), 64, "record_type"),
                "text": text[:MAX_RESULT_CHARACTERS],
                "text_characters": len(text),
                "text_truncated": len(text) > MAX_RESULT_CHARACTERS,
                "score": round(score, 8),
                "keyword_score": keyword_score,
                "semantic_score": semantic_score,
            })
        results.sort(key=lambda item: (item["score"], str(item["timestamp"] or "")), reverse=True)
        selected = results[:limit]
        running_characters = 0
        bounded = []
        for item in selected:
            next_characters = running_characters + len(item["text"])
            if bounded and next_characters > MAX_RESPONSE_CHARACTERS:
                break
            bounded.append(item)
            running_characters = next_characters
        selected = bounded
        try:
            pointer_index = GuardedFeatures(self.store).raw_pointer_index(
                [
                    {
                        **raw_by_id[item["message_id"]],
                        "content_sha256": index_hashes.get(item["message_id"], ""),
                    }
                    for item in selected
                ]
            )
        except (OSError, ValueError) as exc:
            raise ReadRequestError("source-unavailable", "raw provenance is unavailable") from exc
        for item in selected:
            record = raw_by_id[item["message_id"]]
            pointer = pointer_index.get(
                (str(record.get("_path", "")), item["message_id"]),
                {"raw_line_start": None, "raw_line_end": None},
            )
            verified = bool(pointer.get("verified_against_raw"))
            item["provenance"] = {
                "confidence": "verified" if verified else "unverified",
                "raw_path": bounded_string(record.get("_path"), MAX_PATH_CHARACTERS, "raw_path"),
                **pointer,
                "record_sha256": index_hashes.get(item["message_id"]),
                "verified_against_raw": verified,
            }
        payload = {
            "schema_version": 1,
            "operation": "memory.query",
            "query": query,
            "mode": mode,
            "limit": limit,
            "count": len(selected),
            "results": selected,
            "confidence": (
                "no-claims"
                if not selected
                else "verified"
                if all(item["provenance"]["verified_against_raw"] for item in selected)
                else "unverified"
            ),
            "semantic_provider": semantic_provider,
            "warnings": warnings,
            "response_text_characters": running_characters,
            "response_text_character_limit": MAX_RESPONSE_CHARACTERS,
            "response_byte_limit": MAX_SERIALIZED_BYTES,
            "read_only": True,
        }
        while selected and len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_SERIALIZED_BYTES:
            selected.pop()
            payload["count"] = len(selected)
            payload["response_text_characters"] = sum(len(item["text"]) for item in selected)
            payload["confidence"] = (
                "no-claims"
                if not selected
                else "verified"
                if all(item["provenance"]["verified_against_raw"] for item in selected)
                else "unverified"
            )
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_SERIALIZED_BYTES:
            raise ReadRequestError("response-too-large", "bounded response metadata exceeds the byte limit")
        return payload


def error_payload(exc: ReadRequestError) -> dict[str, Any]:
    return {"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}, "read_only": True}
