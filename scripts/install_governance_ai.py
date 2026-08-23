#!/usr/bin/env python3
"""Install or remove the five-minute model-free governance AI scheduler."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from platform_atomic import atomic_replace_bytes
    from platform_runtime import executable_entry_path
    import platform_scheduler as scheduler
except ModuleNotFoundError:
    from scripts.platform_atomic import atomic_replace_bytes
    from scripts.platform_runtime import executable_entry_path
    from scripts import platform_scheduler as scheduler


MACOS_LABEL = "com.openai.codex.memory-wuxian-governance-ai"
WINDOWS_TASK_NAME = "MemoryWuxianGovernanceAI"
INTERVAL_SECONDS = 300
TASK_XML_NAMESPACE = scheduler.TASK_XML_NAMESPACE
launchctl_domain = scheduler.launchctl_domain
windows_system_executable = scheduler.windows_system_executable
windows_user_id = scheduler.windows_user_id


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    atomic_replace_bytes(path, payload)


def scheduler_command(python: Path, skill: Path, archive: Path) -> list[str]:
    return [
        str(python),
        str(skill / "scripts" / "memory_cli.py"),
        "--root",
        str(archive),
        "--config",
        str(skill / "config.yaml"),
        "environment-governance-ai-tick",
        "--run-ai",
        "--maximum-batches",
        "1",
    ]


def macos_job_spec(python: Path, skill: Path, archive: Path) -> scheduler.MacOSJobSpec:
    logs = archive / "environment" / "governance-ai"
    return scheduler.MacOSJobSpec(
        label=MACOS_LABEL,
        command=tuple(scheduler_command(python, skill, archive)),
        interval_seconds=INTERVAL_SECONDS,
        run_at_load=True,
        keep_alive=False,
        process_type="Background",
        stdout_path=logs / "scheduler.stdout.log",
        stderr_path=logs / "scheduler.stderr.log",
    )


def macos_plist(python: Path, skill: Path, archive: Path) -> dict[str, object]:
    return scheduler.render_macos_plist(macos_job_spec(python, skill, archive))


def windows_task_spec(python: Path, skill: Path, archive: Path) -> scheduler.WindowsTaskSpec:
    executable = python.with_name("pythonw.exe")
    if not executable.is_file():
        executable = python
    return scheduler.WindowsTaskSpec(
        task_name=WINDOWS_TASK_NAME,
        description=(
            "Check the MemoryWuxian governance queue every five minutes and invoke "
            "one ephemeral AI worker only for a due batch."
        ),
        command=executable,
        arguments=tuple(scheduler_command(python, skill, archive)[1:]),
        interval="PT5M",
        execution_limit="PT20M",
        priority=None,
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
        user_id=windows_user_id(),
        start_boundary=boundary,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    archive = Path(args.archive_root).expanduser().resolve()
    skill = Path(args.skill_root).expanduser().resolve()
    python = executable_entry_path(args.python_executable)
    if args.uninstall:
        if sys.platform == "darwin":
            output = scheduler.uninstall_macos_job(
                MACOS_LABEL,
                runner=subprocess.run,
                domain=launchctl_domain(),
            )
            print(output)
            return 0
        if sys.platform == "win32":
            schtasks = windows_system_executable(r"System32\schtasks.exe")
            scheduler.uninstall_windows_task(
                WINDOWS_TASK_NAME,
                schtasks=schtasks,
                runner=subprocess.run,
                end_first=False,
            )
            print(WINDOWS_TASK_NAME)
            return 0
        raise SystemExit("governance AI scheduling supports macOS and Windows")
    for path in (archive, skill / "config.yaml", skill / "scripts/memory_cli.py", python):
        if not path.exists():
            raise SystemExit(f"required path does not exist: {path}")
    logs = archive / "environment" / "governance-ai"
    logs.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        output = scheduler.install_macos_job(
            macos_job_spec(python, skill, archive),
            load=args.load,
            runner=subprocess.run,
            write_bytes=atomic_write_bytes,
            domain=launchctl_domain() if args.load else None,
            bootout_kwargs={"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL},
        )
        print(output)
        return 0
    if sys.platform == "win32":
        schtasks = windows_system_executable(r"System32\schtasks.exe")
        spec = windows_task_spec(python, skill, archive)
        scheduler.install_windows_task(
            spec,
            windows_xml(python, skill, archive),
            temporary_prefix=".memory-wuxian-governance-ai.",
            schtasks=schtasks,
            load=args.load,
            runner=subprocess.run,
            write_bytes=atomic_write_bytes,
        )
        print(WINDOWS_TASK_NAME)
        return 0
    raise SystemExit("governance AI scheduling supports macOS and Windows")


if __name__ == "__main__":
    raise SystemExit(main())
