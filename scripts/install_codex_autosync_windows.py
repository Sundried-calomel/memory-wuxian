#!/usr/bin/env python3
"""Install the Windows collector through a rollback-safe Task Scheduler transaction."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

try:
    from platform_process import no_window_kwargs
    from collector_activation import resolve_activation_since
except ModuleNotFoundError:
    from scripts.platform_process import no_window_kwargs
    from scripts.collector_activation import resolve_activation_since


DEFAULT_TASK_NAME = "MemoryWuxianCodexSync"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MemoryWuxianCodexSync"
JOURNAL_FORMAT = "memory-wuxian-windows-install-journal-v1"
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
Runner = Callable[..., subprocess.CompletedProcess[Any]]


def active_root_pointer() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "memory-wuxian-active-root.txt"


def default_codex_cli() -> str:
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    bundled = Path.home() / ".codex/.sandbox-bin/codex.exe"
    return str(bundled if bundled.exists() else Path("codex.exe"))


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


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.replace("\n", "\r\n").encode("utf-8"))


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


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


def command_generation(command: Sequence[str]) -> str:
    digest = hashlib.sha256(canonical_json(list(command))).hexdigest()
    return f"windows-task-{digest}"


def task_xml(command: Sequence[str]) -> bytes:
    ET.register_namespace("", TASK_NAMESPACE)
    task = ET.Element(f"{{{TASK_NAMESPACE}}}Task", {"version": "1.4"})
    triggers = ET.SubElement(task, f"{{{TASK_NAMESPACE}}}Triggers")
    logon = ET.SubElement(triggers, f"{{{TASK_NAMESPACE}}}LogonTrigger")
    ET.SubElement(logon, f"{{{TASK_NAMESPACE}}}Enabled").text = "true"
    principals = ET.SubElement(task, f"{{{TASK_NAMESPACE}}}Principals")
    principal = ET.SubElement(principals, f"{{{TASK_NAMESPACE}}}Principal", {"id": "Author"})
    ET.SubElement(principal, f"{{{TASK_NAMESPACE}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{TASK_NAMESPACE}}}RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(task, f"{{{TASK_NAMESPACE}}}Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("Hidden", "true"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        ET.SubElement(settings, f"{{{TASK_NAMESPACE}}}{name}").text = value
    restart = ET.SubElement(settings, f"{{{TASK_NAMESPACE}}}RestartOnFailure")
    ET.SubElement(restart, f"{{{TASK_NAMESPACE}}}Interval").text = "PT30S"
    ET.SubElement(restart, f"{{{TASK_NAMESPACE}}}Count").text = "5"
    actions = ET.SubElement(task, f"{{{TASK_NAMESPACE}}}Actions", {"Context": "Author"})
    execute = ET.SubElement(actions, f"{{{TASK_NAMESPACE}}}Exec")
    ET.SubElement(execute, f"{{{TASK_NAMESPACE}}}Command").text = str(command[0])
    ET.SubElement(execute, f"{{{TASK_NAMESPACE}}}Arguments").text = subprocess.list2cmdline(list(command[1:]))
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _xml_value(root: ET.Element, name: str) -> str:
    value = root.find(f".//{{{TASK_NAMESPACE}}}{name}")
    return "" if value is None or value.text is None else value.text


def inspect_task_xml(payload: bytes) -> dict[str, Any]:
    root = ET.fromstring(payload)
    return {
        "command": _xml_value(root, "Command"),
        "arguments": _xml_value(root, "Arguments"),
        "hidden": _xml_value(root, "Hidden").lower() == "true",
        "multiple_instances": _xml_value(root, "MultipleInstancesPolicy"),
        "restart_interval": _xml_value(root, "Interval"),
        "restart_count": _xml_value(root, "Count"),
    }


def verify_task_definition(payload: bytes, command: Sequence[str]) -> dict[str, Any]:
    actual = inspect_task_xml(payload)
    expected = inspect_task_xml(task_xml(command))
    if actual != expected:
        raise RuntimeError("scheduled task does not match the intended command, archive root, or policy")
    if actual["command"].lower().endswith(("powershell.exe", "powershell")):
        raise RuntimeError("collector task must launch the native executable directly")
    return actual


def _completed_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _decode_output(value: Any) -> str:
    payload = _completed_bytes(value)
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", errors="replace")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return payload.decode("utf-8", errors="replace")


def query_task_xml(task_name: str, runner: Runner = subprocess.run) -> bytes | None:
    result = runner(
        ["schtasks.exe", "/Query", "/TN", task_name, "/XML", "ONE"],
        check=False,
        capture_output=True,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        return None
    if isinstance(result.stdout, str):
        return result.stdout.encode("utf-8")
    return _completed_bytes(result.stdout)


def _task_command(arguments: list[str], runner: Runner, *, check: bool) -> Any:
    return runner(arguments, check=check, capture_output=not check, **no_window_kwargs())


def register_task(task_name: str, payload: bytes, runner: Runner = subprocess.run) -> None:
    runtime = Path(tempfile.mkdtemp(prefix="memory-wuxian-task-"))
    definition = runtime / "collector-task.xml"
    try:
        atomic_write_bytes(definition, payload)
        _task_command(["schtasks.exe", "/Create", "/TN", task_name, "/XML", str(definition), "/F"], runner, check=True)
    finally:
        shutil.rmtree(runtime, ignore_errors=True)


def remove_task(task_name: str, runner: Runner = subprocess.run) -> None:
    for arguments in (
        ["schtasks.exe", "/End", "/TN", task_name],
        ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
    ):
        _task_command(arguments, runner, check=False)


def remove_run_key(runner: Runner = subprocess.run) -> None:
    runner(
        ["reg.exe", "DELETE", RUN_KEY, "/V", RUN_VALUE, "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **no_window_kwargs(),
    )


def query_run_key(runner: Runner = subprocess.run) -> dict[str, str] | None:
    result = runner(
        ["reg.exe", "QUERY", RUN_KEY, "/V", RUN_VALUE],
        check=False,
        capture_output=True,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        return None
    text = _decode_output(result.stdout)
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].casefold() == RUN_VALUE.casefold() and parts[1].startswith("REG_"):
            return {"type": parts[1], "data": parts[2]}
    return None


def restore_run_key(snapshot: dict[str, str] | None, runner: Runner = subprocess.run) -> None:
    if snapshot is None:
        remove_run_key(runner)
        return
    runner(
        [
            "reg.exe", "ADD", RUN_KEY, "/V", RUN_VALUE,
            "/T", snapshot["type"], "/D", snapshot["data"], "/F",
        ],
        check=True,
        **no_window_kwargs(),
    )


def probe_candidate(command: Sequence[str], runner: Runner = subprocess.run) -> None:
    result = runner(
        [str(command[0]), "--help"],
        check=False,
        capture_output=True,
        timeout=30,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        detail = _decode_output(result.stderr).strip()
        raise RuntimeError(detail or "candidate collector executable probe failed")


def _read_optional(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _encoded(payload: bytes | None) -> str | None:
    return None if payload is None else base64.b64encode(payload).decode("ascii")


def _journal_phase(path: Path, journal: dict[str, Any], phase: str, **details: Any) -> None:
    event = {"phase": phase, "at": datetime.now(timezone.utc).isoformat()}
    event.update(details)
    journal["phase"] = phase
    journal.setdefault("history", []).append(event)
    atomic_write_bytes(path, canonical_json(journal))


def _move_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError(f"transaction destination already exists: {destination}")
    source.replace(destination)


def _restore_generation(journal: dict[str, Any]) -> None:
    generation = journal.get("generation")
    if not generation or not generation.get("switched"):
        return
    skill_root = Path(generation["skill_root"])
    previous = Path(generation["previous_root"])
    failed = Path(generation["failed_root"])
    if skill_root.exists():
        _move_directory(skill_root, failed)
    if previous.exists():
        _move_directory(previous, skill_root)
    generation["switched"] = False


def _restore_transaction(journal: dict[str, Any], runner: Runner) -> None:
    task_name = str(journal["task_name"])
    rollback = journal["rollback"]
    remove_task(task_name, runner)
    # The restored task must never start against the failed generation.
    _restore_generation(journal)
    old_task = rollback.get("task_xml")
    if old_task is not None:
        register_task(task_name, base64.b64decode(old_task), runner)
        _task_command(["schtasks.exe", "/Run", "/TN", task_name], runner, check=False)
    _restore_file(Path(journal["command_manifest"]), _decoded(rollback.get("command_manifest")))
    _restore_file(Path(journal["active_root_pointer"]), _decoded(rollback.get("active_root_pointer")))
    _restore_file(Path(journal["lifecycle_manifest"]), _decoded(rollback.get("lifecycle_manifest")))
    restore_run_key(rollback.get("run_key"), runner)


def _decoded(value: str | None) -> bytes | None:
    return None if value is None else base64.b64decode(value)


def commit_transaction(journal_path: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("phase") == "commit":
        return journal
    if journal.get("phase") != "verify":
        raise RuntimeError(f"cannot commit transaction in phase {journal.get('phase')!r}")
    remove_run_key(runner)
    _journal_phase(journal_path, journal, "commit")
    return journal


def rollback_transaction(
    journal_path: Path,
    *,
    runner: Runner = subprocess.run,
    error: str = "requested rollback",
) -> dict[str, Any]:
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("phase") == "rollback":
        return journal
    if journal.get("phase") == "commit":
        raise RuntimeError("committed transaction cannot be rolled back by the installer")
    _restore_transaction(journal, runner)
    _journal_phase(journal_path, journal, "rollback", error=error)
    return journal


def _parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def wait_for_watermark_progress(
    archive_root: Path,
    *,
    previous_pid: int | None,
    started_at: datetime,
    timeout_seconds: float = 120,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    telemetry_path = archive_root / "imports" / "codex" / "collector-telemetry.json"
    deadline = time.monotonic() + timeout_seconds
    last_error = "collector telemetry did not appear"
    while time.monotonic() < deadline:
        try:
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
            pid = int(telemetry["pid"])
            updated = _parse_time(telemetry["updated_at"])
            source = telemetry.get("source_watermark")
            archive = telemetry.get("archive_watermark")
            if int(telemetry.get("format_version", 0)) < 2:
                last_error = "collector telemetry format is not readiness-capable"
            elif telemetry.get("phase") != "ready":
                last_error = "collector has not reached the ready phase"
            elif previous_pid is not None and pid == previous_pid:
                last_error = "collector PID did not change"
            elif updated < started_at:
                last_error = "collector telemetry did not advance after activation"
            elif not telemetry.get("ready"):
                last_error = "collector has not reached watcher-ready state"
            elif source != archive:
                last_error = "archive watermark has not reached the observed source watermark"
            else:
                return telemetry
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            last_error = str(error)
        sleep(0.25)
    raise RuntimeError(last_error)


def _restore_file(path: Path, payload: bytes | None) -> None:
    if payload is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_bytes(path, payload)


def install_transaction(
    *,
    task_name: str,
    command: Sequence[str],
    archive_root: Path,
    command_manifest: Path,
    pointer: Path,
    journal_path: Path,
    runner: Runner = subprocess.run,
    readiness_probe: Callable[..., dict[str, Any]] = wait_for_watermark_progress,
    candidate_probe_command: Sequence[str] | None = None,
    prepare_mutation: Callable[[dict[str, Any]], None] | None = None,
    defer_commit: bool = False,
    journal_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intended_xml = task_xml(command)
    old_task_xml = query_task_xml(task_name, runner)
    old_manifest = _read_optional(command_manifest)
    old_pointer = _read_optional(pointer)
    lifecycle_manifest = archive_root / "imports" / "codex" / "collector-lifecycle.json"
    old_lifecycle = _read_optional(lifecycle_manifest)
    old_run_key = query_run_key(runner)
    previous_pid = None
    telemetry_path = archive_root / "imports" / "codex" / "collector-telemetry.json"
    try:
        previous_pid = int(json.loads(telemetry_path.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    journal: dict[str, Any] = {
        "format": JOURNAL_FORMAT,
        "format_version": 1,
        "generation_id": command_generation(command),
        "task_name": task_name,
        "command": list(command),
        "archive_root": str(archive_root),
        "command_manifest": str(command_manifest),
        "active_root_pointer": str(pointer),
        "lifecycle_manifest": str(lifecycle_manifest),
        "rollback": {
            "task_xml": _encoded(old_task_xml),
            "command_manifest": _encoded(old_manifest),
            "active_root_pointer": _encoded(old_pointer),
            "lifecycle_manifest": _encoded(old_lifecycle),
            "run_key": old_run_key,
        },
        "history": [],
    }
    if journal_extra:
        journal.update(journal_extra)
    _journal_phase(journal_path, journal, "prepare")
    mutated = False
    try:
        verify_task_definition(intended_xml, command)
        probe_candidate(candidate_probe_command or command, runner)
        _journal_phase(journal_path, journal, "verify", candidate_runnable=True)
        started_at = datetime.now(timezone.utc)
        mutated = True
        if prepare_mutation is not None:
            prepare_mutation(journal)
            atomic_write_bytes(journal_path, canonical_json(journal))
        remove_run_key(runner)
        remove_task(task_name, runner)
        register_task(task_name, intended_xml, runner)
        actual_xml = query_task_xml(task_name, runner)
        if actual_xml is None:
            raise RuntimeError("scheduled task disappeared after registration")
        actual_task = verify_task_definition(actual_xml, command)
        manifest = {
            "format_version": 2,
            "generation_id": journal["generation_id"],
            "command": list(command),
            "console_window": False,
            "startup_owner": "task-scheduler",
            "task_name": task_name,
        }
        atomic_write_bytes(command_manifest, canonical_json(manifest))
        atomic_write_text(pointer, f"{archive_root}\n")
        lifecycle = {
            "format": "memory-wuxian-collector-lifecycle-v1",
            "generation": journal["generation_id"],
            "archive_root": str(archive_root),
            "expected_command": list(command),
            "startup_owners": [
                {
                    "owner_id": f"task:{task_name}",
                    "kind": "windows-task",
                    "generation": journal["generation_id"],
                    "archive_root": str(archive_root),
                    "command": list(command),
                    "pid_identity": "required",
                }
            ],
        }
        atomic_write_bytes(lifecycle_manifest, canonical_json(lifecycle))
        _task_command(["schtasks.exe", "/Run", "/TN", task_name], runner, check=True)
        telemetry = readiness_probe(archive_root, previous_pid=previous_pid, started_at=started_at)
        journal["verification"] = {
            "scheduled_task": actual_task,
            "pid": int(telemetry["pid"]),
            "source_watermark": telemetry.get("source_watermark"),
            "archive_watermark": telemetry.get("archive_watermark"),
        }
        atomic_write_bytes(journal_path, canonical_json(journal))
        if not defer_commit:
            remove_run_key(runner)
            _journal_phase(journal_path, journal, "commit")
        return journal
    except BaseException as error:
        if mutated:
            _restore_transaction(journal, runner)
        _journal_phase(journal_path, journal, "rollback", error=str(error))
        raise


def install_generation_transaction(
    *,
    candidate_root: Path,
    skill_root: Path,
    runtime_directory: Path,
    task_name: str,
    command: Sequence[str],
    candidate_command: Sequence[str],
    archive_root: Path,
    command_manifest: Path,
    pointer: Path,
    runner: Runner = subprocess.run,
    readiness_probe: Callable[..., dict[str, Any]] = wait_for_watermark_progress,
    defer_commit: bool = False,
) -> tuple[dict[str, Any], Path]:
    transaction_root = runtime_directory / "transactions" / uuid.uuid4().hex
    staged_root = transaction_root / "candidate"
    previous_root = transaction_root / "previous-generation"
    failed_root = transaction_root / "failed-generation"
    transaction_root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(candidate_root, staged_root)
    if skill_root.exists() and (skill_root / "config.yaml").exists():
        shutil.copy2(skill_root / "config.yaml", staged_root / "config.yaml")
    for required in (staged_root / "SKILL.md", staged_root / "config.yaml", staged_root / "bin" / "memory-wuxian-collector.exe"):
        if not required.exists():
            raise RuntimeError(f"candidate generation is incomplete: {required}")

    staged_command = list(candidate_command)
    staged_command[0] = str(staged_root / "bin" / "memory-wuxian-collector.exe")
    config_index = staged_command.index("--config") + 1
    staged_command[config_index] = str(staged_root / "config.yaml")
    journal_path = transaction_root / "journal.json"

    def switch_generation(journal: dict[str, Any]) -> None:
        # Record the rollback obligation before either rename. If the second
        # rename fails, the first one still has a durable restoration path.
        journal["generation"]["switched"] = True
        if skill_root.exists():
            _move_directory(skill_root, previous_root)
        _move_directory(staged_root, skill_root)

    journal = install_transaction(
        task_name=task_name,
        command=command,
        archive_root=archive_root,
        command_manifest=command_manifest,
        pointer=pointer,
        journal_path=journal_path,
        runner=runner,
        readiness_probe=readiness_probe,
        candidate_probe_command=staged_command,
        prepare_mutation=switch_generation,
        defer_commit=defer_commit,
        journal_extra={
            "generation": {
                "skill_root": str(skill_root),
                "previous_root": str(previous_root),
                "failed_root": str(failed_root),
                "switched": False,
            }
        },
    )
    return journal, journal_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the persistent Windows Memory Wuxian collector task")
    parser.add_argument("--archive-root")
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--candidate-root")
    parser.add_argument("--sessions-root", default="~/.codex/sessions")
    parser.add_argument("--collector-executable")
    parser.add_argument("--python-executable", default=shutil.which("python") or "python.exe")
    parser.add_argument("--codex-cli", default=default_codex_cli())
    parser.add_argument("--since")
    parser.add_argument("--debounce-ms", type=int, default=400)
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME)
    parser.add_argument("--backend", choices=("auto", "task"), default="auto")
    parser.add_argument(
        "--runtime-directory",
        default=str(Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "MemoryWuxian"),
    )
    parser.add_argument("--output")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--defer-commit", action="store_true")
    parser.add_argument("--commit-journal")
    parser.add_argument("--rollback-journal")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if os.name != "nt" and (args.load or args.uninstall or args.commit_journal or args.rollback_journal):
        raise SystemExit("Windows task registration is only available on Windows")
    if args.debounce_ms < 100:
        raise SystemExit("--debounce-ms must be at least 100")
    runtime_directory = Path(args.runtime_directory).expanduser().resolve()
    if args.commit_journal:
        journal = commit_transaction(Path(args.commit_journal).expanduser().resolve())
        print(f"install-journal:{Path(args.commit_journal).expanduser().resolve()}")
        print(f"transaction:{journal['phase']}")
        return 0
    if args.rollback_journal:
        journal = rollback_transaction(Path(args.rollback_journal).expanduser().resolve())
        print(f"install-journal:{Path(args.rollback_journal).expanduser().resolve()}")
        print(f"transaction:{journal['phase']}")
        return 0
    if not args.archive_root:
        raise SystemExit("--archive-root is required")
    if args.uninstall:
        try:
            from install_maintenance_supervisor import uninstall as uninstall_maintenance
        except ModuleNotFoundError:
            from scripts.install_maintenance_supervisor import uninstall as uninstall_maintenance
        uninstall_maintenance(platform_name="win32", runner=subprocess.run)
        remove_task(args.task_name)
        remove_run_key()
        for stale in (
            Path(args.archive_root).expanduser().resolve() / "imports/codex/run-collector-hidden.vbs",
            runtime_directory / "run-collector-hidden.vbs",
        ):
            stale.unlink(missing_ok=True)
        return 0

    skill_root = Path(args.skill_root).expanduser().resolve()
    candidate_root = Path(args.candidate_root).expanduser().resolve() if args.candidate_root else None
    archive_root = Path(args.archive_root).expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve()
    collector = Path(args.collector_executable or skill_root / "bin" / "memory-wuxian-collector.exe").expanduser().resolve()
    python_executable = Path(args.python_executable).expanduser().resolve()
    codex_cli = Path(args.codex_cli).expanduser().resolve()
    config = skill_root / "config.yaml"
    validation_config = candidate_root / "config.yaml" if candidate_root else config
    validation_collector = candidate_root / "bin" / "memory-wuxian-collector.exe" if candidate_root else collector
    for label, path in {
        "skill config": validation_config,
        "sessions root": sessions_root,
        "collector": validation_collector,
        "Python": python_executable,
        "Codex CLI": codex_cli,
    }.items():
        if not path.exists():
            raise SystemExit(f"{label} does not exist: {path}")

    archive_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = archive_root / "imports" / "codex"
    output = Path(args.output).expanduser().resolve() if args.output else runtime_dir / "collector-command.json"
    since = resolve_activation_since(archive_root, args.since)
    command = collector_command(
        collector, archive_root, config, sessions_root, python_executable,
        codex_cli, since, args.debounce_ms,
    )
    preview = {
        "format_version": 2,
        "generation_id": command_generation(command),
        "command": command,
        "console_window": False,
        "startup_owner": "task-scheduler",
        "task_name": args.task_name,
    }
    if args.load:
        journal_path = runtime_directory / "install-journal.json"
        if candidate_root:
            candidate_command = collector_command(
                candidate_root / "bin" / "memory-wuxian-collector.exe",
                archive_root,
                candidate_root / "config.yaml",
                sessions_root,
                python_executable,
                codex_cli,
                since,
                args.debounce_ms,
            )
            journal, journal_path = install_generation_transaction(
                candidate_root=candidate_root,
                skill_root=skill_root,
                runtime_directory=runtime_directory,
                task_name=args.task_name,
                command=command,
                candidate_command=candidate_command,
                archive_root=archive_root,
                command_manifest=output,
                pointer=active_root_pointer(),
                defer_commit=True,
            )
        else:
            journal = install_transaction(
                task_name=args.task_name,
                command=command,
                archive_root=archive_root,
                command_manifest=output,
                pointer=active_root_pointer(),
                journal_path=journal_path,
                defer_commit=True,
            )
        for stale in (
            runtime_dir / "run-collector.cmd",
            runtime_dir / "run-collector-hidden.vbs",
            runtime_directory / "run-collector-hidden.vbs",
        ):
            stale.unlink(missing_ok=True)
        try:
            from install_maintenance_supervisor import install as install_maintenance
        except ModuleNotFoundError:
            from scripts.install_maintenance_supervisor import install as install_maintenance
        try:
            install_maintenance(
                archive_root,
                skill_root,
                python_executable,
                platform_name="win32",
                load=True,
                runner=subprocess.run,
            )
            if not args.defer_commit:
                journal = commit_transaction(journal_path)
        except BaseException as error:
            rollback_transaction(journal_path, error=str(error))
            raise
        print(f"task:{args.task_name}:{journal['generation_id']}")
        print(f"install-journal:{journal_path}")
    else:
        atomic_write_bytes(output, canonical_json(preview))
        atomic_write_text(active_root_pointer(), f"{archive_root}\n")
    print(f"command-manifest:{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
