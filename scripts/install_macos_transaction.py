#!/usr/bin/env python3
"""Transactionally upgrade one installed macOS MemoryWuxian user runtime."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

try:
    from platform_runtime import executable_entry_path
except ModuleNotFoundError:
    from scripts.platform_runtime import executable_entry_path


COLLECTOR_LABEL = "com.memorywuxian.codex-sync"
Runner = Callable[..., subprocess.CompletedProcess]


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def prepare_candidate(source_root: Path, candidate: Path, current: Path) -> None:
    shutil.rmtree(candidate, ignore_errors=True)
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
    for executable in (
        candidate / "bin" / "memory-wuxian-collector",
        candidate / "bin" / "memory-wuxian-envelope",
    ):
        executable.chmod(executable.stat().st_mode | 0o111)


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
    codex_home = skill_root.parent.parent
    update_root = codex_home / "updates" / "memory-wuxian"
    candidate = update_root / "candidate"
    rollback = update_root / "rollback-current"
    failed = update_root / "failed-candidate"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{COLLECTOR_LABEL}.plist"
    old_plist = plist.read_bytes() if plist.is_file() else None
    active_root_pointer = codex_home / "memory-wuxian-active-root.txt"
    old_active_root = (
        active_root_pointer.read_bytes() if active_root_pointer.is_file() else None
    )
    old_pid = launchctl_pid(COLLECTOR_LABEL, runner)

    update_root.mkdir(parents=True, exist_ok=True)
    prepare_candidate(source_root, candidate, skill_root)
    probe = probe_candidate(candidate, runner)

    shutil.rmtree(rollback, ignore_errors=True)
    shutil.rmtree(failed, ignore_errors=True)
    if skill_root.exists():
        os.replace(skill_root, rollback)
    try:
        os.replace(candidate, skill_root)
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
                "--since",
                datetime.now().astimezone().isoformat(timespec="seconds"),
                "--load",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        telemetry = wait_for_collector(archive_root, previous_pid=old_pid)
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
    except Exception as error:
        runner(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
            check=False,
            capture_output=True,
            text=True,
        )
        if skill_root.exists():
            os.replace(skill_root, failed)
        if rollback.exists():
            os.replace(rollback, skill_root)
        if old_plist is not None:
            atomic_bytes(plist, old_plist)
            runner(
                ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                check=True,
                capture_output=True,
                text=True,
            )
            wait_for_launch_agent(COLLECTOR_LABEL, runner=runner)
        if old_active_root is None:
            active_root_pointer.unlink(missing_ok=True)
        else:
            atomic_bytes(active_root_pointer, old_active_root)
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or "").strip()
            command = " ".join(str(part) for part in error.cmd)
            message = f"post-switch command failed ({command})"
            if detail:
                message += f":\n{detail}"
            raise RuntimeError(message) from error
        raise

    shutil.rmtree(rollback, ignore_errors=True)
    shutil.rmtree(failed, ignore_errors=True)
    return {
        "status": "installed",
        "probe": probe,
        "previous_pid": old_pid,
        "active_pid": int(telemetry["pid"]),
        "telemetry_updated_at": telemetry["updated_at"],
        "archive_root": str(archive_root),
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
        source_root=Path(args.source_root).expanduser().resolve(),
        skill_root=Path(args.skill_root).expanduser().resolve(),
        archive_root=Path(args.archive_root).expanduser().resolve(),
        sessions_root=Path(args.sessions_root).expanduser().resolve(),
        python_executable=executable_entry_path(args.python_executable),
        codex_cli=Path(args.codex_cli).expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
