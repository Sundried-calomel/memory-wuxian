#!/usr/bin/env python3
"""Serve the local Memory Wuxian dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from conversation_titles import (
    archive_conversation_titles,
    codex_thread_metadata,
    codex_thread_titles,
)
from memory_cli import (
    MemoryStore,
    atomic_write_json,
    environment_cloud_transport,
    load_simple_yaml,
    local_platform_name,
    local_runtime_versions,
    read_jsonl,
)
from memory_cloud_transport import CloudFolderTransport
from memory_environment import EnvironmentRegistry
from memory_environment_conflicts import EnvironmentConflictStore
from memory_environment_incoming import EnvironmentIncomingProcessor
from memory_environment_promotions import PromotionStore
from memory_federation import FederationManager
from memory_governance_ai import GovernanceAIQueue
from memory_guarded_features import GuardedFeatures
from platform_lock import exclusive_lock
from platform_process import no_window_kwargs
from token_usage import aggregate_ledgers, normalize_usage, token_usage_ledgers


SKILL_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = SKILL_ROOT / "dashboard/index.html"
DASHBOARD_ICON = SKILL_ROOT / "assets/memory-wuxian.ico"
CLOUD_SCHEDULER_LABEL = "com.openai.codex.memory-wuxian-cloud-sync"
CLOUD_SCHEDULER_TASK = "MemoryWuxianCloudSync"
GOVERNANCE_AI_SCHEDULER_LABEL = "com.openai.codex.memory-wuxian-governance-ai"
GOVERNANCE_AI_SCHEDULER_TASK = "MemoryWuxianGovernanceAI"


def background_subprocess_kwargs() -> dict[str, Any]:
    """Compatibility wrapper for the shared no-console process policy."""
    if sys.platform != "win32":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def cloud_scheduler_status() -> dict[str, Any]:
    if sys.platform == "darwin":
        plist = (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"{CLOUD_SCHEDULER_LABEL}.plist"
        )
        installed = plist.is_file()
        running = False
        if installed:
            uid = os.getuid()
            result = subprocess.run(
                [
                    "/bin/launchctl",
                    "print",
                    f"gui/{uid}/{CLOUD_SCHEDULER_LABEL}",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
            running = result.returncode == 0
        return {
            "platform": "macos",
            "installed": installed,
            "running": running,
        }
    if sys.platform == "win32":
        import winreg

        task_key = (
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule"
            rf"\TaskCache\Tree\{CLOUD_SCHEDULER_TASK}"
        )
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, task_key):
                installed = True
        except OSError:
            installed = False
        return {
            "platform": "windows",
            "installed": installed,
            "running": installed,
        }
    return {
        "platform": sys.platform,
        "installed": False,
        "running": False,
    }


def governance_ai_scheduler_status() -> dict[str, Any]:
    if sys.platform == "darwin":
        plist = (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"{GOVERNANCE_AI_SCHEDULER_LABEL}.plist"
        )
        installed = plist.is_file()
        running = False
        if installed:
            result = subprocess.run(
                [
                    "/bin/launchctl",
                    "print",
                    f"gui/{os.getuid()}/{GOVERNANCE_AI_SCHEDULER_LABEL}",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
            running = result.returncode == 0
        return {"platform": "macos", "installed": installed, "running": running}
    if sys.platform == "win32":
        import winreg

        key = (
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule"
            rf"\TaskCache\Tree\{GOVERNANCE_AI_SCHEDULER_TASK}"
        )
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key):
                installed = True
        except OSError:
            installed = False
        return {"platform": "windows", "installed": installed, "running": installed}
    return {"platform": sys.platform, "installed": False, "running": False}


def set_governance_ai_scheduler(store: MemoryStore, enabled: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "install_governance_ai.py"),
        "--archive-root",
        str(store.root),
        "--skill-root",
        str(SKILL_ROOT),
        "--python-executable",
        sys.executable,
        "--load" if enabled else "--uninstall",
    ]
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **background_subprocess_kwargs(),
    )
    return governance_ai_scheduler_status()


def set_cloud_scheduler(store: MemoryStore, enabled: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "install_cloud_sync.py"),
        "--archive-root",
        str(store.root.resolve()),
        "--skill-root",
        str(SKILL_ROOT),
        "--python-executable",
        sys.executable,
        "--load" if enabled else "--uninstall",
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        **background_subprocess_kwargs(),
    )
    return {
        "command": "installed" if enabled else "uninstalled",
        "detail": result.stdout.strip(),
        **cloud_scheduler_status(),
    }


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


def persisted_session_telemetry(ledger: dict[str, Any]) -> dict[str, Any] | None:
    reported = normalize_usage(ledger.get("reported_usage"))
    latest = normalize_usage(ledger.get("latest_request_usage"))
    if reported is None or latest is None:
        return None
    window = int(ledger.get("model_context_window") or 0)
    request_tokens = int(latest["total_tokens"])
    return {
        "measurement": "codex-reported-model-usage",
        "request_tokens": request_tokens,
        "input_tokens": int(latest["input_tokens"]),
        "cached_input_tokens": int(latest["cached_input_tokens"]),
        "cache_write_input_tokens": int(latest["cache_write_input_tokens"]),
        "output_tokens": int(latest["output_tokens"]),
        "reasoning_output_tokens": int(latest["reasoning_output_tokens"]),
        "context_window": window,
        "window_ratio_percent": round(request_tokens * 100 / window, 2) if window else None,
        "reported_usage": reported,
        "reported_total_tokens": int(reported["total_tokens"]),
        "reported_input_tokens": int(reported["input_tokens"]),
        "reported_cached_input_tokens": int(reported["cached_input_tokens"]),
        "reported_cache_write_input_tokens": int(
            reported["cache_write_input_tokens"]
        ),
        "reported_output_tokens": int(reported["output_tokens"]),
        "reported_reasoning_output_tokens": int(
            reported["reasoning_output_tokens"]
        ),
        "model_request_count": int(ledger.get("model_request_count") or 0),
        "counter_reset_count": int(ledger.get("counter_reset_count") or 0),
        "last_token_event": ledger.get("last_token_event"),
        "updated_at": ledger.get("updated_at"),
    }


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


def dashboard_health(status: dict[str, Any]) -> str:
    actionable_fields = (
        "integrity_issues",
        "issues",
        "warnings",
        "failed_jobs",
        "failed_summary_jobs",
    )
    return (
        "attention"
        if any(status.get(field) for field in actionable_fields)
        else "ok"
    )


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
    usage_ledgers = token_usage_ledgers(store.root)
    usage_by_conversation = {
        str(ledger.get("conversation_id")): ledger
        for ledger in usage_ledgers
        if ledger.get("conversation_id")
    }
    reported_usage = aggregate_ledgers(usage_ledgers)
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
        telemetry = (
            persisted_session_telemetry(usage_by_conversation[conversation_id])
            if conversation_id in usage_by_conversation
            else (session_telemetry(Path(source_path)) if source_path else None)
        )
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
            "origin_node": str((items[0].get("source") or {}).get("origin_node") or "local"),
            "source_kind": source_kind or "codex",
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
    codex_conversation_ids = {
        conversation_id
        for conversation_id in by_conversation
        if conversation_id.startswith("codex:")
    }
    measured_codex_conversations = len(
        codex_conversation_ids.intersection(usage_by_conversation)
    )
    return {
        "generated_at": now.isoformat(),
        "archive_root": str(store.root),
        "health": dashboard_health(status),
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
            "reported_total_tokens": int(
                reported_usage["reported_usage"]["total_tokens"]
            ),
            "reported_input_tokens": int(
                reported_usage["reported_usage"]["input_tokens"]
            ),
            "reported_cached_input_tokens": int(
                reported_usage["reported_usage"]["cached_input_tokens"]
            ),
            "reported_output_tokens": int(
                reported_usage["reported_usage"]["output_tokens"]
            ),
            "reported_reasoning_output_tokens": int(
                reported_usage["reported_usage"]["reasoning_output_tokens"]
            ),
            "token_usage_conversations": int(
                reported_usage["measured_conversations"]
            ),
            "token_usage_model_requests": int(
                reported_usage["model_request_count"]
            ),
            "token_usage_counter_resets": int(
                reported_usage["counter_reset_count"]
            ),
            "measured_archived_codex_conversations": measured_codex_conversations,
            "unmeasured_archived_codex_conversations": (
                len(codex_conversation_ids) - measured_codex_conversations
            ),
            "storage_bytes": archive_storage_bytes(store),
            **retrieval_stats,
            "summary_counts": status.get("summary_counts", {}),
            "policy_events": status.get("policy_events", 0),
            "active_policies": status.get("active_policies", 0),
            "policy_events_needing_review": status.get(
                "policy_events_needing_review", 0
            ),
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
        "estimation_note": "The archive estimate covers visible stored dialogue only. Codex-reported usage is persisted separately from rollout token_count telemetry and can include instructions, tools, reasoning, cached input, and outputs. Cached input and reasoning are reported subfields and are not added to total_tokens a second time.",
    }


class DashboardSnapshotCache:
    """Persist expensive archive statistics and invalidate them by file metadata."""

    FORMAT_VERSION = 3

    def __init__(self, store: MemoryStore):
        self.store = store
        self.path = store.root / "dashboard/status-snapshot.json"
        self._lock = threading.Lock()
        self._signature = ""
        self._payload: dict[str, Any] | None = None
        self._refreshing = False

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
        token_usage_dir = getattr(
            self.store,
            "codex_token_usage_dir",
            self.store.root / "imports" / "codex" / "token-usage",
        )
        paths.extend(
            path for path in token_usage_dir.glob("*.json") if path.is_file()
        )
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

    def get_fast(self) -> dict[str, Any]:
        with self._lock:
            payload = self._payload
            if payload is None:
                try:
                    snapshot = json.loads(self.path.read_text(encoding="utf-8"))
                    payload = snapshot.get("payload")
                except (OSError, json.JSONDecodeError):
                    payload = None
            if isinstance(payload, dict):
                response = dict(payload)
                response["collector"] = collector_telemetry(self.store.root)
                response["served_at"] = datetime.now(timezone.utc).isoformat()
                response["snapshot"] = {"persisted": True, "refreshing": True}
                if not self._refreshing:
                    self._refreshing = True

                    def refresh() -> None:
                        try:
                            self.get()
                        finally:
                            with self._lock:
                                self._refreshing = False

                    threading.Thread(
                        target=refresh,
                        name="memory-wuxian-dashboard-refresh",
                        daemon=True,
                    ).start()
                return response
        return self.get()


class EnvironmentDashboardCache:
    """Cache a read-only Environment inventory independently from archive data."""

    FORMAT_VERSION = 1
    OBJECT_CLASSES = (
        "global-rule",
        "project-rule",
        "global-skill",
        "project-skill",
    )
    ACTIVITY_DIRECTORIES = (
        "conflicts",
        "promotions",
        "receipts",
        "staging",
        "governance-ai",
    )

    def __init__(self, archive_root: Path):
        self.registry = EnvironmentRegistry(archive_root)
        self.root = self.registry.root
        self._lock = threading.Lock()
        self._signature = ""
        self._payload: dict[str, Any] | None = None

    @staticmethod
    def _file_stamp(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return str(path), stat.st_size, stat.st_mtime_ns

    def source_signature(self) -> str:
        if not self.root.is_dir():
            return "uninitialized"
        paths = [self.registry.registry_path, self.registry.state_path]
        directory_stamps = []
        for name in self.ACTIVITY_DIRECTORIES:
            directory = self.root / name
            if directory.is_dir():
                try:
                    stat = directory.stat()
                    directory_stamps.append((str(directory), stat.st_mtime_ns))
                except OSError:
                    pass
                paths.extend(path for path in directory.rglob("*.json") if path.is_file())
        stamps = []
        for path in sorted(set(paths), key=str):
            try:
                stamps.append(self._file_stamp(path))
            except OSError:
                continue
        encoded = json.dumps(
            [stamps, directory_stamps],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _read_json_objects(self, directory_name: str) -> list[dict[str, Any]]:
        directory = self.root / directory_name
        if not directory.is_dir():
            return []
        values = []
        for path in sorted(directory.rglob("*.json"), key=str):
            value = self._read_json_object(path)
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _latest_by(
        values: list[dict[str, Any]], key: str
    ) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for value in values:
            identifier = value.get(key)
            if isinstance(identifier, str) and identifier:
                latest[identifier] = value
        return latest

    def build(self) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        if not self.root.is_dir():
            return {
                "format_version": self.FORMAT_VERSION,
                "initialized": False,
                "validation_status": "not-initialized",
                "validation_error": None,
                "generated_at": generated_at,
                "object_classes": {
                    name: {"count": 0} for name in self.OBJECT_CLASSES
                },
                "projects": [],
                "artifacts": [],
                "conflicts": [],
                "promotions": [],
                "installations": [],
                "incoming": {
                    "staged_events": 0,
                    "processed_events": 0,
                    "decision_counts": {},
                    "pending_conflicts": 0,
                },
                "governance_ai": GovernanceAIQueue(
                    self.registry.archive_root
                ).status(),
            }

        try:
            status = self.registry.status()
            records = self.registry.list()
            projects = self.registry.projects()
            shown_records = [
                self.registry.show(record["artifact_id"]) for record in records
            ]
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            return {
                "format_version": self.FORMAT_VERSION,
                "initialized": True,
                "validation_status": "needs-attention",
                "validation_error": str(error),
                "generated_at": generated_at,
                "object_classes": {
                    name: {"count": 0} for name in self.OBJECT_CLASSES
                },
                "projects": [],
                "artifacts": [],
                "conflicts": [],
                "promotions": [],
                "installations": [],
                "incoming": {
                    "staged_events": 0,
                    "processed_events": 0,
                    "decision_counts": {},
                    "pending_conflicts": 0,
                },
                "governance_ai": {
                    **GovernanceAIQueue(self.registry.archive_root).status(),
                    "scheduler": governance_ai_scheduler_status(),
                },
            }
        conflicts = EnvironmentConflictStore(self.registry.archive_root).list()
        promotions = PromotionStore(self.registry.archive_root).list()
        receipts = [
            value
            for value in self._read_json_objects("receipts")
            if value.get("artifact_id") and value.get("revision_id")
        ]
        latest_conflicts = self._latest_by(conflicts, "artifact_id")
        latest_installations = self._latest_by(receipts, "artifact_id")
        class_counts: Counter[str] = Counter()
        artifacts = []
        for shown in shown_records:
            artifact = shown["artifact"]
            revision = shown["revision"]
            artifact_id = artifact["artifact_id"]
            object_class = artifact["object_class"]
            class_counts[object_class] += 1
            installation = latest_installations.get(artifact_id, {})
            conflict = latest_conflicts.get(artifact_id, {})
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "object_class": object_class,
                    "display_name": artifact.get("display_name"),
                    "scope": artifact["scope"],
                    "project_id": artifact.get("project_id"),
                    "revision_id": revision["revision_id"],
                    "version": revision["version"],
                    "base_revision_id": revision["base_revision_id"],
                    "lifecycle_state": revision["lifecycle_state"],
                    "installation_status": installation.get("result"),
                    "conflict_status": conflict.get("status")
                    or conflict.get("resolution_state"),
                    "promotion_status": next(
                        (
                            item.get("review_state")
                            for item in promotions
                            if item.get("source_skill_id")
                            == artifact_id.split(":", 1)[-1]
                        ),
                        None,
                    ),
                }
            )
        return {
            "format_version": self.FORMAT_VERSION,
            "initialized": bool(status["initialized"]),
            "validation_status": "valid",
            "validation_error": None,
            "generated_at": generated_at,
            "object_classes": {
                name: {"count": class_counts[name]}
                for name in self.OBJECT_CLASSES
            },
            "projects": [
                {
                    key: value.get(key)
                    for key in (
                        "project_id",
                        "display_name",
                        "active",
                        "local_root",
                    )
                    if key in value
                }
                for value in projects
            ],
            "artifacts": artifacts,
            "conflicts": [
                {
                    key: value.get(key)
                    for key in (
                        "conflict_id",
                        "artifact_id",
                        "status",
                        "resolution_state",
                        "created_at",
                    )
                    if key in value
                }
                for value in conflicts
            ],
            "promotions": [
                {
                    key: value.get(key)
                    for key in (
                        "promotion_id",
                        "source_project_id",
                        "source_skill_id",
                        "classification",
                        "review_state",
                    )
                    if key in value
                }
                for value in promotions
            ],
            "installations": [
                {
                    key: value.get(key)
                    for key in (
                        "receipt_id",
                        "artifact_id",
                        "revision_id",
                        "result",
                        "created_at",
                    )
                    if key in value
                }
                for value in receipts
            ],
            "incoming": EnvironmentIncomingProcessor(
                self.registry.archive_root,
                platform=local_platform_name(),
                runtime_versions=local_runtime_versions([]),
            ).status(),
            "governance_ai": {
                **GovernanceAIQueue(self.registry.archive_root).status(),
                "scheduler": governance_ai_scheduler_status(),
            },
        }

    def get(self) -> dict[str, Any]:
        signature = self.source_signature()
        with self._lock:
            if self._payload is None or self._signature != signature:
                self._payload = self.build()
                self._signature = signature
            payload = dict(self._payload)
        payload["served_at"] = datetime.now(timezone.utc).isoformat()
        payload["snapshot"] = {
            "source_signature": signature,
            "persisted": False,
        }
        return payload


def make_handler(store: MemoryStore):
    snapshot_cache = DashboardSnapshotCache(store)
    environment_cache = EnvironmentDashboardCache(store.root)

    class Handler(BaseHTTPRequestHandler):
        def memory_search(self, request) -> dict[str, Any]:
            parameters = parse_qs(request.query)
            query = str(parameters.get("q", [""])[0]).strip()
            mode = str(parameters.get("mode", ["hybrid"])[0])
            if not query or len(query) > 500:
                raise ValueError("query must contain 1 to 500 characters")
            if mode not in {"keyword", "semantic", "hybrid"}:
                raise ValueError("search mode must be keyword, semantic, or hybrid")
            try:
                limit = max(1, min(50, int(parameters.get("limit", ["20"])[0])))
            except ValueError as exc:
                raise ValueError("limit must be an integer") from exc

            raw_records = store.read_all_raw()
            raw_by_id = {str(item["message_id"]): item for item in raw_records}
            titles = archive_conversation_titles(raw_records)
            ranked: dict[str, dict[str, Any]] = {}
            normalized_query = store.normalize_search_text(query)
            query_terms = [term for term in normalized_query.split() if term]

            if mode in {"keyword", "hybrid"}:
                for record in raw_records:
                    text = str(record.get("text", ""))
                    normalized = store.normalize_search_text(text)
                    if not normalized:
                        continue
                    exact = normalized_query in normalized
                    matched = sum(term in normalized for term in query_terms)
                    if not exact and not matched:
                        continue
                    score = 1.0 if exact else matched / max(1, len(query_terms))
                    ranked[str(record["message_id"])] = {
                        "keyword_score": score,
                        "semantic_score": None,
                    }

            if mode in {"semantic", "hybrid"}:
                semantic = GuardedFeatures(store).semantic_retrieve(
                    query, max(limit * 3, 30)
                )
                for position, match in enumerate(semantic["matches"]):
                    message_id = str(match["message_id"])
                    item = ranked.setdefault(
                        message_id,
                        {"keyword_score": None, "semantic_score": None},
                    )
                    item["semantic_score"] = float(match["score"])
                    item["semantic_rank"] = position + 1

            results = []
            for message_id, scores in ranked.items():
                record = raw_by_id.get(message_id)
                if not record:
                    continue
                keyword_score = scores.get("keyword_score")
                semantic_score = scores.get("semantic_score")
                if mode == "keyword":
                    score = float(keyword_score or 0)
                elif mode == "semantic":
                    score = float(semantic_score or 0)
                else:
                    score = max(
                        float(keyword_score or 0),
                        float(semantic_score or 0),
                    )
                    if keyword_score is not None and semantic_score is not None:
                        score = min(1.0, score + 0.08)
                if record.get("speaker") == "tool":
                    score *= 0.72
                conversation_id = str(record.get("conversation_id", ""))
                results.append({
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "conversation_title": titles.get(conversation_id, conversation_id),
                    "timestamp": record.get("timestamp"),
                    "speaker": record.get("speaker"),
                    "record_type": record.get("record_type"),
                    "text": str(record.get("text", "")),
                    "score": round(score, 8),
                    "keyword_score": keyword_score,
                    "semantic_score": semantic_score,
                    "raw_path": record.get("_path"),
                    "raw_line_start": None,
                    "raw_line_end": None,
                    "record_sha256": record.get("content_sha256"),
                })
            results.sort(
                key=lambda item: (item["score"], str(item["timestamp"] or "")),
                reverse=True,
            )
            selected = results[:limit]
            pointers = GuardedFeatures(store)
            for item in selected:
                item.update(pointers.raw_pointer(raw_by_id[item["message_id"]]))
            return {
                "query": query,
                "mode": mode,
                "count": min(limit, len(results)),
                "results": selected,
                "verified_against_raw": True,
                "semantic_provider": "multilingual-e5-small"
                if mode in {"semantic", "hybrid"} else None,
            }

        def handle(self) -> None:
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                return

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def discard_request_body(self, maximum: int = 65536) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if 0 < length <= maximum:
                self.rfile.read(length)

        def cloud_payload(self) -> dict[str, Any]:
            federation_manager = FederationManager(store)
            devices = federation_manager.status()
            archive_transport = CloudFolderTransport(federation_manager)
            cloud = dict(archive_transport.status())
            archive_stream = {"stream_id": "archive-v1", **cloud}
            environment_transport = environment_cloud_transport(
                store, archive_transport, bootstrap=False
            )
            cloud["streams"] = {
                "archive-v1": archive_stream,
                "environment-v1": environment_transport.status(),
            }
            cloud["scheduler"] = cloud_scheduler_status()
            devices["cloud"] = cloud
            return devices

        def do_GET(self):
            request = urlparse(self.path)
            path = request.path
            if path == "/api/status":
                payload = (
                    snapshot_cache.get()
                    if request.query == "refresh=1"
                    else snapshot_cache.get_fast()
                )
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
            elif path == "/api/events":
                event_path = store.root / "dashboard/events.jsonl"
                try:
                    stat = event_path.stat()
                    last_stamp: tuple[int, int] = (
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                except OSError:
                    last_stamp = (0, 0)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last_heartbeat = 0.0
                try:
                    while True:
                        try:
                            stat = event_path.stat()
                            stamp = (stat.st_size, stat.st_mtime_ns)
                        except OSError:
                            stamp = (0, 0)
                        if stamp != last_stamp:
                            last_stamp = stamp
                            payload = snapshot_cache.get()
                            event_id = str(int(time.time() * 1000))
                            frame = (
                                f"id: {event_id}\n"
                                "event: status\n"
                                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            )
                            self.wfile.write(frame.encode("utf-8"))
                            self.wfile.flush()
                        if time.monotonic() - last_heartbeat >= 15:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            last_heartbeat = time.monotonic()
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                return
            elif path == "/api/devices":
                body = json.dumps(self.cloud_payload(), ensure_ascii=False).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
            elif path == "/api/environment":
                body = json.dumps(
                    environment_cache.get(), ensure_ascii=False
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
            elif path == "/api/memory-search":
                try:
                    body = json.dumps(
                        self.memory_search(request), ensure_ascii=False
                    ).encode("utf-8")
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(500, {"error": str(exc)})
                    return
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

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in {
                "/api/cloud",
                "/api/import-chatgpt",
                "/api/environment",
            }:
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self.discard_request_body()
                self.close_connection = True
                self.send_json(403, {"error": "origin-not-allowed"})
                return
            if path == "/api/import-chatgpt":
                self.import_chatgpt()
                return
            if not self.headers.get("Content-Type", "").startswith(
                "application/json"
            ):
                self.send_json(415, {"error": "json-required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    raise ValueError("invalid request length")
                request = json.loads(self.rfile.read(length))
                action = request.get("action")
                if path == "/api/environment":
                    if action == "process-incoming":
                        result = EnvironmentIncomingProcessor(
                            store.root,
                            platform=local_platform_name(),
                            runtime_versions=local_runtime_versions([]),
                        ).process(
                            apply=True,
                            auto_register_compatible_rules=False,
                            maximum_events=100,
                        )
                    elif action == "governance-ai-enable":
                        queue = GovernanceAIQueue(store)
                        policy = queue.policy()
                        coordinator = (
                            policy["coordinator_node_id"]
                            or queue.local_node_id()
                        )
                        configured = queue.configure(
                            {
                                "enabled": True,
                                "mode": "automatic-drafts",
                                "coordinator_node_id": coordinator,
                            },
                            apply=True,
                        )
                        try:
                            scheduler = set_governance_ai_scheduler(store, True)
                        except Exception:
                            queue.configure({"enabled": False}, apply=True)
                            raise
                        result = {
                            "status": "enabled",
                            "policy": configured["policy"],
                            "scheduler": scheduler,
                        }
                    elif action == "governance-ai-disable":
                        queue = GovernanceAIQueue(store)
                        configured = queue.configure(
                            {"enabled": False}, apply=True
                        )
                        result = {
                            "status": "disabled",
                            "policy": configured["policy"],
                            "scheduler": set_governance_ai_scheduler(store, False),
                        }
                    elif action == "governance-ai-run":
                        result = GovernanceAIQueue(store).tick(
                            run_ai=True,
                            maximum_batches=1,
                        )
                    else:
                        raise ValueError("unsupported Environment action")
                    self.send_json(
                        200,
                        {
                            "result": result,
                            "environment": environment_cache.get(),
                        },
                    )
                    return
                manager = FederationManager(store)
                transport = CloudFolderTransport(manager)
                environment_transport = environment_cloud_transport(
                    store, transport, bootstrap=True
                )
                if action == "enable":
                    if not transport.status().get("configured"):
                        raise ValueError("cloud transport is not configured")
                    if not environment_transport.status().get("configured"):
                        raise ValueError(
                            "environment cloud transport is not configured"
                        )
                    transport.set_enabled(True)
                    environment_transport.set_enabled(True)
                    try:
                        scheduler = set_cloud_scheduler(store, True)
                    except Exception:
                        transport.set_enabled(False)
                        environment_transport.set_enabled(False)
                        raise
                    result = {"status": "enabled", "scheduler": scheduler}
                elif action == "disable":
                    transport.set_enabled(False)
                    environment_transport.set_enabled(False)
                    result = {
                        "status": "disabled",
                        "scheduler": set_cloud_scheduler(store, False),
                    }
                elif action == "sync":
                    if not transport.status().get("enabled"):
                        raise ValueError("cloud transport is disabled")
                    if not environment_transport.status().get("enabled"):
                        raise ValueError(
                            "environment cloud transport is disabled"
                        )
                    streams: dict[str, dict[str, Any]] = {}
                    with exclusive_lock(
                        store.root / ".locks" / "federation.lock"
                    ):
                        for name, stream_transport in (
                            ("archive", transport),
                            ("environment", environment_transport),
                        ):
                            try:
                                stream_result = stream_transport.sync(force=True)
                                streams[name] = {
                                    "status": "ok",
                                    "result": stream_result,
                                }
                            except Exception as exc:
                                streams[name] = {
                                    "status": "error",
                                    "error": str(exc),
                                }
                    successful = sum(
                        stream["status"] == "ok" for stream in streams.values()
                    )
                    result = {
                        "status": (
                            "ok"
                            if successful == len(streams)
                            else "partial" if successful else "failed"
                        ),
                        "streams": streams,
                    }
                else:
                    raise ValueError("unsupported cloud action")
                self.send_json(
                    200,
                    {
                        "result": result,
                        "devices": self.cloud_payload(),
                    },
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except subprocess.CalledProcessError as exc:
                self.send_json(
                    500,
                    {
                        "error": "cloud scheduler command failed",
                        "returncode": exc.returncode,
                    },
                )
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})

        def import_chatgpt(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 20 * 1024 * 1024 * 1024:
                    raise ValueError("invalid export file size")
                filename = Path(
                    unquote(self.headers.get("X-Filename", "chatgpt-export.zip"))
                ).name
                suffix = Path(filename).suffix.casefold()
                if suffix not in {".zip", ".json"}:
                    raise ValueError("select a ChatGPT export ZIP or conversations.json")
                temporary_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        prefix="memory-wuxian-chatgpt-",
                        suffix=suffix,
                        delete=False,
                    ) as temporary:
                        temporary_path = Path(temporary.name)
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("incomplete export upload")
                            temporary.write(chunk)
                            remaining -= len(chunk)
                    result = store.import_chatgpt_export(temporary_path)
                    if result["imported_messages"] or result["repaired_transcripts"]:
                        backup = store.create_backup_snapshot(
                            "chatgpt-export-import",
                            {
                                "source": filename,
                                "imported_messages": result["imported_messages"],
                                "imported_conversations": result[
                                    "imported_conversations"
                                ],
                            },
                        )
                        result["backup"] = str(backup) if backup else None
                    else:
                        result["backup"] = None
                    result["source"] = filename
                    self.send_json(200, {"result": result})
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
            except (ValueError, json.JSONDecodeError, OSError, zipfile.BadZipFile) as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})

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
        def apply_windows_icon() -> None:
            if os.name != "nt" or not DASHBOARD_ICON.exists():
                return
            import ctypes
            import time

            user32 = ctypes.windll.user32
            image_icon = 1
            load_from_file = 0x0010
            wm_seticon = 0x0080
            icon_small = 0
            icon_big = 1
            handle = 0
            for _ in range(100):
                handle = user32.FindWindowW(None, "Memory无限状态台")
                if handle:
                    break
                time.sleep(0.05)
            if not handle:
                return
            icon = user32.LoadImageW(
                None,
                str(DASHBOARD_ICON),
                image_icon,
                0,
                0,
                load_from_file,
            )
            if icon:
                user32.SendMessageW(handle, wm_seticon, icon_big, icon)
                user32.SendMessageW(handle, wm_seticon, icon_small, icon)

        icon_thread = threading.Thread(
            target=apply_windows_icon,
            name="memory-wuxian-window-icon",
            daemon=True,
        )
        icon_thread.start()
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
