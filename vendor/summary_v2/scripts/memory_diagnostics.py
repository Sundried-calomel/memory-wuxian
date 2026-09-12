#!/usr/bin/env python3
"""Redacted diagnostic bundles without raw conversation content."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from platform_transaction import atomic_write_canonical_json, canonical_json_bytes


SECRET_KEYS = {"token", "password", "secret", "authorization", "private_key", "credential"}
WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\s\"']+")
POSIX_HOME = re.compile(r"/(?:Users|home)/[^/\s]+(?:/[^\s\"']*)?")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(marker in str(key).casefold() for marker in SECRET_KEYS) else redact(item)
            for key, item in value.items()
            if str(key) not in {"text", "raw_text", "conversation_content"}
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = WINDOWS_PATH.sub("[LOCAL_PATH]", value)
        text = POSIX_HOME.sub("[LOCAL_PATH]", text)
        text = re.sub(r"(?i)(token|password|secret|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
        return text[:2000]
    return value


def create_diagnostic_bundle(
    archive_root: Path,
    service_state: Dict[str, Any],
    queue_jobs: list[Dict[str, Any]],
) -> Dict[str, Any]:
    root = Path(archive_root)
    jobs = [
        {
            "job_id": job.get("job_id"),
            "kind": job.get("kind"),
            "state": job.get("state"),
            "attempts": job.get("attempts"),
            "max_attempts": job.get("max_attempts"),
            "last_error": job.get("last_error"),
        }
        for job in queue_jobs
    ]
    payload = redact({
        "format": "memory-wuxian-diagnostic-bundle-v1",
        "service_state": service_state,
        "jobs": jobs,
        "contains_raw_dialogue": False,
    })
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / "diagnostics" / f"diagnostic-{timestamp}-{digest[:12]}.json"
    atomic_write_canonical_json(path, payload)
    return {
        "status": "created",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "contains_raw_dialogue": False,
    }
