#!/usr/bin/env python3
"""Run real Windows installer rehearsals without touching production resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence
import uuid

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from install_windows_transaction import (
    ProductInstallAdapter,
    WindowsInstallResourceNamespace,
    _tree_sha256,
    _verify_manifest_identity,
    build_manifest,
    default_installer_resource_root,
)
from platform_process import no_window_kwargs
from platform_scheduler import (
    inspect_windows_task_xml,
    query_windows_task_xml,
    uninstall_windows_task,
    windows_system_executable,
)
from windows_install_manifest import file_sha256, write_manifest
from windows_installer_transaction import InstallerExit, WindowsInstallerTransaction


PRODUCTION_TASKS = (
    "MemoryWuxianCodexSync",
    "MemoryWuxianMaintenance",
    "MemoryWuxianAutoUpdate",
)
PRODUCTION_RUN_VALUES = (
    "MemoryWuxianCodexSync",
    "MemoryWuxianAutoUpdate",
)
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_namespace(work_root: Path, run_id: str) -> WindowsInstallResourceNamespace:
    prefix = f"MemoryWuxianRehearsal-{run_id}"
    return WindowsInstallResourceNamespace(
        collector_task_name=f"{prefix}-Collector",
        collector_run_value=f"{prefix}-Collector",
        maintenance_task_name=f"{prefix}-Maintenance",
        auto_update_task_name=f"{prefix}-AutoUpdate",
        auto_update_run_value=f"{prefix}-AutoUpdate",
        dashboard_desktop=(work_root / "Desktop").resolve(),
        dashboard_shortcut_name=f"{prefix}.lnk",
    )


def _task_snapshot(name: str) -> dict[str, Any]:
    payload = query_windows_task_xml(
        name,
        schtasks=windows_system_executable("System32/schtasks.exe"),
        runner=subprocess.run,
        runner_kwargs=no_window_kwargs(),
    )
    if payload is None:
        return {"exists": False, "semantic_sha256": None}
    semantic = canonical_bytes(inspect_windows_task_xml(payload))
    return {"exists": True, "semantic_sha256": sha256_bytes(semantic)}


def _registry_snapshot(value_name: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(windows_system_executable("System32/reg.exe")), "QUERY", RUN_KEY, "/V", value_name],
        check=False,
        capture_output=True,
        **no_window_kwargs(),
    )
    return {
        "exists": result.returncode == 0,
        "stdout_sha256": sha256_bytes(result.stdout) if result.returncode == 0 else None,
    }


def _file_snapshot(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.is_file(), "sha256": file_sha256(path) if path.is_file() else None}


def production_snapshot() -> dict[str, Any]:
    home = Path.home()
    return {
        "tasks": {name: _task_snapshot(name) for name in PRODUCTION_TASKS},
        "run_values": {name: _registry_snapshot(name) for name in PRODUCTION_RUN_VALUES},
        "shortcut": _file_snapshot(home / "Desktop" / "Memory无限状态台.lnk"),
        "archive_pointer": _file_snapshot(home / ".codex" / "memory-wuxian-active-root.txt"),
    }


def namespaced_snapshot(namespace: WindowsInstallResourceNamespace, target: Path, pointer: Path) -> dict[str, Any]:
    shortcut = (namespace.dashboard_desktop or Path()) / namespace.dashboard_shortcut_name
    return {
        "tasks": {
            name: _task_snapshot(name)
            for name in (
                namespace.collector_task_name,
                namespace.maintenance_task_name,
                namespace.auto_update_task_name,
            )
        },
        "run_values": {
            namespace.collector_run_value: _registry_snapshot(namespace.collector_run_value),
            namespace.auto_update_run_value: _registry_snapshot(namespace.auto_update_run_value),
        },
        "shortcut": _file_snapshot(shortcut),
        "pointer": _file_snapshot(pointer),
        "target_tree_sha256": _tree_sha256(target) if target.is_dir() else None,
    }


def _scenario(
    *,
    name: str,
    work_root: Path,
    candidate: Path,
    target: Path,
    archive: Path,
    pointer: Path,
    sessions: Path,
    runtime: Path,
    python: Path,
    bundle_id: str,
    codex_cli: Path,
    namespace: WindowsInstallResourceNamespace,
    failure_point: str | None = None,
) -> dict[str, Any]:
    transaction_root = work_root / "transactions" / name
    transaction_root.mkdir(parents=True, exist_ok=False)
    sessions.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(argparse.Namespace(
        operation="install",
        source_entrypoint="manual",
        candidate_root=str(candidate),
        skill_root=str(target),
        archive_root=str(archive),
        archive_pointer=str(pointer),
        sessions_root=str(sessions),
        python_executable=str(python),
        runtime_bundle_root=str(runtime),
        runtime_bundle_id=bundle_id,
        codex_cli=str(codex_cli),
        journal_path=str(transaction_root / "journal.json"),
        manifest_output=str(transaction_root / "request.json"),
        failure_point=failure_point,
    ))
    manifest_path = transaction_root / "request.json"
    write_manifest(manifest_path, manifest)
    controller = WindowsInstallerTransaction(
        journal_path=transaction_root / "journal.json",
        adapters=[ProductInstallAdapter(
            codex_cli=codex_cli,
            resource_root=default_installer_resource_root(rehearsal=True),
            resources=namespace,
        )],
        identity_validator=_verify_manifest_identity,
    )
    result = controller.execute(manifest, failure_point=failure_point)
    journal = json.loads(result.journal_path.read_text(encoding="utf-8"))
    return {
        "name": name,
        "exit_code": int(result.exit_code),
        "phase": result.phase,
        "transaction_id": result.transaction_id,
        "manifest_sha256": file_sha256(manifest_path),
        "journal": str(result.journal_path),
        "journal_sha256": file_sha256(result.journal_path),
        "mutation_resources": [item["resource_id"] for item in journal["mutations"]],
    }


def _cleanup(namespace: WindowsInstallResourceNamespace) -> dict[str, Any]:
    prefix = "MemoryWuxianRehearsal-"
    task_names = (
        namespace.collector_task_name,
        namespace.maintenance_task_name,
        namespace.auto_update_task_name,
    )
    value_names = (namespace.collector_run_value, namespace.auto_update_run_value)
    if any(not name.startswith(prefix) for name in (*task_names, *value_names)):
        raise RuntimeError("refusing cleanup outside the rehearsal namespace")
    shortcut_name = namespace.dashboard_shortcut_name
    if (
        namespace.dashboard_desktop is None
        or not shortcut_name.startswith(prefix)
        or Path(shortcut_name).name != shortcut_name
        or Path(shortcut_name).suffix.casefold() != ".lnk"
    ):
        raise RuntimeError("refusing shortcut cleanup outside the rehearsal namespace")
    schtasks = windows_system_executable("System32/schtasks.exe")
    for name in task_names:
        uninstall_windows_task(
            name,
            schtasks=schtasks,
            runner=subprocess.run,
            end_first=True,
            runner_kwargs=no_window_kwargs(),
        )
    reg = windows_system_executable("System32/reg.exe")
    for name in value_names:
        subprocess.run(
            [str(reg), "DELETE", RUN_KEY, "/V", name, "/F"],
            check=False,
            capture_output=True,
            **no_window_kwargs(),
        )
    shortcut = namespace.dashboard_desktop / shortcut_name
    shortcut.unlink(missing_ok=True)
    evidence = {
        "tasks": {name: _task_snapshot(name) for name in task_names},
        "run_values": {name: _registry_snapshot(name) for name in value_names},
        "shortcut": _file_snapshot(shortcut),
    }
    if any(item["exists"] for item in evidence["tasks"].values()):
        raise RuntimeError("rehearsal task cleanup verification failed")
    if any(item["exists"] for item in evidence["run_values"].values()):
        raise RuntimeError("rehearsal Run-value cleanup verification failed")
    if evidence["shortcut"]["exists"]:
        raise RuntimeError("rehearsal shortcut cleanup verification failed")
    return evidence


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("real Windows rehearsal requires Windows")
    candidate = Path(args.candidate_root).resolve(strict=True)
    runtime = Path(args.runtime_bundle_root).resolve(strict=True)
    python = Path(args.python_executable).resolve(strict=True)
    codex_cli = Path(args.codex_cli).resolve(strict=True)
    v215_source = Path(args.v215_source).resolve(strict=True)
    work_root = Path(args.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=False)
    if not (candidate / "config.defaults.yaml").is_file():
        raise RuntimeError("packaged candidate is missing config.defaults.yaml")
    bundle = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    namespace = build_namespace(work_root, uuid.uuid4().hex[:12])
    production_before = production_snapshot()
    scenarios: list[dict[str, Any]] = []
    clean_root = work_root / "clean-profile"
    clean_target = clean_root / ".codex" / "skills" / "memory-wuxian"
    clean_archive = clean_root / "Documents" / "MemoryWuxianArchive"
    clean_pointer = clean_root / ".codex" / "memory-wuxian-active-root.txt"
    clean_sessions = clean_root / ".codex" / "sessions"
    cleanup_evidence: dict[str, Any] | None = None
    try:
        scenarios.append(_scenario(
            name="clean-install", work_root=work_root, candidate=candidate,
            target=clean_target, archive=clean_archive, pointer=clean_pointer,
            sessions=clean_sessions, runtime=runtime, python=python,
            bundle_id=bundle["bundle_id"], codex_cli=codex_cli, namespace=namespace,
        ))
        if scenarios[-1]["exit_code"] != int(InstallerExit.SUCCESS):
            raise RuntimeError("clean-install rehearsal failed")
        scenarios.append(_scenario(
            name="repeat-install", work_root=work_root, candidate=candidate,
            target=clean_target, archive=clean_archive, pointer=clean_pointer,
            sessions=clean_sessions, runtime=runtime, python=python,
            bundle_id=bundle["bundle_id"], codex_cli=codex_cli, namespace=namespace,
        ))
        if scenarios[-1]["exit_code"] != int(InstallerExit.SUCCESS):
            raise RuntimeError("repeat-install rehearsal failed")
        rollback_before = namespaced_snapshot(namespace, clean_target, clean_pointer)
        scenarios.append(_scenario(
            name="failure-rollback", work_root=work_root, candidate=candidate,
            target=clean_target, archive=clean_archive, pointer=clean_pointer,
            sessions=clean_sessions, runtime=runtime, python=python,
            bundle_id=bundle["bundle_id"], codex_cli=codex_cli, namespace=namespace,
            failure_point="before-verify:dashboard-shortcut",
        ))
        rollback_after = namespaced_snapshot(namespace, clean_target, clean_pointer)
        if scenarios[-1]["exit_code"] != int(InstallerExit.EFFECT_VERIFICATION_FAILED):
            raise RuntimeError("failure-rollback rehearsal returned the wrong exit code")
        if rollback_before != rollback_after:
            raise RuntimeError("failure-rollback did not restore the exact namespaced baseline")
        upgrade_root = work_root / "v215-profile"
        upgrade_target = upgrade_root / ".codex" / "skills" / "memory-wuxian"
        upgrade_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(v215_source, upgrade_target)
        scenarios.append(_scenario(
            name="v215-upgrade", work_root=work_root, candidate=candidate,
            target=upgrade_target,
            archive=upgrade_root / "Documents" / "MemoryWuxianArchive",
            pointer=upgrade_root / ".codex" / "memory-wuxian-active-root.txt",
            sessions=upgrade_root / ".codex" / "sessions",
            runtime=runtime, python=python, bundle_id=bundle["bundle_id"],
            codex_cli=codex_cli, namespace=namespace,
        ))
        if scenarios[-1]["exit_code"] != int(InstallerExit.SUCCESS):
            raise RuntimeError("v2.15.0 upgrade rehearsal failed")
        migration = json.loads((upgrade_target / "config-migration-receipt.json").read_text(encoding="utf-8"))
    finally:
        cleanup_evidence = _cleanup(namespace)
    production_after = production_snapshot()
    if production_before != production_after:
        raise RuntimeError("production Windows resources changed during namespaced rehearsal")
    receipt = {
        "schema_version": 1,
        "status": "passed",
        "candidate_version": "2.20.0",
        "candidate_tree_sha256": _tree_sha256(candidate),
        "runtime_bundle_id": bundle["bundle_id"],
        "namespace": {
            "collector_task": namespace.collector_task_name,
            "maintenance_task": namespace.maintenance_task_name,
            "auto_update_task": namespace.auto_update_task_name,
            "collector_run_value": namespace.collector_run_value,
            "auto_update_run_value": namespace.auto_update_run_value,
            "desktop": str(namespace.dashboard_desktop),
            "shortcut": namespace.dashboard_shortcut_name,
        },
        "scenarios": scenarios,
        "rollback_exact": True,
        "production_resources_unchanged": True,
        "cleanup": cleanup_evidence,
        "migration_steps": migration["steps"],
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(receipt))
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--candidate-root", required=True)
    result.add_argument("--runtime-bundle-root", required=True)
    result.add_argument("--python-executable", required=True)
    result.add_argument("--codex-cli", required=True)
    result.add_argument("--v215-source", required=True)
    result.add_argument("--work-root", required=True)
    result.add_argument("--output", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    receipt = run(parser().parse_args(argv))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
