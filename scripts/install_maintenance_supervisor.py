#!/usr/bin/env python3
"""Install the bounded, low-frequency Memory Wuxian maintenance scheduler."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, Sequence
from xml.etree import ElementTree as ET

try:
    from install_cloud_sync import TASK_XML_NAMESPACE, atomic_write_bytes, launchctl_domain
    from platform_process import no_window_kwargs
    from platform_runtime import executable_entry_path
except ModuleNotFoundError:
    from scripts.install_cloud_sync import TASK_XML_NAMESPACE, atomic_write_bytes, launchctl_domain
    from scripts.platform_process import no_window_kwargs
    from scripts.platform_runtime import executable_entry_path


MACOS_LABEL = "com.openai.codex.memory-wuxian-maintenance"
WINDOWS_TASK_NAME = "MemoryWuxianMaintenance"
INTERVAL_SECONDS = 300
DEFAULT_MAXIMUM_SEMANTIC_JOBS = 4
SEMANTIC_JOB_TIMEOUT_SECONDS = 900
MAINTENANCE_CLOSEOUT_MARGIN_SECONDS = 600
WINDOWS_EXECUTION_LIMIT_SECONDS = (
    DEFAULT_MAXIMUM_SEMANTIC_JOBS * SEMANTIC_JOB_TIMEOUT_SECONDS
    + MAINTENANCE_CLOSEOUT_MARGIN_SECONDS
)
Runner = Callable[..., subprocess.CompletedProcess]


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
        "--once",
    ]


def macos_plist(python: Path, skill: Path, archive: Path) -> dict:
    logs = archive / "maintenance"
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": maintenance_command(python, skill, archive),
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "scheduler.stdout.log"),
        "StandardErrorPath": str(logs / "scheduler.stderr.log"),
    }


def _windows_user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def windows_xml(python: Path, skill: Path, archive: Path) -> bytes:
    pythonw = python.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError(f"pythonw.exe is required for focus-safe maintenance: {pythonw}")
    ET.register_namespace("", TASK_XML_NAMESPACE)
    task = ET.Element(f"{{{TASK_XML_NAMESPACE}}}Task", {"version": "1.4"})
    info = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}RegistrationInfo")
    ET.SubElement(info, f"{{{TASK_XML_NAMESPACE}}}Description").text = (
        "Reconcile MemoryWuxian capture, summary, and backup debt without opening a console window."
    )
    triggers = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Triggers")
    trigger = ET.SubElement(triggers, f"{{{TASK_XML_NAMESPACE}}}TimeTrigger")
    repetition = ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}Repetition")
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}Interval").text = "PT5M"
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}StopAtDurationEnd").text = "false"
    ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}StartBoundary").text = (
        dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    )
    ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}Enabled").text = "true"
    principals = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Principals")
    principal = ET.SubElement(principals, f"{{{TASK_XML_NAMESPACE}}}Principal", {"id": "Author"})
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}UserId").text = _windows_user_id()
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("Enabled", "true"),
        ("Hidden", "true"),
        ("ExecutionTimeLimit", iso8601_duration(WINDOWS_EXECUTION_LIMIT_SECONDS)),
        ("Priority", "7"),
    ):
        ET.SubElement(settings, f"{{{TASK_XML_NAMESPACE}}}{name}").text = value
    actions = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Actions", {"Context": "Author"})
    action = ET.SubElement(actions, f"{{{TASK_XML_NAMESPACE}}}Exec")
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Command").text = str(pythonw)
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Arguments").text = subprocess.list2cmdline(
        maintenance_command(python, skill, archive)[1:]
    )
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


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
        output = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"
        atomic_write_bytes(output, plistlib.dumps(macos_plist(python, skill, archive), sort_keys=True))
        if load:
            domain = launchctl_domain()
            runner(["/bin/launchctl", "bootout", domain, str(output)], check=False)
            runner(["/bin/launchctl", "bootstrap", domain, str(output)], check=True)
        return str(output)
    if platform_name == "win32":
        payload = windows_xml(python, skill, archive)
        fd, temporary = tempfile.mkstemp(prefix=".memory-wuxian-maintenance.", suffix=".xml")
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            atomic_write_bytes(temporary_path, payload)
            runner(["schtasks.exe", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(temporary_path), "/F"], check=True, **no_window_kwargs())
        finally:
            temporary_path.unlink(missing_ok=True)
        if load:
            runner(["schtasks.exe", "/Run", "/TN", WINDOWS_TASK_NAME], check=True, **no_window_kwargs())
        return WINDOWS_TASK_NAME
    raise ValueError("maintenance scheduling supports Windows and macOS")


def uninstall(*, platform_name: str, runner: Runner) -> None:
    if platform_name == "darwin":
        output = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"
        runner(["/bin/launchctl", "bootout", launchctl_domain(), str(output)], check=False)
        output.unlink(missing_ok=True)
        return
    if platform_name == "win32":
        runner(["schtasks.exe", "/End", "/TN", WINDOWS_TASK_NAME], check=False, **no_window_kwargs())
        runner(["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], check=False, **no_window_kwargs())
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
