#!/usr/bin/env python3
"""Shared operating-system scheduler mechanics for explicit job policies."""

from __future__ import annotations

import datetime as dt
import getpass
import os
import plistlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence
from xml.etree import ElementTree as ET


TASK_XML_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
Runner = Callable[..., subprocess.CompletedProcess]
ByteWriter = Callable[[Path, bytes], None]


@dataclass(frozen=True)
class MacOSJobSpec:
    label: str
    command: tuple[str, ...]
    interval_seconds: int
    run_at_load: bool
    keep_alive: bool
    process_type: str
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True)
class WindowsTaskSpec:
    task_name: str
    description: str
    command: Path
    arguments: tuple[str, ...]
    interval: str
    execution_limit: str
    priority: Optional[str]
    allow_hard_terminate: Optional[bool]
    multiple_instances: str
    disallow_start_on_batteries: bool
    stop_on_batteries: bool
    start_when_available: bool
    network_required: bool
    hidden: bool
    logon_type: str
    run_level: str


def windows_user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip() or getpass.getuser()
    return f"{domain}\\{username}" if domain else username


def windows_system_executable(relative_path: str) -> Path:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return system_root / relative_path


def launchctl_domain() -> str:
    getuid = getattr(os, "getuid", None)
    uid = int(getuid()) if callable(getuid) else int(os.environ.get("UID", "0"))
    return f"gui/{uid}"


def render_macos_plist(spec: MacOSJobSpec) -> dict[str, object]:
    return {
        "Label": spec.label,
        "ProgramArguments": list(spec.command),
        "RunAtLoad": spec.run_at_load,
        "StartInterval": spec.interval_seconds,
        "KeepAlive": spec.keep_alive,
        "ProcessType": spec.process_type,
        "StandardOutPath": str(spec.stdout_path),
        "StandardErrorPath": str(spec.stderr_path),
    }


def render_windows_task_xml(
    spec: WindowsTaskSpec,
    *,
    user_id: str,
    start_boundary: Optional[str] = None,
) -> bytes:
    ET.register_namespace("", TASK_XML_NAMESPACE)
    task = ET.Element(f"{{{TASK_XML_NAMESPACE}}}Task", {"version": "1.4"})
    registration = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}RegistrationInfo")
    ET.SubElement(registration, f"{{{TASK_XML_NAMESPACE}}}Description").text = (
        spec.description
    )

    triggers = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Triggers")
    trigger = ET.SubElement(triggers, f"{{{TASK_XML_NAMESPACE}}}TimeTrigger")
    repetition = ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}Repetition")
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}Interval").text = spec.interval
    ET.SubElement(repetition, f"{{{TASK_XML_NAMESPACE}}}StopAtDurationEnd").text = "false"
    ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}StartBoundary").text = (
        start_boundary
        or dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    )
    ET.SubElement(trigger, f"{{{TASK_XML_NAMESPACE}}}Enabled").text = "true"

    principals = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Principals")
    principal = ET.SubElement(
        principals,
        f"{{{TASK_XML_NAMESPACE}}}Principal",
        {"id": "Author"},
    )
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}UserId").text = user_id
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}LogonType").text = spec.logon_type
    ET.SubElement(principal, f"{{{TASK_XML_NAMESPACE}}}RunLevel").text = spec.run_level

    settings = ET.SubElement(task, f"{{{TASK_XML_NAMESPACE}}}Settings")
    values: list[tuple[str, str]] = [
        ("MultipleInstancesPolicy", spec.multiple_instances),
        ("DisallowStartIfOnBatteries", str(spec.disallow_start_on_batteries).lower()),
        ("StopIfGoingOnBatteries", str(spec.stop_on_batteries).lower()),
    ]
    if spec.allow_hard_terminate is not None:
        values.append(("AllowHardTerminate", str(spec.allow_hard_terminate).lower()))
    values.extend(
        [
            ("StartWhenAvailable", str(spec.start_when_available).lower()),
            ("RunOnlyIfNetworkAvailable", str(spec.network_required).lower()),
            ("Enabled", "true"),
            ("Hidden", str(spec.hidden).lower()),
            ("ExecutionTimeLimit", spec.execution_limit),
        ]
    )
    if spec.priority is not None:
        values.append(("Priority", spec.priority))
    for name, value in values:
        ET.SubElement(settings, f"{{{TASK_XML_NAMESPACE}}}{name}").text = value

    actions = ET.SubElement(
        task,
        f"{{{TASK_XML_NAMESPACE}}}Actions",
        {"Context": "Author"},
    )
    action = ET.SubElement(actions, f"{{{TASK_XML_NAMESPACE}}}Exec")
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Command").text = str(spec.command)
    ET.SubElement(action, f"{{{TASK_XML_NAMESPACE}}}Arguments").text = (
        subprocess.list2cmdline(list(spec.arguments))
    )
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def install_macos_job(
    spec: MacOSJobSpec,
    *,
    load: bool,
    runner: Runner,
    write_bytes: ByteWriter,
    domain: Optional[str],
    bootout_kwargs: Optional[Mapping[str, object]] = None,
) -> Path:
    output = Path.home() / "Library" / "LaunchAgents" / f"{spec.label}.plist"
    write_bytes(output, plistlib.dumps(render_macos_plist(spec), sort_keys=True))
    if load:
        if domain is None:
            raise ValueError("launchctl domain is required when loading a job")
        runner(
            ["/bin/launchctl", "bootout", domain, str(output)],
            check=False,
            **dict(bootout_kwargs or {}),
        )
        runner(["/bin/launchctl", "bootstrap", domain, str(output)], check=True)
    return output


def uninstall_macos_job(
    label: str,
    *,
    runner: Runner,
    domain: str,
    bootout_kwargs: Optional[Mapping[str, object]] = None,
) -> Path:
    output = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    runner(
        ["/bin/launchctl", "bootout", domain, str(output)],
        check=False,
        **dict(bootout_kwargs or {}),
    )
    output.unlink(missing_ok=True)
    return output


def install_windows_task(
    spec: WindowsTaskSpec,
    payload: bytes,
    *,
    temporary_prefix: str,
    schtasks: Path | str,
    load: bool,
    runner: Runner,
    write_bytes: ByteWriter,
    runner_kwargs: Optional[Mapping[str, object]] = None,
) -> None:
    fd, temporary = tempfile.mkstemp(prefix=temporary_prefix, suffix=".xml")
    os.close(fd)
    temporary_path = Path(temporary)
    kwargs = dict(runner_kwargs or {})
    try:
        write_bytes(temporary_path, payload)
        runner(
            [
                str(schtasks),
                "/Create",
                "/TN",
                spec.task_name,
                "/XML",
                str(temporary_path),
                "/F",
            ],
            check=True,
            **kwargs,
        )
    finally:
        temporary_path.unlink(missing_ok=True)
    if load:
        runner(
            [str(schtasks), "/Run", "/TN", spec.task_name],
            check=True,
            **kwargs,
        )


def uninstall_windows_task(
    task_name: str,
    *,
    schtasks: Path | str,
    runner: Runner,
    end_first: bool,
    runner_kwargs: Optional[Mapping[str, object]] = None,
) -> None:
    kwargs = dict(runner_kwargs or {})
    if end_first:
        runner(
            [str(schtasks), "/End", "/TN", task_name],
            check=False,
            **kwargs,
        )
    runner(
        [str(schtasks), "/Delete", "/TN", task_name, "/F"],
        check=False,
        **kwargs,
    )
