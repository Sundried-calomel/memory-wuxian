#!/usr/bin/env python3
"""Install or remove the Windows scheduled task for Memory Wuxian."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence


DEFAULT_TASK_NAME = "MemoryWuxianCodexSync"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MemoryWuxianCodexSync"


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
        subprocess.run(
            ["schtasks.exe", "/End", "/TN", args.task_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", args.task_name, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["reg.exe", "DELETE", RUN_KEY, "/V", RUN_VALUE, "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
    runtime_dir = archive_root / "imports" / "codex"
    output = Path(args.output).expanduser().resolve() if args.output else runtime_dir / "run-collector.cmd"
    since = args.since or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    dt.datetime.fromisoformat(since[:-1] + "+00:00" if since.endswith("Z") else since)
    log_path = runtime_dir / "scheduled-task.log"
    def ps_quote(value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    powershell = (
        "$env:HOME=$env:USERPROFILE\n"
        f"$env:MEMORY_WUXIAN_PYTHON={ps_quote(python_executable)}\n"
        f"$env:MEMORY_WUXIAN_CODEX={ps_quote(codex_cli)}\n"
        "while ($true) {\n"
        f"  & {ps_quote(collector)} --archive-root {ps_quote(archive_root)} "
        f"--config {ps_quote(config)} --sessions-root {ps_quote(sessions_root)} "
        f"--since {ps_quote(since)} --debounce-ms {args.debounce_ms} "
        f"*>> {ps_quote(log_path)}\n"
        "  Start-Sleep -Seconds 5\n"
        "}\n"
    )
    encoded = base64.b64encode(powershell.encode("utf-16le")).decode("ascii")
    wrapper = (
        "@echo off\n"
        "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass "
        f"-EncodedCommand {encoded}\n"
    )
    atomic_write_text(output, wrapper)
    if args.load:
        task = None
        if args.backend != "run-key":
            task = subprocess.run(
                [
                    "schtasks.exe", "/Create", "/TN", args.task_name,
                    "/SC", "ONLOGON", "/RL", "LIMITED", "/TR", str(output), "/F",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        if task is not None and task.returncode == 0:
            subprocess.run(["schtasks.exe", "/Run", "/TN", args.task_name], check=True)
            print(f"task:{args.task_name}")
        else:
            if args.backend == "task":
                raise SystemExit(task.stderr.strip() or "Task Scheduler registration failed")
            run_command = (
                "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass "
                f"-EncodedCommand {encoded}"
            )
            subprocess.run(
                ["reg.exe", "ADD", RUN_KEY, "/V", RUN_VALUE, "/T", "REG_SZ", "/D", run_command, "/F"],
                check=True,
            )
            creation_flags = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            )
            try:
                subprocess.Popen(
                    [
                        "powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
                        "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=creation_flags,
                )
            except PermissionError:
                print("immediate-start:deferred-by-process-policy")
            print(f"run-key:{RUN_KEY}\\{RUN_VALUE}")
    print(f"wrapper:{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
