#!/usr/bin/env python3
"""Deterministic staging processor for Memory Wuxian Environment updates."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from memory_environment import (
    EnvironmentRegistry,
    atomic_write_json,
    canonical_bytes,
    now_iso,
    read_json,
)
from memory_environment_conflicts import EnvironmentConflictStore
from memory_environment_exchange import EnvironmentExchangeManager
from memory_environment_skills import _runtime_satisfies
from platform_lock import exclusive_lock


PLATFORMS = {"macos", "windows", "linux"}
STAGE_FIELDS = {
    "schema_version",
    "stream_id",
    "origin_node_id",
    "event_sequence",
    "artifact",
    "revision",
    "content_base64",
    "package_attachment",
    "received_bundle_id",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_keys(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    required: Iterable[str],
    label: str,
) -> None:
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f"{label}: unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label}: missing fields: {sorted(missing)}")


class EnvironmentIncomingProcessor:
    """Validate peer staging and route it without AI or implicit installation."""

    def __init__(
        self,
        archive_root: Path | str,
        *,
        platform: str,
        runtime_versions: Mapping[str, str],
    ):
        if platform not in PLATFORMS:
            raise ValueError("unsupported local platform")
        self.registry = EnvironmentRegistry(archive_root)
        self.platform = platform
        self.runtime_versions = dict(runtime_versions)
        self.incoming_root = self.registry.staging_dir / "incoming"
        self.decisions_root = self.registry.staging_dir / "decisions"
        self.accepted_root = self.registry.staging_dir / "accepted"
        self.completed_root = self.registry.staging_dir / "completed"
        self.batch_root = self.registry.staging_dir / "processing-batches"
        self.validated_packages = self.registry.staging_dir / "validated" / "packages"
        self.lock_path = self.registry.locks_dir / "environment-incoming.lock"
        self.conflicts = EnvironmentConflictStore(archive_root)

    def status(self) -> Dict[str, Any]:
        decisions = self._decisions()
        completions = self._completions()
        pending_files = self._processable_stage_paths()
        counts: Dict[str, int] = {}
        for decision in decisions:
            state = str(decision["decision"])
            counts[state] = counts.get(state, 0) + 1
        return {
            "stream_id": "environment-v1",
            "staged_events": len(pending_files),
            "processed_events": len(completions),
            "decision_counts": counts,
            "pending_conflicts": len(self.conflicts.list(pending_only=True)),
        }

    def process(
        self,
        *,
        apply: bool = False,
        auto_register_compatible_rules: bool = False,
        maximum_events: int = 100,
    ) -> Dict[str, Any]:
        if maximum_events < 1:
            raise ValueError("maximum_events must be positive")
        paths = self._processable_stage_paths()
        results = []
        actionable_count = 0
        if not apply:
            for path in paths:
                result = self._assess_path(
                    path, persist=False, auto_register=False
                )
                results.append(result)
                if result["decision"] != "no-change":
                    actionable_count += 1
                if actionable_count >= maximum_events:
                    break
            return {
                "status": "preview",
                "processed": actionable_count,
                "examined": len(results),
                "results": results,
            }
        self.registry.init()
        with exclusive_lock(self.lock_path):
            for path in paths:
                try:
                    result = self._assess_path(
                        path,
                        persist=True,
                        auto_register=auto_register_compatible_rules,
                    )
                    if result["decision"] != "no-change":
                        self._record_completion(path, result)
                        actionable_count += 1
                    results.append(result)
                    if actionable_count >= maximum_events:
                        break
                except Exception as error:
                    failure = self._record_batch_result(
                        status="partial",
                        results=results,
                        failed_path=path,
                        error=error,
                    )
                    return {
                        "status": "partial",
                        "processed": actionable_count,
                        "examined": len(results) + 1,
                        "results": results,
                        "failed_stage_path": str(
                            path.relative_to(self.registry.root)
                        ),
                        "error": f"{type(error).__name__}: {error}",
                        "batch_evidence_path": str(failure),
                    }
            actionable = [
                item for item in results if item["decision"] != "no-change"
            ]
            evidence = (
                self._record_batch_result(
                    status="processed",
                    results=actionable,
                )
                if actionable
                else None
            )
        return {
            "status": "processed",
            "processed": len(actionable),
            "examined": len(results),
            "results": results,
            "batch_evidence_path": str(evidence) if evidence else None,
        }

    def accept(self, stage_sha256: str, *, apply: bool = False) -> Dict[str, Any]:
        """Explicitly register one compatible, nondivergent event without installing it."""

        if not isinstance(stage_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", stage_sha256
        ):
            raise ValueError("stage_sha256 is invalid")
        matches = [
            path
            for path in self._stage_paths()
            if _sha256(path.read_bytes()) == stage_sha256
        ]
        if len(matches) != 1:
            raise ValueError("stage_sha256 does not identify exactly one staged event")
        path = matches[0]
        record = read_json(path)
        artifact, revision, content = self._validate_stage(record, path)
        compatibility = self._compatibility(revision)
        if not compatibility["compatible"]:
            raise ValueError(f"staged event is incompatible: {compatibility['reason']}")
        current = self._current(artifact["artifact_id"])
        if current is not None and (
            current["revision"]["revision_id"] != revision["revision_id"]
            and revision["base_revision_id"]
            != current["revision"]["revision_id"]
        ):
            raise ValueError("staged event diverges from current revision")
        if artifact["scope"] == "project" and not any(
            project["project_id"] == artifact["project_id"]
            for project in self.registry.projects()
        ):
            raise ValueError("project-scoped event has no local project binding")
        package_path = None
        if artifact["object_class"].endswith("-skill"):
            package = base64.b64decode(
                record["package_attachment"]["content_base64"], validate=True
            )
            package_hash = record["package_attachment"]["package_sha256"]
            package_path = self.validated_packages / f"{package_hash}.zip"
        result = {
            "status": "preview",
            "stage_sha256": stage_sha256,
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["revision_id"],
            "package_path": str(package_path) if package_path else None,
            "requires_install": True,
        }
        if not apply:
            return result
        self.registry.init()
        with exclusive_lock(self.lock_path):
            if package_path is not None:
                self._write_immutable_package(package_path, package)
            if current is None or current["revision"]["revision_id"] != revision["revision_id"]:
                self.registry.register(
                    {
                        "schema_version": 1,
                        "artifacts": [
                            {
                                "artifact": artifact,
                                "revision": revision,
                                "content": content.decode("utf-8"),
                            }
                        ],
                        "projects": [],
                    },
                    apply=True,
                )
            acceptance = {
                "schema_version": 1,
                "stage_sha256": stage_sha256,
                "artifact_id": artifact["artifact_id"],
                "revision_id": revision["revision_id"],
                "package_path": str(package_path) if package_path else None,
                "accepted_at": now_iso(),
            }
            acceptance_path = self.accepted_root / f"{stage_sha256}.json"
            if acceptance_path.exists():
                existing = read_json(acceptance_path)
                stable = {
                    key: acceptance[key]
                    for key in (
                        "schema_version",
                        "stage_sha256",
                        "artifact_id",
                        "revision_id",
                        "package_path",
                    )
                }
                if {key: existing.get(key) for key in stable} != stable:
                    raise ValueError("incoming acceptance record conflicts")
            else:
                atomic_write_json(acceptance_path, acceptance)
        return {**result, "status": "accepted", "acceptance_path": str(acceptance_path)}

    def _assess_path(
        self, path: Path, *, persist: bool, auto_register: bool
    ) -> Dict[str, Any]:
        record = read_json(path)
        artifact, revision, content = self._validate_stage(record, path)
        stage_sha256 = _sha256(path.read_bytes())
        decision_path = self.decisions_root / f"{stage_sha256}.json"
        if decision_path.exists():
            existing = read_json(decision_path)
            if existing.get("stage_sha256") != stage_sha256:
                raise ValueError("incoming decision identity mismatch")
            return {**existing, "status": "no-change"}

        current = self._current(artifact["artifact_id"])
        compatibility = self._compatibility(revision)
        if not compatibility["compatible"]:
            decision = "pending-review"
            reason = compatibility["reason"]
        elif current is None:
            decision = "pending-review"
            reason = "new-artifact"
        elif current["revision"]["revision_id"] == revision["revision_id"]:
            decision = "no-change"
            reason = "revision-already-current"
        elif current["revision"]["content_sha256"] == revision["content_sha256"]:
            decision = "no-change"
            reason = "content-already-current"
        elif revision["base_revision_id"] == current["revision"]["revision_id"]:
            if artifact["object_class"] == "global-rule":
                decision = "ready-fast-forward"
                reason = "remote-base-is-current"
            else:
                decision = "pending-review"
                reason = (
                    "skill-install-review"
                    if artifact["object_class"].endswith("-skill")
                    else (
                        "runtime-contract-review"
                        if artifact["object_class"] == "global-runtime-contract"
                        else "project-binding-review"
                    )
                )
        else:
            assessment = self._conflict_assessment(artifact, revision, current)
            conflict = self.conflicts.assess(assessment, apply=persist)
            decision = "conflict"
            reason = conflict["conflict"]["conflict_kind"]

        result = {
            "schema_version": 1,
            "stage_sha256": stage_sha256,
            "stage_path": str(path.relative_to(self.registry.root)),
            "origin_node_id": record["origin_node_id"],
            "event_sequence": record["event_sequence"],
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["revision_id"],
            "decision": decision,
            "reason": reason,
            "compatibility": compatibility,
            "registered": False,
            "created_at": now_iso(),
        }
        if (
            persist
            and auto_register
            and decision == "ready-fast-forward"
            and artifact["object_class"] == "global-rule"
        ):
            self.registry.register(
                {
                    "schema_version": 1,
                    "artifacts": [
                        {
                            "artifact": artifact,
                            "revision": revision,
                            "content": content.decode("utf-8"),
                        }
                    ],
                    "projects": [],
                },
                apply=True,
            )
            result["registered"] = True
            result["decision"] = "registered-fast-forward"
        if persist and decision != "no-change":
            atomic_write_json(decision_path, result)
        return result

    def _validate_stage(
        self, record: Any, path: Path
    ) -> tuple[Dict[str, Any], Dict[str, Any], bytes]:
        if path.is_symlink() or not path.is_file():
            raise ValueError("incoming stage path is unsafe")
        if not isinstance(record, dict):
            raise ValueError("incoming stage record must be an object")
        _strict_keys(record, STAGE_FIELDS, STAGE_FIELDS, "incoming stage")
        if record["schema_version"] != 1 or record["stream_id"] != "environment-v1":
            raise ValueError("incoming stage format is unsupported")
        artifact = self.registry._validate_artifact(record["artifact"])
        revision = self.registry._validate_revision(record["revision"])
        if revision["artifact_id"] != artifact["artifact_id"]:
            raise ValueError("incoming artifact/revision identity mismatch")
        if revision["origin_node_id"] != record["origin_node_id"]:
            raise ValueError("incoming origin identity mismatch")
        try:
            content = base64.b64decode(record["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("incoming content base64 is invalid") from error
        if _sha256(content) != revision["content_sha256"]:
            raise ValueError("incoming content hash mismatch")
        if artifact["object_class"].endswith("-skill"):
            EnvironmentExchangeManager._validate_skill_attachment(
                record["package_attachment"], revision, content
            )
        elif record["package_attachment"] is not None:
            raise ValueError("incoming rule cannot carry a Skill package")
        return artifact, revision, content

    def _current(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.registry.show(artifact_id)
        except KeyError:
            return None

    def _compatibility(self, revision: Dict[str, Any]) -> Dict[str, Any]:
        if self.platform not in revision["supported_platforms"]:
            return {
                "compatible": False,
                "reason": "platform-incompatible",
                "platform": self.platform,
            }
        for runtime, requirement in revision["runtime_requirements"].items():
            actual = self.runtime_versions.get(runtime)
            if actual is None or not _runtime_satisfies(actual, requirement):
                return {
                    "compatible": False,
                    "reason": "runtime-incompatible",
                    "runtime": runtime,
                    "required": requirement,
                    "actual": actual,
                }
        return {"compatible": True, "reason": "compatible"}

    def _conflict_assessment(
        self,
        artifact: Dict[str, Any],
        revision: Dict[str, Any],
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        local_revision = current["revision"]
        base_hash = None
        if revision["base_revision_id"]:
            try:
                base = self.registry._read_relative_json(
                    (
                        f"revisions/{revision['base_revision_id'].split(':', 1)[1]}.json"
                    ),
                    "base_revision",
                )
                base_hash = base["content_sha256"]
            except (FileNotFoundError, ValueError, KeyError):
                base_hash = None
        block = (
            "skill-tree"
            if artifact["object_class"].endswith("-skill")
            else (
                "runtime-interface"
                if artifact["object_class"] == "global-runtime-contract"
                else "document"
            )
        )
        return {
            "artifact_id": artifact["artifact_id"],
            "object_class": artifact["object_class"],
            "base_revision_id": revision["base_revision_id"],
            "local_revision_id": local_revision["revision_id"],
            "remote_revision_id": revision["revision_id"],
            "base_content_sha256": base_hash,
            "local_content_sha256": local_revision["content_sha256"],
            "remote_content_sha256": revision["content_sha256"],
            "local_changed_blocks": [block],
            "remote_changed_blocks": [block],
            "local_deleted": False,
            "remote_deleted": False,
            "unregistered_local_change": False,
            "project_identity_ambiguous": (
                artifact["scope"] == "project"
                and not any(
                    project["project_id"] == artifact["project_id"]
                    for project in self.registry.projects()
                )
            ),
            "platform_incompatible": False,
            "permission_expansion": False,
            "network_expansion": False,
        }

    def _stage_paths(self) -> List[Path]:
        if not self.incoming_root.is_dir():
            return []
        return sorted(
            path
            for path in self.incoming_root.glob("*/*.json")
            if path.is_file() and not path.is_symlink()
            and self._is_committed_stage(path)
        )

    def _processable_stage_paths(self) -> List[Path]:
        return [
            path
            for path in self._stage_paths()
            if not self._completion_path(path).exists()
        ]

    def _is_committed_stage(self, path: Path) -> bool:
        try:
            record = read_json(path)
            origin = str(record["origin_node_id"])
            bundle_id = str(record["received_bundle_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return True
        receipt = (
            self.registry.root
            / "replicas"
            / "peers"
            / origin
            / "receipts"
            / f"{bundle_id}.json"
        )
        transaction = (
            self.registry.root
            / "replicas"
            / "peers"
            / origin
            / "transactions"
            / bundle_id
            / "transaction.json"
        )
        # Legacy/manual stage fixtures have no transaction marker. New imports
        # become visible only after their durable bundle receipt exists.
        return receipt.is_file() or not transaction.exists()

    def _completion_path(self, path: Path) -> Path:
        return self.completed_root / f"{_sha256(path.read_bytes())}.json"

    def _record_completion(self, path: Path, result: Dict[str, Any]) -> Path:
        stage_sha256 = _sha256(path.read_bytes())
        evidence = {
            "schema_version": 1,
            "stage_sha256": stage_sha256,
            "stage_path": str(path.relative_to(self.registry.root)),
            "decision": result["decision"],
            "registered": bool(result.get("registered")),
            "completed_at": now_iso(),
        }
        target = self.completed_root / f"{stage_sha256}.json"
        if target.exists():
            existing = read_json(target)
            stable = (
                "schema_version",
                "stage_sha256",
                "stage_path",
                "decision",
                "registered",
            )
            if any(existing.get(key) != evidence[key] for key in stable):
                raise ValueError("incoming completion evidence conflicts")
        else:
            atomic_write_json(target, evidence)
        return target

    def _record_batch_result(
        self,
        *,
        status: str,
        results: List[Dict[str, Any]],
        failed_path: Optional[Path] = None,
        error: Optional[Exception] = None,
    ) -> Path:
        evidence = {
            "schema_version": 1,
            "status": status,
            "completed_stage_sha256": [
                str(result["stage_sha256"])
                for result in results
                if result["decision"] != "no-change"
            ],
            "failed_stage_path": (
                str(failed_path.relative_to(self.registry.root))
                if failed_path is not None
                else None
            ),
            "error": (
                f"{type(error).__name__}: {error}" if error is not None else None
            ),
            "created_at": now_iso(),
        }
        digest = _sha256(canonical_bytes(evidence))
        target = self.batch_root / f"{digest}.json"
        atomic_write_json(target, evidence)
        return target

    def _decisions(self) -> List[Dict[str, Any]]:
        if not self.decisions_root.is_dir():
            return []
        return [
            read_json(path)
            for path in sorted(self.decisions_root.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    def _completions(self) -> List[Dict[str, Any]]:
        if not self.completed_root.is_dir():
            return []
        return [
            read_json(path)
            for path in sorted(self.completed_root.glob("*.json"))
            if path.is_file() and not path.is_symlink()
        ]

    @staticmethod
    def _write_immutable_package(path: Path, payload: bytes) -> None:
        expected = path.stem
        if _sha256(payload) != expected:
            raise ValueError("validated Skill package filename/hash mismatch")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("validated Skill package path is unsafe")
            if _sha256(path.read_bytes()) != expected:
                raise ValueError("validated Skill package content hash mismatch")
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
