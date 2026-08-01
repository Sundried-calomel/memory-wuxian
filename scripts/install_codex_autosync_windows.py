#!/usr/bin/env python3
"""Install or remove the Windows scheduled task for Memory Wuxian."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

try:
    from platform_process import detached_no_window_kwargs, no_window_kwargs
    from collector_activation import resolve_activation_since
except ModuleNotFoundError:
    from scripts.platform_process import detached_no_window_kwargs, no_window_kwargs
    from scripts.collector_activation import resolve_activation_since


DEFAULT_TASK_NAME = "MemoryWuxianCodexSync"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MemoryWuxianCodexSync"


def active_root_pointer() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "memory-wuxian-active-root.txt"


def default_codex_cli() -> str:
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    bundled = Path.home() / ".codex/.sandbox-bin/codex.exe"
    return str(bundled if bundled.exists() else Path("codex.exe"))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def collector_command(
    collector: Path,
    archive_root: Path,
    config: Path,
    sessions_root: Path,
    python_executable: Path,
    codex_cli: Path,
    since: str,
    debounce_ms: int,
) -> list[str]:
    return [
        str(collector),
        "--archive-root", str(archive_root),
        "--config", str(config),
        "--sessions-root", str(sessions_root),
        "--python-executable", str(python_executable),
        "--codex-cli", str(codex_cli),
        "--since", since,
        "--debounce-ms", str(debounce_ms),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the persistent Windows Memory Wuxian collector task"
    )
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--sessions-root", default="~/.codex/sessions")
    parser.add_argument("--collector-executable")
    parser.add_argument("--python-executable", default=shutil.which("python") or "python.exe")
    parser.add_argument("--codex-cli", default=default_codex_cli())
    parser.add_argument("--since")
    parser.add_argument("--debounce-ms", type=int, default=400)
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    parser.add_argument("--backend", choices=("auto", "task", "run-key"), default="auto")
    parser.add_argument(
        "--runtime-directory",
        default=str(Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "MemoryWuxian"),
    )
    parser.add_argument("--output")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if os.name != "nt" and (args.load or args.uninstall):
        raise SystemExit("Windows task registration is only available on Windows")
    if args.debounce_ms < 100:
        raise SystemExit("--debounce-ms must be at least 100")
    if args.uninstall:
        try:
            from install_maintenance_supervisor import uninstall as uninstall_maintenance
        except ModuleNotFoundError:
            from scripts.install_maintenance_supervisor import uninstall as uninstall_maintenance
        uninstall_maintenance(platform_name="win32", runner=subprocess.run)
        subprocess.run(
            ["schtasks.exe", "/End", "/TN", args.task_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", args.task_name, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        subprocess.run(
            ["reg.exe", "DELETE", RUN_KEY, "/V", RUN_VALUE, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        archive_root = Path(args.archive_root).expanduser().resolve()
        (archive_root / "imports/codex/run-collector-hidden.vbs").unlink(missing_ok=True)
        runtime_directory = Path(args.runtime_directory).expanduser().resolve()
        (runtime_directory / "run-collector-hidden.vbs").unlink(missing_ok=True)
        return 0

    skill_root = Path(args.skill_root).expanduser().resolve()
    archive_root = Path(args.archive_root).expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve()
    collector = Path(
        args.collector_executable
        or skill_root / "bin" / "memory-wuxian-collector.exe"
    ).expanduser().resolve()
    python_executable = Path(args.python_executable).expanduser().resolve()
    codex_cli = Path(args.codex_cli).expanduser().resolve()
    config = skill_root / "config.yaml"
    for label, path in {
        "skill config": config,
        "sessions root": sessions_root,
        "collector": collector,
        "Python": python_executable,
        "Codex CLI": codex_cli,
    }.items():
        if not path.exists():
            raise SystemExit(f"{label} does not exist: {path}")

    archive_root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(active_root_pointer(), f"{archive_root}\n")
    runtime_dir = archive_root / "imports" / "codex"
    output = Path(args.output).expanduser().resolve() if args.output else runtime_dir / "collector-command.json"
    since = resolve_activation_since(archive_root, args.since)
    command = collector_command(
        collector, archive_root, config, sessions_root, python_executable,
        codex_cli, since, args.debounce_ms,
    )
    atomic_write_text(
        output,
        json.dumps(
            {"format_version": 1, "command": command, "console_window": False},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
    )
    if args.load:
        subprocess.run(
            ["reg.exe", "DELETE", RUN_KEY, "/V", RUN_VALUE, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        for stale in (
            runtime_dir / "run-collector.cmd",
            runtime_dir / "run-collector-hidden.vbs",
            Path(args.runtime_directory).expanduser().resolve() / "run-collector-hidden.vbs",
        ):
            stale.unlink(missing_ok=True)
        task = None
        if args.backend != "run-key":
            task_command = subprocess.list2cmdline(command)
            task = subprocess.run(
                [
                    "schtasks.exe", "/Create", "/TN", args.task_name,
                    "/SC", "ONLOGON", "/RL", "LIMITED", "/TR", task_command, "/F",
                ],
                check=False,
                capture_output=True,
                text=True,
                **no_window_kwargs(),
            )
        if task is not None and task.returncode == 0:
            subprocess.run(
                ["schtasks.exe", "/Run", "/TN", args.task_name],
                check=True,
                **no_window_kwargs(),
            )
            print(f"task:{args.task_name}")
        else:
            if args.backend == "task":
                raise SystemExit(task.stderr.strip() or "Task Scheduler registration failed")
            run_command = subprocess.list2cmdline(command)
            subprocess.run(
                ["reg.exe", "ADD", RUN_KEY, "/V", RUN_VALUE, "/T", "REG_SZ", "/D", run_command, "/F"],
                check=True,
                **no_window_kwargs(),
            )
            try:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    **detached_no_window_kwargs(),
                )
            except PermissionError:
                print("immediate-start:deferred-by-process-policy")
            print(f"run-key:{RUN_KEY}\\{RUN_VALUE}")
        try:
            from install_maintenance_supervisor import install as install_maintenance
        except ModuleNotFoundError:
            from scripts.install_maintenance_supervisor import install as install_maintenance
        install_maintenance(
            archive_root,
            skill_root,
            python_executable,
            platform_name="win32",
            load=True,
            runner=subprocess.run,
        )
    print(f"command-manifest:{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
