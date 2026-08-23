#!/usr/bin/env python3
"""Product-shell composition root for the unified Windows installer."""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
for import_root in (SCRIPT_DIR, PROJECT_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

import yaml

try:
    from platform_process import no_window_kwargs
    from windows_install_manifest import (
        CodexCliIdentity,
        PackageIdentity,
        RuntimeBundle,
        WindowsInstallManifest,
        file_sha256,
        validate_manifest,
        read_manifest,
        write_manifest,
    )
    from install_maintenance_supervisor import WINDOWS_TASK_NAME as MAINTENANCE_TASK_NAME, windows_xml as maintenance_windows_xml
    from windows_install_migrations import default_registry
    from windows_installer_transaction import TransactionToken, WindowsInstallerTransaction
    from platform_atomic import atomic_replace_bytes
    from platform_scheduler import (
        WindowsTaskSpec,
        decode_windows_output,
        inspect_windows_task_xml,
        query_windows_task_xml,
        register_windows_task,
        render_windows_task_xml,
        uninstall_windows_task,
        windows_task_xml_equivalent,
        windows_system_executable,
        windows_user_id,
    )
    from memory_cli import MemoryStore, load_simple_yaml
    from memory_federation import FederationManager
except ModuleNotFoundError:
    from scripts.platform_process import no_window_kwargs
    from scripts.windows_install_manifest import (
        CodexCliIdentity,
        PackageIdentity,
        RuntimeBundle,
        WindowsInstallManifest,
        file_sha256,
        validate_manifest,
        read_manifest,
        write_manifest,
    )
    from scripts.install_maintenance_supervisor import WINDOWS_TASK_NAME as MAINTENANCE_TASK_NAME, windows_xml as maintenance_windows_xml
    from scripts.windows_install_migrations import default_registry
    from scripts.windows_installer_transaction import TransactionToken, WindowsInstallerTransaction
    from scripts.platform_atomic import atomic_replace_bytes
    from scripts.platform_scheduler import (
        WindowsTaskSpec,
        decode_windows_output,
        inspect_windows_task_xml,
        query_windows_task_xml,
        register_windows_task,
        render_windows_task_xml,
        uninstall_windows_task,
        windows_task_xml_equivalent,
        windows_system_executable,
        windows_user_id,
    )
    from scripts.memory_cli import MemoryStore, load_simple_yaml
    from scripts.memory_federation import FederationManager


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command), check=False, capture_output=True, text=True, encoding="utf-8",
        errors="replace", **no_window_kwargs(),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def _copy_skill_projection(source: Path, destination: Path) -> str:
    source = source.resolve(strict=True)

    def ignore(directory: str, names: list[str]) -> set[str]:
        if Path(directory).resolve() == source and "runtime" in names:
            return {"runtime"}
        return set()

    shutil.copytree(source, destination, ignore=ignore)
    if (destination / "runtime").exists():
        raise RuntimeError("Skill projection retained the external runtime bundle")
    return _tree_sha256(destination)


def default_installer_resource_root(*, rehearsal: bool = False) -> Path:
    base = Path(os.environ.get("PROGRAMDATA") or tempfile.gettempdir()).expanduser().resolve()
    product = "MemoryWuxianRehearsal" if rehearsal else "MemoryWuxian"
    return base / product / "installer-resources"


def _version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise RuntimeError("candidate pyproject.toml has no version")
    return match.group(1)


def _installed_version(root: Path) -> str | None:
    project = root / "pyproject.toml"
    if not project.is_file():
        return None
    return _version(root)


def _verify_manifest_identity(manifest: WindowsInstallManifest) -> None:
    if _version(manifest.candidate_root) != manifest.package.version:
        raise RuntimeError("candidate version drifted after manifest creation")
    if _tree_sha256(manifest.candidate_root) != manifest.package.sha256:
        raise RuntimeError("candidate tree drifted after manifest creation")
    runtime = manifest.runtime_bundle
    for path, expected, label in (
        (runtime.python_executable, runtime.python_sha256, "runtime interpreter"),
        (runtime.dependency_lock, runtime.dependency_lock_sha256, "runtime dependency lock"),
        (runtime.bundle_manifest, runtime.bundle_manifest_sha256, "runtime bundle manifest"),
        (manifest.codex_cli.path, manifest.codex_cli.sha256, "Codex CLI"),
    ):
        if file_sha256(path) != expected:
            raise RuntimeError(f"{label} drifted after manifest creation")


def _snapshot(path: Path, backup_root: Path, label: str) -> dict[str, Any]:
    path = path.resolve()
    evidence: dict[str, Any] = {"path": str(path), "existed": path.is_file()}
    if path.is_file():
        payload = path.read_bytes()
        backup = backup_root / f"{label}.bin"
        atomic_replace_bytes(backup, payload, create_parent=True)
        evidence.update({"backup": str(backup), "sha256": hashlib.sha256(payload).hexdigest()})
    return evidence


def _restore(snapshot: dict[str, Any], *, created_sha256: str | None = None) -> None:
    path = Path(snapshot["path"])
    if snapshot["existed"]:
        backup = Path(snapshot["backup"])
        atomic_replace_bytes(path, backup.read_bytes(), create_parent=True)
        if file_sha256(path) != snapshot["sha256"]:
            raise RuntimeError(f"rollback hash mismatch: {path}")
        return
    if path.exists():
        if not path.is_file() or created_sha256 is None or file_sha256(path) != created_sha256:
            raise RuntimeError(f"refusing to remove changed rollback target: {path}")
        path.unlink()


@dataclass(frozen=True)
class WindowsInstallResourceNamespace:
    collector_task_name: str = "MemoryWuxianCodexSync"
    collector_run_key: str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    collector_run_value: str = "MemoryWuxianCodexSync"
    maintenance_task_name: str = MAINTENANCE_TASK_NAME
    auto_update_task_name: str = "MemoryWuxianAutoUpdate"
    auto_update_run_key: str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    auto_update_run_value: str = "MemoryWuxianAutoUpdate"
    dashboard_desktop: Path | None = None
    dashboard_shortcut_name: str = "Memory无限状态台.lnk"

    def __post_init__(self) -> None:
        strings = (
            self.collector_task_name,
            self.collector_run_key,
            self.collector_run_value,
            self.maintenance_task_name,
            self.auto_update_task_name,
            self.auto_update_run_key,
            self.auto_update_run_value,
            self.dashboard_shortcut_name,
        )
        if any(not value or "\x00" in value for value in strings):
            raise ValueError("Windows installer resource names must be non-empty")
        if self.dashboard_desktop is not None and not self.dashboard_desktop.is_absolute():
            raise ValueError("rehearsal desktop must be absolute")


class _BoundMutation:
    owned_paths: tuple[str, ...] = ()
    owned_tasks: tuple[str, ...] = ()
    owned_registry_values: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()

    def __init__(self, manifest: WindowsInstallManifest, token: TransactionToken) -> None:
        self.manifest = manifest
        self._transaction_id = token.transaction_id
        self._secret = token.secret

    def _check(self, token: TransactionToken) -> None:
        if token.transaction_id != self._transaction_id or token.secret != self._secret:
            raise RuntimeError("transaction token mismatch")

    def _python(self) -> str:
        return str(self.manifest.runtime_bundle.python_executable)

    def discard_prepare(self, token: TransactionToken) -> None:
        self._check(token)

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        return {"status": "verified"}


class CollectorGenerationMutation(_BoundMutation):
    name = "collector-generation"
    resource_id = "installed-capture-generation"
    compensation = "rollback the collector generation subordinate journal"
    owned_paths = (
        "target_skill_root/** except config overlays",
        "archive_root/imports/codex/collector-command.json",
        "archive_root/imports/codex/collector-lifecycle.json",
        "archive_pointer",
        "bounded legacy collector launchers",
    )
    owned_tasks = ("MemoryWuxianCodexSync",)
    owned_registry_values = (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MemoryWuxianCodexSync",)
    forbidden_paths = ("archive_root/raw/**", "archive_root/summaries/**")

    def __init__(
        self,
        manifest: WindowsInstallManifest,
        token: TransactionToken,
        *,
        codex_cli: Path,
        resource_root: Path,
        task_name: str = "MemoryWuxianCodexSync",
        run_key: str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        run_value: str = "MemoryWuxianCodexSync",
    ) -> None:
        super().__init__(manifest, token)
        self.codex_cli = codex_cli
        self.resource_root = resource_root
        self.collector_journal = resource_root / "collector" / "install-journal.json"
        self.skill_candidate = resource_root / "skill-candidate"
        self.skill_candidate_sha256: str | None = None
        self.task_name = task_name
        self.run_key = run_key
        self.run_value = run_value
        self.owned_tasks = (task_name,)
        self.owned_registry_values = (f"{run_key}\\{run_value}",)

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.skill_candidate.exists():
            raise RuntimeError("transaction-private Skill projection already exists")
        self.skill_candidate_sha256 = _copy_skill_projection(
            self.manifest.candidate_root, self.skill_candidate
        )
        return {
            "owner": "install_codex_autosync_windows.py",
            "deferred_commit": True,
            "collector_journal": str(self.collector_journal),
            "skill_candidate": str(self.skill_candidate),
            "skill_candidate_sha256": self.skill_candidate_sha256,
            "excluded_top_level": ["runtime"],
        }

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        self.collector_journal = Path(evidence["collector_journal"]).resolve()
        self.skill_candidate = Path(evidence["skill_candidate"]).resolve()
        self.skill_candidate_sha256 = str(evidence["skill_candidate_sha256"])
        if evidence.get("excluded_top_level") != ["runtime"]:
            raise RuntimeError("Skill projection exclusion contract drifted")
        if not self.skill_candidate.is_dir() or _tree_sha256(self.skill_candidate) != self.skill_candidate_sha256:
            raise RuntimeError("transaction-private Skill projection is missing or drifted")
        if (self.skill_candidate / "runtime").exists():
            raise RuntimeError("transaction-private Skill projection contains runtime")

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        output = _run([
            self._python(), str(self.manifest.candidate_root / "scripts/install_codex_autosync_windows.py"),
            "--archive-root", str(self.manifest.archive_root), "--skill-root", str(self.manifest.target_skill_root),
            "--candidate-root", str(self.skill_candidate), "--sessions-root", str(self.manifest.sessions_root),
            "--python-executable", self._python(), "--codex-cli", str(self.codex_cli),
            "--backend", "task", "--runtime-directory", str(self.collector_journal.parent),
            "--journal-path", str(self.collector_journal),
            "--task-name", self.task_name,
            "--run-key", self.run_key,
            "--run-value", self.run_value,
            "--active-root-pointer", str(self.manifest.archive_pointer),
            "--defer-commit", "--skip-maintenance", "--load",
        ])
        lines = [line for line in output.stdout.splitlines() if line.startswith("install-journal:")]
        if not lines:
            raise RuntimeError("collector installer returned no durable journal")
        observed = Path(lines[-1].split(":", 1)[1]).resolve()
        if observed != self.collector_journal.resolve():
            raise RuntimeError("collector installer journal path drifted")
        return {"collector_journal": str(self.collector_journal)}

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if not self.collector_journal.is_file():
            raise RuntimeError("collector journal is missing after apply")
        journal = json.loads(self.collector_journal.read_text(encoding="utf-8"))
        if Path(journal.get("active_root_pointer", "")).resolve() != self.manifest.archive_pointer.resolve():
            raise RuntimeError("collector journal archive pointer drifted")
        pointer = self.manifest.archive_pointer
        if not pointer.is_file():
            raise RuntimeError("collector did not establish the active archive pointer")
        try:
            pointer_root = Path(pointer.read_text(encoding="utf-8").strip()).expanduser().resolve()
        except (OSError, UnicodeDecodeError) as error:
            raise RuntimeError("collector archive pointer is unreadable") from error
        if pointer_root != self.manifest.archive_root.resolve():
            raise RuntimeError("collector archive pointer targets the wrong archive")
        required = ("SKILL.md", "config.yaml", "bin/memory-wuxian-collector.exe")
        missing = [item for item in required if not (self.manifest.target_skill_root / item).is_file()]
        if missing:
            raise RuntimeError(f"installed generation is incomplete: {', '.join(missing)}")
        if (self.manifest.target_skill_root / "runtime").exists():
            raise RuntimeError("installed Skill generation duplicated the external runtime bundle")
        return {
            "journal_sha256": file_sha256(self.collector_journal),
            "required_files": list(required),
            "runtime_externalized": True,
        }

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        _run([self._python(), str(self.manifest.target_skill_root / "scripts/install_codex_autosync_windows.py"), "--commit-journal", str(self.collector_journal)])
        return {"status": "committed", "collector_journal": str(self.collector_journal)}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if not self.collector_journal.is_file():
            return {"status": "no-mutation-observed"}
        _run([self._python(), str(self.manifest.candidate_root / "scripts/install_codex_autosync_windows.py"), "--rollback-journal", str(self.collector_journal)])
        return {"status": "rolled-back", "collector_journal": str(self.collector_journal)}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if not self.collector_journal.is_file():
            return {"status": "verified", "journal": "not-created"}
        document = json.loads(self.collector_journal.read_text(encoding="utf-8"))
        if document.get("phase") != "rollback":
            raise RuntimeError("collector rollback journal is not verified")
        return {"status": "verified", "collector_phase": "rollback"}


class ConfigurationMigrationMutation(_BoundMutation):
    name = "configuration-migration"
    resource_id = "configuration-overlay"
    compensation = "restore exact pre-transaction config and migration receipt bytes"
    owned_paths = ("target_skill_root/config.yaml", "target_skill_root/config-migration-receipt.json")
    forbidden_paths = ("archive_root/**",)

    def __init__(self, manifest: WindowsInstallManifest, token: TransactionToken, *, backup_root: Path) -> None:
        super().__init__(manifest, token)
        target = manifest.target_skill_root
        self.installed_version = _installed_version(target)
        self.config = target / "config.yaml"
        self.receipt = target / "config-migration-receipt.json"
        self.backup_root = backup_root
        self.config_snapshot: dict[str, Any] = {}
        self.receipt_snapshot: dict[str, Any] = {}
        self.created_hashes: dict[Path, str] = {}
        self.migration_evidence: dict[str, Any] | None = None
        self.expected_config = backup_root / "expected-config.yaml"
        self.expected_receipt = backup_root / "expected-receipt.json"

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        self.config_snapshot = _snapshot(self.config, self.backup_root, "config")
        self.receipt_snapshot = _snapshot(self.receipt, self.backup_root, "config-receipt")
        source = self.config if self.config.is_file() else self.manifest.candidate_root / "config.yaml"
        defaults_path = self.manifest.candidate_root / "config.defaults.yaml"
        if not defaults_path.is_file():
            defaults_path = self.manifest.target_skill_root / "config.defaults.yaml"
        current = yaml.safe_load(source.read_text(encoding="utf-8"))
        defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict) or not isinstance(defaults, dict):
            raise RuntimeError("candidate config and defaults must be YAML mappings")
        migrated, evidence = default_registry().migrate_document(
            current, defaults, from_version=self.installed_version, to_version=self.manifest.package.version
        )
        config_bytes = yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False).encode("utf-8")
        receipt_bytes = (json.dumps({"format": "memory-wuxian-windows-migration-v2", **evidence}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        atomic_replace_bytes(self.expected_config, config_bytes, create_parent=True)
        atomic_replace_bytes(self.expected_receipt, receipt_bytes, create_parent=True)
        self.created_hashes = {self.config: hashlib.sha256(config_bytes).hexdigest(), self.receipt: hashlib.sha256(receipt_bytes).hexdigest()}
        self.migration_evidence = evidence
        return {
            "config": self.config_snapshot, "receipt": self.receipt_snapshot,
            "installed_version": self.installed_version, "expected_config": str(self.expected_config),
            "expected_receipt": str(self.expected_receipt), "created_hashes": {str(path): value for path, value in self.created_hashes.items()},
            "migration_evidence": evidence,
        }

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        self.config_snapshot = evidence["config"]
        self.receipt_snapshot = evidence["receipt"]
        self.installed_version = evidence.get("installed_version")
        self.expected_config = Path(evidence["expected_config"])
        self.expected_receipt = Path(evidence["expected_receipt"])
        self.created_hashes = {Path(path): value for path, value in evidence["created_hashes"].items()}
        self.migration_evidence = evidence["migration_evidence"]

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        atomic_replace_bytes(self.config, self.expected_config.read_bytes(), create_parent=False)
        atomic_replace_bytes(self.receipt, self.expected_receipt.read_bytes(), create_parent=False)
        return dict(self.migration_evidence or {})

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.migration_evidence is None or not self.migration_evidence.get("idempotent"):
            raise RuntimeError("migration did not prove idempotence")
        if file_sha256(self.config) != self.created_hashes[self.config]:
            raise RuntimeError("configuration drifted after migration")
        return {"config_sha256": self.created_hashes[self.config], "steps": self.migration_evidence["steps"]}

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        return {"status": "committed", "backup_evidence_retained": True}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        _restore(self.receipt_snapshot, created_sha256=self.created_hashes.get(self.receipt))
        _restore(self.config_snapshot, created_sha256=self.created_hashes.get(self.config))
        return {"status": "rolled-back", "config_restored": True}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        for snapshot in (self.config_snapshot, self.receipt_snapshot):
            path = Path(snapshot["path"])
            if path.is_file() != snapshot["existed"]:
                raise RuntimeError(f"configuration rollback existence mismatch: {path}")
            if path.is_file() and file_sha256(path) != snapshot["sha256"]:
                raise RuntimeError(f"configuration rollback hash mismatch: {path}")
        return {"status": "verified", "config_restored": True}


class ArchiveInitializationMutation(_BoundMutation):
    name = "archive-initialization"
    resource_id = "archive-scaffold"
    compensation = "remove only unchanged scaffolding created by this transaction; never touch raw records"
    owned_paths = ("bounded MemoryStore.init metadata",)
    forbidden_paths = ("archive_root/raw/**", "archive_pointer")

    def __init__(self, manifest: WindowsInstallManifest, token: TransactionToken, *, backup_root: Path) -> None:
        super().__init__(manifest, token)
        self.backup_root = backup_root
        self.files: list[Path] = []
        self.directories: list[Path] = []
        self.snapshots: list[dict[str, Any]] = []
        self.directory_evidence: list[dict[str, Any]] = []
        self.pointer_evidence: dict[str, Any] = {}
        self.created_hashes: dict[Path, str] = {}

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        probe = self.backup_root / "layout-probe"
        if probe.exists():
            shutil.rmtree(probe)
        config_path = self.manifest.candidate_root / "config.yaml"
        if not config_path.is_file():
            config_path = self.manifest.target_skill_root / "config.yaml"
        config = load_simple_yaml(config_path)
        MemoryStore(probe, config).init()
        relative_files = sorted(path.relative_to(probe) for path in probe.rglob("*") if path.is_file())
        relative_dirs = sorted((path.relative_to(probe) for path in probe.rglob("*") if path.is_dir()), key=lambda path: len(path.parts))
        self.files = [self.manifest.archive_root / path for path in relative_files]
        self.directories = [self.manifest.archive_root, *[self.manifest.archive_root / path for path in relative_dirs]]
        self.snapshots = [
            {"path": str(path), "existed": path.is_file(), "expected_sha256": file_sha256(probe / relative)}
            for path, relative in zip(self.files, relative_files)
        ]
        self.directory_evidence = [{"path": str(path), "existed": path.is_dir()} for path in self.directories]
        pointer = self.manifest.archive_pointer
        self.pointer_evidence = {"path": str(pointer), "existed": pointer.is_file(), "sha256": file_sha256(pointer) if pointer.is_file() else None}
        shutil.rmtree(probe)
        return {"bounded_files": self.snapshots, "bounded_directories": self.directory_evidence, "archive_pointer": self.pointer_evidence, "raw_archive_targeted": False, "archive_pointer_targeted": False}

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        self.snapshots = evidence["bounded_files"]
        self.directory_evidence = evidence["bounded_directories"]
        self.files = [Path(item["path"]) for item in self.snapshots]
        self.directories = [Path(item["path"]) for item in self.directory_evidence]
        self.pointer_evidence = evidence["archive_pointer"]
        self.created_hashes = {Path(item["path"]): item["expected_sha256"] for item in self.snapshots if not item["existed"]}

    def discard_prepare(self, token: TransactionToken) -> None:
        self._check(token)
        probe = self.backup_root / "layout-probe"
        if probe.exists():
            shutil.rmtree(probe)

    def _verify_pointer(self) -> None:
        pointer = Path(self.pointer_evidence["path"])
        if pointer.is_file() != self.pointer_evidence["existed"]:
            raise RuntimeError("active archive pointer existence changed during installation")
        if pointer.is_file() and file_sha256(pointer) != self.pointer_evidence["sha256"]:
            raise RuntimeError("active archive pointer bytes changed during installation")

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        config_path = self.manifest.candidate_root / "config.yaml"
        if not config_path.is_file():
            config_path = self.manifest.target_skill_root / "config.yaml"
        config = load_simple_yaml(config_path)
        MemoryStore(self.manifest.archive_root, config).init()
        self._verify_pointer()
        self.created_hashes = {Path(item["path"]): item["expected_sha256"] for item in self.snapshots if not item["existed"]}
        return {"created_files": [str(path) for path in self.created_hashes], "raw_archive_targeted": False}

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        missing = [str(path) for path in self.files if not path.is_file()]
        if missing:
            raise RuntimeError(f"archive scaffolding is incomplete: {', '.join(missing)}")
        pointer = Path(self.pointer_evidence["path"])
        if self.pointer_evidence["existed"]:
            self._verify_pointer()
            pointer_status = "preserved"
        else:
            pointer_status = "delegated-to-installed-capture-generation"
        return {"files_present": len(self.files), "archive_pointer_status": pointer_status}

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        return {"status": "committed", "append_only_archive_preserved": True}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        retained_runtime_files: list[str] = []
        for snapshot in reversed(self.snapshots):
            path = Path(snapshot["path"])
            if snapshot["existed"] or not path.exists():
                continue
            created_sha256 = self.created_hashes.get(path)
            if path.is_file() and created_sha256 is not None and file_sha256(path) == created_sha256:
                path.unlink()
            else:
                retained_runtime_files.append(str(path))
        retained_runtime_directories: list[str] = []
        for directory in reversed(self.directory_evidence):
            path = Path(directory["path"])
            if not directory["existed"] and path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    retained_runtime_directories.append(str(path))
        self._verify_pointer()
        return {
            "status": "rolled-back",
            "raw_archive_targeted": False,
            "retained_runtime_files": retained_runtime_files,
            "retained_runtime_directories": retained_runtime_directories,
        }

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        self._verify_pointer()
        unchanged_files = []
        retained_runtime_files = []
        for item in self.snapshots:
            path = Path(item["path"])
            if item["existed"] or not path.exists():
                continue
            if path.is_file() and file_sha256(path) == item["expected_sha256"]:
                unchanged_files.append(str(path))
            else:
                retained_runtime_files.append(str(path))
        empty_directories = []
        retained_runtime_directories = []
        for item in self.directory_evidence:
            path = Path(item["path"])
            if item["existed"] or not path.is_dir():
                continue
            if any(path.iterdir()):
                retained_runtime_directories.append(str(path))
            else:
                empty_directories.append(str(path))
        if unchanged_files or empty_directories:
            leftovers = unchanged_files + empty_directories
            raise RuntimeError(f"archive initialization rollback left removable scaffolding: {', '.join(leftovers)}")
        return {
            "status": "verified",
            "raw_archive_targeted": False,
            "retained_runtime_files": retained_runtime_files,
            "retained_runtime_directories": retained_runtime_directories,
        }


class FederationNodeInitializationMutation(_BoundMutation):
    name = "federation-node-initialization"
    resource_id = "local-federation-node"
    compensation = "remove only unchanged local-node scaffolding created by this transaction"
    owned_paths = (
        "archive_root/federation/node.json",
        "archive_root/federation/export-state.json",
        "archive_root/federation/export-ledger.jsonl",
        "archive_root/federation/sync-log.jsonl",
        "archive_root/federation/peers/",
        "archive_root/federation/token-usage-snapshots/",
        "replica_root/global-index/",
        "replica_root/peers/",
    )
    forbidden_paths = (
        "archive_root/raw/**",
        "archive_root/summaries/**",
        "archive_root/federation/peers/*.json",
        "replica_root/peers/**",
    )

    def __init__(self, manifest: WindowsInstallManifest, token: TransactionToken, *, backup_root: Path) -> None:
        super().__init__(manifest, token)
        self.backup_root = backup_root
        self.files: list[dict[str, Any]] = []
        self.directories: list[dict[str, Any]] = []
        self.created_hashes: dict[Path, str] = {}
        self.display_name = (os.environ.get("COMPUTERNAME") or platform.node() or "MemoryWuxian").strip()

    def _config(self) -> dict[str, Any]:
        path = self.manifest.target_skill_root / "config.yaml"
        if not path.is_file():
            path = self.manifest.candidate_root / "config.yaml"
        return load_simple_yaml(path)

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        config = self._config()
        target_manager = FederationManager(MemoryStore(self.manifest.archive_root, config))
        with tempfile.TemporaryDirectory(prefix="mwf-probe-") as temporary:
            probe_root = Path(temporary)
            probe_archive = probe_root / "a"
            probe_replica = probe_root / "r"
            probe_config = deepcopy(config)
            federation = probe_config.setdefault("federation", {})
            if not isinstance(federation, dict):
                raise RuntimeError("federation configuration must be a mapping")
            federation["replica_directory"] = str(probe_replica)
            probe_manager = FederationManager(MemoryStore(probe_archive, probe_config))
            probe_manager.init_node(self.display_name)

            probe_node = probe_manager.node_path
            node_document = json.loads(probe_node.read_text(encoding="utf-8"))
            node_document["replica_root"] = str(target_manager.replica_root)
            atomic_replace_bytes(
                probe_node,
                json.dumps(node_document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            )

            mappings = (
                (probe_archive, self.manifest.archive_root),
                (probe_replica, target_manager.replica_root),
            )
            self.files = []
            self.directories = []
            for source_root, target_root in mappings:
                for expected in sorted(item for item in source_root.rglob("*") if item.is_file()):
                    target = target_root / expected.relative_to(source_root)
                    existed = target.is_file()
                    prior_sha256 = file_sha256(target) if existed else None
                    expected_bytes = expected.read_bytes()
                    self.files.append({
                        "path": str(target),
                        "existed": existed,
                        "prior_sha256": prior_sha256,
                        "expected_base64": base64.b64encode(expected_bytes).decode("ascii"),
                        "expected_sha256": prior_sha256 or hashlib.sha256(expected_bytes).hexdigest(),
                    })
                source_directories = sorted(
                    (item for item in source_root.rglob("*") if item.is_dir()),
                    key=lambda item: (len(item.relative_to(source_root).parts), item.as_posix()),
                )
                for expected in (source_root, *source_directories):
                    target = target_root / expected.relative_to(source_root)
                    self.directories.append({"path": str(target), "existed": target.is_dir()})
        return {
            "display_name": self.display_name,
            "bounded_files": self.files,
            "bounded_directories": self.directories,
            "raw_archive_targeted": False,
            "existing_node_preserved": next(
                item["existed"] for item in self.files if Path(item["path"]).name == "node.json"
            ),
        }

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        self.display_name = evidence["display_name"]
        self.files = evidence["bounded_files"]
        self.directories = evidence["bounded_directories"]
        self.created_hashes = {
            Path(item["path"]): item["expected_sha256"]
            for item in self.files
            if not item["existed"]
        }

    def discard_prepare(self, token: TransactionToken) -> None:
        self._check(token)

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        for item in self.directories:
            Path(item["path"]).mkdir(parents=True, exist_ok=True)
        for item in self.files:
            path = Path(item["path"])
            if item["existed"]:
                if not path.is_file() or file_sha256(path) != item["prior_sha256"]:
                    raise RuntimeError(f"existing federation file drifted before apply: {path}")
                continue
            if path.exists():
                raise RuntimeError(f"federation path appeared after prepare: {path}")
            expected = base64.b64decode(item["expected_base64"], validate=True)
            if hashlib.sha256(expected).hexdigest() != item["expected_sha256"]:
                raise RuntimeError(f"federation prepare evidence drifted before apply: {path}")
            atomic_replace_bytes(path, expected, create_parent=True)
            self.created_hashes[path] = item["expected_sha256"]
        return {
            "created_files": [str(path) for path in self.created_hashes],
            "existing_node_preserved": any(
                item["existed"] and Path(item["path"]).name == "node.json" for item in self.files
            ),
        }

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        for item in self.files:
            path = Path(item["path"])
            if not path.is_file() or file_sha256(path) != item["expected_sha256"]:
                raise RuntimeError(f"federation node resource drifted after apply: {path}")
        node = next(Path(item["path"]) for item in self.files if Path(item["path"]).name == "node.json")
        document = json.loads(node.read_text(encoding="utf-8"))
        return {
            "node_id": document["node_id"],
            "display_name": document["display_name"],
            "files_present": len(self.files),
        }

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        node = next(
            Path(item["path"])
            for item in self.files
            if Path(item["path"]).name == "node.json"
        )
        return {
            "status": "committed",
            "existing_node_preserved": node not in self.created_hashes,
        }

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        retained_files: list[str] = []
        for item in reversed(self.files):
            path = Path(item["path"])
            if item["existed"] or not path.exists():
                continue
            if path.is_file() and file_sha256(path) == item["expected_sha256"]:
                path.unlink()
            else:
                retained_files.append(str(path))
        retained_directories: list[str] = []
        for item in reversed(self.directories):
            path = Path(item["path"])
            if not item["existed"] and path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    retained_directories.append(str(path))
        return {"status": "rolled-back", "retained_files": retained_files, "retained_directories": retained_directories}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        retained_files: list[str] = []
        for item in self.files:
            path = Path(item["path"])
            if item["existed"]:
                if not path.is_file() or file_sha256(path) != item["prior_sha256"]:
                    raise RuntimeError(f"federation rollback changed existing bytes: {path}")
            elif path.is_file():
                if file_sha256(path) == item["expected_sha256"]:
                    raise RuntimeError(f"federation rollback left removable file: {path}")
                retained_files.append(str(path))
        return {"status": "verified", "existing_files_preserved": True, "retained_files": retained_files}


class MaintenanceRegistrationMutation(_BoundMutation):
    name = "maintenance-registration"
    resource_id = "maintenance-scheduler"
    compensation = "restore the exact previous MemoryWuxianMaintenance task definition"
    owned_tasks = ("MemoryWuxianMaintenance",)
    forbidden_paths = ("archive_root/raw/**",)

    def __init__(
        self,
        manifest: WindowsInstallManifest,
        token: TransactionToken,
        *,
        backup_root: Path,
        task_name: str = MAINTENANCE_TASK_NAME,
    ) -> None:
        super().__init__(manifest, token)
        self.backup_root = backup_root
        self.task_name = task_name
        self.owned_tasks = (task_name,)
        self.schtasks = windows_system_executable("System32/schtasks.exe")
        self.previous_xml: bytes | None = None
        self.expected_xml: bytes | None = None
        self.backup = backup_root / "maintenance-task.xml"
        self.expected = backup_root / "expected-maintenance-task.xml"

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        self.previous_xml = query_windows_task_xml(
            self.task_name,
            schtasks=self.schtasks,
            runner=subprocess.run,
            runner_kwargs=no_window_kwargs(),
        )
        if self.previous_xml is not None:
            atomic_replace_bytes(self.backup, self.previous_xml, create_parent=True)
        self.expected_xml = maintenance_windows_xml(
            self.manifest.runtime_bundle.python_executable,
            self.manifest.target_skill_root,
            self.manifest.archive_root,
        )
        atomic_replace_bytes(self.expected, self.expected_xml, create_parent=True)
        return {
            "task_name": self.task_name,
            "previous_existed": self.previous_xml is not None,
            "backup": str(self.backup) if self.previous_xml is not None else None,
            "backup_sha256": hashlib.sha256(self.previous_xml).hexdigest() if self.previous_xml is not None else None,
            "expected": str(self.expected),
        }

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        backup = evidence.get("backup")
        self.previous_xml = Path(backup).read_bytes() if backup else None
        if self.previous_xml is not None and hashlib.sha256(self.previous_xml).hexdigest() != evidence["backup_sha256"]:
            raise RuntimeError("maintenance task backup drifted")
        self.expected_xml = Path(evidence["expected"]).read_bytes()

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        register_windows_task(
            self.task_name,
            self.expected_xml or b"",
            temporary_prefix="memory-wuxian-maintenance-",
            schtasks=self.schtasks,
            runner=subprocess.run,
            write_bytes=lambda path, payload: atomic_replace_bytes(path, payload),
            runner_kwargs=no_window_kwargs(),
            error_prefix="maintenance task registration failed",
        )
        return {"task_name": self.task_name}

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        current = query_windows_task_xml(
            self.task_name,
            schtasks=self.schtasks,
            runner=subprocess.run,
            runner_kwargs=no_window_kwargs(),
        )
        if current is None or not windows_task_xml_equivalent(current, self.expected_xml or b""):
            raise RuntimeError("maintenance task drifted after registration")
        return {"task": inspect_windows_task_xml(current)}

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        return {"status": "committed", "task_name": self.task_name}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.previous_xml is None:
            uninstall_windows_task(
                self.task_name,
                schtasks=self.schtasks,
                runner=subprocess.run,
                end_first=True,
                runner_kwargs=no_window_kwargs(),
            )
        else:
            register_windows_task(
                self.task_name,
                self.previous_xml,
                temporary_prefix="memory-wuxian-maintenance-restore-",
                schtasks=self.schtasks,
                runner=subprocess.run,
                write_bytes=lambda path, payload: atomic_replace_bytes(path, payload),
                runner_kwargs=no_window_kwargs(),
                error_prefix="maintenance task rollback failed",
            )
        return {"status": "rolled-back", "previous_existed": self.previous_xml is not None}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        current = query_windows_task_xml(
            self.task_name,
            schtasks=self.schtasks,
            runner=subprocess.run,
            runner_kwargs=no_window_kwargs(),
        )
        if (current is None) != (self.previous_xml is None):
            raise RuntimeError("maintenance rollback existence mismatch")
        if current is not None and not windows_task_xml_equivalent(current, self.previous_xml or b""):
            raise RuntimeError("maintenance rollback definition mismatch")
        return {"status": "verified", "previous_existed": self.previous_xml is not None}


class AutoUpdateRegistrationMutation(_BoundMutation):
    name = "auto-update-registration"
    resource_id = "auto-update-scheduler"
    compensation = "restore exact previous Task Scheduler XML; never create a Run-key fallback"
    owned_tasks = ("MemoryWuxianAutoUpdate",)
    owned_registry_values = (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\MemoryWuxianAutoUpdate",)
    forbidden_paths = ("archive_root/**",)
    task_name = "MemoryWuxianAutoUpdate"
    run_key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    run_value = "MemoryWuxianAutoUpdate"

    def __init__(
        self,
        manifest: WindowsInstallManifest,
        token: TransactionToken,
        *,
        backup_root: Path,
        task_name: str = "MemoryWuxianAutoUpdate",
        run_key: str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        run_value: str = "MemoryWuxianAutoUpdate",
    ) -> None:
        super().__init__(manifest, token)
        self.backup_root = backup_root
        self.task_name = task_name
        self.run_key = run_key
        self.run_value = run_value
        self.owned_tasks = (task_name,)
        self.owned_registry_values = (f"{run_key}\\{run_value}",)
        self.schtasks = windows_system_executable("System32/schtasks.exe")
        self.reg = windows_system_executable("System32/reg.exe")
        self.previous_xml: bytes | None = None
        self.previous_run_value: str | None = None
        self.backup = backup_root / "auto-update-task.xml"
        self.expected_xml: bytes | None = None

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        kwargs = no_window_kwargs()
        self.previous_xml = query_windows_task_xml(self.task_name, schtasks=self.schtasks, runner=subprocess.run, runner_kwargs=kwargs)
        query = subprocess.run(
            [str(self.reg), "QUERY", self.run_key, "/V", self.run_value],
            check=False, capture_output=True, **kwargs,
        )
        text = decode_windows_output(query.stdout)
        match = re.search(rf"^\s*{re.escape(self.run_value)}\s+REG_SZ\s+(.*)$", text, re.MULTILINE)
        self.previous_run_value = match.group(1).strip() if query.returncode == 0 and match else None
        if self.previous_xml is not None:
            atomic_replace_bytes(self.backup, self.previous_xml, create_parent=True)
        self.expected_xml = render_windows_task_xml(self._spec(), user_id=windows_user_id())
        expected = self.backup_root / "expected-auto-update-task.xml"
        atomic_replace_bytes(expected, self.expected_xml, create_parent=True)
        return {"task_name": self.task_name, "previous_task_existed": self.previous_xml is not None, "backup": str(self.backup) if self.previous_xml is not None else None, "backup_sha256": hashlib.sha256(self.previous_xml).hexdigest() if self.previous_xml is not None else None, "expected": str(expected), "previous_run_value": self.previous_run_value, "run_key_fallback": False}

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        backup = evidence.get("backup")
        self.previous_xml = Path(backup).read_bytes() if backup else None
        if self.previous_xml is not None and hashlib.sha256(self.previous_xml).hexdigest() != evidence["backup_sha256"]:
            raise RuntimeError("auto-update task backup drifted")
        self.expected_xml = Path(evidence["expected"]).read_bytes()
        self.previous_run_value = evidence.get("previous_run_value")

    def _spec(self) -> WindowsTaskSpec:
        python = self.manifest.runtime_bundle.python_executable
        pythonw = python.with_name("pythonw.exe")
        if not pythonw.is_file():
            pythonw = python
        return WindowsTaskSpec(
            task_name=self.task_name, description="Memory Wuxian daily stable release check",
            command=pythonw, arguments=(str(self.manifest.target_skill_root / "scripts/auto_update.py"), "--skill-root", str(self.manifest.target_skill_root), "--channel", "stable"),
            interval="P1D", execution_limit="PT2H", priority="7", allow_hard_terminate=True,
            multiple_instances="IgnoreNew", disallow_start_on_batteries=False, stop_on_batteries=False,
            start_when_available=True, network_required=True, hidden=True, logon_type="InteractiveToken", run_level="LeastPrivilege",
        )

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        register_windows_task(self.task_name, self.expected_xml or b"", temporary_prefix="memory-wuxian-update-", schtasks=self.schtasks, runner=subprocess.run, write_bytes=lambda path, payload: atomic_replace_bytes(path, payload), runner_kwargs=no_window_kwargs(), error_prefix="auto-update task registration failed")
        removed = subprocess.run(
            [str(self.reg), "DELETE", self.run_key, "/V", self.run_value, "/F"],
            check=False, capture_output=True, **no_window_kwargs(),
        )
        if self.previous_run_value is not None and removed.returncode != 0:
            raise RuntimeError("legacy auto-update Run value could not be removed")
        return {"task_name": self.task_name, "run_key_fallback": False}

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        current = query_windows_task_xml(self.task_name, schtasks=self.schtasks, runner=subprocess.run, runner_kwargs=no_window_kwargs())
        if current is None:
            raise RuntimeError("auto-update task is missing")
        observed = inspect_windows_task_xml(current)
        if not windows_task_xml_equivalent(current, self.expected_xml or b""):
            raise RuntimeError("auto-update task drifted after registration")
        query = subprocess.run([str(self.reg), "QUERY", self.run_key, "/V", self.run_value], check=False, capture_output=True, **no_window_kwargs())
        if query.returncode == 0:
            raise RuntimeError("legacy auto-update Run value remains active")
        return {"task": observed, "run_key_fallback": False}

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        return {"status": "committed", "task_name": self.task_name}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.previous_xml is None:
            uninstall_windows_task(self.task_name, schtasks=self.schtasks, runner=subprocess.run, end_first=True, runner_kwargs=no_window_kwargs())
        else:
            register_windows_task(self.task_name, self.previous_xml, temporary_prefix="memory-wuxian-restore-", schtasks=self.schtasks, runner=subprocess.run, write_bytes=lambda path, payload: atomic_replace_bytes(path, payload), runner_kwargs=no_window_kwargs(), error_prefix="auto-update task rollback failed")
        if self.previous_run_value is None:
            subprocess.run([str(self.reg), "DELETE", self.run_key, "/V", self.run_value, "/F"], check=False, capture_output=True, **no_window_kwargs())
        else:
            restored_run = subprocess.run([str(self.reg), "ADD", self.run_key, "/V", self.run_value, "/T", "REG_SZ", "/D", self.previous_run_value, "/F"], check=False, capture_output=True, **no_window_kwargs())
            if restored_run.returncode != 0:
                raise RuntimeError("legacy auto-update Run value rollback failed")
        restored = query_windows_task_xml(self.task_name, schtasks=self.schtasks, runner=subprocess.run, runner_kwargs=no_window_kwargs())
        if (restored is None) != (self.previous_xml is None):
            raise RuntimeError("auto-update task rollback verification failed")
        if restored is not None and not windows_task_xml_equivalent(restored, self.previous_xml or b""):
            raise RuntimeError("auto-update task rollback content verification failed")
        return {"status": "rolled-back", "previous_task_restored": self.previous_xml is not None}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        restored = query_windows_task_xml(self.task_name, schtasks=self.schtasks, runner=subprocess.run, runner_kwargs=no_window_kwargs())
        if (restored is None) != (self.previous_xml is None):
            raise RuntimeError("auto-update rollback verification failed")
        if restored is not None and not windows_task_xml_equivalent(restored, self.previous_xml or b""):
            raise RuntimeError("auto-update rollback semantic mismatch")
        query = subprocess.run([str(self.reg), "QUERY", self.run_key, "/V", self.run_value], check=False, capture_output=True, **no_window_kwargs())
        text = decode_windows_output(query.stdout)
        match = re.search(rf"^\s*{re.escape(self.run_value)}\s+REG_SZ\s+(.*)$", text, re.MULTILINE)
        observed_run = match.group(1).strip() if query.returncode == 0 and match else None
        if observed_run != self.previous_run_value:
            raise RuntimeError("legacy auto-update Run value rollback mismatch")
        return {"status": "verified", "previous_task_restored": self.previous_xml is not None}


class DashboardShortcutMutation(_BoundMutation):
    name = "dashboard-shortcut"
    resource_id = "dashboard-launcher"
    compensation = "restore exact prior shortcut and launcher config bytes"
    owned_paths = ("Desktop/Memory无限状态台.lnk", "CODEX_HOME/memory-wuxian-dashboard-launcher.json")
    forbidden_paths = ("archive_root/**",)

    def __init__(
        self,
        manifest: WindowsInstallManifest,
        token: TransactionToken,
        *,
        backup_root: Path,
        desktop: Path | None = None,
        shortcut_name: str = "Memory无限状态台.lnk",
    ) -> None:
        super().__init__(manifest, token)
        self.backup_root = backup_root
        self.desktop = desktop
        self.shortcut_name = shortcut_name
        self.shortcut: Path | None = None
        codex_home = manifest.target_skill_root.parent.parent
        self.launcher_config = codex_home / "memory-wuxian-dashboard-launcher.json"
        self.shortcut_snapshot: dict[str, Any] = {}
        self.config_snapshot: dict[str, Any] = {}
        self.created_hashes: dict[Path, str] = {}
        self.expected_launcher_config: dict[str, Any] = {}
        self.expected_launcher_sha256: str | None = None

    def prepare(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.desktop is None:
            desktop_result = _run(["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"])
            self.desktop = Path(desktop_result.stdout.strip()).resolve()
        else:
            self.desktop = self.desktop.resolve()
            self.desktop.mkdir(parents=True, exist_ok=True)
        self.shortcut = self.desktop / self.shortcut_name
        self.shortcut_snapshot = _snapshot(self.shortcut, self.backup_root, "dashboard-shortcut")
        self.config_snapshot = _snapshot(self.launcher_config, self.backup_root, "dashboard-launcher-config")
        self.expected_launcher_config = {
            "schema_version": 1,
            "python_executable": str(self.manifest.runtime_bundle.python_executable.with_name("pythonw.exe") if self.manifest.runtime_bundle.python_executable.with_name("pythonw.exe").is_file() else self.manifest.runtime_bundle.python_executable),
            "archive_root": str(self.manifest.archive_root),
        }
        candidate_launcher = self.manifest.candidate_root / "bin/memory-wuxian-dashboard-launcher.exe"
        self.expected_launcher_sha256 = file_sha256(candidate_launcher)
        return {"desktop": str(self.desktop), "shortcut": self.shortcut_snapshot, "launcher_config": self.config_snapshot, "expected_launcher_config": self.expected_launcher_config, "expected_launcher_sha256": self.expected_launcher_sha256, "real_launch_effect_gate": "S12-installed-runtime-effect-receipt"}

    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None:
        self._check(token)
        self.desktop = Path(evidence["desktop"])
        self.shortcut_snapshot = evidence["shortcut"]
        self.config_snapshot = evidence["launcher_config"]
        self.shortcut = Path(self.shortcut_snapshot["path"])
        self.expected_launcher_config = evidence["expected_launcher_config"]
        self.expected_launcher_sha256 = evidence["expected_launcher_sha256"]

    def apply(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.desktop is None or self.shortcut is None:
            raise RuntimeError("dashboard shortcut mutation is not prepared")
        _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.manifest.target_skill_root / "scripts/install_dashboard_shortcut_windows.ps1"), "-SkillRoot", str(self.manifest.target_skill_root), "-ArchiveRoot", str(self.manifest.archive_root), "-PythonExecutable", self._python(), "-Desktop", str(self.desktop), "-ShortcutName", self.shortcut_name])
        self.created_hashes[self.shortcut] = file_sha256(self.shortcut)
        self.created_hashes[self.launcher_config] = file_sha256(self.launcher_config)
        return {"shortcut": str(self.shortcut), "launcher_config": str(self.launcher_config)}

    def verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        result = _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.manifest.target_skill_root / "scripts/inspect_dashboard_shortcut_windows.ps1"), "-Path", str(self.shortcut)])
        observed = json.loads(result.stdout)
        expected_target = str(self.manifest.target_skill_root / "bin/memory-wuxian-dashboard-launcher.exe")
        expected_icon = str(self.manifest.target_skill_root / "assets/memory-wuxian.ico") + ",0"
        expected = {
            "target": Path(expected_target).resolve(),
            "working_directory": self.manifest.target_skill_root.resolve(),
            "arguments": "",
            "icon": expected_icon,
            "target_exists": True,
        }
        if not observed.get("exists"):
            raise RuntimeError("dashboard shortcut is missing")
        for field in ("target", "working_directory"):
            if Path(observed.get(field, "")).resolve() != expected[field]:
                raise RuntimeError(f"dashboard shortcut {field} verification failed")
        for field in ("arguments", "icon", "target_exists"):
            if observed.get(field) != expected[field]:
                raise RuntimeError(f"dashboard shortcut {field} verification failed")
        if json.loads(self.launcher_config.read_text(encoding="utf-8")) != self.expected_launcher_config:
            raise RuntimeError("dashboard launcher config verification failed")
        launcher = self.manifest.target_skill_root / "bin/memory-wuxian-dashboard-launcher.exe"
        if self.expected_launcher_sha256 is None or file_sha256(launcher) != self.expected_launcher_sha256:
            raise RuntimeError("dashboard launcher executable hash verification failed")
        return observed

    def commit(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.shortcut is None:
            raise RuntimeError("dashboard shortcut mutation is not prepared")
        return {"status": "committed", "shortcut_sha256": self.created_hashes[self.shortcut]}

    def rollback(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        if self.shortcut is None:
            raise RuntimeError("dashboard shortcut mutation is not prepared")
        if self.config_snapshot["existed"]:
            _restore(self.config_snapshot, created_sha256=self.created_hashes.get(self.launcher_config))
        elif self.launcher_config.is_file():
            if json.loads(self.launcher_config.read_text(encoding="utf-8")) != self.expected_launcher_config:
                raise RuntimeError("refusing to remove an unrecognized launcher config during rollback")
            self.launcher_config.unlink()
        if self.shortcut_snapshot["existed"]:
            _restore(self.shortcut_snapshot, created_sha256=self.created_hashes.get(self.shortcut))
        elif self.shortcut.is_file():
            inspection = _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.manifest.target_skill_root / "scripts/inspect_dashboard_shortcut_windows.ps1"), "-Path", str(self.shortcut)])
            observed = json.loads(inspection.stdout)
            if Path(observed.get("target", "")).resolve() != (self.manifest.target_skill_root / "bin/memory-wuxian-dashboard-launcher.exe").resolve():
                raise RuntimeError("refusing to remove an unrecognized shortcut during rollback")
            self.shortcut.unlink()
        return {"status": "rolled-back", "shortcut_restored": True}

    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]:
        self._check(token)
        for snapshot in (self.shortcut_snapshot, self.config_snapshot):
            path = Path(snapshot["path"])
            if path.is_file() != snapshot["existed"]:
                raise RuntimeError(f"dashboard rollback existence mismatch: {path}")
            if path.is_file() and file_sha256(path) != snapshot["sha256"]:
                raise RuntimeError(f"dashboard rollback hash mismatch: {path}")
        return {"status": "verified", "shortcut_restored": True}


class ProductInstallAdapter:
    def __init__(
        self,
        *,
        codex_cli: Path,
        resource_root: Path,
        resources: WindowsInstallResourceNamespace | None = None,
    ) -> None:
        self.codex_cli = codex_cli
        self.resource_root = resource_root
        self.resources = resources or WindowsInstallResourceNamespace()

    def build(self, manifest: WindowsInstallManifest, token: TransactionToken) -> list[Any]:
        if manifest.operation not in {"install", "repair"}:
            raise RuntimeError("product install adapter does not own uninstall")
        expected = (
            "archive",
            "auto-update",
            "collector",
            "config",
            "federation-node",
            "maintenance",
            "shortcut",
        )
        if manifest.requested_components != expected:
            raise RuntimeError("product installer requires the exact full component set")
        if self.codex_cli.resolve() != manifest.codex_cli.path.resolve():
            raise RuntimeError("composition Codex CLI identity does not match the manifest")
        root = self.resource_root / token.transaction_id
        return [
            ArchiveInitializationMutation(manifest, token, backup_root=root / "archive"),
            FederationNodeInitializationMutation(manifest, token, backup_root=root / "federation-node"),
            CollectorGenerationMutation(
                manifest, token, codex_cli=self.codex_cli, resource_root=root,
                task_name=self.resources.collector_task_name,
                run_key=self.resources.collector_run_key,
                run_value=self.resources.collector_run_value,
            ),
            ConfigurationMigrationMutation(manifest, token, backup_root=root / "config"),
            MaintenanceRegistrationMutation(
                manifest, token, backup_root=root / "maintenance",
                task_name=self.resources.maintenance_task_name,
            ),
            AutoUpdateRegistrationMutation(
                manifest, token, backup_root=root / "scheduler",
                task_name=self.resources.auto_update_task_name,
                run_key=self.resources.auto_update_run_key,
                run_value=self.resources.auto_update_run_value,
            ),
            DashboardShortcutMutation(
                manifest, token, backup_root=root / "shortcut",
                desktop=self.resources.dashboard_desktop,
                shortcut_name=self.resources.dashboard_shortcut_name,
            ),
        ]


def build_manifest(args: argparse.Namespace) -> WindowsInstallManifest:
    candidate = Path(args.candidate_root).resolve()
    python = Path(args.python_executable).resolve()
    bundle_root = Path(args.runtime_bundle_root).resolve()
    bundle_manifest = bundle_root / "runtime-manifest.json"
    dependency_lock = bundle_root / "runtime-lock.json"
    manifest = WindowsInstallManifest(
        schema_version=2,
        operation=args.operation,
        source_entrypoint=args.source_entrypoint,
        candidate_root=candidate,
        target_skill_root=Path(args.skill_root).resolve(),
        archive_root=Path(args.archive_root).resolve(),
        archive_pointer=Path(args.archive_pointer).resolve(),
        sessions_root=Path(args.sessions_root).resolve(),
        runtime_bundle=RuntimeBundle(
            python_executable=python,
            python_sha256=file_sha256(python),
            dependency_lock=dependency_lock,
            dependency_lock_sha256=file_sha256(dependency_lock),
            bundle_root=bundle_root,
            bundle_manifest=bundle_manifest,
            bundle_manifest_sha256=file_sha256(bundle_manifest),
            bundle_id=args.runtime_bundle_id,
        ),
        codex_cli=CodexCliIdentity(
            path=Path(args.codex_cli).resolve(),
            sha256=file_sha256(Path(args.codex_cli).resolve()),
        ),
        package=PackageIdentity(_version(candidate), _tree_sha256(candidate)),
        requested_components=(
            "archive",
            "auto-update",
            "collector",
            "config",
            "federation-node",
            "maintenance",
            "shortcut",
        ),
    )
    return validate_manifest(manifest.to_document())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-manifest")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--operation", choices=("install", "repair"), default="install")
    parser.add_argument("--source-entrypoint", choices=("inno", "manual", "auto-update"))
    parser.add_argument("--candidate-root")
    parser.add_argument("--skill-root")
    parser.add_argument("--archive-root")
    parser.add_argument("--archive-pointer")
    parser.add_argument("--sessions-root")
    parser.add_argument("--python-executable")
    parser.add_argument("--runtime-bundle-root")
    parser.add_argument("--runtime-bundle-id")
    parser.add_argument("--codex-cli")
    parser.add_argument("--journal-path")
    parser.add_argument("--manifest-output")
    parser.add_argument("--failure-point", help=argparse.SUPPRESS)
    return parser


def _require_prepare_arguments(args: argparse.Namespace) -> None:
    required = (
        "source_entrypoint", "candidate_root", "skill_root", "archive_root",
        "archive_pointer", "sessions_root", "python_executable",
        "runtime_bundle_root", "runtime_bundle_id", "codex_cli", "manifest_output",
    )
    missing = [name.replace("_", "-") for name in required if not getattr(args, name)]
    if missing:
        raise SystemExit(f"manifest preparation requires: {', '.join(missing)}")


def execute_manifest(path: Path, *, failure_point: str | None = None) -> int:
    manifest_path = path.resolve()
    manifest = read_manifest(manifest_path)
    transaction_root = manifest_path.parent
    journal_path = transaction_root / "journal.json"
    controller = WindowsInstallerTransaction(
        journal_path=journal_path,
        adapters=[
            ProductInstallAdapter(
                codex_cli=manifest.codex_cli.path,
                resource_root=default_installer_resource_root(),
            )
        ],
        identity_validator=_verify_manifest_identity,
    )
    result = controller.execute(manifest, failure_point=failure_point)
    print(json.dumps({"transaction_id": result.transaction_id, "phase": result.phase, "journal_path": str(result.journal_path), "exit_code": int(result.exit_code)}, sort_keys=True))
    return int(result.exit_code)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute_manifest:
        if args.prepare_only:
            raise SystemExit("--execute-manifest and --prepare-only are mutually exclusive")
        return execute_manifest(Path(args.execute_manifest), failure_point=args.failure_point)
    _require_prepare_arguments(args)
    manifest = build_manifest(args)
    manifest_path = Path(args.manifest_output).resolve()
    write_manifest(manifest_path, manifest)
    if args.prepare_only:
        print(json.dumps({"status": "prepared", "manifest_path": str(manifest_path), "manifest_sha256": file_sha256(manifest_path)}, sort_keys=True))
        return 0
    if not args.journal_path:
        raise SystemExit("direct internal execution requires --journal-path")
    return execute_manifest(manifest_path, failure_point=args.failure_point)


if __name__ == "__main__":
    raise SystemExit(main())
