#!/usr/bin/env python3
"""Install the bounded, low-frequency Memory Wuxian maintenance scheduler."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

try:
    from platform_atomic import atomic_replace_bytes
    from platform_process import no_window_kwargs
    from platform_runtime import executable_entry_path
    import platform_scheduler as scheduler
except ModuleNotFoundError:
    from scripts.platform_atomic import atomic_replace_bytes
    from scripts.platform_process import no_window_kwargs
    from scripts.platform_runtime import executable_entry_path
    from scripts import platform_scheduler as scheduler


MACOS_LABEL = "com.openai.codex.memory-wuxian-maintenance"
LEGACY_MACOS_LABEL = "com.memorywuxian.semantic-backfill"
WINDOWS_TASK_NAME = "MemoryWuxianMaintenance"
INTERVAL_SECONDS = 300
DEFAULT_MAXIMUM_SEMANTIC_JOBS = 8
SEMANTIC_JOB_TIMEOUT_SECONDS = 900
MAINTENANCE_CLOSEOUT_MARGIN_SECONDS = 600
WINDOWS_EXECUTION_LIMIT_SECONDS = (
    DEFAULT_MAXIMUM_SEMANTIC_JOBS * SEMANTIC_JOB_TIMEOUT_SECONDS
    + MAINTENANCE_CLOSEOUT_MARGIN_SECONDS
)
TASK_XML_NAMESPACE = scheduler.TASK_XML_NAMESPACE
launchctl_domain = scheduler.launchctl_domain
Runner = Callable[..., subprocess.CompletedProcess]


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    atomic_replace_bytes(path, payload)


def retire_legacy_macos_semantic_backfill(
    *,
    runner: Runner,
    home: Optional[Path] = None,
) -> dict[str, object]:
    """Retire only the obsolete launcher; historical archive bytes are untouched."""
    home = home or Path.home()
    plist = home / "Library" / "LaunchAgents" / f"{LEGACY_MACOS_LABEL}.plist"
    existed = plist.is_file()
    runner(
        ["/bin/launchctl", "bootout", launchctl_domain(), str(plist)],
        check=False,
        capture_output=True,
        text=True,
    )
    plist.unlink(missing_ok=True)
    return {
        "label": LEGACY_MACOS_LABEL,
        "plist": str(plist),
        "status": "retired" if existed else "absent",
    }


def iso8601_duration(seconds: int) -> str:
    if seconds <= 0 or seconds % 60:
        raise ValueError("task duration must be a positive whole number of minutes")
    return f"PT{seconds // 60}M"


def maintenance_command(python: Path, skill: Path, archive: Path) -> list[str]:
    return [
        str(python),
        str(skill / "scripts" / "maintenance_supervisor.py"),
        "--root", str(archive),
        "--config", str(skill / "config.yaml"),
        "--max-semantic-jobs", str(DEFAULT_MAXIMUM_SEMANTIC_JOBS),
        "--once",
    ]


def macos_job_spec(python: Path, skill: Path, archive: Path) -> scheduler.MacOSJobSpec:
    logs = archive / "maintenance"
    return scheduler.MacOSJobSpec(
        label=MACOS_LABEL,
        command=tuple(maintenance_command(python, skill, archive)),
        interval_seconds=INTERVAL_SECONDS,
        run_at_load=True,
        keep_alive=False,
        process_type="Background",
        stdout_path=logs / "scheduler.stdout.log",
        stderr_path=logs / "scheduler.stderr.log",
    )


def macos_plist(python: Path, skill: Path, archive: Path) -> dict[str, object]:
    return scheduler.render_macos_plist(macos_job_spec(python, skill, archive))


def _windows_user_id() -> str:
    return scheduler.windows_user_id()


def windows_task_spec(python: Path, skill: Path, archive: Path) -> scheduler.WindowsTaskSpec:
    pythonw = python.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError(f"pythonw.exe is required for focus-safe maintenance: {pythonw}")
    return scheduler.WindowsTaskSpec(
        task_name=WINDOWS_TASK_NAME,
        description=(
            "Reconcile MemoryWuxian capture, summary, and backup debt without "
            "opening a console window."
        ),
        command=pythonw,
        arguments=tuple(maintenance_command(python, skill, archive)[1:]),
        interval="PT5M",
        execution_limit=iso8601_duration(WINDOWS_EXECUTION_LIMIT_SECONDS),
        priority="7",
        allow_hard_terminate=None,
        multiple_instances="IgnoreNew",
        disallow_start_on_batteries=False,
        stop_on_batteries=False,
        start_when_available=True,
        network_required=False,
        hidden=True,
        logon_type="InteractiveToken",
        run_level="LeastPrivilege",
    )


def windows_xml(python: Path, skill: Path, archive: Path) -> bytes:
    boundary = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    return scheduler.render_windows_task_xml(
        windows_task_spec(python, skill, archive),
        user_id=_windows_user_id(),
        start_boundary=boundary,
    )


def install(
    archive: Path,
    skill: Path,
    python: Path,
    *,
    platform_name: str,
    load: bool,
    runner: Runner,
) -> str:
    for path in (archive, skill / "config.yaml", skill / "scripts" / "maintenance_supervisor.py", python):
        if not path.exists():
            raise ValueError(f"required maintenance path does not exist: {path}")
    (archive / "maintenance").mkdir(parents=True, exist_ok=True)
    if platform_name == "darwin":
        output = scheduler.install_macos_job(
            macos_job_spec(python, skill, archive),
            load=load,
            runner=runner,
            write_bytes=atomic_write_bytes,
            domain=launchctl_domain() if load else None,
        )
        return str(output)
    if platform_name == "win32":
        spec = windows_task_spec(python, skill, archive)
        scheduler.install_windows_task(
            spec,
            windows_xml(python, skill, archive),
            temporary_prefix=".memory-wuxian-maintenance.",
            schtasks="schtasks.exe",
            load=load,
            runner=runner,
            write_bytes=atomic_write_bytes,
            runner_kwargs=no_window_kwargs(),
        )
        return WINDOWS_TASK_NAME
    raise ValueError("maintenance scheduling supports Windows and macOS")


def uninstall(*, platform_name: str, runner: Runner) -> None:
    if platform_name == "darwin":
        scheduler.uninstall_macos_job(
            MACOS_LABEL,
            runner=runner,
            domain=launchctl_domain(),
        )
        return
    if platform_name == "win32":
        scheduler.uninstall_windows_task(
            WINDOWS_TASK_NAME,
            schtasks="schtasks.exe",
            runner=runner,
            end_first=True,
            runner_kwargs=no_window_kwargs(),
        )
        return
    raise ValueError("maintenance scheduling supports Windows and macOS")


def main(argv: Optional[Sequence[str]] = None, *, platform_name: Optional[str] = None, runner: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    platform_name = platform_name or sys.platform
    if args.uninstall:
        uninstall(platform_name=platform_name, runner=runner)
        return 0
    archive = Path(args.archive_root).expanduser().resolve()
    skill = Path(args.skill_root).expanduser().resolve()
    python = executable_entry_path(args.python_executable, platform_name=platform_name)
    print(install(archive, skill, python, platform_name=platform_name, load=args.load, runner=runner))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
