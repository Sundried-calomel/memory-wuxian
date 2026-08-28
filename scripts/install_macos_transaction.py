#!/usr/bin/env python3
"""Transactionally upgrade one installed macOS MemoryWuxian user runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

if sys.platform == "darwin":
    import fcntl
else:  # pragma: no cover - the installer is macOS-only
    fcntl = None

import yaml

try:
    from install_maintenance_supervisor import retire_legacy_macos_semantic_backfill
    from migrate_config import migrate_config
    from platform_atomic import (
        ParentSync,
        atomic_replace_bytes,
        durable_replace as platform_durable_replace,
        sync_directory,
    )
    from platform_runtime import executable_entry_path
    from collector_lifecycle import (
        create_installed_effect_probe,
        inspect_startup_owner,
        remove_installed_effect_probe,
        watermark_reached,
    )
    from platform_process import _unique_command_argument
except ModuleNotFoundError:
    from scripts.install_maintenance_supervisor import retire_legacy_macos_semantic_backfill
    from scripts.migrate_config import migrate_config
    from scripts.platform_atomic import (
        ParentSync,
        atomic_replace_bytes,
        durable_replace as platform_durable_replace,
        sync_directory,
    )
    from scripts.platform_runtime import executable_entry_path
    from scripts.collector_lifecycle import (
        create_installed_effect_probe,
        inspect_startup_owner,
        remove_installed_effect_probe,
        watermark_reached,
    )
    from scripts.platform_process import _unique_command_argument


COLLECTOR_LABEL = "com.memorywuxian.codex-sync"
COLLECTOR_READY_TIMEOUT_SECONDS = 900
Runner = Callable[..., subprocess.CompletedProcess]


def exact_macos_root(value: str, label: str, *, must_exist: bool = True) -> Path:
    """Canonicalize roots while allowing only Apple's fixed /private aliases."""

    supplied = Path(value).expanduser()
    if not supplied.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {supplied}")
    resolved = supplied.resolve(strict=must_exist)
    lexical = Path(os.path.abspath(supplied))
    if lexical != resolved:
        aliases = {
            Path("/var"): Path("/private/var"),
            Path("/tmp"): Path("/private/tmp"),
            Path("/etc"): Path("/private/etc"),
        }
        allowed = any(
            lexical == alias
            and resolved == target
            or lexical.is_relative_to(alias)
            and resolved == target / lexical.relative_to(alias)
            for alias, target in aliases.items()
        )
        if not allowed:
            raise ValueError(f"{label} contains an unsupported path alias: {supplied}")
    return resolved


def atomic_bytes(path: Path, payload: bytes) -> None:
    atomic_replace_bytes(path, payload, parent_sync=ParentSync.REQUIRED)


def fsync_directory(path: Path) -> None:
    sync_directory(path, policy=ParentSync.REQUIRED)


def durable_replace(source: Path, destination: Path) -> None:
    platform_durable_replace(
        source,
        destination,
        parent_sync=ParentSync.REQUIRED,
    )


def preserve_failed_tree(
    source: Path,
    *,
    expected_generation: str,
    preferred_path: Path,
    failed_root: Path,
    transaction_id: str,
    reason: str,
) -> Path:
    """Move a failed active tree once while preserving prior failure evidence."""

    actual_generation = tree_generation(source)[0]
    alternate_path = (
        failed_root
        / f"{actual_generation}-{transaction_id}-{reason}-displaced"
    )
    destinations = (
        [preferred_path]
        if actual_generation == expected_generation
        else [alternate_path]
    )
    for destination in destinations:
        if destination.exists():
            if tree_generation(destination)[0] == actual_generation:
                shutil.rmtree(source)
                return destination
            continue
        durable_replace(source, destination)
        return destination
    raise RuntimeError("failed candidate evidence conflicts with preserved recovery trees")


def event(timestamp: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        {"timestamp": timestamp, "type": "event_msg", "payload": payload},
        ensure_ascii=False,
    ) + "\n"


def probe_candidate(candidate: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    collector = candidate / "bin" / "memory-wuxian-collector"
    if not collector.is_file() or not os.access(collector, os.X_OK):
        raise ValueError("candidate collector is missing or not executable")
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-candidate-") as temporary:
        root = Path(temporary)
        archive = root / "archive"
        sessions = root / "sessions"
        sessions.mkdir()
        config = root / "config.yaml"
        candidate_config = candidate / "config.yaml"
        if not candidate_config.is_file():
            raise ValueError("candidate config.yaml is missing")
        config_payload = yaml.safe_load(candidate_config.read_text(encoding="utf-8"))
        if not isinstance(config_payload, dict):
            raise ValueError("candidate config.yaml must contain a mapping")
        config_payload.setdefault("backup", {})["enabled"] = False
        config_payload.setdefault("ai_summary", {})["enabled"] = False
        config.write_text(
            yaml.safe_dump(config_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        runner(
            [
                sys.executable,
                str(candidate / "scripts" / "memory_cli.py"),
                "--root",
                str(archive),
                "--config",
                str(config),
                "init",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        session = sessions / "rollout-2099-01-01T00-00-00-memory-wuxian-probe.jsonl"
        session.write_text(
            json.dumps(
                {
                    "timestamp": "2099-01-01T00:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "memory-wuxian-upgrade-probe"},
                }
            )
            + "\n"
            + event(
                "2099-01-01T00:00:01Z",
                {"type": "user_message", "message": "MemoryWuxian upgrade probe"},
            )
            + event(
                "2099-01-01T00:00:02Z",
                {
                    "type": "agent_message",
                    "phase": "final_answer",
                    "message": "MemoryWuxian upgrade probe complete",
                },
            ),
            encoding="utf-8",
        )
        command = [
            str(collector),
            "--archive-root",
            str(archive),
            "--config",
            str(config),
            "--sessions-root",
            str(sessions),
            "--session-file",
            str(session),
            "--once",
        ]
        try:
            completed = runner(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(f"candidate collector probe failed: {detail}") from error
        payload = json.loads(completed.stdout)
        if int(payload.get("imported_messages") or 0) != 2:
            raise ValueError("candidate did not import the two probe messages")
        raw = list(archive.glob("raw/*/*/*.md"))
        cursor = archive / "imports" / "codex" / "memory-wuxian-upgrade-probe.json"
        if len(raw) != 1 or not cursor.is_file():
            raise ValueError("candidate did not persist probe raw text and cursor")
        text = raw[0].read_text(encoding="utf-8")
        if "MemoryWuxian upgrade probe complete" not in text:
            raise ValueError("candidate probe raw text is incomplete")
        return {
            "status": "passed",
            "imported_messages": 2,
            "raw_records": 2,
        }


def launchctl_pid(label: str, runner: Runner = subprocess.run) -> int | None:
    completed = runner(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.strip().startswith("pid ="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def wait_for_collector(
    archive_root: Path,
    *,
    previous_pid: int | None,
    minimum_watermark: str | None = None,
    effect_started_at: datetime | None = None,
    timeout_seconds: float = 300,
) -> dict[str, Any]:
    telemetry_path = archive_root / "imports" / "codex" / "collector-telemetry.json"
    deadline = time.monotonic() + timeout_seconds
    last_error = "collector telemetry did not appear"
    while time.monotonic() < deadline:
        try:
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
            pid = int(telemetry["pid"])
            updated = datetime.fromisoformat(
                str(telemetry["updated_at"]).replace("Z", "+00:00")
            )
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            os.kill(pid, 0)
            if previous_pid is not None and pid == previous_pid:
                last_error = "collector PID did not change"
            elif int(telemetry.get("format_version") or 0) >= 2 and not bool(
                telemetry.get("ready")
            ):
                last_error = "collector is alive and still completing startup synchronization"
            elif age > 30:
                last_error = f"collector telemetry is stale by {age:.1f} seconds"
            elif minimum_watermark is not None and not watermark_reached(
                telemetry.get("archive_watermark"), minimum_watermark
            ):
                last_error = "collector archive watermark has not reached the installed effect probe"
            else:
                return telemetry
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(last_error)


def wait_for_launch_agent(
    label: str,
    *,
    runner: Runner = subprocess.run,
    timeout_seconds: float = 30,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pid = launchctl_pid(label, runner)
        if pid is not None:
            return pid
        time.sleep(0.5)
    raise RuntimeError(f"{label} was not restored under launchd")


def installed_collector_label(plist: Path) -> str:
    """Return the actual installed owner label, including legacy generations."""
    if not plist.is_file():
        return COLLECTOR_LABEL
    try:
        payload = plistlib.loads(plist.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return COLLECTOR_LABEL
    label = payload.get("Label")
    return label if isinstance(label, str) and label else COLLECTOR_LABEL


@contextmanager
def quiesce_collector_for_cutover(
    archive_root: Path,
    plist: Path,
    *,
    runner: Runner = subprocess.run,
    timeout_seconds: float = 900,
):
    """Drain the current archive transaction, then stop the old collector."""
    if fcntl is None:
        raise RuntimeError("collector cutover is supported only on macOS")
    lock_path = archive_root / ".locks" / "archive.lock"
    recovery_debt = archive_root / "pending" / "native-recovery-debt.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    booted_out = False
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "current collector did not reach an idle archive boundary "
                        f"within {timeout_seconds:.0f} seconds"
                    )
                time.sleep(0.5)
        yielded = False
        try:
            if recovery_debt.exists():
                raise RuntimeError(
                    "native recovery debt remains after the archive became idle"
                )
            active_label = installed_collector_label(plist)
            previous_pid = launchctl_pid(active_label, runner)
            if previous_pid is not None and not plist.is_file():
                raise RuntimeError(
                    "current collector is running but its LaunchAgent plist is missing"
                )
            if previous_pid is not None:
                completed = runner(
                    [
                        "/bin/launchctl",
                        "bootout",
                        f"gui/{os.getuid()}",
                        str(plist),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "").strip()
                    raise RuntimeError(
                        "failed to stop the current collector at an idle boundary"
                        + (f": {detail}" if detail else "")
                    )
                booted_out = True
                deadline = time.monotonic() + 30
                while launchctl_pid(active_label, runner) is not None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "current collector did not stop after idle cutover"
                        )
                    time.sleep(0.25)
            yielded = True
            yield {
                "status": "quiesced",
                "previous_pid": previous_pid,
                "recovery_debt_present": False,
            }
        except Exception:
            if booted_out and not yielded and plist.is_file():
                runner(
                    [
                        "/bin/launchctl",
                        "bootstrap",
                        f"gui/{os.getuid()}",
                        str(plist),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            raise
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prepare_candidate(source_root: Path, candidate: Path, current: Path) -> None:
    if candidate.exists():
        raise FileExistsError(f"candidate staging path already exists: {candidate}")
    shutil.copytree(
        source_root,
        candidate,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git",
            "target",
            "outputs",
            "dist",
            "memory",
            "__pycache__",
            "*.pyc",
        ),
    )
    if (current / "config.yaml").is_file():
        shutil.copy2(current / "config.yaml", candidate / "config.yaml")
        migrate_config(
            candidate / "config.yaml",
            source_root / "config.yaml",
            apply=True,
        )
    for executable in (
        candidate / "bin" / "memory-wuxian-collector",
        candidate / "bin" / "memory-wuxian-envelope",
    ):
        executable.chmod(executable.stat().st_mode | 0o111)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def tree_generation(root: Path) -> tuple[str, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "size": path.stat().st_size,
                    "sha256": digest,
                    "mode": path.stat().st_mode & 0o777,
                }
            )
    encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"mwg-{hashlib.sha256(encoded).hexdigest()}", entries


def transaction_identifier(candidate_generation: str, paths: dict[str, str]) -> str:
    encoded = json.dumps(
        {"candidate_generation": candidate_generation, "paths": paths},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mwt-{hashlib.sha256(encoded).hexdigest()}"


def rotate_completed_rollback_attempt(transaction_root: Path) -> Path | None:
    """Preserve an earlier rollback before reusing a deterministic transaction ID."""
    if not (transaction_root / "rollback-receipt.json").is_file():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archived = transaction_root.with_name(f"{transaction_root.name}-attempt-{timestamp}")
    if archived.exists():
        raise RuntimeError(f"rollback attempt archive already exists: {archived}")
    durable_replace(transaction_root, archived)
    return archived


def transition(journal_path: Path, journal: dict[str, Any], state: str, **details: Any) -> None:
    allowed = {
        "prepare": {"prepare", "verify", "rollback"},
        "verify": {"commit", "rollback"},
        "commit": set(),
        "rollback": {"rollback"},
    }
    current = journal.get("state")
    if current not in allowed or state not in allowed[current]:
        raise RuntimeError(f"invalid transaction transition: {current} -> {state}")
    timestamp = datetime.now(timezone.utc).isoformat()
    journal["state"] = state
    journal["updated_at"] = timestamp
    journal.update(details)
    journal.setdefault("events", []).append(
        {"stage": state, "timestamp": timestamp, "details": details}
    )
    atomic_json(journal_path, journal)


def snapshot_file(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        return {"existed": False}
    payload = source.read_bytes()
    atomic_bytes(destination, payload)
    return {"existed": True, "sha256": hashlib.sha256(payload).hexdigest()}


def restore_snapshot(destination: Path, snapshot: Path, record: dict[str, Any]) -> None:
    if bool(record.get("existed")):
        payload = snapshot.read_bytes()
        if hashlib.sha256(payload).hexdigest() != record.get("sha256"):
            raise RuntimeError(f"rollback snapshot hash mismatch: {snapshot}")
        atomic_bytes(destination, payload)
    else:
        destination.unlink(missing_ok=True)


def _watermark(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def collector_lifecycle_manifest(
    *,
    generation: str,
    archive_root: Path,
    expected_command: list[str],
    telemetry: dict[str, Any],
    launchd_pid: int,
) -> dict[str, Any]:
    telemetry_pid = telemetry.get("pid")
    if telemetry_pid != launchd_pid:
        raise RuntimeError("lifecycle manifest PID does not align with collector telemetry")
    if telemetry.get("ready") is not True or telemetry.get("phase") != "ready":
        raise RuntimeError("lifecycle manifest requires ready collector telemetry")
    source = _watermark(telemetry.get("source_watermark"))
    archived = _watermark(telemetry.get("archive_watermark"))
    if source is None or source != archived:
        raise RuntimeError("lifecycle manifest requires converged telemetry watermarks")
    live_generation = telemetry.get("lifecycle_generation")
    if live_generation is not None and live_generation != generation:
        raise RuntimeError("lifecycle generation does not align with collector telemetry")
    owner = {
        "owner_id": f"launchd:{COLLECTOR_LABEL}",
        "kind": "launch-agent",
        "generation": generation,
        "archive_root": str(archive_root),
        "command": list(expected_command),
        "pid_identity": "required",
    }
    return {
        "format": "memory-wuxian-collector-lifecycle-v1",
        "generation": generation,
        "archive_root": str(archive_root),
        "expected_command": list(expected_command),
        "startup_owners": [owner],
        "verified_telemetry": {
            "pid": telemetry_pid,
            "ready": True,
            "phase": "ready",
            "updated_at": telemetry.get("updated_at"),
            "source_watermark": source,
            "archive_watermark": archived,
        },
    }


def persist_collector_lifecycle(
    path: Path,
    *,
    generation: str,
    archive_root: Path,
    expected_command: list[str],
    telemetry: dict[str, Any],
    launchd_pid: int,
) -> dict[str, Any]:
    manifest = collector_lifecycle_manifest(
        generation=generation,
        archive_root=archive_root,
        expected_command=expected_command,
        telemetry=telemetry,
        launchd_pid=launchd_pid,
    )
    atomic_json(path, manifest)
    if read_json(path) != manifest:
        raise RuntimeError("collector lifecycle manifest verification failed")
    return manifest


def verify_collector_lifecycle_alignment(
    path: Path,
    *,
    generation: str,
    archive_root: Path,
    expected_command: list[str],
    telemetry: dict[str, Any],
    launchd_pid: int,
) -> dict[str, Any]:
    expected = collector_lifecycle_manifest(
        generation=generation,
        archive_root=archive_root,
        expected_command=expected_command,
        telemetry=telemetry,
        launchd_pid=launchd_pid,
    )
    observed = read_json(path)
    for field in (
        "format",
        "generation",
        "archive_root",
        "expected_command",
        "startup_owners",
    ):
        if observed.get(field) != expected.get(field):
            raise RuntimeError("collector lifecycle manifest does not align with the active generation")
    return observed


def validate_installed_launch_contract(
    *,
    skill_root: Path,
    archive_root: Path,
    sessions_root: Path,
    python_executable: Path,
    codex_cli: Path,
    plist: Path,
    active_root_pointer: Path,
    telemetry: dict[str, Any],
    previous_archive_watermark: str | None,
    required_effect_watermark: str | None = None,
    effect_started_at: datetime | None = None,
    runner: Runner,
) -> dict[str, Any]:
    activation = read_json(archive_root / "imports" / "codex" / "collector-activation.json")
    since = activation.get("since")
    if not isinstance(since, str) or not since:
        raise RuntimeError("collector activation boundary is missing")
    payload = plistlib.loads(plist.read_bytes())
    expected_arguments = [
        str(skill_root / "bin" / "memory-wuxian-collector"),
        "--archive-root",
        str(archive_root),
        "--config",
        str(skill_root / "config.yaml"),
        "--sessions-root",
        str(sessions_root),
        "--since",
        since,
        "--debounce-ms",
        "400",
    ]
    expected_logs = archive_root / "imports" / "codex"
    if payload.get("Label") != COLLECTOR_LABEL or payload.get("ProgramArguments") != expected_arguments:
        raise RuntimeError("installed collector command does not exactly match the candidate contract")
    expected_environment = {
        "RUST_BACKTRACE": "1",
        "MEMORY_WUXIAN_PYTHON": str(python_executable),
        "MEMORY_WUXIAN_CODEX": str(codex_cli),
    }
    if payload.get("EnvironmentVariables") != expected_environment:
        raise RuntimeError("installed collector environment does not exactly match the candidate contract")
    if payload.get("StandardOutPath") != str(expected_logs / "launch-agent.stdout.log") or payload.get(
        "StandardErrorPath"
    ) != str(expected_logs / "launch-agent.stderr.log"):
        raise RuntimeError("installed collector log paths do not exactly match the archive root")
    if active_root_pointer.read_bytes() != f"{archive_root}\n".encode("utf-8"):
        raise RuntimeError("active archive root pointer does not exactly match the requested root")
    launchd_pid = launchctl_pid(COLLECTOR_LABEL, runner)
    if launchd_pid is None or launchd_pid != int(telemetry["pid"]):
        raise RuntimeError("launchd PID does not match collector telemetry")
    source = _watermark(telemetry.get("source_watermark"))
    archived = _watermark(telemetry.get("archive_watermark"))
    if telemetry.get("source_watermark") != telemetry.get("archive_watermark"):
        raise RuntimeError("collector source and archive watermarks did not converge")
    if not isinstance(telemetry.get("last_archive_update"), str):
        raise RuntimeError("collector did not publish a bounded archive effect")
    if required_effect_watermark is not None and not watermark_reached(
        archived, required_effect_watermark
    ):
        raise RuntimeError("collector did not reach the installed effect probe watermark")
    if previous_archive_watermark is not None and archived is not None:
        if datetime.fromisoformat(archived.replace("Z", "+00:00")) < datetime.fromisoformat(
            previous_archive_watermark.replace("Z", "+00:00")
        ):
            raise RuntimeError("collector archive watermark regressed")
    return {
        "status": "passed",
        "launchd_pid": launchd_pid,
        "expected_command": expected_arguments,
        "source_watermark": source,
        "archive_watermark": archived,
    }


def _command_value(command: list[str], option: str) -> str:
    value = _unique_command_argument(command, option)
    if value is None:
        raise RuntimeError(f"restored collector command has no unique {option} value")
    return value


def verify_restored_collector_effect(
    *,
    lifecycle_path: Path,
    plist: Path,
    active_root_pointer: Path,
    previous_pid: int | None,
    runner: Runner,
    timeout_seconds: float = COLLECTOR_READY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    lifecycle = read_json(lifecycle_path)
    owner = inspect_startup_owner(lifecycle)
    if not owner.get("ok"):
        raise RuntimeError("restored collector lifecycle is not a verified owner contract")
    expected_command = owner["expected_command"]
    archive_root = Path(owner["archive_root"])
    sessions_root = Path(_command_value(expected_command, "--sessions-root"))
    skill_root = Path(expected_command[0]).parent.parent
    plist_payload = plistlib.loads(plist.read_bytes())
    environment = plist_payload.get("EnvironmentVariables")
    if not isinstance(environment, dict):
        raise RuntimeError("restored collector environment is missing")
    python_executable = Path(str(environment.get("MEMORY_WUXIAN_PYTHON", "")))
    codex_cli = Path(str(environment.get("MEMORY_WUXIAN_CODEX", "")))
    telemetry_path = archive_root / "imports" / "codex" / "collector-telemetry.json"
    previous_watermark = None
    try:
        previous_watermark = _watermark(read_json(telemetry_path).get("archive_watermark"))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    probe = create_installed_effect_probe(
        sessions_root,
        previous_watermark=previous_watermark,
    )
    try:
        telemetry = wait_for_collector(
            archive_root,
            previous_pid=previous_pid,
            minimum_watermark=probe["watermark"],
            timeout_seconds=timeout_seconds,
        )
    finally:
        remove_installed_effect_probe(probe)
    effect = validate_installed_launch_contract(
        skill_root=skill_root,
        archive_root=archive_root,
        sessions_root=sessions_root,
        python_executable=python_executable,
        codex_cli=codex_cli,
        plist=plist,
        active_root_pointer=active_root_pointer,
        telemetry=telemetry,
        previous_archive_watermark=previous_watermark,
        required_effect_watermark=probe["watermark"],
        runner=runner,
    )
    if effect["expected_command"] != expected_command:
        raise RuntimeError("restored collector command does not match its lifecycle owner")
    live_generation = telemetry.get("lifecycle_generation")
    if live_generation is not None and live_generation != owner["generation"]:
        raise RuntimeError("restored collector generation does not match its lifecycle owner")
    return {
        **effect,
        "generation": owner["generation"],
        "effect_probe": {key: value for key, value in probe.items() if key != "path"},
    }


def recover_interrupted_transactions(
    *,
    transactions_root: Path,
    staging_root: Path,
    generations_root: Path,
    failed_root: Path,
    skill_root: Path,
    plist: Path,
    maintenance_plist: Path,
    active_root_pointer: Path,
    runner: Runner,
) -> None:
    """Restore every uncommitted generation before admitting a new candidate."""

    if not transactions_root.is_dir():
        return
    for transaction_root in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
        journal_path = transaction_root / "journal.json"
        if not journal_path.is_file():
            continue
        journal = read_json(journal_path)
        transaction = journal.get("transaction_id")
        candidate_generation = journal.get("candidate_generation")
        prior_generation = journal.get("prior_generation")
        if not isinstance(transaction, str) or not isinstance(candidate_generation, str):
            raise RuntimeError(f"invalid interrupted transaction journal: {journal_path}")
        if (transaction_root / "commit-receipt.json").is_file():
            if journal.get("state") == "verify":
                transition(
                    journal_path,
                    journal,
                    "commit",
                    commit_receipt=str(transaction_root / "commit-receipt.json"),
                    recovered=True,
                )
            continue
        if (transaction_root / "rollback-receipt.json").is_file():
            continue
        if journal.get("state") == "commit":
            raise RuntimeError("transaction journal claims commit without a commit receipt")
        paths = journal.get("paths")
        snapshots = journal.get("snapshots")
        if not isinstance(paths, dict) or paths.get("skill_root") != str(skill_root):
            raise RuntimeError("interrupted transaction does not match the requested Skill root")
        if not isinstance(snapshots, dict):
            raise RuntimeError("interrupted transaction has no rollback snapshots")
        archive_root = Path(str(paths.get("archive_root", "")))
        if not archive_root.is_absolute() or not archive_root.is_dir():
            raise RuntimeError("interrupted transaction archive root is unavailable")
        prior_path = generations_root / f"{prior_generation or 'none'}-{transaction}"
        candidate = staging_root / candidate_generation
        failed_path = failed_root / f"{candidate_generation}-{transaction}"
        current_generation = tree_generation(skill_root)[0] if skill_root.is_dir() else None
        switched = prior_path.is_dir() or current_generation == candidate_generation

        if switched:
            with quiesce_collector_for_cutover(archive_root, plist, runner=runner):
                pass
            runner(
                [
                    "/bin/launchctl",
                    "bootout",
                    f"gui/{os.getuid()}",
                    str(maintenance_plist),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if skill_root.exists() and current_generation != prior_generation:
                preserve_failed_tree(
                    skill_root,
                    expected_generation=candidate_generation,
                    preferred_path=failed_path,
                    failed_root=failed_root,
                    transaction_id=transaction,
                    reason="recovery",
                )
            if prior_generation is not None:
                if not prior_path.is_dir():
                    raise RuntimeError("interrupted transaction lost its prior generation")
                if not skill_root.exists():
                    durable_replace(prior_path, skill_root)
        elif current_generation != prior_generation:
            raise RuntimeError("interrupted transaction pre-state cannot be reconstructed")

        restore_snapshot(
            plist,
            transaction_root / "collector.plist",
            snapshots["collector_plist"],
        )
        restore_snapshot(
            maintenance_plist,
            transaction_root / "maintenance.plist",
            snapshots["maintenance_plist"],
        )
        restore_snapshot(
            active_root_pointer,
            transaction_root / "active-root.txt",
            snapshots["active_root_pointer"],
        )
        lifecycle_snapshot = snapshots.get("collector_lifecycle")
        if isinstance(lifecycle_snapshot, dict):
            restore_snapshot(
                archive_root / "imports" / "codex" / "collector-lifecycle.json",
                transaction_root / "collector-lifecycle.json",
                lifecycle_snapshot,
            )
        if snapshots["collector_plist"].get("existed") and launchctl_pid(
            COLLECTOR_LABEL, runner
        ) is None:
            runner(
                ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                check=True,
                capture_output=True,
                text=True,
            )
            wait_for_launch_agent(COLLECTOR_LABEL, runner=runner)
        restored_effect = None
        if isinstance(lifecycle_snapshot, dict) and lifecycle_snapshot.get("existed"):
            restored_effect = verify_restored_collector_effect(
                lifecycle_path=archive_root / "imports" / "codex" / "collector-lifecycle.json",
                plist=plist,
                active_root_pointer=active_root_pointer,
                previous_pid=None,
                runner=runner,
            )
        if snapshots["maintenance_plist"].get("existed"):
            runner(
                [
                    "/bin/launchctl",
                    "bootstrap",
                    f"gui/{os.getuid()}",
                    str(maintenance_plist),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        if candidate.exists():
            if failed_path.exists():
                existing_generation = tree_generation(failed_path)[0]
                if existing_generation != candidate_generation:
                    raise RuntimeError("staged candidate conflicts with recovery evidence")
                shutil.rmtree(candidate)
            else:
                durable_replace(candidate, failed_path)
        receipt = {
            "format_version": 1,
            "status": "rolled-back",
            "transaction_id": transaction,
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
            "candidate_evidence": str(failed_path),
            "error": "recovered interrupted transaction",
            "restored_effect": restored_effect,
        }
        atomic_json(transaction_root / "rollback-receipt.json", receipt)
        transition(
            journal_path,
            journal,
            "rollback",
            phase="restored",
            recovered=True,
            rollback_receipt=str(transaction_root / "rollback-receipt.json"),
        )


def install(
    *,
    source_root: Path,
    skill_root: Path,
    archive_root: Path,
    sessions_root: Path,
    python_executable: Path,
    codex_cli: Path,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    for name, path, kind in (
        ("source root", source_root, "dir"),
        ("archive root", archive_root, "dir"),
        ("sessions root", sessions_root, "dir"),
        ("Python executable", python_executable, "file"),
        ("Codex CLI", codex_cli, "file"),
    ):
        if not path.is_absolute() or (kind == "dir" and not path.is_dir()) or (kind == "file" and not path.is_file()):
            raise ValueError(f"{name} must be an existing absolute {kind}: {path}")
    if not skill_root.is_absolute():
        raise ValueError(f"skill root must be absolute: {skill_root}")
    if source_root == skill_root or archive_root in (source_root, skill_root):
        raise ValueError("source, Skill, and archive roots must be distinct")
    for owner in (source_root, skill_root):
        if archive_root.is_relative_to(owner):
            raise ValueError(f"archive root must not be inside {owner}")
    for required in (
        source_root / "SKILL.md",
        source_root / "config.yaml",
        source_root / "scripts" / "install_codex_autosync.py",
        source_root / "bin" / "memory-wuxian-collector",
    ):
        if not required.is_file():
            raise ValueError(f"candidate package root is incomplete: {required}")

    codex_home = skill_root.parent.parent
    update_root = codex_home / "updates" / "memory-wuxian"
    staging_root = update_root / "staging"
    generations_root = update_root / "generations"
    transactions_root = update_root / "transactions"
    failed_root = update_root / "failed"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{COLLECTOR_LABEL}.plist"
    maintenance_plist = (
        Path.home()
        / "Library"
        / "LaunchAgents"
        / "com.openai.codex.memory-wuxian-maintenance.plist"
    )
    active_root_pointer = codex_home / "memory-wuxian-active-root.txt"
    old_pid = launchctl_pid(installed_collector_label(plist), runner)
    old_telemetry: dict[str, Any] = {}
    telemetry_path = archive_root / "imports" / "codex" / "collector-telemetry.json"
    lifecycle_path = archive_root / "imports" / "codex" / "collector-lifecycle.json"
    if telemetry_path.is_file():
        try:
            old_telemetry = read_json(telemetry_path)
        except (OSError, ValueError, json.JSONDecodeError):
            old_telemetry = {}
    previous_archive_watermark = _watermark(old_telemetry.get("archive_watermark"))

    for directory in (staging_root, generations_root, transactions_root, failed_root):
        directory.mkdir(parents=True, exist_ok=True)
    recover_interrupted_transactions(
        transactions_root=transactions_root,
        staging_root=staging_root,
        generations_root=generations_root,
        failed_root=failed_root,
        skill_root=skill_root,
        plist=plist,
        maintenance_plist=maintenance_plist,
        active_root_pointer=active_root_pointer,
        runner=runner,
    )
    candidate_temporary = Path(tempfile.mkdtemp(prefix=".candidate-", dir=staging_root))
    try:
        candidate_temporary.rmdir()
        prepare_candidate(source_root, candidate_temporary, skill_root)
        candidate_generation, candidate_manifest = tree_generation(candidate_temporary)
        candidate = staging_root / candidate_generation
        if candidate.exists():
            existing_generation, _ = tree_generation(candidate)
            if existing_generation != candidate_generation:
                raise RuntimeError("existing candidate staging generation is corrupt")
            shutil.rmtree(candidate_temporary)
        else:
            durable_replace(candidate_temporary, candidate)
    except Exception:
        if candidate_temporary.exists():
            shutil.rmtree(candidate_temporary)
        raise

    paths = {
        "source_root": str(source_root),
        "skill_root": str(skill_root),
        "archive_root": str(archive_root),
        "sessions_root": str(sessions_root),
        "python_executable": str(python_executable),
        "codex_cli": str(codex_cli),
    }
    transaction_id = transaction_identifier(candidate_generation, paths)
    transaction_root = transactions_root / transaction_id
    rotate_completed_rollback_attempt(transaction_root)
    transaction_root.mkdir(parents=True, exist_ok=True)
    journal_path = transaction_root / "journal.json"
    commit_receipt = transaction_root / "commit-receipt.json"
    rollback_receipt = transaction_root / "rollback-receipt.json"

    if commit_receipt.is_file():
        committed = read_json(commit_receipt)
        if (
            committed.get("status") != "committed"
            or committed.get("transaction_id") != transaction_id
            or committed.get("candidate_generation") != candidate_generation
        ):
            raise RuntimeError("commit receipt does not match the candidate transaction")
        current_generation, _ = tree_generation(skill_root)
        if current_generation != candidate_generation:
            raise RuntimeError("commit receipt exists but the verified generation is not active")
        telemetry = wait_for_collector(archive_root, previous_pid=None, timeout_seconds=30)
        effect_started_at = datetime.now(timezone.utc)
        effect_probe = create_installed_effect_probe(
            sessions_root, previous_watermark=previous_archive_watermark
        )
        try:
            telemetry = wait_for_collector(
                archive_root,
                previous_pid=None,
                minimum_watermark=effect_probe["watermark"],
                effect_started_at=effect_started_at,
                timeout_seconds=30,
            )
        finally:
            remove_installed_effect_probe(effect_probe)
        effect = validate_installed_launch_contract(
            skill_root=skill_root,
            archive_root=archive_root,
            sessions_root=sessions_root,
            python_executable=python_executable,
            codex_cli=codex_cli,
            plist=plist,
            active_root_pointer=active_root_pointer,
            telemetry=telemetry,
            previous_archive_watermark=previous_archive_watermark,
            required_effect_watermark=effect_probe["watermark"],
            effect_started_at=effect_started_at,
            runner=runner,
        )
        verify_collector_lifecycle_alignment(
            lifecycle_path,
            generation=candidate_generation,
            archive_root=archive_root,
            expected_command=effect["expected_command"],
            telemetry=telemetry,
            launchd_pid=effect["launchd_pid"],
        )
        persist_collector_lifecycle(
            lifecycle_path,
            generation=candidate_generation,
            archive_root=archive_root,
            expected_command=effect["expected_command"],
            telemetry=telemetry,
            launchd_pid=effect["launchd_pid"],
        )
        if candidate.exists():
            shutil.rmtree(candidate)
            fsync_directory(candidate.parent)
        committed_prior = committed.get("prior_generation_path")
        if isinstance(committed_prior, str) and committed_prior:
            committed_prior_path = Path(committed_prior)
            if committed_prior_path.is_dir():
                expected_prior = committed.get("prior_generation")
                if tree_generation(committed_prior_path)[0] != expected_prior:
                    raise RuntimeError("committed rollback generation hash mismatch")
                shutil.rmtree(committed_prior_path)
                fsync_directory(committed_prior_path.parent)
        return {
            "status": "installed",
            "idempotent": True,
            "transaction_id": transaction_id,
            "candidate_generation": candidate_generation,
            "active_pid": effect["launchd_pid"],
            "archive_root": str(archive_root),
        }

    snapshots = {
        "collector_plist": snapshot_file(plist, transaction_root / "collector.plist"),
        "maintenance_plist": snapshot_file(maintenance_plist, transaction_root / "maintenance.plist"),
        "active_root_pointer": snapshot_file(active_root_pointer, transaction_root / "active-root.txt"),
        "collector_lifecycle": snapshot_file(
            lifecycle_path, transaction_root / "collector-lifecycle.json"
        ),
    }
    prior_generation = None
    if skill_root.exists():
        prior_generation, prior_manifest = tree_generation(skill_root)
    else:
        prior_manifest = []
    journal: dict[str, Any] = {
        "format_version": 1,
        "transaction_id": transaction_id,
        "state": "prepare",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "events": [],
        "paths": paths,
        "candidate_generation": candidate_generation,
        "candidate_manifest": candidate_manifest,
        "prior_generation": prior_generation,
        "prior_manifest": prior_manifest,
        "snapshots": snapshots,
    }
    transition(journal_path, journal, "prepare", phase="candidate-staged")
    probe = probe_candidate(candidate, runner)
    transition(journal_path, journal, "prepare", phase="candidate-probed", probe=probe)

    with quiesce_collector_for_cutover(
        archive_root,
        plist,
        runner=runner,
    ) as cutover:
        pass

    moved_current = False
    candidate_active = False
    prior_path = generations_root / f"{prior_generation or 'none'}-{transaction_id}"
    failed_path = failed_root / f"{candidate_generation}-{transaction_id}"
    legacy_semantic_backfill: dict[str, object] | None = None
    try:
        transition(journal_path, journal, "prepare", phase="activating", cutover=cutover)
        if skill_root.exists():
            if prior_path.exists():
                raise RuntimeError(f"prior generation destination already exists: {prior_path}")
            durable_replace(skill_root, prior_path)
            moved_current = True
        durable_replace(candidate, skill_root)
        candidate_active = True
        runner(
            [
                str(python_executable),
                str(skill_root / "scripts" / "install_codex_autosync.py"),
                "--archive-root",
                str(archive_root),
                "--skill-root",
                str(skill_root),
                "--sessions-root",
                str(sessions_root),
                "--python-executable",
                str(python_executable),
                "--codex-cli",
                str(codex_cli),
                "--load",
                "--defer-maintenance",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        telemetry = wait_for_collector(
            archive_root,
            previous_pid=old_pid,
            timeout_seconds=COLLECTOR_READY_TIMEOUT_SECONDS,
        )
        effect_started_at = datetime.now(timezone.utc)
        effect_probe = create_installed_effect_probe(
            sessions_root, previous_watermark=previous_archive_watermark
        )
        try:
            telemetry = wait_for_collector(
                archive_root,
                previous_pid=old_pid,
                minimum_watermark=effect_probe["watermark"],
                effect_started_at=effect_started_at,
                timeout_seconds=COLLECTOR_READY_TIMEOUT_SECONDS,
            )
        finally:
            remove_installed_effect_probe(effect_probe)
        effect = validate_installed_launch_contract(
            skill_root=skill_root,
            archive_root=archive_root,
            sessions_root=sessions_root,
            python_executable=python_executable,
            codex_cli=codex_cli,
            plist=plist,
            active_root_pointer=active_root_pointer,
            telemetry=telemetry,
            previous_archive_watermark=previous_archive_watermark,
            required_effect_watermark=effect_probe["watermark"],
            effect_started_at=effect_started_at,
            runner=runner,
        )
        lifecycle = persist_collector_lifecycle(
            lifecycle_path,
            generation=candidate_generation,
            archive_root=archive_root,
            expected_command=effect["expected_command"],
            telemetry=telemetry,
            launchd_pid=effect["launchd_pid"],
        )
        transition(
            journal_path,
            journal,
            "verify",
            effect=effect,
            collector_lifecycle=lifecycle,
        )
        runner(
            [
                str(python_executable),
                str(skill_root / "scripts" / "install_maintenance_supervisor.py"),
                "--archive-root",
                str(archive_root),
                "--skill-root",
                str(skill_root),
                "--python-executable",
                str(python_executable),
                "--load",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        runner(
            [
                str(python_executable),
                str(skill_root / "scripts" / "install_dashboard_app_macos.py"),
                "--skill-root",
                str(skill_root),
                "--archive-root",
                str(archive_root),
                "--python-executable",
                str(python_executable),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        legacy_semantic_backfill = retire_legacy_macos_semantic_backfill(
            runner=runner,
        )
        receipt = {
            "format_version": 1,
            "status": "committed",
            "transaction_id": transaction_id,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "candidate_generation": candidate_generation,
            "prior_generation": prior_generation,
            "prior_generation_path": str(prior_path) if moved_current else None,
            "paths": paths,
            "probe": probe,
            "installed_effect_probe": {
                key: value for key, value in effect_probe.items() if key != "path"
            },
            "effect": effect,
        }
        atomic_json(commit_receipt, receipt)
        transition(journal_path, journal, "commit", commit_receipt=str(commit_receipt))
    except Exception as error:
        transition(journal_path, journal, "rollback", phase="started", error=str(error))
        rollback_error: Exception | None = None
        runner(
            [
                "/bin/launchctl",
                "bootout",
                f"gui/{os.getuid()}",
                str(maintenance_plist),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        runner(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            failed_pid = None
            try:
                failed_pid = int(read_json(telemetry_path)["pid"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
            candidate_evidence = failed_path
            if candidate_active and skill_root.exists():
                candidate_evidence = preserve_failed_tree(
                    skill_root,
                    expected_generation=candidate_generation,
                    preferred_path=failed_path,
                    failed_root=failed_root,
                    transaction_id=transaction_id,
                    reason="rollback",
                )
            if moved_current and prior_path.exists():
                durable_replace(prior_path, skill_root)
            restore_snapshot(plist, transaction_root / "collector.plist", snapshots["collector_plist"])
            restore_snapshot(
                maintenance_plist,
                transaction_root / "maintenance.plist",
                snapshots["maintenance_plist"],
            )
            restore_snapshot(
                active_root_pointer,
                transaction_root / "active-root.txt",
                snapshots["active_root_pointer"],
            )
            restore_snapshot(
                lifecycle_path,
                transaction_root / "collector-lifecycle.json",
                snapshots["collector_lifecycle"],
            )
            if bool(snapshots["collector_plist"].get("existed")):
                runner(
                    ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                wait_for_launch_agent(COLLECTOR_LABEL, runner=runner)
            rollback_effect = None
            if bool(snapshots["collector_lifecycle"].get("existed")):
                rollback_effect = verify_restored_collector_effect(
                    lifecycle_path=lifecycle_path,
                    plist=plist,
                    active_root_pointer=active_root_pointer,
                    previous_pid=failed_pid,
                    runner=runner,
                )
            if bool(snapshots["maintenance_plist"].get("existed")):
                runner(
                    ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(maintenance_plist)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            atomic_json(
                rollback_receipt,
                {
                    "format_version": 1,
                    "status": "rolled-back",
                    "transaction_id": transaction_id,
                    "rolled_back_at": datetime.now(timezone.utc).isoformat(),
                    "candidate_evidence": str(candidate_evidence),
                    "error": str(error),
                    "restored_effect": rollback_effect,
                },
            )
            transition(
                journal_path,
                journal,
                "rollback",
                phase="restored",
                rollback_receipt=str(rollback_receipt),
            )
        except Exception as restore_error:
            rollback_error = restore_error
            transition(
                journal_path,
                journal,
                "rollback",
                phase="failed",
                rollback_error=str(restore_error),
            )
        if rollback_error is not None:
            raise RuntimeError(f"upgrade failed and rollback failed: {rollback_error}") from error
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
            command = " ".join(str(part) for part in error.cmd)
            message = f"post-switch command failed ({command})"
            if detail:
                message += f":\n{detail}"
            raise RuntimeError(message) from error
        raise

    if moved_current:
        shutil.rmtree(prior_path, ignore_errors=True)
        fsync_directory(prior_path.parent)
    return {
        "status": "installed",
        "transaction_id": transaction_id,
        "candidate_generation": candidate_generation,
        "commit_receipt": str(commit_receipt),
        "prior_generation_path": None,
        "rollback_generation_pruned_after_commit": moved_current,
        "probe": probe,
        "cutover": cutover,
        "previous_pid": old_pid,
        "active_pid": int(telemetry["pid"]),
        "telemetry_updated_at": telemetry["updated_at"],
        "archive_root": str(archive_root),
        "legacy_semantic_backfill": legacy_semantic_backfill,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--sessions-root", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--codex-cli", required=True)
    args = parser.parse_args()
    result = install(
        source_root=exact_macos_root(args.source_root, "source root"),
        skill_root=exact_macos_root(args.skill_root, "Skill root", must_exist=False),
        archive_root=exact_macos_root(args.archive_root, "archive root"),
        sessions_root=exact_macos_root(args.sessions_root, "sessions root"),
        python_executable=executable_entry_path(args.python_executable),
        codex_cli=Path(args.codex_cli).expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
