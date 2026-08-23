#!/usr/bin/env python3
"""Closed manifest contract for the unified Windows installer transaction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from platform_transaction import atomic_write_canonical_json, read_canonical_json
except ModuleNotFoundError:
    from scripts.platform_transaction import atomic_write_canonical_json, read_canonical_json


MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "operation",
        "source_entrypoint",
        "candidate_root",
        "target_skill_root",
        "archive_root",
        "archive_pointer",
        "sessions_root",
        "runtime_bundle",
        "codex_cli",
        "package",
        "requested_components",
    }
)
RUNTIME_FIELDS = frozenset(
    {
        "python_executable",
        "python_sha256",
        "dependency_lock",
        "dependency_lock_sha256",
        "bundle_root",
        "bundle_manifest",
        "bundle_manifest_sha256",
        "bundle_id",
    }
)
PACKAGE_FIELDS = frozenset({"version", "sha256"})
CODEX_CLI_FIELDS = frozenset({"path", "sha256"})
OPERATIONS = frozenset({"install", "repair", "uninstall"})
ENTRYPOINTS = frozenset({"inno", "manual", "auto-update"})
COMPONENTS = frozenset(
    {"archive", "collector", "config", "federation-node", "maintenance", "auto-update", "shortcut"}
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class InstallManifestError(ValueError):
    pass


def _closed_object(value: Any, fields: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise InstallManifestError(f"{label} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _absolute_path(value: Any, label: str, *, must_exist: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise InstallManifestError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise InstallManifestError(f"{label} must be absolute")
    resolved = path.resolve(strict=False)
    if must_exist and not resolved.exists():
        raise InstallManifestError(f"{label} does not exist: {resolved}")
    return resolved


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise InstallManifestError(f"{label} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class RuntimeBundle:
    python_executable: Path
    python_sha256: str
    dependency_lock: Path
    dependency_lock_sha256: str
    bundle_root: Path
    bundle_manifest: Path
    bundle_manifest_sha256: str
    bundle_id: str


@dataclass(frozen=True)
class PackageIdentity:
    version: str
    sha256: str


@dataclass(frozen=True)
class CodexCliIdentity:
    path: Path
    sha256: str


@dataclass(frozen=True)
class WindowsInstallManifest:
    schema_version: int
    operation: str
    source_entrypoint: str
    candidate_root: Path
    target_skill_root: Path
    archive_root: Path
    archive_pointer: Path
    sessions_root: Path
    runtime_bundle: RuntimeBundle
    codex_cli: CodexCliIdentity
    package: PackageIdentity
    requested_components: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        value = asdict(self)
        for field in ("candidate_root", "target_skill_root", "archive_root", "archive_pointer", "sessions_root"):
            value[field] = str(value[field])
        value["runtime_bundle"]["python_executable"] = str(value["runtime_bundle"]["python_executable"])
        for field in ("dependency_lock", "bundle_root", "bundle_manifest"):
            value["runtime_bundle"][field] = str(value["runtime_bundle"][field])
        value["codex_cli"]["path"] = str(value["codex_cli"]["path"])
        value["requested_components"] = list(self.requested_components)
        return value


def validate_manifest(document: Any) -> WindowsInstallManifest:
    value = _closed_object(document, MANIFEST_FIELDS, "manifest")
    if value["schema_version"] != 2:
        raise InstallManifestError("manifest schema_version is unsupported")
    if value["operation"] not in OPERATIONS:
        raise InstallManifestError("manifest operation is unsupported")
    if value["source_entrypoint"] not in ENTRYPOINTS:
        raise InstallManifestError("manifest source_entrypoint is unsupported")
    runtime = _closed_object(value["runtime_bundle"], RUNTIME_FIELDS, "runtime_bundle")
    codex_cli = _closed_object(value["codex_cli"], CODEX_CLI_FIELDS, "codex_cli")
    package = _closed_object(value["package"], PACKAGE_FIELDS, "package")
    if not isinstance(package["version"], str) or not package["version"]:
        raise InstallManifestError("package.version must be non-empty")
    components = value["requested_components"]
    if (
        not isinstance(components, list)
        or any(not isinstance(item, str) or item not in COMPONENTS for item in components)
        or components != sorted(set(components))
    ):
        raise InstallManifestError("requested_components must be unique, sorted, and allowlisted")
    dependency_lock = _absolute_path(runtime["dependency_lock"], "runtime_bundle.dependency_lock", must_exist=True)
    bundle_root = _absolute_path(runtime["bundle_root"], "runtime_bundle.bundle_root", must_exist=True)
    bundle_manifest = _absolute_path(runtime["bundle_manifest"], "runtime_bundle.bundle_manifest", must_exist=True)
    if bundle_manifest.parent != bundle_root:
        raise InstallManifestError("runtime bundle manifest must be directly inside bundle_root")
    bundle_id = runtime["bundle_id"]
    if not isinstance(bundle_id, str) or SHA256_RE.fullmatch(bundle_id) is None:
        raise InstallManifestError("runtime_bundle.bundle_id must be a lowercase SHA-256")
    python_executable = _absolute_path(runtime["python_executable"], "runtime_bundle.python_executable", must_exist=True)
    try:
        python_executable.relative_to(bundle_root)
        dependency_lock.relative_to(bundle_root)
    except ValueError as exc:
        raise InstallManifestError("runtime interpreter and lock must stay inside bundle_root") from exc
    python_sha256 = _sha256(runtime["python_sha256"], "runtime_bundle.python_sha256")
    dependency_lock_sha256 = _sha256(runtime["dependency_lock_sha256"], "runtime_bundle.dependency_lock_sha256")
    bundle_manifest_sha256 = _sha256(runtime["bundle_manifest_sha256"], "runtime_bundle.bundle_manifest_sha256")
    codex_cli_path = _absolute_path(codex_cli["path"], "codex_cli.path", must_exist=True)
    codex_cli_sha256 = _sha256(codex_cli["sha256"], "codex_cli.sha256")
    if file_sha256(python_executable) != python_sha256:
        raise InstallManifestError("runtime interpreter hash drift")
    if file_sha256(dependency_lock) != dependency_lock_sha256:
        raise InstallManifestError("runtime dependency lock hash drift")
    if file_sha256(bundle_manifest) != bundle_manifest_sha256:
        raise InstallManifestError("runtime bundle manifest hash drift")
    if file_sha256(codex_cli_path) != codex_cli_sha256:
        raise InstallManifestError("Codex CLI hash drift")
    return WindowsInstallManifest(
        schema_version=2,
        operation=value["operation"],
        source_entrypoint=value["source_entrypoint"],
        candidate_root=_absolute_path(value["candidate_root"], "candidate_root", must_exist=True),
        target_skill_root=_absolute_path(value["target_skill_root"], "target_skill_root"),
        archive_root=_absolute_path(value["archive_root"], "archive_root"),
        archive_pointer=_absolute_path(value["archive_pointer"], "archive_pointer"),
        sessions_root=_absolute_path(value["sessions_root"], "sessions_root", must_exist=True),
        runtime_bundle=RuntimeBundle(
            python_executable=python_executable,
            python_sha256=python_sha256,
            dependency_lock=dependency_lock,
            dependency_lock_sha256=dependency_lock_sha256,
            bundle_root=bundle_root,
            bundle_manifest=bundle_manifest,
            bundle_manifest_sha256=bundle_manifest_sha256,
            bundle_id=bundle_id,
        ),
        codex_cli=CodexCliIdentity(path=codex_cli_path, sha256=codex_cli_sha256),
        package=PackageIdentity(
            version=package["version"],
            sha256=_sha256(package["sha256"], "package.sha256"),
        ),
        requested_components=tuple(components),
    )


def read_manifest(path: Path) -> WindowsInstallManifest:
    return validate_manifest(read_canonical_json(path))


def write_manifest(path: Path, manifest: WindowsInstallManifest) -> bytes:
    validated = validate_manifest(manifest.to_document())
    return atomic_write_canonical_json(path, validated.to_document())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
