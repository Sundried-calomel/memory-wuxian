#!/usr/bin/env python3
"""Fail-closed diagnostics for the packaged Windows installer boundary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence


class DiagnosticError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json(value))
    os.replace(temporary, path)


def path_contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_root(output_root: Path, guard_paths: Sequence[Path]) -> Path:
    root = output_root.resolve()
    for guard in guard_paths:
        protected = guard.resolve()
        if path_contains(protected, root) or path_contains(root, protected):
            raise DiagnosticError(f"diagnostic root overlaps protected path: {guard}")
    return root


def snapshot_path(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.exists():
        return {"path": str(resolved), "kind": "missing"}
    if resolved.is_file():
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "kind": "file",
            "size": stat.st_size,
            "sha256": sha256_file(resolved),
        }
    entries: list[dict[str, Any]] = []
    for candidate in sorted(resolved.rglob("*"), key=lambda item: str(item).casefold()):
        relative = candidate.relative_to(resolved).as_posix()
        if candidate.is_symlink():
            entries.append({"path": relative, "kind": "symlink", "target": os.readlink(candidate)})
        elif candidate.is_file():
            stat = candidate.stat()
            entries.append({"path": relative, "kind": "file", "size": stat.st_size, "sha256": sha256_file(candidate)})
        elif candidate.is_dir():
            entries.append({"path": relative, "kind": "directory"})
    return {"path": str(resolved), "kind": "directory", "entries": entries}


def snapshots(paths: Sequence[Path]) -> list[dict[str, Any]]:
    return [snapshot_path(path) for path in paths]


def snapshot_summary(value: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    payload = canonical_json(value)
    path.write_bytes(payload)
    entry_count = sum(1 + len(item.get("entries", [])) for item in value)
    return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "entry_count": entry_count}


def sandbox_executable() -> Path | None:
    system_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsSandbox.exe"
    return candidate if candidate.is_file() else None


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def prepare_sandbox(args: argparse.Namespace) -> int:
    installer = Path(args.installer).resolve(strict=True)
    expected = args.expected_sha256.lower()
    actual = sha256_file(installer)
    if actual != expected:
        raise DiagnosticError("installer SHA-256 does not match the frozen candidate")
    guards = [Path(item) for item in args.guard_path]
    root = validate_root(Path(args.output_root), guards)
    before = snapshots(guards)
    if root.exists() and any(root.iterdir()):
        raise DiagnosticError("diagnostic output root must be absent or empty")
    input_root = root / "input"
    output_root = root / "output"
    input_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    copied = input_root / installer.name
    shutil.copy2(installer, copied)
    if sha256_file(copied) != expected:
        raise DiagnosticError("copied installer hash drifted")
    script = input_root / "run-exact-installer.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$output = 'C:\\diagnostic-output'\n"
        "$installer = " + powershell_literal("C:\\diagnostic-input\\" + copied.name) + "\n"
        "$stdout = Join-Path $output 'installer.stdout.log'\n"
        "$stderr = Join-Path $output 'installer.stderr.log'\n"
        "$inno = Join-Path $output 'installer.inno.log'\n"
        "$started = (Get-Date).ToString('o')\n"
        "$process = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG=' + $inno)) -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr\n"
        "$result = [ordered]@{schema_version=1; started_at=$started; completed_at=(Get-Date).ToString('o'); installer_sha256=" + powershell_literal(expected) + "; exit_code=$process.ExitCode; inno_log=$inno; stdout_log=$stdout; stderr_log=$stderr}\n"
        "$result | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath (Join-Path $output 'complete-chain-result.json') -Encoding UTF8\n"
        "shutdown.exe /s /t 0\n",
        encoding="utf-8-sig",
        newline="\r\n",
    )
    config = root / "MemoryWuxianInstallerDiagnostic.wsb"
    config.write_text(
        "<Configuration>\n"
        "  <Networking>Disable</Networking>\n"
        "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
        "  <MappedFolders>\n"
        "    <MappedFolder><HostFolder>" + str(input_root) + "</HostFolder><SandboxFolder>C:\\diagnostic-input</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>\n"
        "    <MappedFolder><HostFolder>" + str(output_root) + "</HostFolder><SandboxFolder>C:\\diagnostic-output</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>\n"
        "  </MappedFolders>\n"
        "  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\\diagnostic-input\\run-exact-installer.ps1</Command></LogonCommand>\n"
        "</Configuration>\n",
        encoding="utf-8",
        newline="\r\n",
    )
    after = snapshots(guards)
    before_summary = snapshot_summary(before, root / "guard-before.json")
    after_summary = snapshot_summary(after, root / "guard-after.json")
    result = {
        "schema_version": 1,
        "status": "prepared" if sandbox_executable() else "backend-unavailable",
        "backend": "WindowsSandbox",
        "backend_executable": str(sandbox_executable()) if sandbox_executable() else None,
        "installer": str(installer),
        "installer_sha256": actual,
        "bundle": str(config),
        "guard_snapshot_unchanged": before == after,
        "guard_snapshot_before": before_summary,
        "guard_snapshot_after": after_summary,
    }
    atomic_json(root / "prepare-receipt.json", result)
    if before != after:
        raise DiagnosticError("preparation changed a protected path")
    print(canonical_json(result).decode("utf-8"), end="")
    return 0 if sandbox_executable() else 3


def load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("memory_wuxian_diagnostic_broker", path)
    if spec is None or spec.loader is None:
        raise DiagnosticError("broker module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def replay_broker(args: argparse.Namespace) -> int:
    broker_path = Path(args.broker).resolve(strict=True)
    manifest = Path(args.manifest).resolve(strict=True)
    runtime_python = Path(args.runtime_python).resolve(strict=True)
    guards = [Path(item) for item in args.guard_path]
    root = validate_root(Path(args.output_root), guards)
    before = snapshots(guards)
    if root.exists() and any(root.iterdir()):
        raise DiagnosticError("diagnostic output root must be absent or empty")
    root.mkdir(parents=True, exist_ok=True)
    child_result = root / "child-result.json"
    controller = root / "diagnostic-child-controller.py"
    controller.write_text(
        "import argparse, json, os, pathlib, sys, traceback\n"
        "p=argparse.ArgumentParser(); p.add_argument('--execute-manifest', required=True); a=p.parse_args()\n"
        "out=pathlib.Path(__file__).with_name('child-result.json')\n"
        "try:\n"
        " r={'schema_version':1,'status':'passed','python':sys.executable,'pid':os.getpid(),'manifest':str(pathlib.Path(a.execute_manifest).resolve())}\n"
        "except BaseException:\n"
        " r={'schema_version':1,'status':'failed','traceback':traceback.format_exc()}\n"
        "out.write_text(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\\n',encoding='utf-8',newline='\\n')\n",
        encoding="utf-8",
        newline="\n",
    )
    broker = load_module(broker_path)
    sid = broker.current_user_sid()
    ledger = broker.NonceLedger(root / "nonces")
    payload = broker.build_request(manifest, controller, ledger, target_sid=sid)
    request = root / "broker-request.json"
    request.write_bytes(canonical_json(payload))
    completed = subprocess.run(
        [str(runtime_python), str(broker_path), "--dispatch-request", str(request), "--request-sha256", sha256_file(request), "--nonce-root", str(root / "nonces")],
        check=False,
        capture_output=True,
    )
    after = snapshots(guards)
    before_summary = snapshot_summary(before, root / "guard-before.json")
    after_summary = snapshot_summary(after, root / "guard-after.json")
    receipt = {
        "schema_version": 1,
        "status": "passed" if completed.returncode == 0 and child_result.is_file() and before == after else "failed",
        "broker_exit_code": completed.returncode,
        "broker_stdout_utf8": completed.stdout.decode("utf-8", errors="replace"),
        "broker_stderr_utf8": completed.stderr.decode("utf-8", errors="replace"),
        "request_sha256": sha256_file(request),
        "manifest_sha256": sha256_file(manifest),
        "controller_sha256": sha256_file(controller),
        "child_receipt": str(child_result),
        "child_receipt_exists": child_result.is_file(),
        "child_receipt_sha256": sha256_file(child_result) if child_result.is_file() else None,
        "guard_snapshot_unchanged": before == after,
        "guard_snapshot_before": before_summary,
        "guard_snapshot_after": after_summary,
    }
    atomic_json(root / "broker-replay-receipt.json", receipt)
    print(canonical_json(receipt).decode("utf-8"), end="")
    return 0 if receipt["status"] == "passed" else 4


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    sandbox = commands.add_parser("prepare-sandbox")
    sandbox.add_argument("--installer", required=True)
    sandbox.add_argument("--expected-sha256", required=True)
    sandbox.add_argument("--output-root", required=True)
    sandbox.add_argument("--guard-path", action="append", default=[])
    sandbox.set_defaults(func=prepare_sandbox)
    replay = commands.add_parser("replay-broker")
    replay.add_argument("--broker", required=True)
    replay.add_argument("--manifest", required=True)
    replay.add_argument("--runtime-python", required=True)
    replay.add_argument("--output-root", required=True)
    replay.add_argument("--guard-path", action="append", default=[])
    replay.set_defaults(func=replay_broker)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return int(args.func(args))
    except (DiagnosticError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"diagnostic-error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
