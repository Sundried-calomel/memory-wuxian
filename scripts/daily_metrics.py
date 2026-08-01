from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from token_usage import (
    DAILY_USAGE_TIMEZONE,
    DAILY_USAGE_TZINFO,
    normalize_usage,
    token_usage_ledgers,
)


STALE_AFTER_SECONDS = 24 * 60 * 60


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _record_day(record: dict[str, Any]) -> str:
    timestamp = str(record.get("timestamp") or "")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(DAILY_USAGE_TZINFO).date().isoformat()
    except ValueError:
        return "unknown"


def _latest_ledgers(paths: Iterable[Path]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = str(ledger.get("session_id") or "")
        if (
            not session_id
            or ledger.get("format_version") != 2
            or ledger.get("measurement") != "codex-reported-model-usage"
            or not isinstance(ledger.get("daily_usage"), dict)
        ):
            continue
        rank = (
            int(ledger.get("token_event_count") or 0),
            str(ledger.get("updated_at") or ""),
            int((normalize_usage(ledger.get("reported_usage")) or {}).get("total_tokens", 0)),
        )
        previous = latest.get(session_id)
        if previous is None or rank > previous[0]:
            latest[session_id] = (rank, ledger)
    return [value[1] for value in latest.values()]


def _device_daily(
    node_id: str,
    display_name: str,
    records: Iterable[dict[str, Any]],
    ledgers: Iterable[dict[str, Any]],
    *,
    local: bool,
    last_sync_at: str | None,
) -> dict[str, Any]:
    days: dict[str, dict[str, int]] = defaultdict(
        lambda: {"messages": 0, "characters": 0, "reported_tokens": 0}
    )
    seen_messages: set[str] = set()
    for record in records:
        message_id = str(record.get("message_id") or "")
        if message_id and message_id in seen_messages:
            continue
        if message_id:
            seen_messages.add(message_id)
        day = _record_day(record)
        days[day]["messages"] += 1
        days[day]["characters"] += len(str(record.get("text") or ""))

    measured_sessions = 0
    for ledger in ledgers:
        daily_usage = ledger.get("daily_usage")
        if not isinstance(daily_usage, dict):
            continue
        measured_sessions += 1
        for day, usage in daily_usage.items():
            normalized = normalize_usage(usage)
            if normalized is not None:
                days[str(day)]["reported_tokens"] += normalized["total_tokens"]

    freshness = "local"
    age_seconds = None
    if not local:
        freshness = "never-synced"
        if last_sync_at:
            try:
                synced = datetime.fromisoformat(last_sync_at.replace("Z", "+00:00"))
                age_seconds = max(
                    0,
                    int((datetime.now(timezone.utc) - synced.astimezone(timezone.utc)).total_seconds()),
                )
                freshness = "stale" if age_seconds > STALE_AFTER_SECONDS else "fresh"
            except ValueError:
                freshness = "invalid"
    return {
        "node_id": node_id,
        "display_name": display_name,
        "local": local,
        "last_sync_at": last_sync_at,
        "freshness": freshness,
        "sync_age_seconds": age_seconds,
        "token_telemetry_available": measured_sessions > 0,
        "measured_sessions": measured_sessions,
        "days": dict(days),
    }


def build_federated_daily_metrics(store: Any, local_records: list[dict[str, Any]]) -> dict[str, Any]:
    from memory_federation import FederationManager

    manager = FederationManager(store)
    if manager.node_path.exists():
        local_node = manager.node()
        local_node_id = str(local_node["node_id"])
        local_name = str(local_node.get("display_name") or local_node_id)
    else:
        local_node_id = "local"
        local_name = "Local device"

    local_ledgers = token_usage_ledgers(store.root)
    devices = [
        _device_daily(
            local_node_id,
            local_name,
            local_records,
            local_ledgers,
            local=True,
            last_sync_at=None,
        )
    ]
    if manager.peers_dir.exists():
        for peer in manager.peers():
            if not peer.get("trusted"):
                continue
            node_id = str(peer["node_id"])
            peer_root = manager.replica_peer_root(node_id)
            state = manager.replica_state(node_id)
            devices.append(
                _device_daily(
                    node_id,
                    str(peer.get("display_name") or node_id),
                    _read_jsonl(peer_root / "raw-records.jsonl"),
                    _latest_ledgers((peer_root / "token-usage").glob("*.json")),
                    local=False,
                    last_sync_at=state.get("last_sync_at"),
                )
            )

    all_days = sorted({day for device in devices for day in device["days"]})
    daily = []
    for day in all_days:
        per_device = []
        for device in devices:
            values = device["days"].get(
                day, {"messages": 0, "characters": 0, "reported_tokens": 0}
            )
            per_device.append(
                {
                    "node_id": device["node_id"],
                    "display_name": device["display_name"],
                    "local": device["local"],
                    **values,
                }
            )
        local_values = per_device[0]
        daily.append(
            {
                "date": day,
                "messages": local_values["messages"],
                "characters": local_values["characters"],
                "reported_tokens": local_values["reported_tokens"],
                "local": {
                    key: local_values[key]
                    for key in ("messages", "characters", "reported_tokens")
                },
                "all_devices": {
                    key: sum(item[key] for item in per_device)
                    for key in ("messages", "characters", "reported_tokens")
                },
                "devices": per_device,
            }
        )

    return {
        "scope": "trusted-synchronized-devices",
        "timezone": DAILY_USAGE_TIMEZONE,
        "token_measurement": "codex-reported-model-usage",
        "devices_included": len(devices),
        "token_telemetry_devices": sum(
            bool(device["token_telemetry_available"]) for device in devices
        ),
        "complete_token_coverage": all(
            device["token_telemetry_available"] for device in devices
        ),
        "stale_devices": [
            device["node_id"]
            for device in devices
            if device["freshness"] in {"stale", "never-synced", "invalid"}
        ],
        "devices": [
            {key: value for key, value in device.items() if key != "days"}
            for device in devices
        ],
        "daily": daily,
    }
