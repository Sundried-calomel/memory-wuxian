#!/usr/bin/env python3
"""Bounded lifecycle and real-effect verification for the collector."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


LIFECYCLE_FORMAT = "memory-wuxian-collector-lifecycle-v1"
MAX_COMMAND_ARGS = 64
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_PROBE_TIMEOUT_SECONDS = 60.0

ProcessInspector = Callable[[int], Mapping[str, Any]]


def _reason(code: str, **details: Any) -> dict[str, Any]:
    item = {"code": code}
    item.update(details)
    return item


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _same_path(left: str | Path, right: str | Path) -> bool:
    return _normalized_path(left) == _normalized_path(right)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_installed_effect_probe(
    sessions_root: str | Path,
    *,
    previous_watermark: str | None = None,
) -> dict[str, Any]:
    """Create one content-free rollout that proves the installed watcher ran."""
    root = Path(sessions_root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("collector effect probe requires an existing absolute sessions root")
    previous = _parse_timestamp(previous_watermark)
    if previous is not None:
        previous = previous.replace(microsecond=0)
        now = datetime.now(timezone.utc)
        if (previous - now).total_seconds() > 5:
            raise ValueError("collector source watermark is implausibly ahead of the system clock")
        deadline = time.monotonic() + 6.5
        while datetime.now(timezone.utc).replace(microsecond=0) <= previous:
            if time.monotonic() >= deadline:
                raise RuntimeError("collector effect probe could not advance beyond the previous watermark")
            time.sleep(0.05)
    probe_id = f"memory-wuxian-install-effect-{uuid.uuid4().hex}"
    path = root / f"rollout-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}-{probe_id}.jsonl"
    payload = (
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "session_meta",
                "payload": {"id": probe_id, "source": "memory-wuxian-install-effect"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0)
    if previous is not None and stamp <= previous:
        path.unlink(missing_ok=True)
        raise RuntimeError("collector effect probe did not advance beyond the previous watermark")
    return {
        "probe_id": probe_id,
        "path": str(path),
        "watermark": stamp.isoformat().replace("+00:00", "Z"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "contains_visible_messages": False,
    }


def remove_installed_effect_probe(probe: Mapping[str, Any]) -> None:
    raw_path = probe.get("path")
    if isinstance(raw_path, str) and raw_path:
        Path(raw_path).unlink(missing_ok=True)


def watermark_reached(value: Any, minimum: str) -> bool:
    observed = _parse_timestamp(value)
    required = _parse_timestamp(minimum)
    return observed is not None and required is not None and observed >= required


def _command(value: Any) -> list[str] | None:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_COMMAND_ARGS
        or any(not isinstance(arg, str) or not arg for arg in value)
    ):
        return None
    return list(value)


def _archive_root_argument(command: Sequence[str]) -> str | None:
    matches = [index for index, arg in enumerate(command) if arg == "--archive-root"]
    if len(matches) != 1 or matches[0] + 1 >= len(command):
        return None
    return command[matches[0] + 1]


def inspect_startup_owner(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one configured startup owner and its immutable identity."""
    reasons: list[dict[str, Any]] = []
    if manifest.get("format") != LIFECYCLE_FORMAT:
        reasons.append(_reason("collector-lifecycle-format-invalid"))

    generation = manifest.get("generation")
    if not isinstance(generation, (str, int)) or isinstance(generation, bool):
        reasons.append(_reason("collector-generation-invalid"))

    archive_root = manifest.get("archive_root")
    if not isinstance(archive_root, str) or not archive_root:
        reasons.append(_reason("collector-archive-root-invalid"))

    expected_command = _command(manifest.get("expected_command"))
    if expected_command is None:
        reasons.append(_reason("collector-command-invalid"))
    elif isinstance(archive_root, str) and archive_root:
        command_root = _archive_root_argument(expected_command)
        if command_root is None or not _same_path(command_root, archive_root):
            reasons.append(_reason("collector-command-archive-root-mismatch"))

    owners = manifest.get("startup_owners")
    if not isinstance(owners, list):
        owners = []
    if len(owners) != 1:
        reasons.append(
            _reason("collector-startup-owner-count-invalid", observed=len(owners), expected=1)
        )
        return {
            "ok": False,
            "reason_codes": reasons,
            "owner": None,
            "expected_command": expected_command,
            "archive_root": archive_root,
            "generation": generation,
        }

    owner = owners[0]
    if not isinstance(owner, Mapping):
        reasons.append(_reason("collector-startup-owner-invalid"))
        owner = {}

    if owner.get("generation") != generation:
        reasons.append(_reason("collector-owner-generation-mismatch"))
    owner_root = owner.get("archive_root")
    if (
        not isinstance(owner_root, str)
        or not isinstance(archive_root, str)
        or not _same_path(owner_root, archive_root)
    ):
        reasons.append(_reason("collector-owner-archive-root-mismatch"))
    owner_command = _command(owner.get("command"))
    if owner_command is None or expected_command is None or owner_command != expected_command:
        reasons.append(_reason("collector-owner-command-mismatch"))
    if owner.get("pid_identity") not in {"required", "not-applicable"}:
        reasons.append(_reason("collector-pid-identity-policy-invalid"))

    return {
        "ok": not reasons,
        "reason_codes": reasons,
        "owner": dict(owner),
        "expected_command": expected_command,
        "archive_root": archive_root,
        "generation": generation,
    }


def inspect_process(pid: int) -> dict[str, Any]:
    """Return bounded process identity without walking process tables."""
    if pid <= 0:
        return {"running": False}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return {"running": False}
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return {"running": True, "identity_available": False}
            return {
                "running": True,
                "identity_available": True,
                "executable": buffer.value,
            }
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return {"running": False}
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc_cmdline.read_bytes()
        command = [part.decode("utf-8") for part in raw.split(b"\0") if part]
        if command:
            return {"running": True, "identity_available": True, "command": command}
    except (OSError, UnicodeDecodeError):
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"running": True, "identity_available": False}
    command = result.stdout.strip()
    return {
        "running": True,
        "identity_available": bool(command),
        "command_line": command or None,
    }


def inspect_live_effect(
    telemetry: Mapping[str, Any],
    *,
    expected_command: Sequence[str],
    generation: str | int,
    pid_identity: str,
    process_inspector: ProcessInspector = inspect_process,
    now: datetime | None = None,
    max_age_seconds: float = 300.0,
) -> dict[str, Any]:
    """Validate readiness, bounded watermark convergence, and live PID identity."""
    reasons: list[dict[str, Any]] = []
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if telemetry.get("ready") is not True or telemetry.get("phase") != "ready":
        reasons.append(_reason("collector-not-ready"))

    updated_at = _parse_timestamp(telemetry.get("updated_at"))
    if updated_at is None:
        reasons.append(_reason("collector-telemetry-timestamp-invalid"))
    else:
        age = (current - updated_at).total_seconds()
        if age < -30 or age > max_age_seconds:
            reasons.append(_reason("collector-telemetry-stale"))

    source = telemetry.get("source_watermark")
    archive = telemetry.get("archive_watermark")
    if source is None or archive is None:
        reasons.append(_reason("collector-watermark-missing"))
    elif source != archive:
        reasons.append(_reason("collector-watermark-not-converged"))

    live_generation = telemetry.get("lifecycle_generation")
    if live_generation is not None and live_generation != generation:
        reasons.append(_reason("collector-live-generation-mismatch"))

    pid = telemetry.get("pid")
    if pid_identity == "required":
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            reasons.append(_reason("collector-live-pid-invalid"))
        else:
            try:
                identity = dict(process_inspector(pid))
            except Exception as exc:  # Boundary adapter must fail closed.
                identity = {"running": False, "error_type": type(exc).__name__}
            if identity.get("running") is not True:
                reasons.append(_reason("collector-live-pid-not-running"))
            elif identity.get("command") is not None:
                if list(identity["command"]) != list(expected_command):
                    reasons.append(_reason("collector-live-command-mismatch"))
            elif identity.get("executable") is not None:
                if not _same_path(identity["executable"], expected_command[0]):
                    reasons.append(_reason("collector-live-executable-mismatch"))
            elif identity.get("command_line") is not None:
                if identity["command_line"] != subprocess.list2cmdline(list(expected_command)):
                    reasons.append(_reason("collector-live-command-mismatch"))
            else:
                reasons.append(_reason("collector-live-identity-unavailable"))

    return {
        "ok": not reasons,
        "reason_codes": reasons,
        "ready": telemetry.get("ready") is True and telemetry.get("phase") == "ready",
        "watermarks_converged": source is not None and source == archive,
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
    }


def _read_json_object(path: Path, missing_code: str, invalid_code: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [_reason(missing_code)]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, [_reason(invalid_code)]
    if not isinstance(value, dict):
        return None, [_reason(invalid_code)]
    return value, []


def _safe_relative(value: Any, default: str) -> Path | None:
    if value is None:
        value = default
    if not isinstance(value, str):
        return None
    candidate = Path(value)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    return candidate


def run_isolated_watermark_probe(
    probe: Mapping[str, Any],
    *,
    probe_parent: str | Path | None = None,
) -> dict[str, Any]:
    """Run one explicitly requested synthetic probe in an isolated temp archive."""
    command = _command(probe.get("command"))
    payload = probe.get("payload", "memory-wuxian synthetic effect probe\n")
    timeout_seconds = probe.get("timeout_seconds", 15.0)
    poll_interval = probe.get("poll_interval_seconds", 0.05)
    source_relative = _safe_relative(
        probe.get("source_relative"), "sessions/synthetic-effect-probe.jsonl"
    )
    telemetry_relative = _safe_relative(
        probe.get("telemetry_relative"), "runtime/collector-status.json"
    )
    reasons: list[dict[str, Any]] = []
    if command is None:
        reasons.append(_reason("collector-probe-command-invalid"))
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        reasons.append(_reason("collector-probe-payload-invalid"))
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
        or timeout_seconds > MAX_PROBE_TIMEOUT_SECONDS
    ):
        reasons.append(_reason("collector-probe-timeout-invalid"))
    if (
        not isinstance(poll_interval, (int, float))
        or isinstance(poll_interval, bool)
        or poll_interval <= 0
        or poll_interval > 1
    ):
        reasons.append(_reason("collector-probe-poll-interval-invalid"))
    if source_relative is None or telemetry_relative is None:
        reasons.append(_reason("collector-probe-relative-path-invalid"))
    if reasons:
        return {"ok": False, "reason_codes": reasons, "probe_executed": False}

    parent = Path(probe_parent) if probe_parent is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-effect-", dir=parent) as temporary:
        archive_root = Path(temporary) / "archive"
        sessions_root = archive_root / "sessions"
        source_path = archive_root / source_relative
        telemetry_path = archive_root / telemetry_relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(payload, encoding="utf-8")
        baseline = {
            "ready": False,
            "phase": "starting",
            "source_watermark": None,
            "archive_watermark": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        telemetry_path.write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8"
        )
        replacements = {
            "{archive_root}": str(archive_root),
            "{sessions_root}": str(sessions_root),
            "{source_path}": str(source_path),
            "{telemetry_path}": str(telemetry_path),
        }
        expanded = [replacements.get(arg, arg) for arg in command]
        try:
            process = subprocess.Popen(
                expanded,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return {
                "ok": False,
                "reason_codes": [_reason("collector-probe-launch-failed")],
                "probe_executed": True,
            }
        deadline = time.monotonic() + float(timeout_seconds)
        advanced = False
        try:
            while time.monotonic() < deadline:
                telemetry, _ = _read_json_object(
                    telemetry_path,
                    "collector-probe-telemetry-missing",
                    "collector-probe-telemetry-invalid",
                )
                if telemetry:
                    source = telemetry.get("source_watermark")
                    archive = telemetry.get("archive_watermark")
                    advanced = (
                        telemetry.get("ready") is True
                        and telemetry.get("phase") == "ready"
                        and source is not None
                        and source == archive
                    )
                    if advanced:
                        break
                time.sleep(float(poll_interval))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        if not advanced:
            return {
                "ok": False,
                "reason_codes": [_reason("collector-probe-watermark-not-advanced")],
                "probe_executed": True,
            }
        payload_bytes = payload.encode("utf-8")
        return {
            "ok": True,
            "reason_codes": [],
            "probe_executed": True,
            "watermark_advanced": True,
            "payload_bytes": len(payload_bytes),
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        }


def verify_collector_lifecycle(
    lifecycle_path: str | Path,
    telemetry_path: str | Path,
    *,
    run_probe: bool = False,
    probe_parent: str | Path | None = None,
    process_inspector: ProcessInspector = inspect_process,
    now: datetime | None = None,
    max_age_seconds: float = 300.0,
) -> dict[str, Any]:
    """Verify configured ownership and installed effect using bounded reads."""
    lifecycle, lifecycle_errors = _read_json_object(
        Path(lifecycle_path),
        "collector-lifecycle-manifest-missing",
        "collector-lifecycle-manifest-invalid",
    )
    if lifecycle_errors:
        return {"ok": False, "reason_codes": lifecycle_errors, "probe": None}

    owner_result = inspect_startup_owner(lifecycle)
    if not owner_result["ok"]:
        return {
            "ok": False,
            "reason_codes": owner_result["reason_codes"],
            "owner": owner_result,
            "effect": None,
            "probe": None,
        }

    telemetry, telemetry_errors = _read_json_object(
        Path(telemetry_path),
        "collector-telemetry-missing",
        "collector-telemetry-invalid",
    )
    if telemetry_errors:
        return {
            "ok": False,
            "reason_codes": telemetry_errors,
            "owner": owner_result,
            "effect": None,
            "probe": None,
        }
    owner = owner_result["owner"]
    effect = inspect_live_effect(
        telemetry,
        expected_command=owner_result["expected_command"],
        generation=owner_result["generation"],
        pid_identity=owner["pid_identity"],
        process_inspector=process_inspector,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    probe_result = None
    reasons = list(effect["reason_codes"])
    if run_probe:
        probe_config = lifecycle.get("synthetic_probe")
        if not isinstance(probe_config, Mapping):
            probe_result = {
                "ok": False,
                "reason_codes": [_reason("collector-probe-config-missing")],
                "probe_executed": False,
            }
        else:
            probe_result = run_isolated_watermark_probe(
                probe_config, probe_parent=probe_parent
            )
        reasons.extend(probe_result["reason_codes"])
    return {
        "ok": not reasons,
        "reason_codes": reasons,
        "owner": owner_result,
        "effect": effect,
        "probe": probe_result,
    }
