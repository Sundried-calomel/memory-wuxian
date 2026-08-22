#!/usr/bin/env python3
"""Preserve the earliest collector coverage boundary across upgrades."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

from platform_atomic import atomic_replace_bytes


FORMAT = "memory-wuxian-collector-activation-v1"


def _parse(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("collector activation boundary must include a timezone")
    return parsed


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, payload: dict) -> None:
    payload_bytes = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_replace_bytes(path, payload_bytes)


def _manifest_since(archive_root: Path) -> Optional[str]:
    path = archive_root / "imports" / "codex" / "collector-command.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    command = payload.get("command")
    if not isinstance(command, list):
        return None
    try:
        index = command.index("--since")
        value = str(command[index + 1])
        _parse(value)
        return value
    except (ValueError, IndexError):
        return None


def _archived_since(archive_root: Path) -> Optional[str]:
    """Recover a conservative earlier boundary from the first raw record."""
    raw_root = archive_root / "raw"
    for path in sorted(raw_root.rglob("*.md")) if raw_root.is_dir() else []:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.startswith("{"):
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = payload.get("timestamp")
                    if isinstance(timestamp, str):
                        _parse(timestamp)
                        return timestamp
        except OSError:
            continue
    return None


def resolve_activation_since(archive_root: Path, requested: Optional[str] = None) -> str:
    """Return and persist the earliest known coverage boundary.

    First installation begins at the explicit boundary or current time. An
    upgrade may move the boundary earlier, but never later.
    """
    archive_root = Path(archive_root).expanduser().resolve()
    state_path = archive_root / "imports" / "codex" / "collector-activation.json"
    candidates: list[dt.datetime] = []
    for value in (requested, _manifest_since(archive_root), _archived_since(archive_root)):
        if value:
            candidates.append(_parse(value))
    try:
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        if existing.get("format") != FORMAT:
            raise ValueError("unsupported collector activation format")
        candidates.append(_parse(str(existing["since"])))
    except FileNotFoundError:
        pass
    if not candidates:
        candidates.append(dt.datetime.now().astimezone())
    since = _iso(min(candidates))
    _atomic_json(state_path, {"format": FORMAT, "since": since})
    return since
