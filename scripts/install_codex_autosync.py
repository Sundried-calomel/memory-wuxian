#!/usr/bin/env python3
"""Install or update the macOS LaunchAgent for Memory無限 Codex synchronization."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence


LABEL = "com.memorywuxian.codex-sync"


def atomic_write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a macOS LaunchAgent that incrementally imports Codex rollout JSONL"
    )
    parser.add_argument("--archive-root", required=True, help="Primary Memory無限 archive root")
    parser.add_argument(
        "--skill-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Installed Memory無限 Skill directory",
    )
    parser.add_argument(
        "--sessions-root",
        default="~/.codex/sessions",
        help="Codex native session directory",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python executable used by LaunchAgent; defaults to the interpreter running this installer",
    )
    parser.add_argument(
        "--since",
        help="Only monitor session files modified from this ISO-8601 time; defaults to installation time",
    )
    parser.add_argument("--interval", type=int, default=15, help="Synchronization interval in seconds")
    parser.add_argument(
        "--output",
        default=f"~/Library/LaunchAgents/{LABEL}.plist",
        help="LaunchAgent plist destination",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Bootstrap and start the LaunchAgent after writing the plist",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 5:
        raise SystemExit("--interval must be at least 5 seconds")
    skill_root = Path(args.skill_root).expanduser().resolve()
    archive_root = Path(args.archive_root).expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve()
    python_executable = Path(args.python_executable).expanduser().absolute()
    output = Path(args.output).expanduser().resolve()
    cli = skill_root / "scripts" / "memory_cli.py"
    config = skill_root / "config.yaml"
    if not cli.is_file() or not config.is_file():
        raise SystemExit(f"Memory無限 installation is incomplete: {skill_root}")
    if not sessions_root.is_dir():
        raise SystemExit(f"Codex sessions directory does not exist: {sessions_root}")
    if not python_executable.is_file():
        raise SystemExit(f"Python executable does not exist: {python_executable}")
    since = args.since or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    dt.datetime.fromisoformat(since[:-1] + "+00:00" if since.endswith("Z") else since)

    log_dir = archive_root / "imports" / "codex"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_executable),
            str(cli),
            "--root",
            str(archive_root),
            "--config",
            str(config),
            "sync-codex",
            "--sessions-root",
            str(sessions_root),
            "--since",
            since,
        ],
        "RunAtLoad": True,
        "StartInterval": args.interval,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1"},
        "StandardOutPath": str(log_dir / "launch-agent.stdout.log"),
        "StandardErrorPath": str(log_dir / "launch-agent.stderr.log"),
    }
    atomic_write_plist(output, payload)
    if args.load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["/bin/launchctl", "bootout", domain, str(output)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(output)], check=True)
        subprocess.run(["/bin/launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
