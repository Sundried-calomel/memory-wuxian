#!/usr/bin/env python3
"""Node-local environment bindings and explicit read-only discovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional

from memory_environment import (
    EnvironmentRegistry,
    _strict_keys,
    canonical_bytes as _canonical_bytes,
    sha256_bytes as _sha256_bytes,
)
from platform_atomic import ParentSync, atomic_replace_bytes, sync_directory
from platform_lock import exclusive_lock
from platform_paths import is_link_like


SCHEMA_VERSION = 1
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
REVISION_ID_RE = re.compile(r"^rev:[0-9a-f]{64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLATFORMS = {"macos", "windows", "linux"}
ROOT_ROLES = {"global-rules", "global-skills"}
DISCOVERY_ROLES = ROOT_ROLES | {"project"}
CLASSIFICATIONS = {"canonical", "project-local", "generated", "excluded"}
INSTALL_STRATEGIES = {"managed-block", "whole-file", "none"}
RULE_EXTENSIONS = {".md", ".txt"}
MAX_FRONTMATTER_BYTES = 64 * 1024


def _current_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _record_hash(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _fsync_directory(path: Path) -> None:
    sync_directory(path, policy=ParentSync.BEST_EFFORT)


def _atomic_write_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    atomic_replace_bytes(path, payload, parent_sync=ParentSync.BEST_EFFORT)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _required_string(value: Any, label: str, pattern: Optional[re.Pattern] = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError(f"{label}: expected non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(f"{label}: invalid format")
    return value


def _nullable_hash(value: Any, label: str, pattern: re.Pattern) -> Optional[str]:
    if value is None:
        return None
    return _required_string(value, label, pattern)


def _safe_relative(value: Any, label: str) -> str:
    text = _required_string(value, label)
    if "\\" in text:
        raise ValueError(f"{label}: use POSIX separators")
    path = PurePosixPath(text)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError(f"{label}: path traversal is forbidden")
    if path.as_posix() != text:
        raise ValueError(f"{label}: path is not normalized")
    return text


def _validate_absolute_syntax(value: Any, platform_name: str, label: str) -> str:
    text = _required_string(value, label)
    if "\x00" in text:
        raise ValueError(f"{label}: NUL is forbidden")
    if platform_name == "windows":
        path = PureWindowsPath(text)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label}: expected an absolute Windows path")
    else:
        path = PurePosixPath(text)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{label}: expected an absolute POSIX path")
    return text


class EnvironmentBindingRegistry:
    """Persist explicit node-local roots and expose verified target mappings."""

    def __init__(
        self,
        registry: EnvironmentRegistry,
        *,
        node_id: str,
        platform_name: Optional[str] = None,
    ):
        self.registry = registry
        self.node_id = _required_string(node_id, "node_id", NODE_ID_RE)
        self.platform = platform_name or _current_platform()
        if self.platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        self.root = self.registry.root
        self.bindings_dir = self.root / "bindings"
        self.path = self.bindings_dir / f"{self.node_id}.json"
        self.lock_path = self.registry.locks_dir / "environment-bindings.lock"
        self.transactions_dir = self.registry.transactions_dir / "bindings"

    def status(self) -> Dict[str, Any]:
        self._assert_storage_paths()
        if not self.path.exists():
            return {
                "initialized": False,
                "node_id": self.node_id,
                "platform": self.platform,
                "path": str(self.path),
            }
        state = self._read_validated()
        return {
            "initialized": True,
            "node_id": self.node_id,
            "platform": self.platform,
            "generation": state["generation"],
            "roots": len(state["roots"]),
            "projects": len(state["projects"]),
            "rule_bindings": len(state["rule_bindings"]),
            "skill_bindings": len(state["skill_bindings"]),
            "path": str(self.path),
        }

    def register_root(
        self,
        *,
        root_id: str,
        role: str,
        owner: str,
        root: str | Path,
        apply: bool = False,
        base_binding_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        if role not in ROOT_ROLES:
            raise ValueError("root role must be global-rules or global-skills")
        record = {
            "root_id": _required_string(root_id, "root_id", LOCAL_ID_RE),
            "role": role,
            "owner": _required_string(owner, "owner"),
            "root": self._validate_root(root),
            "platform": self.platform,
        }
        return self._upsert(
            collection="roots",
            identity_key="root_id",
            record=record,
            apply=apply,
            base_binding_sha256=base_binding_sha256,
        )

    def register_project(
        self,
        *,
        project_id: str,
        apply: bool = False,
        base_binding_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        project = self._project(project_id)
        self._validate_project_root(project)
        return self._upsert(
            collection="projects",
            identity_key="project_id",
            record={"project_id": project_id},
            apply=apply,
            base_binding_sha256=base_binding_sha256,
        )

    def register_rule_binding(
        self,
        binding: Mapping[str, Any],
        *,
        apply: bool = False,
        base_binding_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidate = dict(binding)
        registered_root = self._registered_root(
            _required_string(candidate.get("root_id"), "root_id", LOCAL_ID_RE),
            "global-rules",
        )
        candidate.setdefault("root", registered_root["root"])
        record = self._validate_rule_binding(candidate)
        if record["root"] != registered_root["root"]:
            raise ValueError("rule binding root does not match registered root")
        return self._upsert(
            collection="rule_bindings",
            identity_key="binding_id",
            record=record,
            apply=apply,
            base_binding_sha256=base_binding_sha256,
        )

    def register_project_rule_binding(
        self,
        binding: Mapping[str, Any],
        *,
        apply: bool = False,
        base_binding_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidate = dict(binding)
        project_id = _required_string(
            candidate.get("project_id"), "project_id", LOCAL_ID_RE
        )
        project_binding_id = _required_string(
            candidate.get("project_binding_id"),
            "project_binding_id",
            LOCAL_ID_RE,
        )
        self._registered_project(project_id)
        project = self._project(project_id)
        matches = [
            item
            for item in project["rule_bindings"]
            if item["binding_id"] == project_binding_id
        ]
        if len(matches) != 1:
            raise ValueError("project rule binding is not registered")
        record = self._validate_rule_binding(candidate)
        return self._upsert(
            collection="rule_bindings",
            identity_key="binding_id",
            record=record,
            apply=apply,
            base_binding_sha256=base_binding_sha256,
        )

    def register_skill_binding(
        self,
        binding: Mapping[str, Any],
        *,
        apply: bool = False,
        base_binding_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidate = dict(binding)
        registered_root = self._registered_root(
            _required_string(
                candidate.get("target_root_id"), "target_root_id", LOCAL_ID_RE
            ),
            "global-skills",
        )
        candidate.setdefault("target_root", registered_root["root"])
        record = self._validate_skill_binding(candidate)
        if record["target_root"] != registered_root["root"]:
            raise ValueError("Skill binding target_root does not match registered root")
        return self._upsert(
            collection="skill_bindings",
            identity_key="binding_id",
            record=record,
            apply=apply,
            base_binding_sha256=base_binding_sha256,
        )

    def discover(
        self,
        *,
        role: str,
        root: str | Path,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect one explicit root and return a read-only proposal."""

        if role not in DISCOVERY_ROLES:
            raise ValueError("unsupported discovery role")
        if role == "project":
            if project_id is None:
                raise ValueError("project discovery requires project_id")
            self._registered_project(project_id)
            project = self._project(project_id)
            expected = self._validate_project_root(project)
            supplied = self._validate_root(root)
            if supplied != expected:
                raise ValueError("project root must match EnvironmentRegistry local_root")
            return self._discover_project(project)

        expected_role = role
        supplied = self._validate_root(root)
        registered = [
            item
            for item in self._read_validated(allow_missing=True)["roots"]
            if item["role"] == expected_role and item["root"] == supplied
        ]
        if len(registered) != 1:
            raise ValueError("root is not explicitly registered for this role")
        if role == "global-rules":
            return self._discover_rules(Path(supplied), scope="global")
        return self._discover_skills(Path(supplied), scope="global")

    def scan(
        self,
        *,
        role: str,
        root: str | Path,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.discover(role=role, root=root, project_id=project_id)

    def get_rule_bindings(self) -> List[Dict[str, Any]]:
        state = self._read_validated()
        roots = {item["root_id"]: item for item in state["roots"]}
        mappings: List[Dict[str, Any]] = []
        for binding in state["rule_bindings"]:
            if binding["scope"] == "project":
                self._registered_project(binding["project_id"])
                project = self._project(binding["project_id"])
                project_bindings = [
                    item
                    for item in project["rule_bindings"]
                    if item["binding_id"] == binding["project_binding_id"]
                ]
                if len(project_bindings) != 1:
                    raise ValueError("project rule binding is not registered")
                project_binding = project_bindings[0]
                project_root = Path(self._validate_project_root(project))
                target = self._resolve_target(
                    project_root, project_binding["relative_path"]
                )
                mappings.append(
                    {
                        **binding,
                        "root": str(project_root),
                        "relative_path": project_binding["relative_path"],
                        "classification": project_binding["classification"],
                        "install_strategy": project_binding["install_strategy"],
                        "managed_block_id": project_binding["managed_block_id"],
                        "target_path": str(target),
                    }
                )
                continue
            root = roots.get(binding["root_id"])
            if root is None or root["role"] != "global-rules":
                raise ValueError("rule binding references an unknown rule root")
            target = self._resolve_target(Path(root["root"]), binding["relative_path"])
            mappings.append({**binding, "root": root["root"], "target_path": str(target)})
        return sorted(mappings, key=lambda item: (item["scope"], item["binding_id"]))

    def get_skill_bindings(self) -> List[Dict[str, Any]]:
        state = self._read_validated()
        roots = {item["root_id"]: item for item in state["roots"]}
        mappings: List[Dict[str, Any]] = []
        for binding in state["skill_bindings"]:
            root = roots.get(binding["target_root_id"])
            if root is None or root["role"] != "global-skills":
                raise ValueError("skill binding references an unknown Skill root")
            target = self._resolve_target(Path(root["root"]), binding["skill_id"])
            mappings.append(
                {**binding, "target_root": root["root"], "target_path": str(target)}
            )
        for activation in state["projects"]:
            project = self._project(activation["project_id"])
            project_root = self._validate_project_root(project)
            for binding in project["skill_bindings"]:
                if binding["enabled"]:
                    mappings.append(
                        {
                            **binding,
                            "binding_id": (
                                f"{project['project_id']}.{binding['skill_id']}"
                            ),
                            "scope": "project",
                            "owner": f"project:{project['project_id']}",
                            "project_id": project["project_id"],
                            "project_root": project_root,
                            "platform": self.platform,
                        }
                    )
        return sorted(mappings, key=lambda item: (item["scope"], item["binding_id"]))

    def recover_transactions(self) -> int:
        self._assert_storage_paths()
        with exclusive_lock(self.lock_path):
            return self._recover_transactions_unlocked()

    def _recover_transactions_unlocked(self) -> int:
        self._assert_storage_paths()
        if not self.transactions_dir.exists():
            return 0
        recovered = 0
        for marker_path in sorted(self.transactions_dir.glob("*.json")):
            if is_link_like(marker_path):
                raise ValueError("binding transaction marker must not be a symlink")
            marker = _read_json(marker_path)
            self._validate_transaction(marker)
            current_hash = (
                _sha256_bytes(self.path.read_bytes()) if self.path.exists() else None
            )
            if current_hash == marker["candidate_file_sha256"]:
                marker_path.unlink()
                recovered += 1
                continue
            previous = marker["previous_registry"]
            previous_hash = marker["previous_file_sha256"]
            if current_hash == previous_hash:
                marker_path.unlink()
                recovered += 1
                continue
            if current_hash is not None:
                raise ValueError("binding transaction state is ambiguous")
            if previous is not None:
                _atomic_write_json(self.path, previous)
            marker_path.unlink()
            recovered += 1
        return recovered

    def _upsert(
        self,
        *,
        collection: str,
        identity_key: str,
        record: Dict[str, Any],
        apply: bool,
        base_binding_sha256: Optional[str],
    ) -> Dict[str, Any]:
        state = self._read_validated(allow_missing=True)
        existing = next(
            (
                item
                for item in state[collection]
                if item[identity_key] == record[identity_key]
            ),
            None,
        )
        record_sha256 = _record_hash(record)
        if existing == record:
            return {
                "status": "no-change",
                identity_key: record[identity_key],
                "binding_sha256": record_sha256,
            }
        if existing is None:
            if base_binding_sha256 is not None:
                raise ValueError("new binding must not declare a base")
            action = "create"
        else:
            current_hash = _record_hash(existing)
            if base_binding_sha256 != current_hash:
                raise ValueError("binding base conflict")
            action = "update"
        result = {
            "status": "preview",
            "action": action,
            identity_key: record[identity_key],
            "binding_sha256": record_sha256,
            "previous_binding_sha256": (
                _record_hash(existing) if existing is not None else None
            ),
            "record": json.loads(json.dumps(record)),
        }
        if not apply:
            return result

        with exclusive_lock(self.lock_path):
            self._assert_storage_paths()
            self.bindings_dir.mkdir(parents=True, exist_ok=True)
            self.transactions_dir.mkdir(parents=True, exist_ok=True)
            self._recover_transactions_unlocked()
            current = self._read_validated(allow_missing=True)
            current_existing = next(
                (
                    item
                    for item in current[collection]
                    if item[identity_key] == record[identity_key]
                ),
                None,
            )
            if current_existing == record:
                return {
                    "status": "no-change",
                    identity_key: record[identity_key],
                    "binding_sha256": record_sha256,
                }
            if existing is None:
                if current_existing is not None:
                    raise ValueError("binding appeared after preview")
            elif (
                current_existing is None
                or _record_hash(current_existing) != base_binding_sha256
            ):
                raise ValueError("binding base conflict")
            retained = [
                item
                for item in current[collection]
                if item[identity_key] != record[identity_key]
            ]
            current[collection] = sorted(
                [*retained, record], key=lambda item: item[identity_key]
            )
            current["generation"] += 1
            self._validate_state(current)
            transaction = {
                "schema_version": 1,
                "transaction_id": f"binding-{uuid.uuid4().hex}",
                "node_id": self.node_id,
                "previous_registry": (
                    self._read_validated() if self.path.exists() else None
                ),
                "previous_file_sha256": (
                    _sha256_bytes(self.path.read_bytes())
                    if self.path.exists()
                    else None
                ),
                "candidate_file_sha256": _sha256_bytes(
                    (
                        json.dumps(
                            current,
                            ensure_ascii=False,
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n"
                    ).encode("utf-8")
                ),
            }
            marker = self.transactions_dir / f"{transaction['transaction_id']}.json"
            _atomic_write_json(marker, transaction)
            self._before_commit()
            _atomic_write_json(self.path, current)
            if _sha256_bytes(self.path.read_bytes()) != transaction[
                "candidate_file_sha256"
            ]:
                raise ValueError("binding registry final hash mismatch")
            marker.unlink()
            _fsync_directory(marker.parent)
            return {**result, "status": "registered", "generation": current["generation"]}

    def _read_validated(self, *, allow_missing: bool = False) -> Dict[str, Any]:
        self._assert_storage_paths()
        if not self.path.exists():
            if not allow_missing:
                raise ValueError("binding registry is not initialized")
            return self._empty_state()
        state = _read_json(self.path)
        return self._validate_state(state)

    def _assert_storage_paths(self) -> None:
        for directory, label in (
            (self.registry.locks_dir, "environment lock directory"),
            (self.bindings_dir, "binding directory"),
            (self.transactions_dir, "binding transaction directory"),
        ):
            if directory.exists() and is_link_like(directory):
                raise ValueError(f"{label} must not be a symlink")
        if self.path.exists() and is_link_like(self.path):
            raise ValueError("binding registry must not be a symlink")
        if self.lock_path.exists() and is_link_like(self.lock_path):
            raise ValueError("binding lock file must not be a symlink")

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "node_id": self.node_id,
            "platform": self.platform,
            "generation": 0,
            "roots": [],
            "projects": [],
            "rule_bindings": [],
            "skill_bindings": [],
        }

    def _validate_state(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("binding registry: expected object")
        fields = {
            "schema_version",
            "node_id",
            "platform",
            "generation",
            "roots",
            "projects",
            "rule_bindings",
            "skill_bindings",
        }
        _strict_keys(value, fields, fields, "binding registry")
        if value["schema_version"] != SCHEMA_VERSION:
            raise ValueError("binding registry: unsupported schema_version")
        if value["node_id"] != self.node_id:
            raise ValueError("binding registry: node_id mismatch")
        if value["platform"] != self.platform:
            raise ValueError("binding registry: platform mismatch")
        if not isinstance(value["generation"], int) or isinstance(
            value["generation"], bool
        ) or value["generation"] < 0:
            raise ValueError("binding registry: invalid generation")
        for name in ("roots", "projects", "rule_bindings", "skill_bindings"):
            if not isinstance(value[name], list):
                raise ValueError(f"binding registry.{name}: expected array")
        roots = [self._validate_root_record(item) for item in value["roots"]]
        projects = [self._validate_project_activation(item) for item in value["projects"]]
        rules = [self._validate_rule_binding(item) for item in value["rule_bindings"]]
        skills = [self._validate_skill_binding(item) for item in value["skill_bindings"]]
        self._assert_unique(roots, "root_id", "root")
        self._assert_unique(projects, "project_id", "project")
        self._assert_unique(rules, "binding_id", "rule binding")
        self._assert_unique(skills, "binding_id", "skill binding")
        combined_binding_ids = [
            item["binding_id"] for item in [*rules, *skills]
        ]
        if len(combined_binding_ids) != len(set(combined_binding_ids)):
            raise ValueError("duplicate binding ID across rule and Skill bindings")
        root_locations = [
            (item["role"], item["root"]) for item in roots
        ]
        if len(root_locations) != len(set(root_locations)):
            raise ValueError("duplicate registered root path and role")
        root_map = {item["root_id"]: item for item in roots}
        for rule in rules:
            if rule["scope"] == "project":
                self._project(rule["project_id"])
                continue
            root = root_map.get(rule["root_id"])
            if root is None or root["role"] != "global-rules":
                raise ValueError("rule binding references an unknown rule root")
            if rule["root"] != root["root"]:
                raise ValueError("rule binding root does not match registered root")
        for skill in skills:
            root = root_map.get(skill["target_root_id"])
            if root is None or root["role"] != "global-skills":
                raise ValueError("skill binding references an unknown Skill root")
            if skill["target_root"] != root["root"]:
                raise ValueError(
                    "Skill binding target_root does not match registered root"
                )
        for project in projects:
            self._project(project["project_id"])
        normalized = {
            **value,
            "roots": roots,
            "projects": projects,
            "rule_bindings": rules,
            "skill_bindings": skills,
        }
        return json.loads(json.dumps(normalized))

    def _validate_root_record(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("root: expected object")
        fields = {"root_id", "role", "owner", "root", "platform"}
        _strict_keys(value, fields, fields, "root")
        if value["role"] not in ROOT_ROLES:
            raise ValueError("root: unsupported role")
        if value["platform"] != self.platform:
            raise ValueError("root: platform mismatch")
        normalized = {
            "root_id": _required_string(value["root_id"], "root_id", LOCAL_ID_RE),
            "role": value["role"],
            "owner": _required_string(value["owner"], "owner"),
            "root": _validate_absolute_syntax(
                value["root"], self.platform, "root.root"
            ),
            "platform": value["platform"],
        }
        if self.platform == _current_platform():
            normalized["root"] = self._validate_root(normalized["root"])
        return normalized

    def _validate_project_activation(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("project activation: expected object")
        _strict_keys(value, {"project_id"}, {"project_id"}, "project activation")
        project_id = _required_string(
            value["project_id"], "project_id", LOCAL_ID_RE
        )
        return {"project_id": project_id}

    def _validate_rule_binding(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("rule binding: expected object")
        common_fields = {
            "binding_id",
            "scope",
            "owner",
            "platform",
            "installed_revision_id",
            "installed_content_sha256",
            "base_revision_id",
            "base_content_sha256",
        }
        scope = value.get("scope")
        if scope == "global":
            fields = common_fields | {
                "root_id",
                "root",
                "relative_path",
                "classification",
                "install_strategy",
                "managed_block_id",
            }
            _strict_keys(value, fields, fields, "rule binding")
            if value["classification"] not in CLASSIFICATIONS:
                raise ValueError("rule binding: unsupported classification")
            if value["install_strategy"] not in INSTALL_STRATEGIES:
                raise ValueError("rule binding: unsupported install_strategy")
            managed = value["managed_block_id"]
            if managed is not None:
                managed = _required_string(managed, "managed_block_id", LOCAL_ID_RE)
            if value["install_strategy"] == "managed-block" and managed is None:
                raise ValueError("managed-block requires managed_block_id")
            if value["install_strategy"] != "managed-block" and managed is not None:
                raise ValueError("managed_block_id requires managed-block strategy")
            specific = {
                "root_id": _required_string(
                    value["root_id"], "root_id", LOCAL_ID_RE
                ),
                "root": _validate_absolute_syntax(
                    value["root"], self.platform, "rule root"
                ),
                "relative_path": _safe_relative(
                    value["relative_path"], "rule relative_path"
                ),
                "classification": value["classification"],
                "install_strategy": value["install_strategy"],
                "managed_block_id": managed,
            }
        elif scope == "project":
            fields = common_fields | {"project_id", "project_binding_id"}
            _strict_keys(value, fields, fields, "project rule binding")
            specific = {
                "project_id": _required_string(
                    value["project_id"], "project_id", LOCAL_ID_RE
                ),
                "project_binding_id": _required_string(
                    value["project_binding_id"],
                    "project_binding_id",
                    LOCAL_ID_RE,
                ),
            }
        else:
            raise ValueError("persisted rule binding scope must be global or project")
        normalized = {
            "binding_id": _required_string(
                value["binding_id"], "binding_id", LOCAL_ID_RE
            ),
            "scope": scope,
            "owner": _required_string(value["owner"], "owner"),
            **specific,
            "platform": value["platform"],
            "installed_revision_id": _nullable_hash(
                value["installed_revision_id"],
                "installed_revision_id",
                REVISION_ID_RE,
            ),
            "installed_content_sha256": _nullable_hash(
                value["installed_content_sha256"],
                "installed_content_sha256",
                SHA256_RE,
            ),
            "base_revision_id": _nullable_hash(
                value["base_revision_id"], "base_revision_id", REVISION_ID_RE
            ),
            "base_content_sha256": _nullable_hash(
                value["base_content_sha256"], "base_content_sha256", SHA256_RE
            ),
        }
        if normalized["platform"] != self.platform:
            raise ValueError("rule binding: platform mismatch")
        self._validate_revision_hash_pairs(normalized, "rule binding")
        return normalized

    def _validate_skill_binding(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("skill binding: expected object")
        fields = {
            "binding_id",
            "scope",
            "owner",
            "skill_id",
            "target_root_id",
            "target_root",
            "platform",
            "installed_revision_id",
            "installed_content_sha256",
            "base_revision_id",
            "base_content_sha256",
        }
        _strict_keys(value, fields, fields, "skill binding")
        if value["scope"] != "global":
            raise ValueError("persisted Skill binding scope must be global")
        normalized = {
            "binding_id": _required_string(
                value["binding_id"], "binding_id", LOCAL_ID_RE
            ),
            "scope": "global",
            "owner": _required_string(value["owner"], "owner"),
            "skill_id": _required_string(
                value["skill_id"], "skill_id", SKILL_ID_RE
            ),
            "target_root_id": _required_string(
                value["target_root_id"], "target_root_id", LOCAL_ID_RE
            ),
            "target_root": _validate_absolute_syntax(
                value["target_root"], self.platform, "Skill target_root"
            ),
            "platform": value["platform"],
            "installed_revision_id": _nullable_hash(
                value["installed_revision_id"],
                "installed_revision_id",
                REVISION_ID_RE,
            ),
            "installed_content_sha256": _nullable_hash(
                value["installed_content_sha256"],
                "installed_content_sha256",
                SHA256_RE,
            ),
            "base_revision_id": _nullable_hash(
                value["base_revision_id"], "base_revision_id", REVISION_ID_RE
            ),
            "base_content_sha256": _nullable_hash(
                value["base_content_sha256"], "base_content_sha256", SHA256_RE
            ),
        }
        if normalized["platform"] != self.platform:
            raise ValueError("skill binding: platform mismatch")
        self._validate_revision_hash_pairs(normalized, "skill binding")
        return normalized

    def _registered_root(self, root_id: str, role: str) -> Dict[str, Any]:
        matches = [
            item
            for item in self._read_validated()["roots"]
            if item["root_id"] == root_id and item["role"] == role
        ]
        if len(matches) != 1:
            raise ValueError("target root is not explicitly registered")
        return matches[0]

    def _registered_project(self, project_id: str) -> None:
        matches = [
            item
            for item in self._read_validated()["projects"]
            if item["project_id"] == project_id
        ]
        if len(matches) != 1:
            raise ValueError("project is not explicitly registered on this node")

    def _project(self, project_id: str) -> Dict[str, Any]:
        matches = [
            item for item in self.registry.projects() if item["project_id"] == project_id
        ]
        if len(matches) != 1:
            raise ValueError(f"unknown project: {project_id}")
        if not matches[0]["active"]:
            raise ValueError(f"project is inactive: {project_id}")
        return matches[0]

    def _validate_project_root(self, project: Mapping[str, Any]) -> str:
        root = project.get("local_root")
        if root is None:
            raise ValueError("project has no local_root")
        return self._validate_root(root)

    def _validate_root(self, root: str | Path) -> str:
        text = _validate_absolute_syntax(str(root), self.platform, "root")
        if self.platform != _current_platform():
            return text
        path = Path(text)
        if is_link_like(path):
            raise ValueError("symlink root is forbidden")
        if not path.is_dir():
            raise ValueError("root must be an existing directory")
        resolved = path.resolve(strict=True)
        return str(resolved)

    def _resolve_target(self, root: Path, relative: str) -> Path:
        normalized = _safe_relative(relative, "target relative_path")
        if self.platform != _current_platform():
            raise ValueError("foreign-platform target is inactive on this node")
        candidate = root.joinpath(*PurePosixPath(normalized).parts)
        if is_link_like(root):
            raise ValueError("symlink root is forbidden")
        root_resolved = root.resolve(strict=True)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as error:
            raise ValueError("target path escapes registered root") from error
        if is_link_like(candidate):
            raise ValueError("symlink target is forbidden")
        parent = candidate.parent
        while parent != root and parent != parent.parent:
            if parent.exists() and is_link_like(parent):
                raise ValueError("symlink parent is forbidden")
            parent = parent.parent
        return candidate

    def _discover_rules(self, root: Path, *, scope: str) -> Dict[str, Any]:
        proposed, ambiguous, excluded = [], [], []
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            item = {"name": path.name, "path": str(path)}
            if is_link_like(path):
                excluded.append({**item, "reason": "symlink"})
            elif not path.is_file():
                excluded.append({**item, "reason": "non-recursive"})
            elif path.suffix.lower() not in RULE_EXTENSIONS:
                excluded.append({**item, "reason": "unsupported-extension"})
            else:
                metadata = {
                    **item,
                    "scope": scope,
                    "size": path.stat().st_size,
                    "sha256": self._hash_regular_file(path),
                }
                if path.name in {"AGENTS.md", "PROJECT_AGENTS.md"}:
                    proposed.append(metadata)
                else:
                    ambiguous.append(
                        {**metadata, "reason": "explicit classification required"}
                    )
        return {
            "status": "preview",
            "role": f"{scope}-rules",
            "root": str(root),
            "proposed": proposed,
            "ambiguous": ambiguous,
            "excluded": excluded,
        }

    def _discover_skills(self, root: Path, *, scope: str) -> Dict[str, Any]:
        proposed, ambiguous, excluded = [], [], []
        for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            item = {"name": child.name, "path": str(child)}
            if is_link_like(child):
                excluded.append({**item, "reason": "symlink"})
            elif not child.is_dir():
                excluded.append({**item, "reason": "not-a-directory"})
            else:
                skill_file = child / "SKILL.md"
                if is_link_like(skill_file) or not skill_file.is_file():
                    excluded.append({**item, "reason": "missing-regular-SKILL.md"})
                    continue
                frontmatter, frontmatter_bytes = self._read_skill_frontmatter(
                    skill_file
                )
                skill_id = frontmatter.get("name")
                candidate = {
                    **item,
                    "scope": scope,
                    "skill_id": skill_id,
                    "frontmatter": frontmatter,
                    "frontmatter_sha256": _sha256_bytes(frontmatter_bytes),
                }
                if isinstance(skill_id, str) and SKILL_ID_RE.fullmatch(skill_id):
                    proposed.append(candidate)
                else:
                    ambiguous.append(
                        {**candidate, "reason": "valid frontmatter name required"}
                    )
        return {
            "status": "preview",
            "role": f"{scope}-skills",
            "root": str(root),
            "proposed": proposed,
            "ambiguous": ambiguous,
            "excluded": excluded,
        }

    def _discover_project(self, project: Mapping[str, Any]) -> Dict[str, Any]:
        root = Path(self._validate_project_root(project))
        proposed, ambiguous, excluded = [], [], []
        for binding in project["rule_bindings"]:
            target = self._resolve_target(root, binding["relative_path"])
            if is_link_like(target):
                excluded.append(
                    {"binding_id": binding["binding_id"], "reason": "symlink"}
                )
            elif not target.is_file():
                excluded.append(
                    {"binding_id": binding["binding_id"], "reason": "missing-file"}
                )
            else:
                proposed.append(
                    {
                        **binding,
                        "scope": "project",
                        "project_id": project["project_id"],
                        "path": str(target),
                        "size": target.stat().st_size,
                        "sha256": self._hash_regular_file(target),
                    }
                )
        for binding in project["skill_bindings"]:
            destination = excluded if not binding["enabled"] else ambiguous
            destination.append(
                {
                    **binding,
                    "scope": "project",
                    "project_id": project["project_id"],
                    "reason": (
                        "disabled"
                        if not binding["enabled"]
                        else "project Skill target path is not declared"
                    ),
                }
            )
        return {
            "status": "preview",
            "role": "project",
            "project_id": project["project_id"],
            "root": str(root),
            "proposed": proposed,
            "ambiguous": ambiguous,
            "excluded": excluded,
        }

    @staticmethod
    def _hash_regular_file(path: Path) -> str:
        if is_link_like(path) or not path.is_file():
            raise ValueError("discovery target must be a regular file")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_skill_frontmatter(path: Path) -> tuple[Dict[str, str], bytes]:
        consumed = bytearray()
        with path.open("rb") as handle:
            first = handle.readline(MAX_FRONTMATTER_BYTES + 1)
            consumed.extend(first)
            if first.rstrip(b"\r\n") != b"---":
                return {}, bytes(consumed)
            while len(consumed) <= MAX_FRONTMATTER_BYTES:
                line = handle.readline(MAX_FRONTMATTER_BYTES - len(consumed) + 1)
                if not line:
                    return {}, bytes(consumed)
                consumed.extend(line)
                if line.rstrip(b"\r\n") == b"---":
                    break
            else:
                raise ValueError("Skill frontmatter exceeds discovery limit")
        if len(consumed) > MAX_FRONTMATTER_BYTES:
            raise ValueError("Skill frontmatter exceeds discovery limit")
        text = bytes(consumed).decode("utf-8", errors="strict")
        lines = text.splitlines()
        values: Dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                return values, bytes(consumed)
            if ":" not in line or line[:1].isspace():
                continue
            key, raw = line.split(":", 1)
            key = key.strip()
            if key in {"name", "description"}:
                values[key] = raw.strip().strip("\"'")
        return {}, bytes(consumed)

    @staticmethod
    def _validate_revision_hash_pairs(value: Mapping[str, Any], label: str) -> None:
        for prefix in ("installed", "base"):
            revision = value[f"{prefix}_revision_id"]
            content_hash = value[f"{prefix}_content_sha256"]
            if (revision is None) != (content_hash is None):
                raise ValueError(
                    f"{label}: {prefix} revision and hash must be recorded together"
                )

    @staticmethod
    def _assert_unique(
        records: Iterable[Mapping[str, Any]], key: str, label: str
    ) -> None:
        values = [item[key] for item in records]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {label} ID")

    def _validate_transaction(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("binding transaction: expected object")
        fields = {
            "schema_version",
            "transaction_id",
            "node_id",
            "previous_registry",
            "previous_file_sha256",
            "candidate_file_sha256",
        }
        _strict_keys(value, fields, fields, "binding transaction")
        if value["schema_version"] != 1 or value["node_id"] != self.node_id:
            raise ValueError("binding transaction identity mismatch")
        _required_string(value["transaction_id"], "transaction_id")
        _required_string(
            value["candidate_file_sha256"], "candidate_file_sha256", SHA256_RE
        )
        if value["previous_file_sha256"] is not None:
            _required_string(
                value["previous_file_sha256"],
                "previous_file_sha256",
                SHA256_RE,
            )
        if value["previous_registry"] is not None:
            self._validate_state(value["previous_registry"])
            expected = _sha256_bytes(
                (
                    json.dumps(
                        value["previous_registry"],
                        ensure_ascii=False,
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8")
            )
            if value["previous_file_sha256"] != expected:
                raise ValueError("binding transaction previous hash mismatch")
        elif value["previous_file_sha256"] is not None:
            raise ValueError("binding transaction has hash without previous registry")

    def _before_commit(self) -> None:
        """Test hook immediately before the binding registry commit point."""
