#!/usr/bin/env python3
"""Verified, transactional installation of Memory Wuxian Skill packages."""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml

from memory_environment import EnvironmentRegistry
from platform_lock import exclusive_lock


MANIFEST_NAME = "skill-package-manifest.json"
INSTALLER_LOCK_NAME = "environment-skill-installer.lock"
PLATFORMS = {"macos", "windows", "linux"}
SCOPE_CLASSES = {"global": "global-skill", "project": "project-skill"}
SAFE_CHECKS = {"file-exists", "utf8", "json-parse", "python-compile"}
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
REVISION_RE = re.compile(r"^rev:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
RUNTIME_VERSION_RE = re.compile(r"^(>=|==)?([0-9]+(?:\.[0-9]+){0,3})$")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def skill_package_contract_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return the Registry-bound package contract without the circular revision ID."""

    contract_manifest = json.loads(json.dumps(manifest))
    contract_manifest.pop("source_revision", None)
    return _canonical(
        {
            "format": "memory-wuxian-skill-package-contract-v1",
            "manifest": contract_manifest,
        }
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode(
                    "utf-8"
                )
                + b"\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _strict_keys(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    required: Iterable[str],
    label: str,
) -> None:
    allowed_set = set(allowed)
    required_set = set(required)
    unknown = set(value) - allowed_set
    missing = required_set - set(value)
    if unknown:
        raise ValueError(f"{label}: unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label}: missing fields: {sorted(missing)}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}: expected non-empty string")
    return value


def _safe_relative(value: Any, label: str) -> str:
    text = _string(value, label)
    if "\\" in text or "\x00" in text or re.match(r"^[A-Za-z]:", text):
        raise ValueError(f"{label}: path must be portable POSIX-relative")
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or text.startswith("//")
        or "." in pure.parts
        or ".." in pure.parts
        or pure.as_posix() != text
    ):
        raise ValueError(f"{label}: absolute or traversal path is forbidden")
    for part in pure.parts:
        stem = part.rstrip(" .").split(".", 1)[0].casefold()
        if not part or part.endswith((" ", ".")) or stem in WINDOWS_RESERVED:
            raise ValueError(f"{label}: path is not portable to Windows")
    return text


def _case_key(path: str) -> str:
    return "/".join(part.casefold() for part in PurePosixPath(path).parts)


def _version_tuple(value: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _runtime_satisfies(actual: str, requirement: str) -> bool:
    match = RUNTIME_VERSION_RE.fullmatch(requirement)
    if not match:
        raise ValueError(f"unsupported runtime requirement: {requirement}")
    operator, requested = match.groups()
    actual_tuple = _version_tuple(actual)
    requested_tuple = _version_tuple(requested)
    width = max(len(actual_tuple), len(requested_tuple))
    actual_tuple += (0,) * (width - len(actual_tuple))
    requested_tuple += (0,) * (width - len(requested_tuple))
    return actual_tuple == requested_tuple if operator == "==" else actual_tuple >= requested_tuple


class SkillInstallationError(RuntimeError):
    """A fail-closed Skill installation result."""

    def __init__(self, message: str, *, receipt: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.receipt = receipt


class EnvironmentSkillInstaller:
    """Preview, verify, install, and roll back registered Skill artifacts."""

    def __init__(
        self,
        registry: EnvironmentRegistry,
        *,
        target_node_id: str,
        platform: str,
        runtime_versions: Mapping[str, str],
        global_skill_bindings: Mapping[str, Mapping[str, Any]],
    ):
        if not isinstance(target_node_id, str) or len(target_node_id) < 3:
            raise ValueError("target_node_id must contain at least three characters")
        if platform not in PLATFORMS:
            raise ValueError("unsupported installer platform")
        self.registry = registry
        self.target_node_id = target_node_id
        self.platform = platform
        self.runtime_versions = {
            _string(name, "runtime name"): _string(version, "runtime version")
            for name, version in runtime_versions.items()
        }
        self.global_skill_bindings = {
            key: json.loads(json.dumps(value))
            for key, value in global_skill_bindings.items()
        }
        self.lock_path = self.registry.locks_dir / INSTALLER_LOCK_NAME
        self.staging_root = self.registry.staging_dir / "skill-installs"
        self.transactions_dir = self.registry.transactions_dir / "skill-installs"
        self.rollback_root = self.registry.root / "rollbacks" / "skills"
        self.receipts_dir = self.registry.receipts_dir / "skill-installs"
        self.package_root = self.registry.root / "packages"
        self.package_objects = self.package_root / "sha256"
        self.package_revisions = self.package_root / "by-revision"

    @classmethod
    def from_binding_registry(
        cls,
        registry: EnvironmentRegistry,
        *,
        target_node_id: str,
        platform: str,
        runtime_versions: Mapping[str, str],
        binding_registry: Any,
    ) -> "EnvironmentSkillInstaller":
        """Resolve global Skill destinations only from persisted bindings."""

        bindings: Dict[str, Dict[str, Any]] = {}
        for item in binding_registry.get_skill_bindings():
            if item["scope"] != "global":
                continue
            bindings[item["binding_id"]] = {
                "skill_id": item["skill_id"],
                "root": item["target_root"],
                "relative_path": item["skill_id"],
                "enabled": True,
                "pinned_version": None,
            }
        return cls(
            registry,
            target_node_id=target_node_id,
            platform=platform,
            runtime_versions=runtime_versions,
            global_skill_bindings=bindings,
        )

    def install(
        self,
        *,
        package_path: Path | str,
        artifact_id: str,
        revision_id: str,
        target_binding: str,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Validate and preview by default; mutate only with explicit apply."""

        package = Path(package_path)
        with exclusive_lock(self.lock_path):
            self.recover_transactions()
            prepared = self._prepare(
                package=package,
                artifact_id=artifact_id,
                revision_id=revision_id,
                target_binding=target_binding,
            )
            if prepared["decision"] == "no-change":
                return {
                    "status": "no-change",
                    "artifact_id": artifact_id,
                    "revision_id": revision_id,
                    "target_binding": target_binding,
                    "target_path": str(prepared["target_path"]),
                }
            preview = {
                "status": "preview",
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "target_binding": target_binding,
                "target_path": str(prepared["target_path"]),
                "decision": prepared["decision"],
                "package_sha256": prepared["package_sha256"],
                "previous_installed_sha256": prepared["previous_hash"],
                "final_installed_sha256": prepared["logical_hash"],
                "rehearsal": prepared["rehearsal"],
            }
            if not apply:
                return preview
            return self._apply(prepared)

    def _prepare(
        self,
        *,
        package: Path,
        artifact_id: str,
        revision_id: str,
        target_binding: str,
    ) -> Dict[str, Any]:
        if not package.exists() or package.is_symlink() or not package.is_file():
            raise ValueError("Skill package must be an existing regular file")
        package_bytes = package.read_bytes()
        package_sha256 = _sha256(package_bytes)
        artifact, revision = self._registered_revision(artifact_id, revision_id)
        if artifact["object_class"] not in {"global-skill", "project-skill"}:
            raise ValueError("artifact is not a Skill")
        binding = self._resolve_binding(artifact, target_binding)
        target_path = self._resolve_target(binding)
        manifest, entries = self._inspect_zip(package)
        self._validate_manifest(manifest)
        if manifest["source_revision"] != revision_id:
            raise ValueError("package source_revision does not match registered revision")
        if manifest["skill_id"] != binding["skill_id"]:
            raise ValueError("package skill_id does not match target binding")
        pinned_version = binding.get("pinned_version")
        if pinned_version is not None and manifest["version"] != pinned_version:
            raise ValueError("package version does not match pinned Skill binding")
        if manifest["scope"] != artifact["scope"]:
            raise ValueError("package scope does not match registered artifact")
        if manifest["project_id"] != artifact["project_id"]:
            raise ValueError("package project_id does not match registered artifact")
        if self.platform not in manifest["supported_platforms"]:
            raise ValueError(f"package does not support platform {self.platform}")
        self._validate_runtime_requirements(manifest["runtime_requirements"])
        file_payloads = self._validate_archive_files(package, manifest, entries)
        self._validate_skill_metadata(manifest, file_payloads)
        package_contract = skill_package_contract_bytes(manifest)
        registered_contract = self.registry._resolve_relative(
            revision["object_path"], "object_path"
        ).read_bytes()
        if registered_contract != package_contract:
            raise ValueError(
                "Skill package contract does not match registered revision content"
            )
        logical_hash = self._logical_tree_hash(manifest)
        actual_hash = self._payload_tree_hash(file_payloads, manifest)
        current = self._inspect_current(target_path, manifest)
        if current is not None and current["exact"]:
            return {
                "decision": "no-change",
                "artifact": artifact,
                "revision": revision,
                "manifest": manifest,
                "target_path": target_path,
            }
        previous_receipt = self._latest_successful_receipt(target_binding)
        if current is not None and previous_receipt is None:
            raise ValueError(
                "existing Skill has no verified permission receipt; update requires review"
            )
        self._reject_permission_expansion(manifest, previous_receipt)
        rehearsal = {
            "package_sha256": package_sha256,
            "manifest_sha256": _sha256(_canonical(manifest)),
            "declared_files": len(manifest["files"]),
            "checks": [check["type"] for check in manifest["checks"]],
            "platform": self.platform,
            "runtime_requirements": manifest["runtime_requirements"],
            "network_access": manifest["network_access"],
            "persistent_components": manifest["persistent_components"],
            "codex_discovery": "validated",
        }
        return {
            "decision": "install" if current is None else "update",
            "artifact": artifact,
            "revision": revision,
            "manifest": manifest,
            "entries": entries,
            "file_payloads": file_payloads,
            "package_path": package,
            "package_sha256": package_sha256,
            "logical_hash": logical_hash,
            "actual_hash": actual_hash,
            "target_binding": target_binding,
            "binding": binding,
            "target_path": target_path,
            "previous_hash": None if current is None else current["actual_hash"],
            "rehearsal": rehearsal,
        }

    def _apply(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        transaction_id = f"skill-{uuid.uuid4().hex}"
        prepared = dict(prepared)
        prepared["rehearsal"] = {
            **prepared["rehearsal"],
            "transaction_id": transaction_id,
        }
        staging = self.staging_root / transaction_id
        staged_candidate = staging / "candidate"
        target = prepared["target_path"]
        sibling_candidate = target.parent / f".{target.name}.mw-candidate-{uuid.uuid4().hex}"
        displaced = target.parent / f".{target.name}.mw-previous-{uuid.uuid4().hex}"
        transaction_path: Optional[Path] = None
        target_displaced = False
        candidate_installed = False
        rollback_saved = False
        receipt: Optional[Dict[str, Any]] = None
        try:
            staging.mkdir(parents=True, exist_ok=False)
            self._extract(
                prepared["package_path"],
                prepared["entries"],
                prepared["manifest"],
                staged_candidate,
            )
            self._validate_installed_tree(staged_candidate, prepared["manifest"])
            self._run_checks(staged_candidate, prepared["manifest"]["checks"])
            self._cache_verified_package(prepared)

            target.parent.mkdir(parents=True, exist_ok=True)
            self._assert_safe_target_parent(prepared["binding"], target)
            shutil.copytree(staged_candidate, sibling_candidate, symlinks=False)
            self._validate_installed_tree(sibling_candidate, prepared["manifest"])
            self._run_checks(sibling_candidate, prepared["manifest"]["checks"])

            if target.exists():
                rollback_saved = self._save_verified_rollback(
                    target, prepared["target_binding"], prepared["previous_hash"]
                )
                self._after_rollback_saved(target)
            transaction_path = self._write_transaction(
                transaction_id,
                prepared,
                status="prepared",
                staging=staging,
                sibling_candidate=sibling_candidate,
                displaced=displaced,
            )
            if target.exists():
                os.replace(target, displaced)
                target_displaced = True
            os.replace(sibling_candidate, target)
            candidate_installed = True
            _fsync_directory(target.parent)
            self._after_switch(target)
            self._validate_installed_tree(target, prepared["manifest"])
            self._run_checks(target, prepared["manifest"]["checks"])
            final_hash = self._actual_tree_hash(target)
            receipt = self._receipt(
                prepared,
                result="installed",
                final_hash=final_hash,
                rollback={
                    "available": rollback_saved,
                    "attempted": False,
                    "succeeded": False,
                },
            )
            self._persist_receipt(receipt)
            if displaced.exists():
                shutil.rmtree(displaced)
            try:
                self._finish_transaction(transaction_path, "installed")
            except Exception:
                # The strict installed receipt is the commit point. A later
                # invocation finalizes this prepared marker idempotently.
                return {
                    "status": "installed",
                    "target_path": str(target),
                    "receipt": receipt,
                    "transaction_status": "recovery-required",
                }
            return {
                "status": "installed",
                "target_path": str(target),
                "receipt": receipt,
            }
        except Exception as error:
            rollback_attempted = target_displaced or candidate_installed
            rollback_succeeded = False
            rollback_error: Optional[str] = None
            try:
                if candidate_installed and target.exists():
                    shutil.rmtree(target)
                if target_displaced and displaced.exists():
                    os.replace(displaced, target)
                    rollback_succeeded = True
                elif candidate_installed and not target_displaced:
                    rollback_succeeded = not target.exists()
                _fsync_directory(target.parent)
            except Exception as restore_error:
                rollback_error = str(restore_error)
            result = "rolled-back" if rollback_attempted and rollback_succeeded else "failed"
            final_hash = (
                prepared["previous_hash"]
                if rollback_succeeded or not rollback_attempted
                else None
            )
            receipt = self._receipt(
                prepared,
                result=result,
                final_hash=final_hash,
                rollback={
                    "available": rollback_saved,
                    "attempted": rollback_attempted,
                    "succeeded": rollback_succeeded,
                    "error": rollback_error,
                },
                error=str(error),
            )
            self._persist_receipt(receipt)
            if transaction_path is not None:
                self._finish_transaction(transaction_path, result)
            raise SkillInstallationError(str(error), receipt=receipt) from error
        finally:
            for path in (staging, sibling_candidate):
                if path.exists():
                    shutil.rmtree(path)
            if displaced.exists() and not target_displaced:
                shutil.rmtree(displaced)

    def _registered_revision(
        self, artifact_id: str, revision_id: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        registry = self.registry._read_registry()
        matches = [
            event
            for event in registry["events"]
            if event["operation"] == "artifact-revision"
            and event["artifact_id"] == artifact_id
            and event["revision_id"] == revision_id
        ]
        if len(matches) != 1:
            raise ValueError("Skill artifact revision is not registered exactly once")
        event = matches[0]
        artifact = self.registry._read_relative_json(
            event["artifact_path"], "artifact_path"
        )
        revision = self.registry._read_relative_json(
            event["revision_path"], "revision_path"
        )
        self.registry._validate_artifact(artifact)
        self.registry._validate_revision(revision)
        self.registry._verify_revision_hash(revision)
        self.registry._verify_object(revision)
        if artifact["artifact_id"] != artifact_id or revision["artifact_id"] != artifact_id:
            raise ValueError("registered Skill identity mismatch")
        if revision["revision_id"] != revision_id:
            raise ValueError("registered Skill revision mismatch")
        return artifact, revision

    def _cache_verified_package(self, prepared: Dict[str, Any]) -> Dict[str, Any]:
        package_hash = prepared["package_sha256"]
        package_bytes = prepared["package_path"].read_bytes()
        if _sha256(package_bytes) != package_hash:
            raise ValueError("Skill package changed after validation")
        object_path = self.package_objects / package_hash[:2] / package_hash[2:]
        reference_path = (
            self.package_revisions
            / f"{prepared['revision']['revision_id'].split(':', 1)[1]}.json"
        )
        if object_path.exists():
            if object_path.is_symlink() or not object_path.is_file():
                raise ValueError("Skill package object path is unsafe")
            if _sha256(object_path.read_bytes()) != package_hash:
                raise ValueError("Skill package object hash mismatch")
        else:
            object_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{object_path.name}.", suffix=".tmp", dir=object_path.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(package_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, object_path)
                _fsync_directory(object_path.parent)
            finally:
                temporary.unlink(missing_ok=True)
        reference = {
            "schema_version": 1,
            "artifact_id": prepared["artifact"]["artifact_id"],
            "revision_id": prepared["revision"]["revision_id"],
            "package_sha256": package_hash,
            "package_path": object_path.relative_to(self.registry.root).as_posix(),
            "package_contract_sha256": _sha256(
                skill_package_contract_bytes(prepared["manifest"])
            ),
            "verified_at": _now_iso(),
        }
        if reference_path.exists():
            existing = json.loads(reference_path.read_text(encoding="utf-8"))
            stable_fields = {
                key: reference[key]
                for key in (
                    "schema_version",
                    "artifact_id",
                    "revision_id",
                    "package_sha256",
                    "package_path",
                    "package_contract_sha256",
                )
            }
            existing_stable = {key: existing.get(key) for key in stable_fields}
            if existing_stable != stable_fields:
                raise ValueError("Skill revision already references another package")
        else:
            _atomic_json(reference_path, reference)
        return reference

    def _resolve_binding(
        self, artifact: Dict[str, Any], target_binding: str
    ) -> Dict[str, Any]:
        if not isinstance(target_binding, str) or not target_binding:
            raise ValueError("target_binding must be a non-empty string")
        if artifact["scope"] == "global":
            raw = self.global_skill_bindings.get(target_binding)
            if raw is None:
                raise ValueError("global Skill target binding is not registered")
            allowed = {
                "skill_id",
                "root",
                "relative_path",
                "enabled",
                "pinned_version",
            }
            _strict_keys(raw, allowed, {"skill_id", "root", "relative_path", "enabled"}, "global binding")
            if raw["enabled"] is not True:
                raise ValueError("global Skill target binding is disabled")
            if raw["skill_id"] != artifact["artifact_id"].split(":", 1)[-1]:
                raise ValueError("global Skill artifact and binding identities disagree")
            result = dict(raw)
            result.update({"scope": "global", "project_id": None})
            return result

        project_id = artifact["project_id"]
        project_matches = [
            project
            for project in self.registry.projects()
            if project["project_id"] == project_id
        ]
        if len(project_matches) != 1:
            raise ValueError("project Skill references an unregistered project")
        project = project_matches[0]
        if not project["active"] or not project.get("local_root"):
            raise ValueError("project Skill requires an active local project binding")
        skill_id = artifact["artifact_id"].split(":", 1)[-1]
        expected_binding = f"project:{project_id}:{skill_id}"
        if target_binding != expected_binding:
            raise ValueError("project Skill target binding does not match project_id")
        matches = [
            binding
            for binding in project["skill_bindings"]
            if binding["skill_id"] == skill_id
        ]
        if len(matches) != 1 or not matches[0]["enabled"]:
            raise ValueError("project Skill binding is missing or disabled")
        pinned = matches[0].get("pinned_version")
        return {
            "scope": "project",
            "project_id": project_id,
            "skill_id": skill_id,
            "root": project["local_root"],
            "relative_path": f".codex/skills/{skill_id}",
            "enabled": True,
            "pinned_version": pinned,
        }

    def _resolve_target(self, binding: Dict[str, Any]) -> Path:
        root_text = _string(binding.get("root"), "binding.root")
        relative = _safe_relative(binding.get("relative_path"), "binding.relative_path")
        root_supplied = Path(root_text).expanduser()
        if root_supplied.is_symlink():
            raise ValueError("target binding root symlinks are forbidden")
        root = root_supplied.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("target binding root must be a directory")
        target = root.joinpath(*PurePosixPath(relative).parts)
        if target.is_symlink():
            raise ValueError("target Skill directory symlinks are forbidden")
        parent = target.parent
        while parent != root:
            if parent.exists() and parent.is_symlink():
                raise ValueError("target binding parent symlinks are forbidden")
            parent = parent.parent
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise ValueError("target Skill path escapes registered root") from error
        return target

    def _inspect_zip(
        self, package: Path
    ) -> Tuple[Dict[str, Any], Dict[str, zipfile.ZipInfo]]:
        try:
            archive = zipfile.ZipFile(package)
        except (OSError, zipfile.BadZipFile) as error:
            raise ValueError("invalid Skill ZIP package") from error
        with archive:
            entries: Dict[str, zipfile.ZipInfo] = {}
            case_keys: Dict[str, str] = {}
            for info in archive.infolist():
                name = info.filename.rstrip("/") if info.is_dir() else info.filename
                if not name:
                    continue
                normalized = _safe_relative(name, "ZIP entry")
                folded = _case_key(normalized)
                if normalized in entries:
                    raise ValueError(f"duplicate ZIP entry: {normalized}")
                if folded in case_keys:
                    raise ValueError(
                        f"case-insensitive ZIP path collision: {case_keys[folded]} / {normalized}"
                    )
                if self._zip_is_symlink(info):
                    raise ValueError(f"symlink ZIP entry is forbidden: {normalized}")
                entries[normalized] = info
                case_keys[folded] = normalized
            manifest_info = entries.get(MANIFEST_NAME)
            if manifest_info is None or manifest_info.is_dir():
                raise ValueError(f"package is missing {MANIFEST_NAME}")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("Skill package manifest is not valid UTF-8 JSON") from error
        if not isinstance(manifest, dict):
            raise ValueError("Skill package manifest must be an object")
        return manifest, entries

    @staticmethod
    def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
        return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)

    def _validate_manifest(self, manifest: Dict[str, Any]) -> None:
        fields = {
            "schema_version", "skill_id", "version", "scope", "project_id",
            "source_revision", "files", "supported_platforms",
            "runtime_requirements", "network_access", "persistent_components",
            "checks", "rollback",
        }
        _strict_keys(manifest, fields, fields, "Skill manifest")
        if manifest["schema_version"] != 1:
            raise ValueError("unsupported Skill manifest schema_version")
        if not isinstance(manifest["skill_id"], str) or not SKILL_ID_RE.fullmatch(
            manifest["skill_id"]
        ):
            raise ValueError("invalid manifest skill_id")
        if not isinstance(manifest["version"], str) or not VERSION_RE.fullmatch(
            manifest["version"]
        ):
            raise ValueError("invalid manifest version")
        if manifest["scope"] not in SCOPE_CLASSES:
            raise ValueError("invalid manifest scope")
        if manifest["scope"] == "global":
            if manifest["project_id"] is not None:
                raise ValueError("global Skill project_id must be null")
        elif not isinstance(manifest["project_id"], str) or len(manifest["project_id"]) < 3:
            raise ValueError("project Skill requires project_id")
        if not isinstance(manifest["source_revision"], str) or not REVISION_RE.fullmatch(
            manifest["source_revision"]
        ):
            raise ValueError("invalid manifest source_revision")
        if not isinstance(manifest["files"], list) or not manifest["files"]:
            raise ValueError("manifest files must be a non-empty array")
        seen, folded = set(), set()
        for item in manifest["files"]:
            self._validate_file_declaration(item)
            path = item["path"]
            if path in seen:
                raise ValueError(f"duplicate manifest path: {path}")
            if _case_key(path) in folded:
                raise ValueError(f"case-insensitive manifest path collision: {path}")
            seen.add(path)
            folded.add(_case_key(path))
        if "SKILL.md" not in seen:
            raise ValueError("manifest must declare SKILL.md")
        platforms = manifest["supported_platforms"]
        if (
            not isinstance(platforms, list)
            or not platforms
            or any(platform not in PLATFORMS for platform in platforms)
            or len(platforms) != len(set(platforms))
        ):
            raise ValueError("invalid supported_platforms")
        if not isinstance(manifest["runtime_requirements"], dict):
            raise ValueError("runtime_requirements must be an object")
        self._validate_network_access(manifest["network_access"])
        self._validate_persistent_components(manifest["persistent_components"])
        if not isinstance(manifest["checks"], list):
            raise ValueError("checks must be an array")
        for check in manifest["checks"]:
            self._validate_check(check, seen)
        if not isinstance(manifest["rollback"], dict):
            raise ValueError("rollback must be an object")

    def _validate_file_declaration(self, item: Any) -> None:
        if not isinstance(item, dict):
            raise ValueError("manifest file declaration must be an object")
        fields = {"path", "size", "sha256", "executable"}
        _strict_keys(item, fields, fields, "manifest file")
        item["path"] = _safe_relative(item["path"], "manifest file.path")
        if type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("manifest file.size must be a non-negative integer")
        if not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise ValueError("manifest file.sha256 is invalid")
        if type(item["executable"]) is not bool:
            raise ValueError("manifest file.executable must be boolean")

    @staticmethod
    def _validate_network_access(value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("network_access must be an object")
        _strict_keys(value, {"enabled", "destinations"}, {"enabled", "destinations"}, "network_access")
        if type(value["enabled"]) is not bool or not isinstance(value["destinations"], list):
            raise ValueError("network_access has invalid field types")
        if any(not isinstance(item, str) or not item for item in value["destinations"]):
            raise ValueError("network_access destinations must be non-empty strings")
        if len(value["destinations"]) != len(set(value["destinations"])):
            raise ValueError("network_access destinations contain duplicates")
        if not value["enabled"] and value["destinations"]:
            raise ValueError("disabled network_access cannot declare destinations")

    @staticmethod
    def _validate_persistent_components(value: Any) -> None:
        if not isinstance(value, list):
            raise ValueError("persistent_components must be an array")
        identities = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("persistent component must be an object")
            _strict_keys(
                item,
                {"component_id", "type"},
                {"component_id", "type"},
                "persistent component",
            )
            identity = (_string(item["component_id"], "component_id"), _string(item["type"], "component type"))
            if identity in identities:
                raise ValueError("duplicate persistent component")
            identities.add(identity)

    def _validate_check(self, check: Any, declared_paths: set[str]) -> None:
        if not isinstance(check, dict):
            raise ValueError("check must be an object")
        _strict_keys(check, {"type", "path"}, {"type", "path"}, "check")
        if check["type"] not in SAFE_CHECKS:
            raise ValueError("check type is not in the no-side-effect whitelist")
        path = _safe_relative(check["path"], "check.path")
        if path not in declared_paths:
            raise ValueError("check path must be a declared file")

    def _validate_runtime_requirements(self, requirements: Dict[str, Any]) -> None:
        for name, requirement in requirements.items():
            if not isinstance(name, str) or not name:
                raise ValueError("runtime requirement name is invalid")
            if not isinstance(requirement, str):
                raise ValueError("runtime requirement must be a version string")
            actual = self.runtime_versions.get(name)
            if actual is None:
                raise ValueError(f"required runtime is unavailable: {name}")
            if not _runtime_satisfies(actual, requirement):
                raise ValueError(
                    f"runtime requirement is not satisfied: {name} {requirement}"
                )

    def _validate_archive_files(
        self,
        package: Path,
        manifest: Dict[str, Any],
        entries: Dict[str, zipfile.ZipInfo],
    ) -> Dict[str, bytes]:
        declared = {item["path"]: item for item in manifest["files"]}
        archive_files = {
            name
            for name, info in entries.items()
            if not info.is_dir() and name != MANIFEST_NAME
        }
        archive_directories = {
            name for name, info in entries.items() if info.is_dir()
        }
        undeclared = archive_files - set(declared)
        missing = set(declared) - archive_files
        if undeclared:
            raise ValueError(f"package contains undeclared files: {sorted(undeclared)}")
        if missing:
            raise ValueError(f"package is missing declared files: {sorted(missing)}")
        for directory in archive_directories:
            if not any(
                path == directory or path.startswith(directory + "/")
                for path in declared
            ):
                raise ValueError(f"package contains undeclared directory: {directory}")
        payloads = self._read_payloads(package, declared, entries)
        for path, declaration in declared.items():
            payload = payloads[path]
            if len(payload) != declaration["size"]:
                raise ValueError(f"declared size mismatch: {path}")
            if _sha256(payload) != declaration["sha256"]:
                raise ValueError(f"declared hash mismatch: {path}")
        return payloads

    @staticmethod
    def _read_payloads(
        package: Path,
        declared: Dict[str, Dict[str, Any]],
        entries: Dict[str, zipfile.ZipInfo],
    ) -> Dict[str, bytes]:
        with zipfile.ZipFile(package) as archive:
            return {path: archive.read(entries[path]) for path in declared}

    def _validate_skill_metadata(
        self, manifest: Dict[str, Any], payloads: Dict[str, bytes]
    ) -> None:
        try:
            skill_text = payloads["SKILL.md"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("SKILL.md must be UTF-8") from error
        frontmatter = self._parse_frontmatter(skill_text)
        if frontmatter.get("name") != manifest["skill_id"]:
            raise ValueError("SKILL.md frontmatter name does not match manifest skill_id")
        if not frontmatter.get("description"):
            raise ValueError("SKILL.md frontmatter requires description")
        openai = payloads.get("agents/openai.yaml")
        if openai is not None:
            try:
                values = self._parse_openai_yaml(openai.decode("utf-8"))
            except UnicodeDecodeError as error:
                raise ValueError("agents/openai.yaml must be UTF-8") from error
            if not values.get("display_name") or not values.get("short_description"):
                raise ValueError("agents/openai.yaml requires display_name and short_description")
            default_prompt = values.get("default_prompt")
            if default_prompt is not None and not isinstance(default_prompt, str):
                raise ValueError("agents/openai.yaml default_prompt must be text")

    @staticmethod
    def _parse_frontmatter(text: str) -> Dict[str, Any]:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md is missing YAML frontmatter")
        end = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end = index
                break
        if end is None:
            raise ValueError("SKILL.md frontmatter is not terminated")
        return EnvironmentSkillInstaller._load_yaml_mapping(
            "\n".join(lines[1:end]), "SKILL.md frontmatter"
        )

    @staticmethod
    def _parse_openai_yaml(text: str) -> Dict[str, Any]:
        document = EnvironmentSkillInstaller._load_yaml_mapping(
            text, "agents/openai.yaml"
        )
        interface = document.get("interface")
        if not isinstance(interface, dict):
            raise ValueError("agents/openai.yaml must contain an interface mapping")
        return interface

    @staticmethod
    def _load_yaml_mapping(text: str, label: str) -> Dict[str, Any]:
        try:
            value = yaml.load(text, Loader=_UniqueKeySafeLoader)
        except (yaml.YAMLError, ValueError, TypeError) as error:
            raise ValueError(f"{label} contains invalid safe YAML") from error
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a YAML mapping")
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{label} keys must be text")
        return value

    def _inspect_current(
        self, target: Path, manifest: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not target.exists():
            return None
        self._validate_installed_tree(target, manifest, allow_hash_mismatch=True)
        declared = {item["path"]: item for item in manifest["files"]}
        exact = True
        for path, item in declared.items():
            candidate = target.joinpath(*PurePosixPath(path).parts)
            if not candidate.is_file() or _sha256(candidate.read_bytes()) != item["sha256"]:
                exact = False
                break
            if self.platform != "windows":
                executable = bool(stat.S_IMODE(candidate.stat().st_mode) & stat.S_IXUSR)
                if executable != item["executable"]:
                    exact = False
                    break
        return {
            "logical_hash": self._logical_tree_hash(manifest),
            "actual_hash": self._actual_tree_hash(target),
            "exact": exact,
        }

    def _validate_installed_tree(
        self,
        root: Path,
        manifest: Dict[str, Any],
        *,
        allow_hash_mismatch: bool = False,
    ) -> None:
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Skill target must be a real directory")
        declared = {item["path"]: item for item in manifest["files"]}
        actual = set()
        folded = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Skill tree contains a symlink")
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            if _case_key(relative) in folded:
                raise ValueError("Skill tree contains a case-insensitive collision")
            folded.add(_case_key(relative))
            actual.add(relative)
        if actual != set(declared):
            if allow_hash_mismatch:
                # An existing unmanaged directory may contain different files,
                # but it must still be safe before an explicit replacement.
                return
            raise ValueError("installed Skill files do not exactly match manifest")
        if allow_hash_mismatch:
            return
        for relative, declaration in declared.items():
            path = root.joinpath(*PurePosixPath(relative).parts)
            payload = path.read_bytes()
            if len(payload) != declaration["size"] or _sha256(payload) != declaration["sha256"]:
                raise ValueError(f"installed Skill hash mismatch: {relative}")
            if self.platform != "windows":
                executable = bool(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR)
                if executable != declaration["executable"]:
                    raise ValueError(f"installed executable mode mismatch: {relative}")
        payloads = {
            relative: root.joinpath(*PurePosixPath(relative).parts).read_bytes()
            for relative in declared
        }
        self._validate_skill_metadata(manifest, payloads)

    def _extract(
        self,
        package: Path,
        entries: Dict[str, zipfile.ZipInfo],
        manifest: Dict[str, Any],
        destination: Path,
    ) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        declared = {item["path"]: item for item in manifest["files"]}
        with zipfile.ZipFile(package) as archive:
            for relative, declaration in declared.items():
                target = destination.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = archive.read(entries[relative])
                target.write_bytes(payload)
                if self.platform != "windows":
                    mode = 0o755 if declaration["executable"] else 0o644
                    target.chmod(mode)

    def _run_checks(self, root: Path, checks: List[Dict[str, Any]]) -> None:
        for check in checks:
            path = root.joinpath(*PurePosixPath(check["path"]).parts)
            check_type = check["type"]
            if check_type == "file-exists":
                if not path.is_file():
                    raise ValueError(f"declared check failed: {check_type}")
            elif check_type == "utf8":
                path.read_text(encoding="utf-8")
            elif check_type == "json-parse":
                json.loads(path.read_text(encoding="utf-8"))
            elif check_type == "python-compile":
                with tempfile.TemporaryDirectory() as temporary:
                    py_compile.compile(
                        str(path),
                        cfile=str(Path(temporary) / "checked.pyc"),
                        doraise=True,
                    )
            else:
                raise ValueError("unsafe or unsupported check type")

    def _reject_permission_expansion(
        self, manifest: Dict[str, Any], prior: Optional[Dict[str, Any]]
    ) -> None:
        if prior is None:
            return
        rehearsal = prior.get("rehearsal", {})
        previous_network = rehearsal.get("network_access")
        previous_persistence = rehearsal.get("persistent_components")
        if previous_network is None or previous_persistence is None:
            raise ValueError("prior receipt lacks a verifiable permission contract")
        current_network = manifest["network_access"]
        old_destinations = set(previous_network.get("destinations", []))
        new_destinations = set(current_network["destinations"])
        if (
            current_network["enabled"] and not previous_network.get("enabled", False)
        ) or not new_destinations.issubset(old_destinations):
            raise ValueError("Skill update would expand network access")
        old_components = {_canonical(item) for item in previous_persistence}
        new_components = {_canonical(item) for item in manifest["persistent_components"]}
        if not new_components.issubset(old_components):
            raise ValueError("Skill update would expand persistent components")

    @staticmethod
    def _logical_tree_hash(manifest: Dict[str, Any]) -> str:
        files = [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "size": item["size"],
                "executable": item["executable"],
            }
            for item in manifest["files"]
        ]
        return _sha256(_canonical(sorted(files, key=lambda item: item["path"])))

    def _actual_tree_hash(self, root: Path) -> str:
        records = []
        files = sorted(
            (item for item in root.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(root).as_posix(),
        )
        for path in files:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(path.read_bytes()),
                    "size": path.stat().st_size,
                    "executable": (
                        False
                        if self.platform == "windows"
                        else bool(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR)
                    ),
                }
            )
        return _sha256(_canonical(records))

    def _payload_tree_hash(
        self,
        payloads: Mapping[str, bytes],
        manifest: Optional[Mapping[str, Any]] = None,
    ) -> str:
        executable = {
            item["path"]: bool(item["executable"])
            for item in (manifest or {}).get("files", [])
        }
        records = [
            {
                "path": path,
                "sha256": _sha256(payload),
                "size": len(payload),
                "executable": (
                    False if self.platform == "windows" else executable.get(path, False)
                ),
            }
            for path, payload in sorted(payloads.items())
        ]
        return _sha256(_canonical(records))

    def _save_verified_rollback(
        self, target: Path, target_binding: str, previous_hash: Optional[str]
    ) -> bool:
        if previous_hash is None:
            return False
        binding_hash = _sha256(target_binding.encode("utf-8"))
        rollback_parent = self.rollback_root / binding_hash
        candidate = rollback_parent / f".candidate-{uuid.uuid4().hex}"
        final = rollback_parent / "current"
        rollback_parent.mkdir(parents=True, exist_ok=True)
        if candidate.exists():
            shutil.rmtree(candidate)
        shutil.copytree(target, candidate, symlinks=False)
        if self._actual_tree_hash(candidate) != self._actual_tree_hash(target):
            raise ValueError("rollback copy verification failed")
        previous = rollback_parent / f".previous-{uuid.uuid4().hex}"
        if final.exists():
            os.replace(final, previous)
        os.replace(candidate, final)
        if previous.exists():
            shutil.rmtree(previous)
        _fsync_directory(rollback_parent)
        return True

    def _rollback_path(self, target_binding: str) -> Path:
        binding_hash = _sha256(target_binding.encode("utf-8"))
        return self.rollback_root / binding_hash / "current"

    def _latest_successful_receipt(
        self, target_binding: str
    ) -> Optional[Dict[str, Any]]:
        if not self.receipts_dir.exists():
            return None
        matches = []
        for path in self.receipts_dir.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ValueError("Skill receipt store contains invalid JSON")
            self._validate_receipt(value)
            if value["target_binding"] == target_binding and value["result"] == "installed":
                matches.append((value["created_at"], value))
        return max(matches, default=(None, None), key=lambda item: item[0])[1]

    def _receipt(
        self,
        prepared: Dict[str, Any],
        *,
        result: str,
        final_hash: Optional[str],
        rollback: Dict[str, Any],
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        receipt = {
            "schema_version": 1,
            "receipt_id": f"skill-{uuid.uuid4().hex}",
            "artifact_id": prepared["artifact"]["artifact_id"],
            "revision_id": prepared["revision"]["revision_id"],
            "content_sha256": prepared["package_sha256"],
            "target_node_id": self.target_node_id,
            "target_binding": prepared["target_binding"],
            "previous_installed_sha256": prepared["previous_hash"],
            "final_installed_sha256": final_hash,
            "rehearsal": {
                **prepared["rehearsal"],
                **({"error": error} if error is not None else {}),
            },
            "result": result,
            "rollback": rollback,
            "created_at": _now_iso(),
        }
        self._validate_receipt(receipt)
        return receipt

    def _validate_receipt(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("installation receipt must be an object")
        fields = {
            "schema_version", "receipt_id", "artifact_id", "revision_id",
            "content_sha256", "target_node_id", "target_binding",
            "previous_installed_sha256", "final_installed_sha256", "rehearsal",
            "result", "rollback", "created_at",
        }
        _strict_keys(value, fields, fields, "installation receipt")
        if value["schema_version"] != 1:
            raise ValueError("unsupported receipt schema_version")
        if not isinstance(value["receipt_id"], str) or not RECEIPT_ID_RE.fullmatch(
            value["receipt_id"]
        ):
            raise ValueError("invalid receipt_id")
        for field in ("artifact_id", "target_node_id", "target_binding"):
            if not isinstance(value[field], str) or len(value[field]) < (
                1 if field == "target_binding" else 3
            ):
                raise ValueError(f"invalid receipt {field}")
        if not isinstance(value["revision_id"], str) or not REVISION_RE.fullmatch(
            value["revision_id"]
        ):
            raise ValueError("invalid receipt revision_id")
        if not isinstance(value["content_sha256"], str) or not SHA256_RE.fullmatch(
            value["content_sha256"]
        ):
            raise ValueError("invalid receipt content_sha256")
        for field in ("previous_installed_sha256", "final_installed_sha256"):
            digest = value[field]
            if digest is not None and (
                not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)
            ):
                raise ValueError(f"invalid receipt {field}")
        if value["result"] not in {"installed", "failed", "rolled-back"}:
            raise ValueError("invalid receipt result")
        if not isinstance(value["rehearsal"], dict) or not isinstance(value["rollback"], dict):
            raise ValueError("receipt rehearsal/rollback must be objects")
        try:
            datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("invalid receipt created_at") from error

    def _persist_receipt(self, receipt: Dict[str, Any]) -> None:
        self._validate_receipt(receipt)
        _atomic_json(self.receipts_dir / f"{receipt['receipt_id']}.json", receipt)

    def _write_transaction(
        self,
        transaction_id: str,
        prepared: Dict[str, Any],
        *,
        status: str,
        staging: Path,
        sibling_candidate: Path,
        displaced: Path,
    ) -> Path:
        path = self.transactions_dir / f"{transaction_id}.json"
        _atomic_json(
            path,
            {
                "schema_version": 1,
                "transaction_id": transaction_id,
                "artifact_id": prepared["artifact"]["artifact_id"],
                "revision_id": prepared["revision"]["revision_id"],
                "target_binding": prepared["target_binding"],
                "target_path": str(prepared["target_path"]),
                "staging_path": str(staging),
                "sibling_candidate_path": str(sibling_candidate),
                "displaced_path": str(displaced),
                "rollback_path": str(self._rollback_path(prepared["target_binding"])),
                "had_previous": prepared["previous_hash"] is not None,
                "previous_installed_sha256": prepared["previous_hash"],
                "expected_final_sha256": prepared["actual_hash"],
                "package_sha256": prepared["package_sha256"],
                "rehearsal": prepared["rehearsal"],
                "status": status,
                "created_at": _now_iso(),
            },
        )
        return path

    @staticmethod
    def _finish_transaction(path: Path, status: str) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        value["status"] = status
        value["finished_at"] = _now_iso()
        _atomic_json(path, value)

    def recover_transactions(self) -> List[Dict[str, Any]]:
        """Restore interrupted directory switches from durable transaction state."""

        if not self.transactions_dir.exists():
            return []
        recovered = []
        for path in sorted(self.transactions_dir.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("status") != "prepared":
                continue
            self._validate_transaction(value)
            committed_receipt = self._receipt_for_transaction(value["transaction_id"])
            artifact, revision = self._registered_revision(
                value["artifact_id"], value["revision_id"]
            )
            binding = self._resolve_binding(artifact, value["target_binding"])
            target = self._resolve_target(binding)
            if str(target) != value["target_path"]:
                raise SkillInstallationError("transaction target binding changed")
            staging = self._validated_transaction_path(
                value["staging_path"], self.staging_root, "staging_path"
            )
            sibling = self._validated_sibling_path(
                value["sibling_candidate_path"], target, ".mw-candidate-"
            )
            displaced = self._validated_sibling_path(
                value["displaced_path"], target, ".mw-previous-"
            )
            rollback = self._validated_transaction_path(
                value["rollback_path"], self.rollback_root, "rollback_path"
            )
            if committed_receipt is not None:
                expected = (
                    value["expected_final_sha256"]
                    if committed_receipt["result"] == "installed"
                    else value["previous_installed_sha256"]
                )
                current = self._actual_tree_hash(target) if target.is_dir() else None
                if current != expected:
                    raise SkillInstallationError(
                        "terminal receipt and interrupted target disagree"
                    )
                if committed_receipt["result"] == "installed" and displaced.exists():
                    if displaced.is_symlink() or not displaced.is_dir():
                        raise SkillInstallationError(
                            "committed transaction displaced path is unsafe"
                        )
                    shutil.rmtree(displaced)
                for transient in (sibling, staging):
                    if transient.exists():
                        if transient.is_symlink() or not transient.is_dir():
                            raise SkillInstallationError(
                                "committed transaction transient path is unsafe"
                            )
                        shutil.rmtree(transient)
                value["status"] = committed_receipt["result"]
                value["finished_at"] = _now_iso()
                value["recovery_receipt_id"] = committed_receipt["receipt_id"]
                _atomic_json(path, value)
                recovered.append(committed_receipt)
                continue
            changed = False
            if displaced.exists():
                if displaced.is_symlink() or not displaced.is_dir():
                    raise SkillInstallationError("displaced recovery directory is unsafe")
                if target.exists():
                    if target.is_symlink() or not target.is_dir():
                        raise SkillInstallationError("interrupted target is unsafe")
                    shutil.rmtree(target)
                os.replace(displaced, target)
                changed = True
            elif value["had_previous"]:
                previous_hash = value["previous_installed_sha256"]
                current_hash = self._actual_tree_hash(target) if target.is_dir() else None
                if current_hash != previous_hash:
                    if not rollback.is_dir() or rollback.is_symlink():
                        raise SkillInstallationError(
                            "interrupted update has no verified rollback directory"
                        )
                    if target.exists():
                        if target.is_symlink() or not target.is_dir():
                            raise SkillInstallationError("interrupted target is unsafe")
                        shutil.rmtree(target)
                    shutil.copytree(rollback, target, symlinks=False)
                    if self._actual_tree_hash(target) != self._actual_tree_hash(rollback):
                        raise SkillInstallationError("crash rollback verification failed")
                    changed = True
            elif target.exists():
                if target.is_symlink() or not target.is_dir():
                    raise SkillInstallationError("interrupted new target is unsafe")
                # A fresh install can be removed only when it matches the
                # transaction's candidate; unrelated post-crash data fails closed.
                if self._actual_tree_hash(target) != value["expected_final_sha256"]:
                    raise SkillInstallationError(
                        "interrupted fresh install conflicts with current target"
                    )
                shutil.rmtree(target)
                changed = True
            for transient in (sibling, staging):
                if transient.exists():
                    if transient.is_symlink() or not transient.is_dir():
                        raise SkillInstallationError("transaction transient path is unsafe")
                    shutil.rmtree(transient)
            _fsync_directory(target.parent)
            receipt = {
                "schema_version": 1,
                "receipt_id": f"skill-{uuid.uuid4().hex}",
                "artifact_id": artifact["artifact_id"],
                "revision_id": revision["revision_id"],
                "content_sha256": value["package_sha256"],
                "target_node_id": self.target_node_id,
                "target_binding": value["target_binding"],
                "previous_installed_sha256": value["previous_installed_sha256"],
                "final_installed_sha256": value["previous_installed_sha256"],
                "rehearsal": {
                    **value["rehearsal"],
                    "recovery": "startup-crash-recovery",
                },
                "result": "rolled-back",
                "rollback": {
                    "available": rollback.is_dir(),
                    "attempted": changed,
                    "succeeded": True,
                },
                "created_at": _now_iso(),
            }
            self._persist_receipt(receipt)
            value["status"] = "rolled-back"
            value["finished_at"] = _now_iso()
            value["recovery_receipt_id"] = receipt["receipt_id"]
            _atomic_json(path, value)
            recovered.append(receipt)
        return recovered

    def _receipt_for_transaction(
        self, transaction_id: str
    ) -> Optional[Dict[str, Any]]:
        if not self.receipts_dir.exists():
            return None
        matches = []
        for path in self.receipts_dir.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            self._validate_receipt(value)
            if value["rehearsal"].get("transaction_id") == transaction_id:
                matches.append(value)
        if len(matches) > 1:
            raise SkillInstallationError(
                "transaction has multiple terminal installation receipts"
            )
        return matches[0] if matches else None

    @staticmethod
    def _validated_transaction_path(value: Any, root: Path, label: str) -> Path:
        path = Path(_string(value, label))
        root_resolved = root.resolve(strict=False)
        try:
            path.resolve(strict=False).relative_to(root_resolved)
        except ValueError as error:
            raise SkillInstallationError(f"{label} escapes its transaction root") from error
        return path

    @staticmethod
    def _validated_sibling_path(value: Any, target: Path, marker: str) -> Path:
        path = Path(_string(value, "transaction sibling path"))
        if path.parent != target.parent or not path.name.startswith(f".{target.name}{marker}"):
            raise SkillInstallationError("transaction sibling path is invalid")
        return path

    @staticmethod
    def _validate_transaction(value: Dict[str, Any]) -> None:
        required = {
            "schema_version", "transaction_id", "artifact_id", "revision_id",
            "target_binding", "target_path", "staging_path",
            "sibling_candidate_path", "displaced_path", "rollback_path",
            "had_previous", "previous_installed_sha256", "expected_final_sha256",
            "package_sha256", "rehearsal", "status", "created_at",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise SkillInstallationError("invalid Skill transaction record")
        if value["schema_version"] != 1 or value["status"] != "prepared":
            raise SkillInstallationError("unsupported Skill transaction state")
        if type(value["had_previous"]) is not bool:
            raise SkillInstallationError("invalid Skill transaction previous-state flag")
        for field in ("expected_final_sha256", "package_sha256"):
            if not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field]):
                raise SkillInstallationError(f"invalid Skill transaction {field}")
        previous = value["previous_installed_sha256"]
        if previous is not None and (
            not isinstance(previous, str) or not SHA256_RE.fullmatch(previous)
        ):
            raise SkillInstallationError("invalid Skill transaction previous hash")
        if not isinstance(value["rehearsal"], dict):
            raise SkillInstallationError("invalid Skill transaction rehearsal")

    def _assert_safe_target_parent(
        self, binding: Dict[str, Any], target: Path
    ) -> None:
        resolved = self._resolve_target(binding)
        if resolved != target:
            raise ValueError("target binding changed during installation")
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise ValueError("existing Skill target is not a real directory")

    def _after_switch(self, target: Path) -> None:
        """Test seam after directory switch and before final verification."""

    def _after_rollback_saved(self, target: Path) -> None:
        """Test seam after durable rollback creation and before transaction mutation."""
