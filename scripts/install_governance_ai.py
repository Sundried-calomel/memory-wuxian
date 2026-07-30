#!/usr/bin/env python3
"""Install or remove the five-minute model-free governance AI scheduler."""

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
from xml.etree import ElementTree as ET

from install_cloud_sync import (
    TASK_XML_NAMESPACE,
    atomic_write_bytes,
    launchctl_domain,
    powershell_quote,
    windows_system_executable,
)
try:
    from platform_runtime import executable_entry_path
except ModuleNotFoundError:
    from scripts.platform_runtime import executable_entry_path


MACOS_LABEL = "com.openai.codex.memory-wuxian-governance-ai"
WINDOWS_TASK_NAME = "MemoryWuxianGovernanceAI"
INTERVAL_SECONDS = 300


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


def macos_plist(python: Path, skill: Path, archive: Path) -> dict:
    logs = archive / "environment" / "governance-ai"
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": scheduler_command(python, skill, archive),
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "scheduler.stdout.log"),
        "StandardErrorPath": str(logs / "scheduler.stderr.log"),
    }


def windows_user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def windows_xml(python: Path, skill: Path, archive: Path) -> bytes:
    ET.register_namespace("", TASK_XML_NAMESPACE)
    task = ET.Element(f"{{{TASK_XML_NAMESPACE}}}Task", {"version": "1.4"})
    info = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}RegistrationInfo")
    ET.SubElement(info, f"{{{TASK_XML_NAMESPACE}}}Description").text = (
        "Check the MemoryWuxian governance queue every five minutes and invoke "
        "one ephemeral AI worker only for a due batch."
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
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}UserId").text = windows_user_id()
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
        ("ExecutionTimeLimit", "PT20M"),
    ):
        ET.SubElement(settings, f"{{{TASK_XML_NAMESPACE}}}{name}").text = value
    actions = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Actions", {"Context": "Author"})
    action = ET.SubElement(actions, f"{{{TASK_XML_NAMESPACE}}}Exec")
    executable = python.with_name("pythonw.exe")
    if not executable.is_file():
        executable = python
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Command").text = str(executable)
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Arguments").text = subprocess.list2cmdline(
        scheduler_command(python, skill, archive)[1:]
    )
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


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
            output = Path.home() / "Library/LaunchAgents" / f"{MACOS_LABEL}.plist"
            subprocess.run(
                ["/bin/launchctl", "bootout", launchctl_domain(), str(output)],
                check=False,
            )
            output.unlink(missing_ok=True)
            print(output)
            return 0
        if sys.platform == "win32":
            schtasks = windows_system_executable(r"System32\schtasks.exe")
            subprocess.run([str(schtasks), "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], check=False)
            print(WINDOWS_TASK_NAME)
            return 0
        raise SystemExit("governance AI scheduling supports macOS and Windows")
    for path in (archive, skill / "config.yaml", skill / "scripts/memory_cli.py", python):
        if not path.exists():
            raise SystemExit(f"required path does not exist: {path}")
    logs = archive / "environment" / "governance-ai"
    logs.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        output = Path.home() / "Library/LaunchAgents" / f"{MACOS_LABEL}.plist"
        atomic_write_bytes(output, plistlib.dumps(macos_plist(python, skill, archive), sort_keys=True))
        if args.load:
            subprocess.run(
                ["/bin/launchctl", "bootout", launchctl_domain(), str(output)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["/bin/launchctl", "bootstrap", launchctl_domain(), str(output)],
                check=True,
            )
        print(output)
        return 0
    if sys.platform == "win32":
        schtasks = windows_system_executable(r"System32\schtasks.exe")
        fd, temporary = tempfile.mkstemp(prefix=".memory-wuxian-governance-ai.", suffix=".xml")
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            atomic_write_bytes(temporary_path, windows_xml(python, skill, archive))
            subprocess.run(
                [str(schtasks), "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", str(temporary_path), "/F"],
                check=True,
            )
        finally:
            temporary_path.unlink(missing_ok=True)
        if args.load:
            subprocess.run([str(schtasks), "/Run", "/TN", WINDOWS_TASK_NAME], check=True)
        print(WINDOWS_TASK_NAME)
        return 0
    raise SystemExit("governance AI scheduling supports macOS and Windows")


if __name__ == "__main__":
    raise SystemExit(main())
