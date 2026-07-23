#!/usr/bin/env python3
"""Install or remove the low-frequency MemoryWuxian cloud-sync task."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional, Sequence
from xml.etree import ElementTree as ET


MACOS_LABEL = "com.openai.codex.memory-wuxian-cloud-sync"
WINDOWS_TASK_NAME = "MemoryWuxianCloudSync"
INTERVAL_SECONDS = 300
TASK_XML_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
Runner = Callable[..., subprocess.CompletedProcess]


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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


def macos_plist(
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
) -> dict:
    log_dir = archive_root / "federation"
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": cloud_command(python_executable, skill_root, archive_root),
        "RunAtLoad": True,
        "StartInterval": INTERVAL_SECONDS,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "cloud-sync.stdout.log"),
        "StandardErrorPath": str(log_dir / "cloud-sync.stderr.log"),
    }


def windows_user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    return f"{domain}\\{username}" if domain else username


def windows_system_executable(relative_path: str) -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return system_root / relative_path


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


def windows_task_xml(
    wrapper_path: Path,
    *,
    user_id: str,
    start_boundary: Optional[str] = None,
) -> bytes:
    ET.register_namespace("", TASK_XML_NAMESPACE)
    task = ET.Element(f"{{{TASK_XML_NAMESPACE}}}Task", {"version": "1.4"})
    registration = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}RegistrationInfo")
    ET.SubElement(registration, f"{{{TASK_XML_NAMESPACE}}}Description").text = (
        "Run one MemoryWuxian cloud-folder synchronization pass every five minutes."
    )

    triggers = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Triggers")
    time_trigger = ET.SubElement(triggers, f"{{{TASK_XML_NAMESPACE}}}TimeTrigger")
    repetition = ET.SubElement(time_trigger, f"{{{TASK_XML_NAMESPACE}}}Repetition")
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}Interval").text = "PT5M"
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}StopAtDurationEnd").text = "false"
    ET.SubElement(time_trigger, f"{{{TASK_XML_NAMESPACE}}}StartBoundary").text = (
        start_boundary
        or dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    )
    ET.SubElement(time_trigger, f"{{{TASK_XML_NAMESPACE}}}Enabled").text = "true"

    principals = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Principals")
    principal = ET.SubElement(
        principals,
        f"{{{TASK_XML_NAMESPACE}}}Principal",
        {"id": "Author"},
    )
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}UserId").text = user_id
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("Enabled", "true"),
        ("Hidden", "true"),
        ("ExecutionTimeLimit", "PT10M"),
        ("Priority", "7"),
    ):
        ET.SubElement(settings, f"{{{TASK_XML_NAMESPACE}}}{name}").text = value

    actions = ET.SubElement(
        task,
        f"{{{TASK_XML_NAMESPACE}}}Actions",
        {"Context": "Author"},
    )
    exec_action = ET.SubElement(actions, f"{{{TASK_XML_NAMESPACE}}}Exec")
    powershell = windows_system_executable(
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    ET.SubElement(exec_action, f"{{{TASK_XML_NAMESPACE}}}Command").text = str(powershell)
    encoded_wrapper = base64.b64encode(
        f"& {powershell_quote(wrapper_path)}".encode("utf-16le")
    ).decode("ascii")
    ET.SubElement(exec_action, f"{{{TASK_XML_NAMESPACE}}}Arguments").text = (
        "-NoProfile -NonInteractive -WindowStyle Hidden "
        f"-ExecutionPolicy Bypass -EncodedCommand {encoded_wrapper}"
    )
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


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


def launchctl_domain() -> str:
    getuid = getattr(os, "getuid", None)
    uid = int(getuid()) if callable(getuid) else int(os.environ.get("UID", "0"))
    return f"gui/{uid}"


def install_macos(
    archive_root: Path,
    skill_root: Path,
    python_executable: Path,
    *,
    load: bool,
    runner: Runner,
) -> Path:
    output = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"
    log_dir = archive_root / "federation"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = macos_plist(python_executable, skill_root, archive_root)
    atomic_write_bytes(output, plistlib.dumps(payload, sort_keys=True))
    if load:
        domain = launchctl_domain()
        runner(
            ["/bin/launchctl", "bootout", domain, str(output)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        runner(["/bin/launchctl", "bootstrap", domain, str(output)], check=True)
    return output


def uninstall_macos(*, runner: Runner) -> Path:
    output = Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"
    domain = launchctl_domain()
    runner(
        ["/bin/launchctl", "bootout", domain, str(output)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    output.unlink(missing_ok=True)
    return output


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
    task_xml = windows_task_xml(wrapper_path, user_id=windows_user_id())
    fd, temporary = tempfile.mkstemp(prefix=".memory-wuxian-cloud-sync.", suffix=".xml")
    os.close(fd)
    temporary_path = Path(temporary)
    schtasks = windows_system_executable(r"System32\schtasks.exe")
    try:
        atomic_write_bytes(temporary_path, task_xml)
        runner(
            [
                str(schtasks),
                "/Create",
                "/TN",
                WINDOWS_TASK_NAME,
                "/XML",
                str(temporary_path),
                "/F",
            ],
            check=True,
        )
    finally:
        temporary_path.unlink(missing_ok=True)
    if load:
        runner(
            [str(schtasks), "/Run", "/TN", WINDOWS_TASK_NAME],
            check=True,
        )
    return wrapper_path


def uninstall_windows(*, runner: Runner) -> None:
    schtasks = windows_system_executable(r"System32\schtasks.exe")
    runner(
        [str(schtasks), "/End", "/TN", WINDOWS_TASK_NAME],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runner(
        [str(schtasks), "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
    python_executable = Path(args.python_executable).expanduser().resolve()
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
