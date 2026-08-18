#!/usr/bin/env python3
"""Install or update the native macOS LaunchAgent for Memory無限."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

try:
    from platform_runtime import executable_entry_path
    from collector_activation import resolve_activation_since
except ModuleNotFoundError:
    from scripts.platform_runtime import executable_entry_path
    from scripts.collector_activation import resolve_activation_since


LABEL = "com.memorywuxian.codex-sync"


def exact_root_argument(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{label} must be an absolute path: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise SystemExit(f"{label} does not exist: {path}") from error


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


def atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a persistent macOS LaunchAgent for the native Memory無限 collector"
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
        "--collector-executable",
        help="Compiled collector; defaults to <skill-root>/bin/memory-wuxian-collector",
    )
    parser.add_argument(
        "--python-executable",
        help="Python used only when a due semantic-summary job is executed",
    )
    parser.add_argument(
        "--codex-cli",
        help="Codex CLI used by the ephemeral semantic-summary worker",
    )
    parser.add_argument(
        "--since",
        help="Initial coverage boundary; upgrades preserve the earliest recorded value",
    )
    parser.add_argument(
        "--debounce-ms",
        type=int,
        default=400,
        help="Quiet period used to combine adjacent filesystem events",
    )
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
    parser.add_argument(
        "--defer-maintenance",
        action="store_true",
        help="Start only the collector; the transaction owner loads maintenance after readiness",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.debounce_ms < 100:
        raise SystemExit("--debounce-ms must be at least 100")
    skill_root = exact_root_argument(args.skill_root, "Skill root")
    archive_root = exact_root_argument(args.archive_root, "archive root")
    sessions_root = exact_root_argument(args.sessions_root, "sessions root")
    if archive_root in (skill_root, sessions_root) or archive_root.is_relative_to(skill_root):
        raise SystemExit("archive root must be distinct from Skill and sessions roots")
    collector = Path(
        args.collector_executable or skill_root / "bin" / "memory-wuxian-collector"
    ).expanduser().absolute()
    output = Path(args.output).expanduser().resolve()
    codex_home = Path(
        os.environ.get("CODEX_HOME", str(skill_root.parent.parent))
    ).expanduser().resolve()
    active_root_pointer = codex_home / "memory-wuxian-active-root.txt"
    config = skill_root / "config.yaml"
    if not config.is_file():
        raise SystemExit(f"Memory無限 installation is incomplete: {skill_root}")
    if not sessions_root.is_dir():
        raise SystemExit(f"Codex sessions directory does not exist: {sessions_root}")
    if not collector.is_file() or not os.access(collector, os.X_OK):
        raise SystemExit(
            f"Native collector does not exist or is not executable: {collector}. "
            "Run scripts/build_native_collector.sh first."
        )
    since = resolve_activation_since(archive_root, args.since)
    environment = {"RUST_BACKTRACE": "1"}
    if args.python_executable:
        python_executable = executable_entry_path(args.python_executable)
        if not python_executable.is_file():
            raise SystemExit(f"Python executable does not exist: {python_executable}")
        environment["MEMORY_WUXIAN_PYTHON"] = str(python_executable)
    if args.codex_cli:
        codex_cli = Path(args.codex_cli).expanduser().resolve()
        if not codex_cli.is_file():
            raise SystemExit(f"Codex CLI does not exist: {codex_cli}")
        environment["MEMORY_WUXIAN_CODEX"] = str(codex_cli)

    log_dir = archive_root / "imports" / "codex"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(collector),
            "--archive-root",
            str(archive_root),
            "--config",
            str(config),
            "--sessions-root",
            str(sessions_root),
            "--since",
            since,
            "--debounce-ms",
            str(args.debounce_ms),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "EnvironmentVariables": environment,
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
        if not args.defer_maintenance:
            try:
                from install_maintenance_supervisor import install as install_maintenance
            except ModuleNotFoundError:
                from scripts.install_maintenance_supervisor import install as install_maintenance
            if not args.python_executable:
                raise SystemExit("--python-executable is required when loading background maintenance")
            install_maintenance(
                archive_root,
                skill_root,
                executable_entry_path(args.python_executable),
                platform_name="darwin",
                load=True,
                runner=subprocess.run,
            )
    atomic_write_text(active_root_pointer, f"{archive_root}\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
