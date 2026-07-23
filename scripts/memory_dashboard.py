#!/usr/bin/env python3
"""Serve the read-only Memory Wuxian status dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from conversation_titles import (
    archive_conversation_titles,
    codex_thread_metadata,
    codex_thread_titles,
)
from memory_cli import MemoryStore, atomic_write_json, load_simple_yaml, read_jsonl
from memory_cloud_transport import CloudFolderTransport
from memory_federation import FederationManager


SKILL_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = SKILL_ROOT / "dashboard/index.html"
DASHBOARD_ICON = SKILL_ROOT / "assets/memory-wuxian.ico"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


TELEMETRY_CACHE: dict[str, tuple[int, dict[str, int] | None]] = {}
CJK_PATTERN = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
SESSION_SOURCE_CACHE: dict[str, tuple[int, str | None]] = {}


def estimate_context_tokens(text: str) -> int:
    cjk_count = len(CJK_PATTERN.findall(text))
    return cjk_count + (max(0, len(text) - cjk_count) + 3) // 4


def codex_session_source(path: Path) -> str | None:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = SESSION_SOURCE_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    source = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event.get("type") == "session_meta":
                    value = (event.get("payload") or {}).get("source")
                    source = value if isinstance(value, str) else None
                    break
    except (OSError, json.JSONDecodeError):
        source = None
    SESSION_SOURCE_CACHE[str(path)] = (mtime, source)
    return source


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
            latest = {
                "request_tokens": used,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0),
                "context_window": window,
            }
            latest["window_ratio_percent"] = round(used * 100 / window, 2)
            break
    TELEMETRY_CACHE[str(path)] = (stamp, latest)
    return latest


def collector_telemetry(root: Path) -> dict[str, Any] | None:
    path = root / "imports/codex/collector-telemetry.json"
    try:
        telemetry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    telemetry["cpu_percent"] = None
    telemetry["memory_bytes"] = None
    try:
        import psutil
    except ImportError:
        telemetry["process_running"] = None
        return telemetry
    try:
        process = psutil.Process(int(telemetry["pid"]))
        telemetry["cpu_percent"] = round(process.cpu_percent(interval=0.05), 1)
        telemetry["memory_bytes"] = int(process.memory_info().rss)
        telemetry["process_running"] = process.is_running()
    except (psutil.Error, KeyError, TypeError, ValueError, OSError):
        telemetry["process_running"] = False
    return telemetry


def archive_storage_bytes(store: MemoryStore) -> int:
    paths = [store.state_path]
    for directory in (
        store.raw_dir,
        store.conversation_dir,
        store.summaries_dir,
        store.index_dir,
    ):
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def verified_retrieval_stats(store: MemoryStore) -> dict[str, int]:
    verified = [
        entry
        for entry in read_jsonl(store.retrieval_dir / "retrieval-log.jsonl")
        if entry.get("verification") == "verified"
    ]
    source_files = {
        str(path)
        for entry in verified
        for path in entry.get("raw_files", [])
        if path
    }
    return {
        "verified_retrievals": len(verified),
        "retrieval_source_files": len(source_files),
        "max_retrieval_sources": max(
            (len(set(filter(None, entry.get("raw_files", [])))) for entry in verified),
            default=0,
        ),
    }


def dashboard_data(store: MemoryStore) -> dict[str, Any]:
    all_records = store.read_all_raw()
    hidden_conversations: set[str] = set()
    for record in all_records:
        source_path = record.get("source", {}).get("path")
        if source_path and codex_session_source(Path(source_path)) == "exec":
            hidden_conversations.add(str(record["conversation_id"]))
    records = [
        record for record in all_records
        if str(record["conversation_id"]) not in hidden_conversations
    ]
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
    titles = codex_thread_titles()
    thread_metadata = codex_thread_metadata()
    archive_titles = archive_conversation_titles(records)
    for summary in summaries:
        summary_counts[str(summary.get("conversation_id"))][int(summary["level"])] += 1

    conversations = []
    for conversation_id, items in by_conversation.items():
        items.sort(key=lambda item: int(item.get("sequence", 0)))
        first_user = next((str(item.get("text", "")).strip() for item in items if item.get("speaker") == "user"), conversation_id)
        source_path = next((item.get("source", {}).get("path") for item in reversed(items) if item.get("source", {}).get("path")), None)
        telemetry = session_telemetry(Path(source_path)) if source_path else None
        conversation_text = "".join(str(item.get("text", "")) for item in items)
        native_id = conversation_id.removeprefix("codex:")
        metadata = thread_metadata.get(native_id, {})
        source_kind = str((items[0].get("source") or {}).get("kind") or "")
        default_project = "ChatGPT" if source_kind == "chatgpt-data-export" else "Unassigned"
        conversations.append({
            "conversation_id": conversation_id,
            "title": archive_titles[conversation_id],
            "title_source": (
                "codex-thread"
                if conversation_id.removeprefix("codex:") in titles
                else (
                    "source-title"
                    if any(item.get("source", {}).get("conversation_title") for item in items)
                    else "first-user-message"
                )
            ),
            "archived": bool(metadata.get("archived", False)),
            "project": str(metadata.get("project") or default_project),
            "message_count": len(items),
            "tool_activity_count": sum(item.get("speaker") == "tool" for item in items),
            "character_count": sum(len(str(item.get("text", ""))) for item in items),
            "estimated_archive_tokens": estimate_context_tokens(conversation_text),
            "completed_rounds": len({item.get("round_number") for item in items if item.get("completes_round")}),
            "summary_counts": {str(level): count for level, count in sorted(summary_counts[conversation_id].items())},
            "first_timestamp": items[0].get("timestamp"),
            "last_timestamp": items[-1].get("timestamp"),
            "telemetry": telemetry,
        })
    conversations.sort(key=lambda item: str(item.get("last_timestamp", "")), reverse=True)
    active_conversations = [item for item in conversations if not item["archived"]]
    archived_conversations = [item for item in conversations if item["archived"]]

    timestamps = [parse_time(str(item.get("timestamp", ""))) for item in records]
    timestamps = [item for item in timestamps if item]
    first = min(timestamps) if timestamps else None
    now = datetime.now(timezone.utc)
    archived_text = "".join(str(item.get("text", "")) for item in records)
    message_text = "".join(
        str(item.get("text", ""))
        for item in records
        if item.get("speaker") in {"user", "assistant"}
    )
    archived_characters = len(archived_text)
    retrieval_stats = verified_retrieval_stats(store)
    return {
        "generated_at": now.isoformat(),
        "archive_root": str(store.root),
        "health": "ok" if not status.get("completed_rounds_out_of_order") else "attention",
        "collector": collector_telemetry(store.root),
        "totals": {
            "conversations": len(conversations),
            "active_conversations": len(active_conversations),
            "archived_conversations": len(archived_conversations),
            "messages": len(records),
            "tool_activities": sum(item.get("speaker") == "tool" for item in records),
            "characters": archived_characters,
            "estimated_tokens": estimate_context_tokens(archived_text),
            "message_estimated_tokens": estimate_context_tokens(message_text),
            "storage_bytes": archive_storage_bytes(store),
            **retrieval_stats,
            "summary_counts": status.get("summary_counts", {}),
            "pending_summary_jobs": status.get("pending_summary_jobs", 0),
            "archived_days": max(1, (now - first.astimezone(timezone.utc)).days + 1) if first else 0,
            "first_archived_at": first.isoformat() if first else None,
        },
        "daily": [
            {"date": day, "messages": daily_messages[day], "characters": daily_characters[day]}
            for day in sorted(daily_messages)
        ],
        "conversations": conversations,
        "active_conversations": active_conversations,
        "archived_conversations": archived_conversations,
        "character_note": "Visible user and assistant source text stored in the append-only raw archive; summaries are excluded.",
        "estimation_note": "The archive estimate covers visible stored dialogue only. Codex request telemetry has a different scope and can include instructions, tools, reasoning, and outputs; its ratio to the advertised model window is not a precise remaining-context gauge.",
    }


class DashboardSnapshotCache:
    """Persist expensive archive statistics and invalidate them by file metadata."""

    FORMAT_VERSION = 1

    def __init__(self, store: MemoryStore):
        self.store = store
        self.path = store.root / "dashboard/status-snapshot.json"
        self._lock = threading.Lock()
        self._signature = ""
        self._payload: dict[str, Any] | None = None

    @staticmethod
    def _file_stamp(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return str(path), stat.st_size, stat.st_mtime_ns

    def source_signature(self) -> str:
        paths = [
            self.store.state_path,
            self.store.retrieval_dir / "retrieval-log.jsonl",
            Path.home() / ".codex/state_5.sqlite",
            Path.home() / ".codex/state_5.sqlite-wal",
            Path.home() / ".codex/.codex-global-state.json",
        ]
        paths.extend(self.store.raw_dir.rglob("*.md"))
        paths.extend(path for path in self.store.conversation_dir.rglob("*") if path.is_file())
        paths.extend(path for path in self.store.summaries_dir.rglob("*") if path.is_file())
        paths.extend(path for path in self.store.index_dir.rglob("*") if path.is_file())
        paths.extend(self.store.pending_dir.glob("job-*.json"))
        stamps = []
        for path in sorted(set(paths), key=str):
            try:
                stamps.append(self._file_stamp(path))
            except OSError:
                continue
        encoded = json.dumps(stamps, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _load_persisted(self, signature: str) -> dict[str, Any] | None:
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            snapshot.get("format_version") != self.FORMAT_VERSION
            or snapshot.get("source_signature") != signature
            or not isinstance(snapshot.get("payload"), dict)
        ):
            return None
        return snapshot["payload"]

    def get(self) -> dict[str, Any]:
        signature = self.source_signature()
        with self._lock:
            if self._payload is not None and self._signature == signature:
                payload = self._payload
            else:
                payload = self._load_persisted(signature)
                if payload is None:
                    payload = dashboard_data(self.store)
                    signature = self.source_signature()
                    atomic_write_json(
                        self.path,
                        {
                            "format_version": self.FORMAT_VERSION,
                            "source_signature": signature,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "payload": payload,
                        },
                    )
                self._payload = payload
                self._signature = signature
        response = dict(payload)
        response["collector"] = collector_telemetry(self.store.root)
        response["served_at"] = datetime.now(timezone.utc).isoformat()
        response["snapshot"] = {
            "source_signature": signature,
            "persisted": True,
        }
        return response


def make_handler(store: MemoryStore):
    snapshot_cache = DashboardSnapshotCache(store)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/status":
                body = json.dumps(snapshot_cache.get(), ensure_ascii=False).encode("utf-8")
                etag = f'"{hashlib.sha256(body).hexdigest()}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("ETag", etag)
            elif path == "/api/devices":
                federation_manager = FederationManager(store)
                devices = federation_manager.status()
                devices["cloud"] = CloudFolderTransport(
                    federation_manager
                ).status()
                body = json.dumps(
                    devices,
                    ensure_ascii=False,
                ).encode("utf-8")
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


def run_window(server: ThreadingHTTPServer, url: str) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Native dashboard windows require pywebview. Run bootstrap_windows.ps1 -InstallMissing."
        ) from exc
    thread = threading.Thread(target=server.serve_forever, name="memory-wuxian-dashboard", daemon=True)
    thread.start()
    try:
        webview.create_window(
            "Memory无限状态台",
            url,
            width=1180,
            height=760,
            min_size=(760, 520),
            background_color="#f6f8f5",
        )
        webview.start(
            gui="edgechromium",
            private_mode=True,
            icon=str(DASHBOARD_ICON) if DASHBOARD_ICON.exists() else None,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--window", action="store_true", help="Open a native WebView2 application window")
    args = parser.parse_args()
    store = MemoryStore(Path(args.root).expanduser().resolve(), load_simple_yaml(Path(args.config).expanduser().resolve()))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    url = f"http://{args.host}:{server.server_port}/"
    if args.window:
        print(json.dumps({"status": "opening-window", "url": url}, ensure_ascii=False), flush=True)
        run_window(server, url)
        return 0
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    print(json.dumps({"status": "serving", "url": url}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
