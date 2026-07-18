#!/usr/bin/env python3
"""Serve the read-only Memory Wuxian status dashboard."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from memory_cli import MemoryStore, load_simple_yaml


SKILL_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = SKILL_ROOT / "dashboard/index.html"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


TELEMETRY_CACHE: dict[str, tuple[int, dict[str, int] | None]] = {}


def session_telemetry(path: Path) -> dict[str, int] | None:
    try:
        stamp = path.stat().st_mtime_ns
        cached = TELEMETRY_CACHE.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - 4 * 1024 * 1024))
            chunk = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    latest = None
    for line in reversed(chunk.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload", {})
        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        usage = info.get("last_token_usage") or {}
        used = int(usage.get("total_tokens") or 0)
        window = int(info.get("model_context_window") or 0)
        if window:
            latest = {"used_tokens": used, "context_window": window}
            latest["utilization_percent"] = round(min(100.0, used * 100 / window), 2)
            break
    TELEMETRY_CACHE[str(path)] = (stamp, latest)
    return latest


def dashboard_data(store: MemoryStore) -> dict[str, Any]:
    records = store.read_all_raw()
    summaries = store.summary_records()
    status = store.status()
    by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    daily_messages: Counter[str] = Counter()
    daily_characters: Counter[str] = Counter()
    for record in records:
        by_conversation[str(record["conversation_id"])].append(record)
        day = str(record.get("timestamp", "unknown")).split("T", 1)[0]
        daily_messages[day] += 1
        daily_characters[day] += len(str(record.get("text", "")))

    summary_counts: dict[str, Counter[int]] = defaultdict(Counter)
    for summary in summaries:
        summary_counts[str(summary.get("conversation_id"))][int(summary["level"])] += 1

    conversations = []
    for conversation_id, items in by_conversation.items():
        items.sort(key=lambda item: int(item.get("sequence", 0)))
        first_user = next((str(item.get("text", "")).strip() for item in items if item.get("speaker") == "user"), conversation_id)
        source_path = next((item.get("source", {}).get("path") for item in reversed(items) if item.get("source", {}).get("path")), None)
        telemetry = session_telemetry(Path(source_path)) if source_path else None
        conversations.append({
            "conversation_id": conversation_id,
            "label": first_user.replace("\n", " ")[:72],
            "message_count": len(items),
            "character_count": sum(len(str(item.get("text", ""))) for item in items),
            "completed_rounds": len({item.get("round_number") for item in items if item.get("completes_round")}),
            "summary_counts": {str(level): count for level, count in sorted(summary_counts[conversation_id].items())},
            "first_timestamp": items[0].get("timestamp"),
            "last_timestamp": items[-1].get("timestamp"),
            "telemetry": telemetry,
        })
    conversations.sort(key=lambda item: str(item.get("last_timestamp", "")), reverse=True)

    timestamps = [parse_time(str(item.get("timestamp", ""))) for item in records]
    timestamps = [item for item in timestamps if item]
    first = min(timestamps) if timestamps else None
    now = datetime.now(timezone.utc)
    archived_characters = sum(len(str(item.get("text", ""))) for item in records)
    return {
        "generated_at": now.isoformat(),
        "archive_root": str(store.root),
        "health": "ok" if not status.get("completed_rounds_out_of_order") else "attention",
        "totals": {
            "conversations": len(conversations),
            "messages": len(records),
            "characters": archived_characters,
            "estimated_tokens": (archived_characters + 3) // 4,
            "summary_counts": status.get("summary_counts", {}),
            "pending_summary_jobs": status.get("pending_summary_jobs", 0),
            "archived_days": max(1, (now - first.astimezone(timezone.utc)).days + 1) if first else 0,
            "first_archived_at": first.isoformat() if first else None,
        },
        "daily": [
            {"date": day, "messages": daily_messages[day], "characters": daily_characters[day], "estimated_tokens": (daily_characters[day] + 3) // 4}
            for day in sorted(daily_messages)
        ],
        "conversations": conversations,
        "estimation_note": "Estimated tokens use ceil(visible Unicode characters / 4); model tokenizer counts may differ.",
    }


def make_handler(store: MemoryStore):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/status":
                body = json.dumps(dashboard_data(store), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
            elif path in {"/", "/index.html"}:
                body = INDEX_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    store = MemoryStore(Path(args.root).expanduser().resolve(), load_simple_yaml(Path(args.config).expanduser().resolve()))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    url = f"http://{args.host}:{server.server_port}/"
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    print(json.dumps({"status": "serving", "url": url}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
