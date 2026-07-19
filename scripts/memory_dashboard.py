#!/usr/bin/env python3
"""Serve the read-only Memory Wuxian status dashboard."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
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
THREAD_ID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
THREAD_ID_SEARCH_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
RUNTIME_TITLE_CACHE: tuple[float, dict[str, str]] = (0.0, {})


def estimate_context_tokens(text: str) -> int:
    cjk_count = len(CJK_PATTERN.findall(text))
    return cjk_count + (max(0, len(text) - cjk_count) + 3) // 4


def codex_runtime_titles() -> dict[str, str]:
    global RUNTIME_TITLE_CACHE
    if time.monotonic() - RUNTIME_TITLE_CACHE[0] < 60:
        return RUNTIME_TITLE_CACHE[1]
    codex = Path.home() / ".codex/.sandbox-bin/codex.exe"
    executable = str(codex) if codex.exists() else shutil.which("codex")
    if not executable:
        return {}
    process = None
    killer = None
    titles: dict[str, str] = {}
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        killer = threading.Timer(3, process.terminate)
        killer.start()
        assert process.stdin and process.stdout
        requests = [
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "memory-wuxian-dashboard", "version": "1.0"}}},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "thread/list", "params": {"limit": 100, "archived": False}},
        ]
        for request in requests:
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            response = json.loads(line)
            if response.get("id") != 2:
                continue
            for thread in (response.get("result") or {}).get("data", []):
                if thread.get("id") and thread.get("name"):
                    titles[str(thread["id"])] = str(thread["name"]).strip()
            break
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError):
        titles = {}
    finally:
        if killer:
            killer.cancel()
        if process and process.poll() is None:
            process.terminate()
        if process:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
    RUNTIME_TITLE_CACHE = (time.monotonic(), titles)
    return titles


def codex_thread_titles() -> dict[str, str]:
    runtime_titles = codex_runtime_titles()
    global_state = Path.home() / ".codex/.codex-global-state.json"
    sidebar_candidates: dict[str, list[str]] = defaultdict(list)
    try:
        state = json.loads(global_state.read_text(encoding="utf-8"))

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                object_id = str(value.get("id", ""))
                object_title = value.get("title")
                if THREAD_ID_PATTERN.fullmatch(object_id) and isinstance(object_title, str):
                    sidebar_candidates[object_id].append(object_title.strip())
                for key, child in value.items():
                    key_text = str(key)
                    embedded_id = THREAD_ID_SEARCH_PATTERN.search(key_text)
                    candidate_id = key_text if THREAD_ID_PATTERN.fullmatch(key_text) else (
                        embedded_id.group(0) if embedded_id and "title" in key_text.casefold() else None
                    )
                    if candidate_id and isinstance(child, str):
                        candidate = child.strip()
                        if (
                            candidate
                            and "\\" not in candidate
                            and "/" not in candidate
                            and not candidate.startswith(("client-", "local-", "remote-"))
                            and len(candidate) <= 120
                        ):
                            sidebar_candidates[candidate_id].append(candidate)
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(state)
    except (OSError, json.JSONDecodeError):
        pass

    database = Path.home() / ".codex/state_5.sqlite"
    database_titles: dict[str, str] = {}
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            database_titles = {
                str(thread_id): str(title).strip()
                for thread_id, title in connection.execute("SELECT id, title FROM threads")
                if str(title).strip()
            }
        finally:
            connection.close()
    except sqlite3.Error:
        pass
    fallback_titles = {
        thread_id: min(candidates, key=len)
        for thread_id in set(database_titles) | set(sidebar_candidates)
        if (candidates := sidebar_candidates.get(thread_id) or [database_titles[thread_id]])
    }
    return {**fallback_titles, **runtime_titles}


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

        process = psutil.Process(int(telemetry["pid"]))
        telemetry["cpu_percent"] = round(process.cpu_percent(interval=0.05), 1)
        telemetry["memory_bytes"] = int(process.memory_info().rss)
        telemetry["process_running"] = process.is_running()
    except (ImportError, KeyError, TypeError, ValueError, OSError):
        telemetry["process_running"] = None
    return telemetry


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
    titles = codex_thread_titles()
    for summary in summaries:
        summary_counts[str(summary.get("conversation_id"))][int(summary["level"])] += 1

    conversations = []
    for conversation_id, items in by_conversation.items():
        items.sort(key=lambda item: int(item.get("sequence", 0)))
        first_user = next((str(item.get("text", "")).strip() for item in items if item.get("speaker") == "user"), conversation_id)
        source_path = next((item.get("source", {}).get("path") for item in reversed(items) if item.get("source", {}).get("path")), None)
        telemetry = session_telemetry(Path(source_path)) if source_path else None
        conversation_text = "".join(str(item.get("text", "")) for item in items)
        conversations.append({
            "conversation_id": conversation_id,
            "title": titles.get(conversation_id.removeprefix("codex:"), first_user.replace("\n", " ")[:72]),
            "title_source": "codex-thread" if conversation_id.removeprefix("codex:") in titles else "first-user-message",
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

    timestamps = [parse_time(str(item.get("timestamp", ""))) for item in records]
    timestamps = [item for item in timestamps if item]
    first = min(timestamps) if timestamps else None
    now = datetime.now(timezone.utc)
    archived_text = "".join(str(item.get("text", "")) for item in records)
    archived_characters = len(archived_text)
    return {
        "generated_at": now.isoformat(),
        "archive_root": str(store.root),
        "health": "ok" if not status.get("completed_rounds_out_of_order") else "attention",
        "collector": collector_telemetry(store.root),
        "totals": {
            "conversations": len(conversations),
            "messages": len(records),
            "tool_activities": sum(item.get("speaker") == "tool" for item in records),
            "characters": archived_characters,
            "estimated_tokens": estimate_context_tokens(archived_text),
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
        "character_note": "Visible user and assistant source text stored in the append-only raw archive; summaries are excluded.",
        "estimation_note": "The archive estimate covers visible stored dialogue only. Codex request telemetry has a different scope and can include instructions, tools, reasoning, and outputs; its ratio to the advertised model window is not a precise remaining-context gauge.",
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
