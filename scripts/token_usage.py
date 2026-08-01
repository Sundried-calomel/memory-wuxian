from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence


FORMAT_VERSION = 2
DAILY_USAGE_TIMEZONE = "Asia/Tokyo"
DAILY_USAGE_TZINFO = dt.timezone(dt.timedelta(hours=9), name="JST")
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_session_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id)


def empty_usage() -> Dict[str, int]:
    return {field: 0 for field in USAGE_FIELDS}


def normalize_usage(value: Any) -> Optional[Dict[str, int]]:
    if not isinstance(value, dict):
        return None
    normalized = empty_usage()
    for field in USAGE_FIELDS:
        raw = value.get(field, 0)
        if isinstance(raw, bool):
            return None
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return None
        if number < 0:
            return None
        normalized[field] = number
    return normalized


def add_usage(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    return {field: int(left.get(field, 0)) + int(right.get(field, 0)) for field in USAGE_FIELDS}


def subtract_usage(later: Dict[str, int], earlier: Dict[str, int]) -> Dict[str, int]:
    return {
        field: max(0, int(later.get(field, 0)) - int(earlier.get(field, 0)))
        for field in USAGE_FIELDS
    }


def usage_day(timestamp: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(DAILY_USAGE_TZINFO).date().isoformat()
    except (TypeError, ValueError):
        return "unknown"


def usage_signature(value: Optional[Dict[str, int]]) -> tuple[int, ...]:
    if value is None:
        return ()
    return tuple(int(value.get(field, 0)) for field in USAGE_FIELDS)


def token_usage_path(root: Path, session_id: str) -> Path:
    return root / "imports" / "codex" / "token-usage" / f"{safe_session_id(session_id)}.json"


def load_token_usage(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value.get("format_version") == FORMAT_VERSION else None


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def new_ledger(session_id: str, source_path: Path) -> Dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "measurement": "codex-reported-model-usage",
        "conversation_id": f"codex:{session_id}",
        "session_id": session_id,
        "source": {
            "kind": "codex-rollout-token-count",
            "path": str(source_path),
        },
        "scanned_through_line": 0,
        "first_token_event": None,
        "last_token_event": None,
        "token_event_count": 0,
        "model_request_count": 0,
        "counter_reset_count": 0,
        "closed_segments_usage": empty_usage(),
        "current_segment_usage": empty_usage(),
        "reported_usage": empty_usage(),
        "latest_request_usage": empty_usage(),
        "model_context_window": 0,
        "daily_usage_timezone": DAILY_USAGE_TIMEZONE,
        "daily_usage": {},
        "updated_at": None,
    }


def token_event_payload(event: Dict[str, Any]) -> Optional[tuple[Dict[str, int], Dict[str, int], int]]:
    payload = event.get("payload")
    if (
        event.get("type") != "event_msg"
        or not isinstance(payload, dict)
        or payload.get("type") != "token_count"
    ):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    cumulative = normalize_usage(info.get("total_token_usage"))
    latest_request = normalize_usage(info.get("last_token_usage"))
    if cumulative is None or latest_request is None:
        return None
    try:
        context_window = max(0, int(info.get("model_context_window") or 0))
    except (TypeError, ValueError):
        context_window = 0
    return cumulative, latest_request, context_window


def update_ledger(
    ledger: Dict[str, Any],
    events: Iterable[tuple[int, Dict[str, Any]]],
    *,
    source_path: Path,
    scanned_through_line: int,
) -> tuple[Dict[str, Any], int]:
    previous_signature = usage_signature(normalize_usage(ledger.get("current_segment_usage")))
    changed_events = 0
    for line_number, event in events:
        if line_number <= int(ledger.get("scanned_through_line") or 0):
            continue
        parsed = token_event_payload(event)
        if parsed is None:
            continue
        cumulative, latest_request, context_window = parsed
        current = normalize_usage(ledger.get("current_segment_usage")) or empty_usage()
        closed = normalize_usage(ledger.get("closed_segments_usage")) or empty_usage()
        reset = False
        if (
            int(ledger.get("token_event_count") or 0) > 0
            and cumulative["total_tokens"] < current["total_tokens"]
        ):
            closed = add_usage(closed, current)
            ledger["counter_reset_count"] = int(ledger.get("counter_reset_count") or 0) + 1
            reset = True
        signature = usage_signature(cumulative)
        if signature != previous_signature:
            ledger["model_request_count"] = int(ledger.get("model_request_count") or 0) + 1
        previous_signature = signature
        timestamp = str(event.get("timestamp") or "")
        daily_usage = ledger.setdefault("daily_usage", {})
        day = usage_day(timestamp)
        previous_day_usage = normalize_usage(daily_usage.get(day)) or empty_usage()
        delta = cumulative if reset else subtract_usage(cumulative, current)
        daily_usage[day] = add_usage(previous_day_usage, delta)
        marker = {"line": line_number, "timestamp": timestamp}
        if ledger.get("first_token_event") is None:
            ledger["first_token_event"] = marker
        ledger["last_token_event"] = marker
        ledger["token_event_count"] = int(ledger.get("token_event_count") or 0) + 1
        ledger["closed_segments_usage"] = closed
        ledger["current_segment_usage"] = cumulative
        ledger["reported_usage"] = add_usage(closed, cumulative)
        ledger["latest_request_usage"] = latest_request
        ledger["model_context_window"] = context_window
        changed_events += 1
    ledger["source"] = {
        "kind": "codex-rollout-token-count",
        "path": str(source_path),
    }
    ledger["daily_usage_timezone"] = DAILY_USAGE_TIMEZONE
    ledger["scanned_through_line"] = max(
        int(ledger.get("scanned_through_line") or 0),
        int(scanned_through_line),
    )
    if changed_events:
        ledger["updated_at"] = now_iso()
    return ledger, changed_events


def read_session_metadata(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "session_meta":
                continue
            payload = event.get("payload") or {}
            identifier = payload.get("id") or payload.get("session_id")
            if not identifier:
                continue
            source = payload.get("source")
            return {
                "session_id": str(identifier),
                "excluded_reason": (
                    "subagent-session"
                    if isinstance(source, dict) and "subagent" in source
                    else ("exec-session" if source == "exec" else None)
                ),
            }
    raise ValueError(f"Codex session metadata is missing an ID: {path}")


def persist_token_usage(
    root: Path,
    source_path: Path,
    *,
    session_id: Optional[str] = None,
    start_line: Optional[int] = None,
    write: bool = True,
) -> Dict[str, Any]:
    source_path = source_path.expanduser().resolve()
    metadata = read_session_metadata(source_path)
    session_id = session_id or metadata["session_id"]
    if metadata["excluded_reason"]:
        return {
            "status": "excluded",
            "session_id": session_id,
            "excluded_reason": metadata["excluded_reason"],
            "changed_events": 0,
            "ledger": None,
        }
    destination = token_usage_path(root, session_id)
    ledger = load_token_usage(destination) or new_ledger(session_id, source_path)
    rebuilt_daily_usage = "daily_usage" not in ledger
    if rebuilt_daily_usage:
        ledger = new_ledger(session_id, source_path)
    ledger_line = int(ledger.get("scanned_through_line") or 0)
    effective_start = max(ledger_line, int(start_line or 0))
    events = []
    total_lines = 0
    with source_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            total_lines = line_number
            if line_number <= effective_start:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid Codex JSONL at {source_path}:{line_number}: {exc}"
                ) from exc
            events.append((line_number, event))
    if total_lines < ledger_line:
        raise ValueError(
            f"Codex token source was truncated below its ledger cursor: "
            f"{source_path} ({total_lines} < {ledger_line})"
        )
    ledger, changed_events = update_ledger(
        ledger,
        events,
        source_path=source_path,
        scanned_through_line=total_lines,
    )
    has_measurement = int(ledger.get("token_event_count") or 0) > 0
    if write and has_measurement and (changed_events or not destination.exists()):
        atomic_write_json(destination, ledger)
    return {
        "status": (
            "updated"
            if write and changed_events
            else ("would-update" if changed_events else "unchanged")
        ),
        "session_id": session_id,
        "changed_events": changed_events,
        "rebuilt_daily_usage": rebuilt_daily_usage,
        "has_measurement": has_measurement,
        "ledger": (
            str(destination)
            if destination.exists() or (write and has_measurement)
            else None
        ),
        "reported_usage": ledger["reported_usage"],
        "reported_total_tokens": int(ledger["reported_usage"]["total_tokens"]),
        "model_request_count": int(ledger["model_request_count"]),
        "counter_reset_count": int(ledger["counter_reset_count"]),
    }


def discover_rollouts(roots: Sequence[Path]) -> list[Path]:
    discovered = {
        path.resolve()
        for root in roots
        if root.expanduser().exists()
        for path in root.expanduser().rglob("rollout-*.jsonl")
        if path.is_file()
    }
    return sorted(discovered)


def token_usage_ledgers(root: Path) -> list[Dict[str, Any]]:
    directory = root / "imports" / "codex" / "token-usage"
    if not directory.exists():
        return []
    ledgers = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, dict)
            and value.get("format_version") in {1, FORMAT_VERSION}
            and value.get("measurement") == "codex-reported-model-usage"
            and normalize_usage(value.get("reported_usage")) is not None
        ):
            ledgers.append(value)
    return ledgers


def aggregate_ledgers(ledgers: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    total = empty_usage()
    conversations = 0
    requests = 0
    resets = 0
    for ledger in ledgers:
        usage = normalize_usage(ledger.get("reported_usage"))
        if usage is None:
            continue
        conversations += 1
        total = add_usage(total, usage)
        requests += int(ledger.get("model_request_count") or 0)
        resets += int(ledger.get("counter_reset_count") or 0)
    return {
        "measurement": "codex-reported-model-usage",
        "measured_conversations": conversations,
        "model_request_count": requests,
        "counter_reset_count": resets,
        "reported_usage": total,
    }
