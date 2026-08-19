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

from auto_update import current_version
from conversation_titles import (
    archive_conversation_titles,
    codex_thread_metadata,
    codex_thread_titles,
)
from daily_metrics import build_federated_daily_metrics
from memory_cli import (
    MemoryStore,
    atomic_write_json,
    environment_cloud_transport,
    project_attachment_cloud_transport,
    project_evidence_cloud_transport,
    load_simple_yaml,
    local_platform_name,
    local_runtime_versions,
    read_jsonl,
)
from memory_cloud_transport import CloudFolderTransport
from memory_configuration import compile_configuration, explain_configuration
from memory_environment import EnvironmentRegistry
from memory_environment_capabilities import local_device_capability_offer
from memory_environment_conflicts import EnvironmentConflictStore
from memory_environment_incoming import EnvironmentIncomingProcessor
from platform_paths import is_link_like
from memory_environment_promotions import PromotionStore
from memory_environment_profiles import EnvironmentProfileManager
from memory_federation import FederationManager
from memory_governance_ai import GovernanceAIQueue
from memory_project_evidence import ProjectEvidenceExchangeManager, ProjectEvidenceStore
from memory_project_attachments import ProjectAttachmentExchangeManager
from memory_readonly_service import ReadOnlyMemoryService
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


def project_attachment_lifecycle(
    transport_status: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    """Project exact-byte state without inferring one lifecycle stage from another."""
    local_sequence = int(inventory.get("local_event_sequence") or 0)
    published_sequence = 0
    acknowledged_sequence = 0
    published_peers = 0
    acknowledged_peers = 0
    peers = [
        item
        for item in transport_status.get("peers", [])
        if isinstance(item, dict)
        and item.get("trusted", True)
        and item.get("cloud_ready", True)
    ]
    for peer in peers:
        acknowledged = peer.get("acknowledged") or {}
        outstanding = peer.get("outstanding") or {}
        peer_acknowledged = int(acknowledged.get("last_event_sequence") or 0)
        peer_published = max(
            peer_acknowledged,
            int(outstanding.get("to_event_sequence") or 0),
        )
        if peer_published:
            published_peers += 1
        if local_sequence and peer_acknowledged >= local_sequence:
            acknowledged_peers += 1
        published_sequence = max(published_sequence, peer_published)
        acknowledged_sequence = max(acknowledged_sequence, peer_acknowledged)
    encrypted = bool(transport_status.get("encrypted"))
    return {
        "local_manifest_creation": {
            "state": "recorded" if int(inventory.get("local_manifests") or 0) else "none",
            "manifests": int(inventory.get("local_manifests") or 0),
            "files": int(inventory.get("local_files") or 0),
            "bytes": int(inventory.get("local_bytes") or 0),
            "event_sequence": local_sequence,
        },
        "encrypted_publication": {
            "state": "published" if published_sequence else "ready" if encrypted else "unconfigured",
            "event_sequence": published_sequence,
            "peers": published_peers,
        },
        "peer_acknowledgement": {
            "state": (
                "acknowledged"
                if local_sequence and peers and acknowledged_peers == len(peers)
                else "partial"
                if acknowledged_sequence
                else "pending"
                if local_sequence
                else "none"
            ),
            "event_sequence": acknowledged_sequence,
            "peers": acknowledged_peers,
            "expected_peers": len(peers),
        },
        "verified_reconstruction": {
            "state": "verified" if int(inventory.get("verified_reconstructions") or 0) else "none",
            "receipts": int(inventory.get("verified_reconstructions") or 0),
        },
    }


def windows_process_running(pid: int, kernel32: Any | None = None) -> bool:
    """Check a Windows process without sending a console-control signal."""
    if pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    api = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL

    process_query_limited_information = 0x1000
    still_active = 259
    handle = api.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(api.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == still_active
        )
    finally:
        api.CloseHandle(handle)


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
        **no_window_kwargs(),
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
        **no_window_kwargs(),
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
    telemetry["process_running"] = False
    try:
        import psutil
    except ImportError:
        try:
            pid = int(telemetry["pid"])
            if sys.platform == "win32":
                telemetry["process_running"] = windows_process_running(pid)
            else:
                os.kill(pid, 0)
                telemetry["process_running"] = True
        except (KeyError, TypeError, ValueError, OSError):
            pass
    else:
        try:
            process = psutil.Process(int(telemetry["pid"]))
            telemetry["cpu_percent"] = round(process.cpu_percent(interval=0.05), 1)
            telemetry["memory_bytes"] = int(process.memory_info().rss)
            telemetry["process_running"] = process.is_running()
        except (psutil.Error, KeyError, TypeError, ValueError, OSError):
            pass

    now = datetime.now(timezone.utc)

    def parsed(value: Any) -> datetime | None:
        try:
            text = str(value)
            return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        except (TypeError, ValueError):
            return None

    updated = parsed(telemetry.get("updated_at"))
    interval = max(1, int(telemetry.get("fallback_interval_seconds") or 5))
    telemetry_age = max(0.0, (now - updated).total_seconds()) if updated else None
    telemetry["telemetry_age_seconds"] = (
        round(telemetry_age, 1) if telemetry_age is not None else None
    )
    telemetry["telemetry_stale"] = bool(
        telemetry["process_running"]
        and (telemetry_age is None or telemetry_age > max(90, interval * 2 + 30))
    )

    source_watermark = parsed(telemetry.get("source_watermark"))
    archive_watermark = parsed(telemetry.get("archive_watermark"))
    lag = (
        max(0.0, (source_watermark - archive_watermark).total_seconds())
        if source_watermark and archive_watermark
        else None
    )
    telemetry["archive_lag_seconds"] = round(lag, 1) if lag is not None else None
    telemetry["archive_lagging"] = bool(lag is not None and lag > 1)
    telemetry["startup_pending"] = bool(
        int(telemetry.get("format_version") or 0) >= 2
        and not bool(telemetry.get("ready"))
    )
    alerts = []
    if telemetry["process_running"] and telemetry["startup_pending"]:
        alerts.append("collector-starting")
    if telemetry["process_running"] and telemetry["telemetry_stale"]:
        alerts.append("collector-telemetry-stale")
    if telemetry["archive_lagging"]:
        alerts.append("archive-watermark-lag")
    if not telemetry["process_running"]:
        alerts.append("collector-not-running")
    backup_debt_path = root / "pending/backup-debt.json"
    try:
        backup_debt = json.loads(backup_debt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backup_debt = None
    telemetry["backup_debt"] = backup_debt
    telemetry["alerts"] = alerts
    return telemetry


DEBT_KINDS = (
    "coverage_debt",
    "mechanical_debt",
    "semantic_debt",
    "backup_debt",
)
MAX_DEBT_PROJECTION_BYTES = 1024 * 1024
DEBT_PROJECTION_PATHS = (
    "maintenance/status-projection.json",
    "dashboard/debt-status.json",
    "imports/codex/collector-status-projection.json",
)


def _debt_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _queue_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"pending": 0, "running": 0, "retry": 0, "quarantined": 0}
    counts = value.get("counts")
    if isinstance(counts, dict):
        return {
            "pending": sum(
                _debt_count(counts.get(state))
                for state in ("queued", "retry", "semantic-ready", "running")
            ),
            "running": _debt_count(counts.get("running")),
            "retry": _debt_count(counts.get("retry")),
            "quarantined": _debt_count(counts.get("quarantined")),
        }
    totals = [_queue_counts(child) for child in value.values() if isinstance(child, dict)]
    return {
        field: sum(item[field] for item in totals)
        for field in ("pending", "running", "retry", "quarantined")
    }


def _normalize_debt(value: Any, kind: str = "") -> dict[str, Any]:
    if isinstance(value, (int, float)):
        value = {"count": value}
    if not isinstance(value, dict):
        value = {}
    queue = _queue_counts(value)
    count = _debt_count(
        value.get(
            "count",
            value.get(
                "pending",
                value.get(
                    "remaining",
                    value.get(
                        "total_pending",
                        value.get(
                            "file_count",
                            value.get("job_count", value.get("mutation_count", 0)),
                        ),
                    ),
                ),
            ),
        )
    )
    if kind == "coverage_debt":
        count = _debt_count(value.get("missing_cursor_rollouts")) + _debt_count(
            value.get("incomplete_rollouts")
        )
    elif kind == "semantic_debt":
        count = max(count, _debt_count(value.get("pending_summary_jobs")), queue["pending"])
    elif kind == "mechanical_debt":
        count = max(count, queue["pending"])
    elif kind == "backup_debt" and value.get("present") is not False:
        count = max(count, _debt_count(value.get("mutation_count")), int(bool(value.get("present"))))
    progress = value.get("progress")
    if progress is None and kind == "coverage_debt":
        observed = _debt_count(value.get("observed_bytes"))
        pending = min(observed, _debt_count(value.get("pending_bytes")))
        progress = round((observed - pending) * 100 / observed, 2) if observed else None
    return {
        "count": count,
        "state": str(
            value.get("state")
            or value.get("status")
            or ("pending" if count else "clear")
        ),
        "in_progress": max(
            _debt_count(value.get("in_progress", value.get("running"))),
            queue["running"],
        ),
        "retry": max(_debt_count(value.get("retry", value.get("retries"))), queue["retry"]),
        "quarantined": max(_debt_count(value.get("quarantined")), queue["quarantined"]),
        "permanent_failures": _debt_count(
            value.get("permanent_failures", value.get("failed_permanently"))
        ),
        "oldest_at": value.get("oldest_at") or value.get("oldest_pending_at"),
        "last_error": value.get("last_error"),
        "progress": progress,
    }


def debt_status_projection(root: Path) -> dict[str, Any]:
    """Read one bounded control-plane projection without scanning source rollouts."""
    payload: dict[str, Any] | None = None
    source: str | None = None
    projection_error: str | None = None
    for relative in DEBT_PROJECTION_PATHS:
        path = root / relative
        try:
            if path.stat().st_size > MAX_DEBT_PROJECTION_BYTES:
                projection_error = "projection-too-large"
                source = relative
                break
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            projection_error = "projection-invalid"
            source = relative
            break
        if not isinstance(candidate, dict):
            projection_error = "projection-invalid"
            source = relative
            break
        payload = candidate
        source = relative
        break

    source_debts = (payload or {}).get("debts", (payload or {}).get("debt", payload or {}))
    if not isinstance(source_debts, dict):
        source_debts = {}
        projection_error = "projection-invalid"
    debts = {
        kind: _normalize_debt(
            source_debts.get(kind, source_debts.get(kind.removesuffix("_debt"))),
            kind,
        )
        for kind in DEBT_KINDS
    }

    supervisor_path = root / "maintenance/supervisor-state.json"
    try:
        if supervisor_path.stat().st_size > MAX_DEBT_PROJECTION_BYTES:
            raise ValueError("projection-too-large")
        supervisor = json.loads(supervisor_path.read_text(encoding="utf-8"))
        result = supervisor.get("result", {}) if isinstance(supervisor, dict) else {}
        skipped = result.get("skipped", []) if isinstance(result, dict) else []
        runtime_failure = next(
            (
                item for item in skipped
                if isinstance(item, dict) and item.get("reason") == "runtime-unavailable"
            ),
            None,
        )
        semantic = debts["semantic_debt"]
        throughput = supervisor.get("throughput") if isinstance(supervisor, dict) else None
        if isinstance(throughput, dict):
            semantic["throughput"] = {
                "started_at": throughput.get("started_at"),
                "finished_at": throughput.get("finished_at"),
                "duration_seconds": max(0.0, float(throughput.get("duration_seconds") or 0)),
                "batch_limit": _debt_count(throughput.get("batch_limit")),
                "pending_before": _debt_count(throughput.get("pending_before")),
                "pending_after": _debt_count(throughput.get("pending_after")),
                "completed_jobs": _debt_count(throughput.get("completed_jobs")),
                "scheduled_jobs": _debt_count(throughput.get("scheduled_jobs")),
                "net_pending_change": int(throughput.get("net_pending_change") or 0),
                "parallel_model_limit": max(
                    1, _debt_count(throughput.get("parallel_model_limit"))
                ),
                "recovery_seconds": max(
                    0.0, float(throughput.get("recovery_seconds") or 0)
                ),
                "semantic_dispatch_seconds": max(
                    0.0,
                    float(throughput.get("semantic_dispatch_seconds") or 0),
                ),
                "average_model_seconds": max(
                    0.0, float(throughput.get("average_model_seconds") or 0)
                ),
                "maximum_model_seconds": max(
                    0.0, float(throughput.get("maximum_model_seconds") or 0)
                ),
            }
        if runtime_failure and semantic["count"] > 0 and not result.get("completed_jobs"):
            semantic["state"] = "blocked"
            semantic["last_error"] = str(
                runtime_failure.get("error") or "Codex runtime is unavailable"
            )[:500]
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, ValueError):
        projection_error = projection_error or "supervisor-projection-invalid"

    coverage_path = root / "imports/codex/coverage-status.json"
    try:
        if coverage_path.stat().st_size > MAX_DEBT_PROJECTION_BYTES:
            raise ValueError("projection-too-large")
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        if not isinstance(coverage, dict):
            raise ValueError("projection-invalid")
        debts["coverage_debt"] = _normalize_debt(coverage, "coverage_debt")
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        projection_error = str(exc) if isinstance(exc, ValueError) else "projection-invalid"

    # The legacy backup marker is a small, bounded compatibility source. It is
    # debt, not a collector failure, and disappears as soon as maintenance drains it.
    if source is None:
        backup_path = root / "pending/backup-debt.json"
        try:
            if backup_path.stat().st_size <= MAX_DEBT_PROJECTION_BYTES:
                backup = json.loads(backup_path.read_text(encoding="utf-8"))
                if isinstance(backup, dict):
                    count = _debt_count(backup.get("mutation_count", 1))
                    debts["backup_debt"] = _normalize_debt(
                        {**backup, "count": count, "state": "pending"},
                        "backup_debt",
                    )
                    source = "pending/backup-debt.json"
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass

    return {
        "format": str((payload or {}).get("format") or "memory-wuxian-debt-status-v1"),
        "updated_at": (payload or {}).get("updated_at"),
        "health": (
            "attention"
            if debts["semantic_debt"].get("state") == "blocked"
            else (payload or {}).get("health")
        ),
        "source": source,
        "projection_error": projection_error,
        "debts": debts,
    }


MAX_DASHBOARD_ARCHIVE_ENTRIES = 200000
MAX_DASHBOARD_ARCHIVE_SCAN_SECONDS = 5.0


def _bounded_archive_files(directories: list[Path]) -> list[Path]:
    started = time.monotonic()
    files: list[Path] = []
    stack = []
    for directory in directories:
        if is_link_like(directory):
            raise ValueError("dashboard archive scan root is a link or junction")
        if directory.is_dir():
            stack.append(directory)
    entries_seen = 0
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if (
                    entries_seen > MAX_DASHBOARD_ARCHIVE_ENTRIES
                    or time.monotonic() - started > MAX_DASHBOARD_ARCHIVE_SCAN_SECONDS
                ):
                    raise ValueError("dashboard archive scan exceeded its bounded budget")
                candidate = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError("dashboard archive scan contains a link")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(candidate)
                elif entry.is_file(follow_symlinks=False):
                    files.append(candidate)
    return files


def archive_storage_bytes(store: MemoryStore) -> int:
    paths = [store.state_path]
    paths.extend(
        _bounded_archive_files(
            [store.raw_dir, store.conversation_dir, store.summaries_dir, store.index_dir]
        )
    )
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


def dashboard_health(
    status: dict[str, Any],
    collector: dict[str, Any] | None = None,
    debt_status: dict[str, Any] | None = None,
) -> str:
    if status.get("errors") or (debt_status or {}).get("projection_error"):
        return "error"
    if (debt_status or {}).get("health") == "error":
        return "error"
    debts = (debt_status or {}).get("debts") or {}
    debt_values = [value for value in debts.values() if isinstance(value, dict)]
    permanent_states = {"attention", "quarantined", "permanent-failure", "failed"}
    error_states = {"error", "corrupt", "integrity-failure"}
    if any(str(value.get("state")) in error_states for value in debt_values):
        return "error"
    if (
        any(status.get(field) for field in ("integrity_issues", "issues"))
        or (debt_status or {}).get("health") == "attention"
        or any(
            str(value.get("state")) in permanent_states
            or _debt_count(value.get("quarantined"))
            or _debt_count(value.get("permanent_failures"))
            for value in debt_values
        )
    ):
        return "attention"
    alerts = set((collector or {}).get("alerts") or [])
    if "collector-not-running" in alerts:
        return "error"
    if "collector-telemetry-stale" in alerts:
        return "attention"
    recoverable = bool(
        alerts.intersection({"collector-starting", "archive-watermark-lag"})
        or any(
            _debt_count(value.get("count"))
            or _debt_count(value.get("in_progress"))
            or _debt_count(value.get("retry"))
            for value in debt_values
        )
        or any(status.get(field) for field in ("warnings", "failed_jobs", "failed_summary_jobs"))
    )
    return "catching-up" if recoverable else "ok"


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
    collector = collector_telemetry(store.root)
    debt_status = debt_status_projection(store.root)
    federated_daily = build_federated_daily_metrics(store, records)
    return {
        "generated_at": now.isoformat(),
        "archive_root": str(store.root),
        "health": dashboard_health(status, collector, debt_status),
        "collector": collector,
        "debt_status": debt_status,
        "archive_health": {
            field: status.get(field)
            for field in (
                "errors",
                "integrity_issues",
                "issues",
                "warnings",
                "failed_jobs",
                "failed_summary_jobs",
            )
            if status.get(field)
        },
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
        "daily": federated_daily["daily"],
        "daily_metrics": {
            key: value for key, value in federated_daily.items() if key != "daily"
        },
        "conversations": conversations,
        "active_conversations": active_conversations,
        "archived_conversations": archived_conversations,
        "character_note": "Visible user and assistant source text stored in the append-only raw archive; summaries are excluded.",
        "estimation_note": "The archive estimate covers visible stored dialogue only. Codex-reported usage is persisted separately from rollout token_count telemetry and can include instructions, tools, reasoning, cached input, and outputs. Cached input and reasoning are reported subfields and are not added to total_tokens a second time.",
    }


class DashboardSnapshotCache:
    """Persist expensive archive statistics and invalidate them by file metadata."""

    FORMAT_VERSION = 4

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

    def _federated_daily_sources(self) -> list[Path]:
        """Return only federation files that can change daily chart totals."""
        config = getattr(self.store, "config", {})
        federation_config = (
            config.get("federation", {}) if isinstance(config, dict) else {}
        )
        configured_replica = (
            federation_config.get("replica_directory")
            if isinstance(federation_config, dict)
            else None
        )
        replica_root = (
            Path(str(configured_replica)).expanduser().resolve()
            if configured_replica
            else (self.store.root.parent / f"{self.store.root.name}-federation-cache").resolve()
        )
        metadata_peers = self.store.root / "federation" / "peers"
        replica_peers = replica_root / "peers"
        paths: list[Path] = [metadata_peers, replica_peers]
        paths.extend(_bounded_archive_files([metadata_peers]))
        if not replica_peers.is_dir():
            return paths
        if is_link_like(replica_peers):
            raise ValueError("dashboard federation replica root is a link or junction")
        with os.scandir(replica_peers) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError("dashboard federation replica contains a link")
                if not entry.is_dir(follow_symlinks=False):
                    continue
                peer_root = Path(entry.path)
                paths.extend(
                    [
                        peer_root,
                        peer_root / "raw-records.jsonl",
                        peer_root / "replica-state.json",
                    ]
                )
                paths.extend(_bounded_archive_files([peer_root / "token-usage"]))
        return paths

    def source_signature(self) -> str:
        paths = [
            self.store.state_path,
            self.store.retrieval_dir / "retrieval-log.jsonl",
            Path.home() / ".codex/state_5.sqlite",
            Path.home() / ".codex/state_5.sqlite-wal",
            Path.home() / ".codex/.codex-global-state.json",
        ]
        paths.extend(
            _bounded_archive_files(
                [
                    self.store.raw_dir,
                    self.store.conversation_dir,
                    self.store.summaries_dir,
                    self.store.index_dir,
                    self.store.pending_dir,
                ]
            )
        )
        token_usage_dir = getattr(
            self.store,
            "codex_token_usage_dir",
            self.store.root / "imports" / "codex" / "token-usage",
        )
        paths.extend(_bounded_archive_files([token_usage_dir]))
        paths.extend(self._federated_daily_sources())
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

    def _with_live_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = dict(payload)
        response["collector"] = collector_telemetry(self.store.root)
        response["debt_status"] = debt_status_projection(self.store.root)
        response["health"] = dashboard_health(
            response.get("archive_health", {}),
            response["collector"],
            response["debt_status"],
        )
        return response

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
        response = self._with_live_status(payload)
        response["served_at"] = datetime.now(timezone.utc).isoformat()
        response["snapshot"] = {
            "source_signature": signature,
            "persisted": True,
        }
        return response

    def get_fast(self) -> dict[str, Any]:
        payload = self._payload
        if isinstance(payload, dict):
            response = self._with_live_status(payload)
            response["served_at"] = datetime.now(timezone.utc).isoformat()
            response["snapshot"] = {"persisted": True, "refreshing": True}
            if not self._refreshing:
                self._refreshing = True

                def refresh_cached() -> None:
                    try:
                        self.get()
                    finally:
                        self._refreshing = False

                threading.Thread(
                    target=refresh_cached,
                    name="memory-wuxian-dashboard-refresh",
                    daemon=True,
                ).start()
            return response
        with self._lock:
            payload = self._payload
            if payload is None:
                try:
                    snapshot = json.loads(self.path.read_text(encoding="utf-8"))
                    payload = snapshot.get("payload")
                except (OSError, json.JSONDecodeError):
                    payload = None
            if isinstance(payload, dict):
                response = self._with_live_status(payload)
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
        "global-runtime-contract",
    )
    ACTIVITY_DIRECTORIES = (
        "artifacts",
        "projects",
        "revisions",
        "objects",
        "conflicts",
        "promotions",
        "receipts",
        "staging",
        "governance-ai",
        "profiles",
        "replicas",
    )
    MAX_JSON_FILES = 4096
    MAX_SCAN_SECONDS = 3.0

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

    def _bounded_json_paths(self, directory_name: str) -> list[Path]:
        candidate = self.root / directory_name
        if not candidate.exists():
            return []
        root = self.registry._resolve_relative(
            directory_name, f"dashboard {directory_name} root"
        )
        if not root.is_dir():
            return []
        started = time.monotonic()
        paths = []
        stack = [root]
        directories = 0
        entries_seen = 0
        while stack:
            if time.monotonic() - started > self.MAX_SCAN_SECONDS:
                raise ValueError("dashboard Environment inventory scan timed out")
            directory = stack.pop()
            directories += 1
            if directories > self.MAX_JSON_FILES:
                raise ValueError("dashboard Environment directory count exceeds limit")
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if (
                        entries_seen > self.MAX_JSON_FILES
                        or time.monotonic() - started > self.MAX_SCAN_SECONDS
                    ):
                        raise ValueError("dashboard Environment entry scan exceeds limit")
                    candidate = Path(entry.path)
                    if is_link_like(candidate):
                        raise ValueError("dashboard Environment inventory contains a link")
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(candidate)
                    elif entry.is_file(follow_symlinks=False) and (
                        candidate.suffix == ".json" or directory_name == "objects"
                    ):
                        paths.append(candidate)
                        if len(paths) > self.MAX_JSON_FILES:
                            raise ValueError("dashboard Environment file count exceeds limit")
        return paths

    def _bounded_flat_entries(self, root: Path, label: str) -> list[Path]:
        if not root.is_dir():
            return []
        started = time.monotonic()
        paths = []
        with os.scandir(root) as entries:
            for entry in entries:
                if (
                    len(paths) >= self.MAX_JSON_FILES
                    or time.monotonic() - started > self.MAX_SCAN_SECONDS
                ):
                    raise ValueError(f"{label} exceeds bounded scan limits")
                paths.append(Path(entry.path))
        paths.sort(key=lambda path: path.name)
        return paths

    def source_signature(self) -> str:
        if not self.root.is_dir():
            return "uninitialized"
        registry_path = self.registry._resolve_relative(
            "registry.json", "dashboard Environment registry authority", for_write=True
        )
        state_path = self.registry._resolve_relative(
            "state.json", "dashboard Environment state authority", for_write=True
        )
        paths = [registry_path, state_path]
        directory_stamps = []
        for name in self.ACTIVITY_DIRECTORIES:
            candidate = self.root / name
            if not candidate.exists():
                continue
            directory = self.registry._resolve_relative(
                name, f"dashboard {name} root"
            )
            if directory.is_dir():
                try:
                    stat = directory.stat()
                    directory_stamps.append((str(directory), stat.st_mtime_ns))
                except OSError:
                    pass
                paths.extend(self._bounded_json_paths(name))
        federation_peers = self.registry.archive_root / "federation" / "peers"
        if is_link_like(federation_peers.parent) or is_link_like(federation_peers):
            raise ValueError("dashboard federation peer root is link-like")
        if federation_peers.is_dir():
            try:
                stat = federation_peers.stat()
                directory_stamps.append((str(federation_peers), stat.st_mtime_ns))
            except OSError:
                pass
            for path in self._bounded_flat_entries(
                federation_peers, "dashboard federation peers"
            ):
                if path.suffix != ".json":
                    continue
                if len(paths) > self.MAX_JSON_FILES:
                    raise ValueError("dashboard federation peer count exceeds limit")
                if path.is_file() and not is_link_like(path):
                    paths.append(path)
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
        values = []
        for path in sorted(self._bounded_json_paths(directory_name), key=str):
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
                "profiles": {"generation_count": 0, "export_event_count": 0, "current": None, "peer_profiles": []},
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
                "profiles": {"generation_count": 0, "export_event_count": 0, "current": None, "peer_profiles": []},
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
        profile_manager = EnvironmentProfileManager(self.registry.archive_root)
        profile_error = None
        try:
            profile_status = profile_manager.status()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            profile_error = str(error)
            profile_status = {
                "initialized": True,
                "generation_count": 0,
                "current": None,
                "export_event_count": 0,
            }
        peer_profiles = []
        peer_root = self.registry._resolve_relative(
            "replicas/peers", "dashboard peer replica root", for_write=True
        )
        trusted_peers = set()
        federation_peers = self.registry.archive_root / "federation" / "peers"
        if is_link_like(federation_peers.parent) or is_link_like(federation_peers):
            raise ValueError("dashboard federation peer registry is link-like")
        if federation_peers.is_dir():
            for peer_path in self._bounded_flat_entries(
                federation_peers, "dashboard federation peer registry"
            ):
                if peer_path.suffix != ".json":
                    continue
                if is_link_like(peer_path):
                    raise ValueError("dashboard federation peer record is link-like")
                peer = self._read_json_object(peer_path)
                if peer is None:
                    continue
                if peer.get("trusted") is True and peer.get("node_id") == peer_path.stem:
                    trusted_peers.add(peer_path.stem)
        if peer_root.is_dir():
            for peer in sorted(trusted_peers):
                node = self.registry._resolve_relative(
                    f"replicas/peers/{peer}",
                    "dashboard peer replica",
                )
                profile_dir = self.registry._resolve_relative(
                    f"replicas/peers/{peer}/profiles",
                    "dashboard peer profile replica root",
                )
                if profile_dir.is_dir():
                    replica_count = 0
                    for replica in self._bounded_flat_entries(
                        profile_dir, "dashboard peer profile replicas"
                    ):
                        if replica.suffix != ".json":
                            continue
                        safe_replica = self.registry._resolve_relative(
                            replica.relative_to(self.registry.root).as_posix(),
                            "dashboard peer profile replica",
                        )
                        if safe_replica.is_file():
                            replica_count += 1
                        if replica_count > 1024:
                            raise ValueError("dashboard peer profile replica count exceeds limit")
                    if replica_count:
                        peer_profiles.append({"node_id": peer, "profile_count": replica_count})
        return {
            "format_version": self.FORMAT_VERSION,
            "initialized": bool(status["initialized"]),
            "validation_status": "needs-attention" if profile_error else "valid",
            "validation_error": profile_error,
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
            "profiles": {**profile_status, "peer_profiles": peer_profiles},
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


def system_dashboard_data(
    store: MemoryStore,
    configuration_path: Path | None = None,
) -> dict[str, Any]:
    """Build the read-only configuration and local-capability view."""

    config_path = Path(configuration_path or SKILL_ROOT / "config.yaml")
    compiled = compile_configuration(
        config_path.expanduser().resolve(),
        root_argument=str(store.root),
    )
    runtimes = local_runtime_versions([])
    return {
        "schema_version": 1,
        "configuration": explain_configuration(compiled),
        "capabilities": local_device_capability_offer(
            product_version=current_version(SKILL_ROOT),
            platform=local_platform_name(),
            python_version=runtimes["python"],
        ),
    }


def make_handler(
    store: MemoryStore,
    configuration_path: Path | None = None,
):
    snapshot_cache = DashboardSnapshotCache(store)
    environment_cache = EnvironmentDashboardCache(store.root)

    class Handler(BaseHTTPRequestHandler):
        def memory_search(self, request) -> dict[str, Any]:
            service = ReadOnlyMemoryService(store)
            parameters = parse_qs(request.query, keep_blank_values=True)
            shared = service.query(service.from_query_parameters(parameters))
            selected = []
            for item in shared["results"]:
                provenance = item["provenance"]
                selected.append({
                    **{key: value for key, value in item.items() if key != "provenance"},
                    "raw_path": provenance["raw_path"],
                    "raw_line_start": provenance["raw_line_start"],
                    "raw_line_end": provenance["raw_line_end"],
                    "record_sha256": provenance["record_sha256"],
                })
            return {
                "query": shared["query"],
                "mode": shared["mode"],
                "count": shared["count"],
                "results": selected,
                "verified_against_raw": shared["confidence"] == "verified",
                "semantic_provider": shared["semantic_provider"],
                "warnings": shared["warnings"],
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
            project_evidence_transport = project_evidence_cloud_transport(
                store, archive_transport, bootstrap=False
            )
            project_evidence_status = project_evidence_transport.status()
            project_evidence_status["inventory"] = ProjectEvidenceExchangeManager(
                store
            ).status()
            project_evidence_status["owners"] = ProjectEvidenceStore(store).owner_status()
            project_attachment_transport = project_attachment_cloud_transport(
                store, archive_transport, bootstrap=False
            )
            project_attachment_status = project_attachment_transport.status()
            project_attachment_status["inventory"] = ProjectAttachmentExchangeManager(
                store
            ).status()
            project_attachment_status["lifecycle"] = project_attachment_lifecycle(
                project_attachment_status,
                project_attachment_status["inventory"],
            )
            cloud["streams"] = {
                "archive-v1": archive_stream,
                "environment-v1": environment_transport.status(),
                "project-evidence-v1": project_evidence_status,
            }
            if project_attachment_status.get("configured") or project_attachment_status["inventory"].get("local_manifests"):
                cloud["streams"]["project-attachment-v1"] = project_attachment_status
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
            elif path == "/api/environment-profile":
                try:
                    peer_node_id = parse_qs(request.query).get("peer_node_id", [None])[0]
                    if not peer_node_id:
                        raise ValueError("peer_node_id is required")
                    manager = EnvironmentProfileManager(store.root)
                    comparison = manager.compare(peer_node_id)
                    body = json.dumps(
                        {
                            "comparison": comparison,
                            "plan": manager.convergence_plan_from_comparison(comparison),
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
            elif path == "/api/system":
                try:
                    body = json.dumps(
                        system_dashboard_data(store, configuration_path),
                        ensure_ascii=False,
                    ).encode("utf-8")
                except Exception as exc:
                    self.send_json(500, {"error": str(exc)})
                    return
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
                "/api/project-evidence",
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
                if path == "/api/project-evidence":
                    if action != "refresh-owners":
                        raise ValueError("unsupported project evidence action")
                    result = ProjectEvidenceStore(store).refresh_owners(
                        maximum_owners=20,
                        apply=True,
                    )
                    self.send_json(200, {"result": result, "devices": self.cloud_payload()})
                    return
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
                project_evidence_transport = project_evidence_cloud_transport(
                    store, transport, bootstrap=True
                )
                project_attachment_transport = project_attachment_cloud_transport(
                    store, transport, bootstrap=True
                )
                if action == "enable":
                    if not transport.status().get("configured"):
                        raise ValueError("cloud transport is not configured")
                    if not environment_transport.status().get("configured"):
                        raise ValueError(
                            "environment cloud transport is not configured"
                        )
                    if not project_evidence_transport.status().get("configured"):
                        raise ValueError(
                            "project evidence cloud transport is not configured"
                        )
                    transport.set_enabled(True)
                    environment_transport.set_enabled(True)
                    project_evidence_transport.set_enabled(True)
                    if project_attachment_transport.status().get("configured"):
                        project_attachment_transport.set_enabled(True)
                    try:
                        scheduler = set_cloud_scheduler(store, True)
                    except Exception:
                        transport.set_enabled(False)
                        environment_transport.set_enabled(False)
                        project_evidence_transport.set_enabled(False)
                        if project_attachment_transport.status().get("configured"):
                            project_attachment_transport.set_enabled(False)
                        raise
                    result = {"status": "enabled", "scheduler": scheduler}
                elif action == "disable":
                    transport.set_enabled(False)
                    environment_transport.set_enabled(False)
                    project_evidence_transport.set_enabled(False)
                    if project_attachment_transport.status().get("configured"):
                        project_attachment_transport.set_enabled(False)
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
                    if not project_evidence_transport.status().get("enabled"):
                        raise ValueError(
                            "project evidence cloud transport is disabled"
                        )
                    streams: dict[str, dict[str, Any]] = {}
                    with exclusive_lock(
                        store.root / ".locks" / "federation.lock"
                    ):
                        stream_transports = [
                            ("archive", transport),
                            ("environment", environment_transport),
                            ("project_evidence", project_evidence_transport),
                        ]
                        if project_attachment_transport.status().get("enabled"):
                            stream_transports.append(("project_attachments", project_attachment_transport))
                        for name, stream_transport in stream_transports:
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
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    store = MemoryStore(Path(args.root).expanduser().resolve(), load_simple_yaml(Path(args.config).expanduser().resolve()))
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(store, Path(args.config).expanduser().resolve()),
    )
    url = f"http://{args.host}:{server.server_port}/"
    if args.self_check:
        try:
            if args.window:
                import webview  # noqa: F401
            print(json.dumps({"status": "ready", "url": url}, ensure_ascii=False), flush=True)
            return 0
        finally:
            server.server_close()
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
