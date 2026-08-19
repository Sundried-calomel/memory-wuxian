#!/usr/bin/env python3
"""Install or remove the low-frequency MemoryWuxian cloud-sync task."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

try:
    from platform_atomic import atomic_replace_bytes
    from platform_runtime import executable_entry_path
    import platform_scheduler as scheduler
except ModuleNotFoundError:
    from scripts.platform_atomic import atomic_replace_bytes
    from scripts.platform_runtime import executable_entry_path
    from scripts import platform_scheduler as scheduler


MACOS_LABEL = "com.openai.codex.memory-wuxian-cloud-sync"
WINDOWS_TASK_NAME = "MemoryWuxianCloudSync"
INTERVAL_SECONDS = 300
TASK_XML_NAMESPACE = scheduler.TASK_XML_NAMESPACE
launchctl_domain = scheduler.launchctl_domain
windows_system_executable = scheduler.windows_system_executable
windows_user_id = scheduler.windows_user_id
Runner = Callable[..., subprocess.CompletedProcess]


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    atomic_replace_bytes(path, payload)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    newline: str = "\n",
    encoding: str = "utf-8",
) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    atomic_write_bytes(path, normalized.encode(encoding))


def powershell_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def cloud_command(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> list[str]:
    return [
        str(python_executable),
        str(skill_root / "scripts" / "memory_cli.py"),
        "--root",
        str(archive_root),
        "--config",
        str(skill_root / "config.yaml"),
        "cloud-sync",
    ]


def macos_job_spec(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> scheduler.MacOSJobSpec:
    log_dir = archive_root / "federation"
    return scheduler.MacOSJobSpec(
        label=MACOS_LABEL,
        command=tuple(cloud_command(python_executable, skill_root, archive_root)),
        interval_seconds=INTERVAL_SECONDS,
        run_at_load=True,
        keep_alive=False,
        process_type="Background",
        stdout_path=log_dir / "cloud-sync.stdout.log",
        stderr_path=log_dir / "cloud-sync.stderr.log",
    )


def macos_plist(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> dict[str, object]:
    return scheduler.render_macos_plist(
        macos_job_spec(python_executable, skill_root, archive_root)
    )


def windows_wrapper(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> str:
    stdout_path = archive_root / "federation" / "cloud-sync.stdout.log"
    stderr_path = archive_root / "federation" / "cloud-sync.stderr.log"
    arguments = " ".join(
        powershell_quote(argument)
        for argument in cloud_command(python_executable, skill_root, archive_root)[1:]
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"& {powershell_quote(python_executable)} {arguments} "
        f"1>> {powershell_quote(stdout_path)} "
        f"2>> {powershell_quote(stderr_path)}\n"
        "exit $LASTEXITCODE\n"
    )


def windows_task_spec(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> scheduler.WindowsTaskSpec:
    pythonw = python_executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = python_executable
    return scheduler.WindowsTaskSpec(
        task_name=WINDOWS_TASK_NAME,
        description="Run one MemoryWuxian cloud-folder synchronization pass every five minutes.",
        command=pythonw,
        arguments=tuple(cloud_command(python_executable, skill_root, archive_root)[1:]),
        interval="PT5M",
        execution_limit="PT10M",
        priority="7",
        allow_hard_terminate=True,
        multiple_instances="IgnoreNew",
        disallow_start_on_batteries=False,
        stop_on_batteries=False,
        start_when_available=True,
        network_required=False,
        hidden=True,
        logon_type="InteractiveToken",
        run_level="LeastPrivilege",
    )


def windows_task_xml(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
    *,
    user_id: str,
    start_boundary: Optional[str] = None,
) -> bytes:
    boundary = start_boundary or (
        dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    )
    return scheduler.render_windows_task_xml(
        windows_task_spec(python_executable, skill_root, archive_root),
        user_id=user_id,
        start_boundary=boundary,
    )


def validate_install_paths(
    archive_root: Path,
    skill_root: Path,
    python_executable: Path,
) -> None:
    required = {
        "archive root": archive_root,
        "skill config": skill_root / "config.yaml",
        "MemoryWuxian CLI": skill_root / "scripts" / "memory_cli.py",
        "Python executable": python_executable,
    }
    for label, path in required.items():
        if not path.exists():
            raise SystemExit(f"{label} does not exist: {path}")
    if not archive_root.is_dir():
        raise SystemExit(f"archive root is not a directory: {archive_root}")
    if not python_executable.is_file():
        raise SystemExit(f"Python executable is not a file: {python_executable}")


def install_macos(
    archive_root: Path,
    skill_root: Path,
    python_executable: Path,
    *,
    load: bool,
    runner: Runner,
) -> Path:
    log_dir = archive_root / "federation"
    log_dir.mkdir(parents=True, exist_ok=True)
    return scheduler.install_macos_job(
        macos_job_spec(python_executable, skill_root, archive_root),
        load=load,
        runner=runner,
        write_bytes=atomic_write_bytes,
        domain=launchctl_domain() if load else None,
        bootout_kwargs={"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL},
    )


def uninstall_macos(*, runner: Runner) -> Path:
    return scheduler.uninstall_macos_job(
        MACOS_LABEL,
        runner=runner,
        domain=launchctl_domain(),
        bootout_kwargs={"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL},
    )


def install_windows(
    archive_root: Path,
    skill_root: Path,
    python_executable: Path,
    *,
    load: bool,
    runner: Runner,
) -> Path:
    runtime_dir = archive_root / "federation"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = runtime_dir / "run-cloud-sync.ps1"
    atomic_write_text(
        wrapper_path,
        windows_wrapper(python_executable, skill_root, archive_root),
        newline="\r\n",
        encoding="utf-8-sig",
    )
    task_xml = windows_task_xml(
        python_executable,
        skill_root,
        archive_root,
        user_id=windows_user_id(),
    )
    schtasks = windows_system_executable(r"System32\schtasks.exe")
    scheduler.install_windows_task(
        windows_task_spec(python_executable, skill_root, archive_root),
        task_xml,
        temporary_prefix=".memory-wuxian-cloud-sync.",
        schtasks=schtasks,
        load=load,
        runner=runner,
        write_bytes=atomic_write_bytes,
    )
    return wrapper_path


def uninstall_windows(*, runner: Runner) -> None:
    schtasks = windows_system_executable(r"System32\schtasks.exe")
    scheduler.uninstall_windows_task(
        WINDOWS_TASK_NAME,
        schtasks=schtasks,
        runner=runner,
        end_first=True,
        runner_kwargs={"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--python-executable", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--load",
        action="store_true",
        help="Register and immediately start one cloud-sync pass",
    )
    action.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove only the scheduler task; preserve archives, cloud data, identities, and keys",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    platform_name: Optional[str] = None,
    runner: Runner = subprocess.run,
) -> int:
    args = build_parser().parse_args(argv)
    platform_name = platform_name or sys.platform
    if args.uninstall:
        if platform_name == "darwin":
            output = uninstall_macos(runner=runner)
            print(output)
            return 0
        if platform_name == "win32":
            uninstall_windows(runner=runner)
            print(json.dumps({"task": WINDOWS_TASK_NAME}, ensure_ascii=True))
            return 0
        raise SystemExit("Cloud-sync scheduling supports Windows and macOS")

    archive_root = Path(args.archive_root).expanduser().resolve()
    skill_root = Path(args.skill_root).expanduser().resolve()
    python_executable = executable_entry_path(
        args.python_executable,
        platform_name=platform_name,
    )
    validate_install_paths(archive_root, skill_root, python_executable)
    if platform_name == "darwin":
        output = install_macos(
            archive_root,
            skill_root,
            python_executable,
            load=args.load,
            runner=runner,
        )
        print(output)
        return 0
    if platform_name == "win32":
        output = install_windows(
            archive_root,
            skill_root,
            python_executable,
            load=args.load,
            runner=runner,
        )
        print(
            json.dumps(
                {"task": WINDOWS_TASK_NAME, "wrapper": str(output)},
                ensure_ascii=True,
            )
        )
        return 0
    raise SystemExit("Cloud-sync scheduling supports Windows and macOS")


if __name__ == "__main__":
    raise SystemExit(main())
