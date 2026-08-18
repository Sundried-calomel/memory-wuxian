#!/usr/bin/env python3
"""Desired-versus-actual service state for bounded maintenance."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from memory_jobs import MaintenanceQueue


def _json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def service_state(archive_root: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(archive_root)
    queue = MaintenanceQueue(root)
    telemetry = _json(root / "imports" / "codex" / "collector-telemetry.json")
    semantic_enabled = bool(config.get("summaries", {}).get("automatic_semantic_jobs", True))
    collector_desired = bool(config.get("integration", {}).get("codex", {}).get("enabled", True))
    last_heartbeat = int(telemetry.get("last_heartbeat_epoch") or telemetry.get("heartbeat_epoch") or 0)
    if not last_heartbeat and telemetry.get("updated_at"):
        try:
            last_heartbeat = int(
                datetime.fromisoformat(str(telemetry["updated_at"]).replace("Z", "+00:00")).timestamp()
            )
        except (TypeError, ValueError):
            last_heartbeat = 0
    collector_fresh = bool(last_heartbeat and time.time() - last_heartbeat <= 600)
    supervisor_path = root / "maintenance" / "supervisor-state.json"
    supervisor = _json(supervisor_path)
    supervisor_age = (
        time.time() - supervisor_path.stat().st_mtime
        if supervisor_path.is_file()
        else None
    )
    maintenance_fresh = bool(
        supervisor_age is not None
        and supervisor_age <= 900
        and supervisor.get("status") in {"healthy", "catching-up"}
    )
    desired = {
        "collector": "running" if collector_desired else "stopped",
        "maintenance_queue": "running",
        "semantic_worker": "on-demand" if semantic_enabled else "disabled",
    }
    actual = {
        "collector": "running" if collector_fresh else "stale-or-stopped",
        "maintenance_queue": "running" if maintenance_fresh else "stale-or-stopped",
        "semantic_worker": "not-persistent",
    }
    mismatches = []
    if collector_desired and not collector_fresh:
        mismatches.append("collector is desired but telemetry is stale or unavailable")
    if not collector_desired and collector_fresh:
        mismatches.append("collector telemetry is active while desired state is stopped")
    if not maintenance_fresh:
        mismatches.append("maintenance supervisor state is stale, unhealthy, or unavailable")
    return {
        "format": "memory-wuxian-service-state-v1",
        "desired": desired,
        "actual": actual,
        "mismatches": mismatches,
        "queue": queue.status(),
        "collector_last_heartbeat_epoch": last_heartbeat or None,
        "maintenance_supervisor_status": supervisor.get("status"),
        "maintenance_supervisor_age_seconds": supervisor_age,
        "semantic_ai_policy": "one-shot-explicit-worker-only",
    }
