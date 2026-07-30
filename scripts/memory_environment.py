"""Strict storage primitives for the Memory無限 2.0 Environment Registry.

The module intentionally provides no CLI, installation, synchronization,
dashboard, or AI behavior. Only explicitly supplied manifests are accepted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

from platform_lock import exclusive_lock
from platform_paths import is_link_like

SCHEMA_VERSION = 1
OBJECT_CLASSES = {
    "global-rule",
    "project-rule",
    "global-skill",
    "project-skill",
    "global-runtime-contract",
}
SCOPES = {"global", "project"}
PLATFORMS = {"macos", "windows", "linux"}
LIFECYCLE_STATES = {
    "discovered",
    "staged",
    "installed",
    "superseded",
    "conflict",
    "withdrawn",
}
RULE_CLASSIFICATIONS = {"canonical", "project-local", "generated", "excluded"}
RULE_INSTALL_STRATEGIES = {"managed-block", "whole-file", "none"}
ARTIFACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{2,191}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
BINDING_ID_RE = PROJECT_ID_RE
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_ID_RE = re.compile(r"^rev:[0-9a-f]{64}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def revision_id_for(revision: Dict[str, Any]) -> str:
    payload = dict(revision)
    payload.pop("revision_id", None)
    return f"rev:{sha256_bytes(canonical_bytes(payload))}"


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_keys(
    value: Dict[str, Any], allowed: Iterable[str], required: Iterable[str], label: str
) -> None:
    allowed_set, required_set = set(allowed), set(required)
    unknown, missing = set(value) - allowed_set, required_set - set(value)
    if unknown:
        raise ValueError(f"{label}: unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label}: missing fields: {sorted(missing)}")


def _string(value: Any, label: str, *, maximum: Optional[int] = None) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}: expected non-empty string")
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{label}: exceeds maximum length {maximum}")
    return value


def _nullable_string(
    value: Any, label: str, *, maximum: Optional[int] = None
) -> Optional[str]:
    if value is None:
        return None
    return _string(value, label, maximum=maximum)


def _schema(value: Any, label: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"{label}: unsupported schema_version {value!r}")


def _date_time(value: Any, label: str) -> str:
    text = _string(value, label)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label}: invalid date-time") from error
    return text


class EnvironmentRegistry:
    def __init__(self, archive_root: Path | str):
        self.archive_root = Path(archive_root)
        self.root = self.archive_root / "environment"
        self.locks_dir = self.archive_root / ".locks"
        self.lock_path = self.locks_dir / "environment.lock"
        self.registry_path = self.root / "registry.json"
        self.state_path = self.root / "state.json"
        self.artifacts_dir = self.root / "artifacts"
        self.projects_dir = self.root / "projects"
        self.manifests_dir = self.root / "manifests"
        self.revisions_dir = self.root / "revisions"
        self.objects_dir = self.root / "objects" / "sha256"
        self.staging_dir = self.root / "staging"
        self.transactions_dir = self.root / "transactions"
        self.conflicts_dir = self.root / "conflicts"
        self.receipts_dir = self.root / "receipts"
        self.promotions_dir = self.root / "promotions"
        self.derived_dir = self.root / "derived"

    def init(self) -> Dict[str, Any]:
        self._ensure_layout()
        with self._write_lock():
            recovered = self.recover_transactions()
        return {"status": "initialized", "root": str(self.root), "recovered": recovered}

    def _ensure_layout(self) -> None:
        for directory in (
            self.locks_dir,
            self.artifacts_dir,
            self.projects_dir,
            self.manifests_dir,
            self.revisions_dir,
            self.objects_dir,
            self.staging_dir / "incoming",
            self.staging_dir / "validated",
            self.transactions_dir,
            self.conflicts_dir,
            self.receipts_dir,
            self.promotions_dir,
            self.derived_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            atomic_write_json(self.registry_path, self._empty_registry())
        if not self.state_path.exists():
            atomic_write_json(
                self.state_path,
                {
                    "schema_version": 1,
                    "derived_from_registry_events": 0,
                    "last_rebuilt_at": None,
                },
            )

    @staticmethod
    def _empty_registry() -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "events": [],
            "current_artifacts": {},
            "current_projects": {},
        }

    def recover_transactions(self) -> int:
        if not self.transactions_dir.exists():
            return 0
        registry = self._read_registry()
        committed_ids = {event["event_id"] for event in registry["events"]}
        recovered = 0
        rebuild_required = False
        for marker_path in sorted(self.transactions_dir.glob("*.json")):
            marker = read_json(marker_path)
            self._validate_transaction_marker(marker)
            # Registry replacement is the commit point. Immutable unreferenced
            # files from an interrupted transaction are harmless.
            if marker["event_id"] in committed_ids:
                rebuild_required = True
            marker_path.unlink()
            recovered += 1
        if rebuild_required:
            self._rebuild_derived(registry)
        return recovered

    def status(self) -> Dict[str, Any]:
        if not self.root.exists():
            return {"initialized": False, "root": str(self.root)}
        registry = self._read_registry()
        return {
            "initialized": True,
            "root": str(self.root),
            "registry_events": len(registry["events"]),
            "artifacts": len(registry["current_artifacts"]),
            "projects": len(registry["current_projects"]),
            "pending_transactions": len(list(self.transactions_dir.glob("*.json"))),
        }

    def scan(
        self,
        *,
        manifests: Iterable[Path | str] = (),
        roots: Iterable[Path | str] = (),
    ) -> Dict[str, Any]:
        candidates = [Path(path) for path in manifests]
        for supplied in roots:
            path = Path(supplied)
            candidates.append(
                path if path.is_file() else path / "environment-manifest.json"
            )
        previews = []
        for path in candidates:
            if not path.is_file():
                raise ValueError(f"Explicit environment manifest does not exist: {path}")
            previews.append(self.register(read_json(path), apply=False))
        return {"status": "preview", "manifests": previews}

    def register(self, manifest: Dict[str, Any], *, apply: bool = False) -> Dict[str, Any]:
        normalized = self._validate_manifest(manifest)
        plan = self._build_plan(normalized)
        if not apply:
            return {"status": "preview", **plan}
        self._ensure_layout()
        with self._write_lock():
            self.recover_transactions()
            return self._apply_plan(self._build_plan(normalized))

    def list(self, *, object_class: Optional[str] = None) -> List[Dict[str, Any]]:
        if object_class is not None and object_class not in OBJECT_CLASSES:
            raise ValueError(f"Unsupported object_class: {object_class}")
        registry = self._read_registry()
        records = []
        for artifact_id, entry in registry["current_artifacts"].items():
            artifact = self._read_relative_json(entry["artifact_path"], "artifact_path")
            revision = self._read_relative_json(entry["revision_path"], "revision_path")
            self._validate_artifact(artifact)
            self._validate_revision(revision)
            if artifact["artifact_id"] != artifact_id:
                raise ValueError("registry artifact identity mismatch")
            if entry["artifact_path"] != self._artifact_relative(artifact_id):
                raise ValueError("registry current artifact_path is not canonical")
            if entry["revision_path"] != self._revision_relative(
                artifact_id, revision["revision_id"]
            ):
                raise ValueError("registry current revision_path is not canonical")
            if object_class is None or artifact["object_class"] == object_class:
                records.append(
                    {
                        "artifact_id": artifact_id,
                        "object_class": artifact["object_class"],
                        "revision_id": revision["revision_id"],
                        "artifact_path": entry["artifact_path"],
                        "revision_path": entry["revision_path"],
                    }
                )
        return sorted(records, key=lambda item: item["artifact_id"])

    def show(self, artifact_id: str) -> Dict[str, Any]:
        _string(artifact_id, "artifact_id")
        registry = self._read_registry()
        entry = registry["current_artifacts"].get(artifact_id)
        if entry is None:
            raise KeyError(f"Unknown artifact_id: {artifact_id}")
        return {
            "artifact": self._read_relative_json(entry["artifact_path"], "artifact_path"),
            "revision": self._read_relative_json(entry["revision_path"], "revision_path"),
        }

    def projects(self) -> List[Dict[str, Any]]:
        registry = self._read_registry()
        projects = []
        for project_id, relative_path in registry["current_projects"].items():
            project = self._read_relative_json(relative_path, "project_path")
            self._validate_project(project)
            if project["project_id"] != project_id:
                raise ValueError("registry project identity mismatch")
            if relative_path != self._project_relative(project):
                raise ValueError("registry current project_path is not canonical")
            projects.append(project)
        return sorted(projects, key=lambda item: item["project_id"])

    def diff(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        plan = self._build_plan(self._validate_manifest(manifest))
        return {
            "status": "preview",
            "changes": [
                {
                    "artifact_id": item["artifact"]["artifact_id"],
                    "action": item["action"],
                    "current_revision_id": item["current_revision_id"],
                    "proposed_revision_id": item["revision"]["revision_id"],
                }
                for item in plan["artifacts"]
            ],
            "project_changes": [
                {"project_id": item["project"]["project_id"], "action": item["action"]}
                for item in plan["projects"]
            ],
        }

    def validate(self) -> Dict[str, Any]:
        registry = self._read_registry()
        for event in registry["events"]:
            self._validate_registry_event(event)
            if event["operation"] == "artifact-revision":
                if event["artifact_path"] != self._artifact_relative(event["artifact_id"]):
                    raise ValueError("registry event artifact_path is not canonical")
                if event["revision_path"] != self._revision_relative(
                    event["artifact_id"], event["revision_id"]
                ):
                    raise ValueError("registry event revision_path is not canonical")
                artifact = self._read_relative_json(
                    event["artifact_path"], "event.artifact_path"
                )
                revision = self._read_relative_json(
                    event["revision_path"], "event.revision_path"
                )
                self._validate_artifact(artifact)
                self._validate_revision(revision)
                if (
                    artifact["artifact_id"] != event["artifact_id"]
                    or revision["artifact_id"] != event["artifact_id"]
                    or revision["revision_id"] != event["revision_id"]
                ):
                    raise ValueError("registry event identity mismatch")
                self._verify_revision_hash(revision)
                self._verify_object(revision)
            else:
                if not event["project_path"].startswith(
                    f"projects/{event['project_id']}/"
                ):
                    raise ValueError("registry event project_path is not canonical")
                project = self._read_relative_json(
                    event["project_path"], "event.project_path"
                )
                self._validate_project(project)
                if project["project_id"] != event["project_id"]:
                    raise ValueError("registry project event identity mismatch")
                if event["project_path"] != self._project_relative(project):
                    raise ValueError("registry event project_path is not canonical")
        self.list()
        self.projects()
        return {
            "status": "valid",
            "registry_events": len(registry["events"]),
            "artifacts": len(registry["current_artifacts"]),
            "projects": len(registry["current_projects"]),
        }

    def _validate_manifest(self, manifest: Any) -> Dict[str, Any]:
        if not isinstance(manifest, dict):
            raise ValueError("manifest: expected object")
        _strict_keys(
            manifest,
            {"schema_version", "artifacts", "projects"},
            {"schema_version", "artifacts", "projects"},
            "manifest",
        )
        _schema(manifest["schema_version"], "manifest")
        if not isinstance(manifest["artifacts"], list) or not isinstance(
            manifest["projects"], list
        ):
            raise ValueError("manifest artifacts/projects must be arrays")
        artifacts = [self._validate_manifest_artifact(item) for item in manifest["artifacts"]]
        projects = [self._validate_project(item) for item in manifest["projects"]]
        artifact_ids = [item["artifact"]["artifact_id"] for item in artifacts]
        project_ids = [item["project_id"] for item in projects]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("manifest contains duplicate artifact_id")
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("manifest contains duplicate project_id")
        known_projects = set(project_ids)
        if self.registry_path.exists():
            known_projects.update(self._read_registry()["current_projects"])
        for item in artifacts:
            artifact = item["artifact"]
            if artifact["scope"] == "project" and artifact["project_id"] not in known_projects:
                raise ValueError(
                    f"artifact references unknown project_id: {artifact['project_id']}"
                )
        return {"schema_version": 1, "artifacts": artifacts, "projects": projects}

    def _validate_manifest_artifact(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("manifest artifact: expected object")
        _strict_keys(
            value,
            {"artifact", "revision", "content"},
            {"artifact", "revision", "content"},
            "manifest artifact",
        )
        artifact = self._validate_artifact(value["artifact"])
        revision = self._validate_revision(value["revision"])
        content = value["content"]
        if not isinstance(content, str):
            raise ValueError("manifest artifact.content: expected string")
        if artifact["artifact_id"] != revision["artifact_id"]:
            raise ValueError("artifact/revision artifact_id mismatch")
        if sha256_bytes(content.encode("utf-8")) != revision["content_sha256"]:
            raise ValueError("revision content_sha256 does not match content")
        expected_object_path = self._object_relative(revision["content_sha256"])
        if revision["object_path"] != expected_object_path:
            raise ValueError("revision object_path does not match content_sha256")
        self._verify_revision_hash(revision)
        return {"artifact": artifact, "revision": revision, "content": content}

    def _validate_artifact(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("artifact: expected object")
        fields = {
            "schema_version",
            "artifact_id",
            "object_class",
            "scope",
            "project_id",
            "display_name",
            "created_at",
        }
        required = {
            "schema_version",
            "artifact_id",
            "object_class",
            "scope",
            "created_at",
        }
        _strict_keys(value, fields, required, "artifact")
        _schema(value["schema_version"], "artifact")
        artifact_id = _string(value["artifact_id"], "artifact.artifact_id")
        if not ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ValueError("artifact.artifact_id: invalid format")
        object_class = value["object_class"]
        scope = value["scope"]
        if object_class not in OBJECT_CLASSES or scope not in SCOPES:
            raise ValueError("artifact: unsupported object_class/scope")
        if object_class.startswith("global-") != (scope == "global"):
            raise ValueError("artifact: object_class and scope disagree")
        project_id = value.get("project_id")
        if scope == "project":
            if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
                raise ValueError("artifact.project_id: required project identifier")
        elif project_id is not None:
            raise ValueError("global artifact project_id must be null")
        _nullable_string(value.get("display_name"), "artifact.display_name", maximum=256)
        _date_time(value["created_at"], "artifact.created_at")
        return json.loads(json.dumps(value))

    def _validate_revision(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("revision: expected object")
        fields = {
            "schema_version",
            "revision_id",
            "artifact_id",
            "origin_node_id",
            "version",
            "base_revision_id",
            "content_sha256",
            "object_path",
            "supported_platforms",
            "runtime_requirements",
            "provenance",
            "lifecycle_state",
            "created_at",
        }
        _strict_keys(value, fields, fields, "revision")
        _schema(value["schema_version"], "revision")
        if not REVISION_ID_RE.fullmatch(_string(value["revision_id"], "revision_id")):
            raise ValueError("revision.revision_id: invalid format")
        if not ARTIFACT_ID_RE.fullmatch(_string(value["artifact_id"], "artifact_id")):
            raise ValueError("revision.artifact_id: invalid format")
        if not NODE_ID_RE.fullmatch(_string(value["origin_node_id"], "origin_node_id")):
            raise ValueError("revision.origin_node_id: invalid format")
        if type(value["version"]) is not int or value["version"] < 1:
            raise ValueError("revision.version: expected integer >= 1")
        base = value["base_revision_id"]
        if base is not None and (
            not isinstance(base, str) or not REVISION_ID_RE.fullmatch(base)
        ):
            raise ValueError("revision.base_revision_id: invalid format")
        if not SHA256_RE.fullmatch(_string(value["content_sha256"], "content_sha256")):
            raise ValueError("revision.content_sha256: invalid format")
        if not isinstance(value["supported_platforms"], list) or not value[
            "supported_platforms"
        ]:
            raise ValueError("revision.supported_platforms: expected non-empty array")
        if len(value["supported_platforms"]) != len(set(value["supported_platforms"])):
            raise ValueError("revision.supported_platforms: duplicate platform")
        if any(platform not in PLATFORMS for platform in value["supported_platforms"]):
            raise ValueError("revision.supported_platforms: unsupported platform")
        if not isinstance(value["runtime_requirements"], dict):
            raise ValueError("revision.runtime_requirements: expected object")
        if not isinstance(value["provenance"], dict):
            raise ValueError("revision.provenance: expected object")
        if value["lifecycle_state"] not in LIFECYCLE_STATES:
            raise ValueError("revision.lifecycle_state: unsupported state")
        _date_time(value["created_at"], "revision.created_at")
        self._validate_relative_path(
            value["object_path"], "revision.object_path", prefix="objects/sha256/"
        )
        return json.loads(json.dumps(value))

    def _validate_project(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("project: expected object")
        fields = {
            "schema_version",
            "project_id",
            "display_name",
            "local_root",
            "active",
            "rule_bindings",
            "skill_bindings",
        }
        required = fields - {"local_root"}
        _strict_keys(value, fields, required, "project")
        _schema(value["schema_version"], "project")
        project_id = _string(value["project_id"], "project.project_id")
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("project.project_id: invalid format")
        _string(value["display_name"], "project.display_name", maximum=256)
        _nullable_string(value.get("local_root"), "project.local_root")
        if type(value["active"]) is not bool:
            raise ValueError("project.active: expected boolean")
        if not isinstance(value["rule_bindings"], list) or not isinstance(
            value["skill_bindings"], list
        ):
            raise ValueError("project bindings must be arrays")
        rule_ids, skill_ids = [], []
        for binding in value["rule_bindings"]:
            self._validate_rule_binding(binding)
            rule_ids.append(binding["binding_id"])
        for binding in value["skill_bindings"]:
            self._validate_skill_binding(binding)
            skill_ids.append(binding["skill_id"])
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("project contains duplicate rule binding_id")
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("project contains duplicate skill_id")
        return json.loads(json.dumps(value))

    def _validate_rule_binding(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("rule binding: expected object")
        fields = {
            "binding_id",
            "relative_path",
            "classification",
            "install_strategy",
            "managed_block_id",
        }
        required = {"binding_id", "relative_path", "classification"}
        _strict_keys(value, fields, required, "rule binding")
        if not BINDING_ID_RE.fullmatch(_string(value["binding_id"], "binding_id")):
            raise ValueError("rule binding.binding_id: invalid format")
        relative_path = _string(value["relative_path"], "relative_path")
        path = PurePosixPath(relative_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("rule binding.relative_path must be safe and relative")
        if value["classification"] not in RULE_CLASSIFICATIONS:
            raise ValueError("rule binding.classification: unsupported value")
        if "install_strategy" in value and value["install_strategy"] not in RULE_INSTALL_STRATEGIES:
            raise ValueError("rule binding.install_strategy: unsupported value")
        if "managed_block_id" in value:
            _nullable_string(value["managed_block_id"], "managed_block_id")

    def _validate_skill_binding(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("skill binding: expected object")
        fields = {"skill_id", "enabled", "pinned_version"}
        _strict_keys(value, fields, {"skill_id", "enabled"}, "skill binding")
        if not SKILL_ID_RE.fullmatch(_string(value["skill_id"], "skill_id")):
            raise ValueError("skill binding.skill_id: invalid format")
        if type(value["enabled"]) is not bool:
            raise ValueError("skill binding.enabled: expected boolean")
        if "pinned_version" in value:
            _nullable_string(value["pinned_version"], "pinned_version")

    def _build_plan(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        registry = self._read_registry() if self.registry_path.exists() else self._empty_registry()
        artifacts = []
        for item in manifest["artifacts"]:
            artifact_id = item["artifact"]["artifact_id"]
            revision = item["revision"]
            current = registry["current_artifacts"].get(artifact_id)
            current_revision_id = current["revision_id"] if current else None
            if current_revision_id == revision["revision_id"]:
                action = "no-change"
            elif current is None:
                if revision["base_revision_id"] is not None:
                    raise ValueError("create requires base_revision_id=null")
                if revision["version"] != 1:
                    raise ValueError("create requires version=1")
                action = "create"
            else:
                if revision["base_revision_id"] != current_revision_id:
                    raise ValueError("stale base_revision_id")
                current_revision = self._read_relative_json(
                    current["revision_path"], "current revision_path"
                )
                self._validate_revision(current_revision)
                if revision["version"] != current_revision["version"] + 1:
                    raise ValueError("update version must increment current version by one")
                action = "update"
            artifacts.append(
                {**item, "action": action, "current_revision_id": current_revision_id}
            )
        projects = []
        for project in manifest["projects"]:
            current_path = registry["current_projects"].get(project["project_id"])
            unchanged = (
                current_path is not None
                and canonical_bytes(
                    self._read_relative_json(current_path, "current project_path")
                )
                == canonical_bytes(project)
            )
            projects.append(
                {"project": project, "action": "no-change" if unchanged else "upsert"}
            )
        return {"schema_version": 1, "artifacts": artifacts, "projects": projects}

    def _apply_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        artifact_changes = [
            item for item in plan["artifacts"] if item["action"] != "no-change"
        ]
        project_changes = [
            item for item in plan["projects"] if item["action"] != "no-change"
        ]
        for item in plan["artifacts"]:
            if item["action"] == "no-change":
                self._verify_registered_revision(item["revision"])
        if not artifact_changes and not project_changes:
            return {"status": "no-change", "artifacts": 0, "projects": 0}

        event_id = f"evt-{uuid.uuid4().hex}"
        marker_path = self.transactions_dir / f"{event_id}.json"
        atomic_write_json(
            marker_path,
            {
                "schema_version": 1,
                "event_id": event_id,
                "status": "staging",
                "created_at": now_iso(),
            },
        )
        registry = self._read_registry()
        next_registry = json.loads(json.dumps(registry))
        events = []
        try:
            for item in artifact_changes:
                artifact, revision = item["artifact"], item["revision"]
                object_path = self._resolve_relative(
                    revision["object_path"], "revision.object_path", for_write=True
                )
                content_bytes = item["content"].encode("utf-8")
                if object_path.exists():
                    if is_link_like(object_path) or sha256_bytes(object_path.read_bytes()) != revision[
                        "content_sha256"
                    ]:
                        raise ValueError("existing content-addressed object is invalid")
                else:
                    atomic_write_bytes(object_path, content_bytes)

                artifact_relative = self._artifact_relative(artifact["artifact_id"])
                revision_relative = self._revision_relative(
                    artifact["artifact_id"], revision["revision_id"]
                )
                self._write_immutable_json(
                    artifact_relative, artifact, self._validate_artifact
                )
                self._write_immutable_json(
                    revision_relative, revision, self._validate_revision
                )
                event = {
                    "schema_version": 1,
                    "event_id": event_id,
                    "operation": "artifact-revision",
                    "artifact_id": artifact["artifact_id"],
                    "revision_id": revision["revision_id"],
                    "artifact_path": artifact_relative,
                    "revision_path": revision_relative,
                    "project_id": None,
                    "project_path": None,
                    "recorded_at": now_iso(),
                }
                self._validate_registry_event(event)
                events.append(event)
                next_registry["current_artifacts"][artifact["artifact_id"]] = {
                    "revision_id": revision["revision_id"],
                    "artifact_path": artifact_relative,
                    "revision_path": revision_relative,
                }

            for item in project_changes:
                project = item["project"]
                project_relative = self._project_relative(project)
                self._write_immutable_json(
                    project_relative, project, self._validate_project
                )
                event = {
                    "schema_version": 1,
                    "event_id": event_id,
                    "operation": "project-registration",
                    "artifact_id": None,
                    "revision_id": None,
                    "artifact_path": None,
                    "revision_path": None,
                    "project_id": project["project_id"],
                    "project_path": project_relative,
                    "recorded_at": now_iso(),
                }
                self._validate_registry_event(event)
                events.append(event)
                next_registry["current_projects"][project["project_id"]] = project_relative

            next_registry["events"].extend(events)
            self._before_registry_commit(next_registry)
            atomic_write_json(self.registry_path, next_registry)
            self._rebuild_derived(next_registry)
            marker_path.unlink()
            return {
                "status": "registered",
                "event_id": event_id,
                "artifacts": len(artifact_changes),
                "projects": len(project_changes),
            }
        except Exception:
            # The registry snapshot is the sole visibility boundary. The marker
            # and unreferenced immutable files are recovered on the next write.
            raise

    def _before_registry_commit(self, registry: Dict[str, Any]) -> None:
        """Test seam immediately before the sole authoritative commit."""

    def _verify_registered_revision(self, proposed: Dict[str, Any]) -> None:
        registry = self._read_registry()
        entry = registry["current_artifacts"][proposed["artifact_id"]]
        existing = self._read_relative_json(entry["revision_path"], "revision_path")
        self._validate_revision(existing)
        if canonical_bytes(existing) != canonical_bytes(proposed):
            raise ValueError("same revision_id has different revision content")
        self._verify_revision_hash(existing)
        self._verify_object(existing)

    def _write_immutable_json(self, relative: str, value: Dict[str, Any], validator) -> None:
        path = self._resolve_relative(relative, "immutable path", for_write=True)
        validator(value)
        if path.exists():
            if is_link_like(path) or canonical_bytes(read_json(path)) != canonical_bytes(value):
                raise ValueError("immutable path already contains different content")
            return
        atomic_write_json(path, value)

    def _verify_revision_hash(self, revision: Dict[str, Any]) -> None:
        if revision_id_for(revision) != revision["revision_id"]:
            raise ValueError("revision_id hash mismatch")

    def _verify_object(self, revision: Dict[str, Any]) -> None:
        if revision["object_path"] != self._object_relative(revision["content_sha256"]):
            raise ValueError("revision object_path is not canonical")
        path = self._resolve_relative(revision["object_path"], "object_path")
        if is_link_like(path) or not path.is_file():
            raise ValueError("content object is missing or unsafe")
        if sha256_bytes(path.read_bytes()) != revision["content_sha256"]:
            raise ValueError("content object SHA-256 mismatch")

    def _read_registry(self) -> Dict[str, Any]:
        if not self.registry_path.exists():
            return self._empty_registry()
        value = read_json(self.registry_path)
        if not isinstance(value, dict):
            raise ValueError("registry: expected object")
        fields = {"schema_version", "events", "current_artifacts", "current_projects"}
        _strict_keys(value, fields, fields, "registry")
        _schema(value["schema_version"], "registry")
        if not isinstance(value["events"], list) or not isinstance(
            value["current_artifacts"], dict
        ) or not isinstance(value["current_projects"], dict):
            raise ValueError("registry collections have invalid type")
        for event in value["events"]:
            self._validate_registry_event(event)
        for artifact_id, entry in value["current_artifacts"].items():
            if not ARTIFACT_ID_RE.fullmatch(artifact_id) or not isinstance(entry, dict):
                raise ValueError("registry current_artifacts entry is invalid")
            _strict_keys(
                entry,
                {"revision_id", "artifact_path", "revision_path"},
                {"revision_id", "artifact_path", "revision_path"},
                "current artifact",
            )
            if not REVISION_ID_RE.fullmatch(entry["revision_id"]):
                raise ValueError("registry current revision_id is invalid")
            self._validate_relative_path(entry["artifact_path"], "artifact_path")
            self._validate_relative_path(entry["revision_path"], "revision_path")
        for project_id, path in value["current_projects"].items():
            if not PROJECT_ID_RE.fullmatch(project_id):
                raise ValueError("registry current project_id is invalid")
            self._validate_relative_path(path, "project_path")
        return value

    def _validate_registry_event(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("registry event: expected object")
        fields = {
            "schema_version",
            "event_id",
            "operation",
            "artifact_id",
            "revision_id",
            "artifact_path",
            "revision_path",
            "project_id",
            "project_path",
            "recorded_at",
        }
        _strict_keys(value, fields, fields, "registry event")
        _schema(value["schema_version"], "registry event")
        if not re.fullmatch(r"evt-[0-9a-f]{32}", _string(value["event_id"], "event_id")):
            raise ValueError("registry event.event_id is invalid")
        _date_time(value["recorded_at"], "registry event.recorded_at")
        if value["operation"] == "artifact-revision":
            if not isinstance(value["artifact_id"], str) or not ARTIFACT_ID_RE.fullmatch(
                value["artifact_id"]
            ):
                raise ValueError("registry event artifact_id is invalid")
            if not isinstance(value["revision_id"], str) or not REVISION_ID_RE.fullmatch(
                value["revision_id"]
            ):
                raise ValueError("registry event revision_id is invalid")
            self._validate_relative_path(value["artifact_path"], "artifact_path")
            self._validate_relative_path(value["revision_path"], "revision_path")
            if value["project_id"] is not None or value["project_path"] is not None:
                raise ValueError("artifact registry event has project fields")
        elif value["operation"] == "project-registration":
            if not isinstance(value["project_id"], str) or not PROJECT_ID_RE.fullmatch(
                value["project_id"]
            ):
                raise ValueError("registry event project_id is invalid")
            self._validate_relative_path(value["project_path"], "project_path")
            if any(
                value[field] is not None
                for field in ("artifact_id", "revision_id", "artifact_path", "revision_path")
            ):
                raise ValueError("project registry event has artifact fields")
        else:
            raise ValueError("registry event operation is unsupported")

    def _validate_transaction_marker(self, value: Any) -> None:
        if not isinstance(value, dict):
            raise ValueError("transaction marker: expected object")
        fields = {"schema_version", "event_id", "status", "created_at"}
        _strict_keys(value, fields, fields, "transaction marker")
        _schema(value["schema_version"], "transaction marker")
        if not re.fullmatch(r"evt-[0-9a-f]{32}", value["event_id"]):
            raise ValueError("transaction marker event_id is invalid")
        if value["status"] != "staging":
            raise ValueError("transaction marker status is invalid")
        _date_time(value["created_at"], "transaction marker.created_at")

    def _rebuild_derived(self, registry: Dict[str, Any]) -> None:
        grouped = {name: [] for name in (
            "global-rules.json",
            "project-rules.json",
            "global-skills.json",
            "project-skills.json",
            "global-runtime-contracts.json",
        )}
        name_by_class = {
            "global-rule": "global-rules.json",
            "project-rule": "project-rules.json",
            "global-skill": "global-skills.json",
            "project-skill": "project-skills.json",
            "global-runtime-contract": "global-runtime-contracts.json",
        }
        for artifact_id, entry in registry["current_artifacts"].items():
            artifact = self._read_relative_json(entry["artifact_path"], "artifact_path")
            grouped[name_by_class[artifact["object_class"]]].append(
                {"artifact_id": artifact_id, **entry}
            )
        for name, artifacts in grouped.items():
            atomic_write_json(
                self.manifests_dir / name,
                {"schema_version": 1, "artifacts": artifacts},
            )
        atomic_write_json(
            self.state_path,
            {
                "schema_version": 1,
                "derived_from_registry_events": len(registry["events"]),
                "last_rebuilt_at": now_iso(),
            },
        )

    def _artifact_relative(self, artifact_id: str) -> str:
        return f"artifacts/{sha256_bytes(artifact_id.encode('utf-8'))}.json"

    def _revision_relative(self, artifact_id: str, revision_id: str) -> str:
        artifact_hash = sha256_bytes(artifact_id.encode("utf-8"))
        return f"revisions/{artifact_hash}/{revision_id.removeprefix('rev:')}.json"

    @staticmethod
    def _object_relative(content_sha256: str) -> str:
        return f"objects/sha256/{content_sha256[:2]}/{content_sha256[2:]}"

    def _project_relative(self, project: Dict[str, Any]) -> str:
        digest = sha256_bytes(canonical_bytes(project))
        return f"projects/{project['project_id']}/{digest}.json"

    def _validate_relative_path(
        self, value: Any, label: str, *, prefix: Optional[str] = None
    ) -> str:
        text = _string(value, label)
        if "\\" in text:
            raise ValueError(f"{label}: persistent path must use POSIX separators")
        pure = PurePosixPath(text)
        if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
            raise ValueError(f"{label}: path traversal is forbidden")
        normalized = pure.as_posix()
        if normalized != text or (prefix is not None and not normalized.startswith(prefix)):
            raise ValueError(f"{label}: invalid relative environment path")
        return normalized

    def _resolve_relative(
        self, relative: str, label: str, *, for_write: bool = False
    ) -> Path:
        normalized = self._validate_relative_path(relative, label)
        root = self.root.resolve()
        candidate = self.root.joinpath(*PurePosixPath(normalized).parts)
        # resolve(strict=False) follows existing parent symlinks, catching both
        # read and prospective-write escapes.
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"{label}: path escapes environment root") from error
        if is_link_like(candidate):
            raise ValueError(f"{label}: symlink path is forbidden")
        parent = candidate.parent
        while parent != self.root and parent != parent.parent:
            if parent.exists() and is_link_like(parent):
                raise ValueError(f"{label}: symlink parent is forbidden")
            parent = parent.parent
        if not for_write and not candidate.exists():
            raise ValueError(f"{label}: referenced path does not exist")
        return candidate

    def _read_relative_json(self, relative: str, label: str) -> Dict[str, Any]:
        path = self._resolve_relative(relative, label)
        if not path.is_file():
            raise ValueError(f"{label}: referenced path is not a regular file")
        value = read_json(path)
        if not isinstance(value, dict):
            raise ValueError(f"{label}: expected JSON object")
        return value

    @contextmanager
    def _write_lock(self):
        with exclusive_lock(self.lock_path):
            yield
