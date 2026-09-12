#!/usr/bin/env python3
"""Transactional installation of registered Memory Wuxian rule revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Tuple

from memory_environment import EnvironmentRegistry
from platform_lock import exclusive_lock
from platform_paths import is_link_like


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_ID_RE = re.compile(r"^rev:[0-9a-f]{64}$")
RECEIPT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
ALLOWED_CLASSIFICATIONS = {"canonical", "project-local", "generated", "excluded"}
ALLOWED_STRATEGIES = {"managed-block", "whole-file"}
BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_bytes(path: Path, value: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _canonical_json(value: Dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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
        # Directory fsync is unavailable on some Windows filesystems.
        pass
    finally:
        os.close(descriptor)


class RuleInstallationError(RuntimeError):
    """A fail-closed installation result with an optional persisted receipt."""

    def __init__(self, message: str, *, receipt: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.receipt = receipt


class RuleConflictError(RuleInstallationError):
    """The local and remote rule changed from the same recorded base."""


class EnvironmentRuleInstaller:
    """Install only explicit, pre-registered global or project rule bindings."""

    def __init__(
        self,
        registry: EnvironmentRegistry,
        *,
        target_node_id: str,
        registered_bindings: Mapping[str, Mapping[str, Any]],
    ):
        if not isinstance(target_node_id, str) or len(target_node_id) < 3:
            raise ValueError("target_node_id must contain at least three characters")
        self.registry = registry
        self.target_node_id = target_node_id
        # This mapping is trusted persisted configuration. A future CLI must
        # resolve binding IDs from that configuration and must never construct
        # a binding from an arbitrary command-line path.
        self.registered_bindings = {
            key: json.loads(json.dumps(value))
            for key, value in registered_bindings.items()
        }
        self.receipts_dir = self.registry.root / "receipts"
        self.transactions_dir = self.registry.root / "transactions" / "rule-installs"
        self.lock_path = self.registry.locks_dir / "environment-installer.lock"

    @classmethod
    def from_binding_registry(
        cls,
        registry: EnvironmentRegistry,
        *,
        target_node_id: str,
        binding_registry: Any,
    ) -> "EnvironmentRuleInstaller":
        """Build the trusted installer map from persisted node-local bindings."""

        mappings: Dict[str, Dict[str, Any]] = {}
        for item in binding_registry.get_rule_bindings():
            common = {
                "binding_id": item["binding_id"],
                "scope": item["scope"],
                "owner": item["owner"],
                "base_revision_id": item["base_revision_id"],
                "base_content_sha256": item["base_content_sha256"],
            }
            if item["scope"] == "global":
                normalized = {
                    **common,
                    "root": item["root"],
                    "relative_path": item["relative_path"],
                    "classification": item["classification"],
                    "install_strategy": item["install_strategy"],
                    "managed_block_id": item["managed_block_id"],
                }
            else:
                normalized = {
                    **common,
                    "project_id": item["project_id"],
                    "project_binding_id": item["project_binding_id"],
                }
            mappings[item["binding_id"]] = normalized
        return cls(
            registry,
            target_node_id=target_node_id,
            registered_bindings=mappings,
        )

    def install(
        self,
        *,
        artifact_id: str,
        revision_id: str,
        target_binding: str,
        apply: bool = False,
    ) -> Dict[str, Any]:
        """Preview by default; mutate only when ``apply=True`` is explicit."""

        with exclusive_lock(self.lock_path):
            context: Dict[str, Any] = {
                "artifact_id": artifact_id,
                "revision_id": revision_id,
                "target_binding": target_binding,
                "previous_installed_sha256": None,
                "content_sha256": "0" * 64,
                "rehearsal": {},
            }
            replacement_started = False
            original_bytes: Optional[bytes] = None
            target_path: Optional[Path] = None
            original_mode: Optional[int] = None
            transaction_dir: Optional[Path] = None
            installed_receipt_persisted = False
            try:
                resolved = self._prepare(
                    artifact_id=artifact_id,
                    revision_id=revision_id,
                    target_binding=target_binding,
                )
                context.update(resolved)
                if resolved["decision"] == "no-change":
                    return {
                        "status": "no-change",
                        "artifact_id": artifact_id,
                        "revision_id": revision_id,
                        "target_binding": target_binding,
                        "reason": resolved["reason"],
                    }
                preview = {
                    "status": "preview",
                    "artifact_id": artifact_id,
                    "revision_id": revision_id,
                    "target_binding": target_binding,
                    "target_path": str(resolved["target_path"]),
                    "decision": resolved["decision"],
                    "previous_installed_sha256": resolved[
                        "previous_installed_sha256"
                    ],
                    "candidate_sha256": resolved["candidate_sha256"],
                    "rehearsal": resolved["rehearsal"],
                }
                if not apply:
                    return preview

                target_path = resolved["target_path"]
                original_bytes = resolved["local_bytes"]
                original_mode = resolved["original_mode"]
                transaction_dir = self._create_transaction(
                    resolved=resolved,
                    artifact_id=artifact_id,
                    revision_id=revision_id,
                    target_binding=target_binding,
                )
                transaction = self._load_transaction(transaction_dir)
                context["rehearsal"] = {
                    **context["rehearsal"],
                    "transaction_id": transaction["transaction_id"],
                }
                candidate_path = self._write_candidate(
                    target_path,
                    resolved["candidate_bytes"],
                    original_mode,
                )
                try:
                    self._validate_candidate(
                        candidate_path,
                        expected_bytes=resolved["candidate_bytes"],
                        expected_mode=original_mode,
                        strategy=resolved["strategy"],
                        block_id=resolved.get("block_id"),
                    )
                    self._before_replace(target_path)
                    os.replace(candidate_path, target_path)
                    replacement_started = True
                    _fsync_directory(target_path.parent)
                    self._set_transaction_status(transaction_dir, "replaced")
                    self._after_replace(target_path)
                    final_bytes = self._read_regular_file(target_path)
                    final_hash = _sha256(final_bytes)
                    if final_hash != resolved["candidate_sha256"]:
                        raise ValueError("final target hash does not match candidate")
                    if stat.S_IMODE(target_path.stat().st_mode) != original_mode:
                        raise ValueError("target mode changed during installation")
                    if resolved["strategy"] == "managed-block":
                        self._assert_outside_unchanged(
                            original_bytes,
                            final_bytes,
                            resolved["block_id"],
                        )
                    receipt = self._receipt(
                        context,
                        result="installed",
                        final_installed_sha256=final_hash,
                        rollback={"attempted": False, "succeeded": False},
                    )
                    self._persist_receipt(receipt)
                    installed_receipt_persisted = True
                    self._complete_transaction(
                        transaction_dir, status="installed", receipt=receipt
                    )
                    self._delete_rollback_object(transaction_dir)
                    return {
                        "status": "installed",
                        "target_path": str(target_path),
                        "receipt": receipt,
                    }
                finally:
                    if candidate_path.exists():
                        candidate_path.unlink()
            except Exception as error:
                if isinstance(error, RuleInstallationError) and error.receipt is not None:
                    raise
                if not apply:
                    if isinstance(error, RuleConflictError):
                        raise
                    raise RuleInstallationError(str(error)) from error
                if installed_receipt_persisted:
                    raise RuleInstallationError(
                        "installed receipt is durable; transaction recovery is required",
                        receipt=receipt,
                    ) from error
                rolled_back = False
                rollback_error = None
                transaction = None
                if transaction_dir is not None:
                    try:
                        transaction = self._load_transaction(transaction_dir)
                        current_hash = (
                            _sha256(self._read_regular_file(target_path))
                            if target_path is not None
                            else None
                        )
                        if (
                            transaction["status"] == "replaced"
                            or current_hash == transaction["candidate_sha256"]
                        ):
                            self._restore_from_transaction(
                                transaction_dir, transaction, target_path
                            )
                            rolled_back = True
                    except Exception as rollback_failure:  # pragma: no cover - rare OS fault
                        rollback_error = str(rollback_failure)
                result = "rolled-back" if rolled_back else "failed"
                final_hash = None
                if target_path is not None and target_path.exists() and not is_link_like(target_path):
                    try:
                        final_hash = _sha256(self._read_regular_file(target_path))
                    except Exception:
                        final_hash = None
                receipt = self._receipt(
                    context,
                    result=result,
                    final_installed_sha256=final_hash,
                    rollback={
                        "attempted": replacement_started,
                        "succeeded": rolled_back,
                        "error": rollback_error,
                    },
                    error=str(error),
                )
                self._persist_receipt(receipt)
                if transaction_dir is not None:
                    if rolled_back:
                        self._complete_transaction(
                            transaction_dir, status="rolled-back", receipt=receipt
                        )
                        self._delete_rollback_object(transaction_dir)
                    elif replacement_started:
                        current_transaction = self._load_transaction(transaction_dir)
                        if current_transaction["status"] == "prepared":
                            self._set_transaction_status(
                                transaction_dir, "replaced"
                            )
                    else:
                        self._complete_transaction(
                            transaction_dir, status="aborted", receipt=receipt
                        )
                        self._delete_rollback_object(transaction_dir)
                exception_class = (
                    RuleConflictError
                    if isinstance(error, RuleConflictError)
                    else RuleInstallationError
                )
                raise exception_class(str(error), receipt=receipt) from error

    def recover_pending(self) -> Dict[str, Any]:
        """Recover durable prepared/replaced transactions under the installer lock."""

        with exclusive_lock(self.lock_path):
            result = {
                "status": "recovered",
                "installed": 0,
                "rolled_back": 0,
                "aborted": 0,
            }
            if not self.transactions_dir.exists():
                return result
            for transaction_dir in sorted(self.transactions_dir.iterdir()):
                if not transaction_dir.is_dir() or is_link_like(transaction_dir):
                    continue
                transaction_path = transaction_dir / "transaction.json"
                if not transaction_path.exists():
                    self._discard_incomplete_prepared_directory(transaction_dir)
                    result["aborted"] += 1
                    continue
                transaction = self._load_transaction(transaction_dir)
                if transaction["status"] in {"installed", "rolled-back", "aborted"}:
                    if (transaction_dir / "rollback.bin").exists():
                        self._cleanup_terminal_rollback(
                            transaction_dir, transaction
                        )
                    continue
                if transaction["status"] not in {"prepared", "replaced"}:
                    continue
                recovered = self._recover_transaction(transaction_dir, transaction)
                result[recovered] += 1
            return result

    def _prepare(
        self, *, artifact_id: str, revision_id: str, target_binding: str
    ) -> Dict[str, Any]:
        if not isinstance(target_binding, str) or target_binding not in self.registered_bindings:
            raise ValueError("target binding is not registered")
        binding = self._validate_registered_binding(
            target_binding, self.registered_bindings[target_binding]
        )
        artifact, remote_revision, remote_bytes = self._registered_revision(
            artifact_id, revision_id
        )
        if artifact["object_class"] not in {"global-rule", "project-rule"}:
            raise ValueError("artifact is not a rule")
        if artifact["scope"] != binding["scope"]:
            raise ValueError("artifact scope does not match target binding")
        if binding["scope"] == "project" and artifact["project_id"] != binding["project_id"]:
            raise ValueError("artifact project_id does not match target binding")
        if binding["classification"] in {"project-local", "generated", "excluded"}:
            raise ValueError(
                f"{binding['classification']} bindings cannot install remote rules"
            )
        strategy = binding["install_strategy"]
        if strategy == "whole-file" and not (
            binding["classification"] == "canonical"
            and binding["owner"] == "memory-wuxian"
        ):
            raise ValueError(
                "whole-file requires classification=canonical and owner=memory-wuxian"
            )
        if strategy not in ALLOWED_STRATEGIES:
            raise ValueError("unsupported rule installation strategy")

        target_path = self._resolve_target(binding)
        local_bytes = self._read_regular_file(target_path)
        try:
            local_bytes.decode("utf-8")
            remote_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("rule installation requires UTF-8 content") from error
        original_mode = stat.S_IMODE(target_path.stat().st_mode)

        base_revision_id = binding.get("base_revision_id")
        base_content_sha256 = binding.get("base_content_sha256")
        if not isinstance(base_revision_id, str) or not REVISION_ID_RE.fullmatch(
            base_revision_id
        ):
            raise ValueError("target binding has no recorded base_revision_id")
        if not isinstance(base_content_sha256, str) or not SHA256_RE.fullmatch(
            base_content_sha256
        ):
            raise ValueError("target binding has no recorded base_content_sha256")
        if remote_revision["base_revision_id"] != base_revision_id:
            raise ValueError("remote revision does not descend from recorded base")
        _, base_revision, base_bytes = self._registered_revision(
            artifact_id, base_revision_id
        )
        if (
            base_revision["content_sha256"] != base_content_sha256
            or _sha256(base_bytes) != base_content_sha256
        ):
            raise ValueError("recorded base hash does not match registered base")

        block_id = binding.get("managed_block_id")
        if strategy == "managed-block":
            if not isinstance(block_id, str) or not BLOCK_ID_RE.fullmatch(block_id):
                raise ValueError("managed-block requires a stable managed_block_id")
            local_view = self._comparison_view(
                self._extract_block(local_bytes, block_id)[0]
            )
            base_view = self._comparison_view(
                self._extract_block(base_bytes, block_id)[0]
            )
            remote_view = self._comparison_view(
                self._extract_block(remote_bytes, block_id)[0]
            )
        else:
            local_view, base_view, remote_view = local_bytes, base_bytes, remote_bytes

        decision, reason = self._three_way(local_view, base_view, remote_view)
        if decision == "conflict":
            raise RuleConflictError(reason)
        candidate_bytes = local_bytes
        if decision == "update":
            if strategy == "managed-block":
                candidate_bytes = self._replace_block(
                    local_bytes, remote_bytes, block_id
                )
                self._assert_outside_unchanged(local_bytes, candidate_bytes, block_id)
            else:
                candidate_bytes = remote_bytes
        candidate_hash = _sha256(candidate_bytes)
        return {
            "artifact": artifact,
            "remote_revision": remote_revision,
            "binding": binding,
            "content_sha256": remote_revision["content_sha256"],
            "base_revision_id": base_revision_id,
            "base_content_sha256": base_content_sha256,
            "target_path": target_path,
            "strategy": strategy,
            "block_id": block_id,
            "local_bytes": local_bytes,
            "candidate_bytes": candidate_bytes,
            "candidate_sha256": candidate_hash,
            "previous_installed_sha256": _sha256(local_bytes),
            "original_mode": original_mode,
            "decision": decision,
            "reason": reason,
            "rehearsal": {
                "decision": decision,
                "reason": reason,
                "strategy": strategy,
                "classification": binding["classification"],
                "owner": binding["owner"],
                "base_revision_id": base_revision_id,
                "base_content_sha256": base_content_sha256,
                "local_view_sha256": _sha256(local_view),
                "remote_view_sha256": _sha256(remote_view),
                "candidate_sha256": candidate_hash,
                "outside_bytes_unchanged": strategy == "managed-block",
                "mode_preserved": True,
            },
        }

    @staticmethod
    def _comparison_view(payload: bytes) -> bytes:
        """Normalize text line endings only for managed-block comparison."""

        return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    def _validate_registered_binding(
        self, binding_id: str, source: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if not isinstance(source, Mapping):
            raise ValueError("registered binding must be an object")
        scope = source.get("scope")
        if scope not in {"global", "project"}:
            raise ValueError("registered binding has invalid scope")
        owner = source.get("owner")
        if not isinstance(owner, str) or not owner:
            raise ValueError("registered binding has no owner")
        base_fields = {
            "binding_id",
            "scope",
            "owner",
            "base_revision_id",
            "base_content_sha256",
        }
        if source.get("binding_id") != binding_id:
            raise ValueError("registered binding identity mismatch")
        if scope == "global":
            allowed = base_fields | {
                "root",
                "relative_path",
                "classification",
                "install_strategy",
                "managed_block_id",
            }
            if set(source) - allowed:
                raise ValueError("registered global binding has unsupported fields")
            required = allowed - {"managed_block_id"}
            if not required.issubset(source):
                raise ValueError("registered global binding is incomplete")
            classification = source["classification"]
            strategy = source["install_strategy"]
            result = dict(source)
        else:
            allowed = base_fields | {"project_id", "project_binding_id"}
            if set(source) - allowed:
                raise ValueError("registered project binding has unsupported fields")
            if not allowed.issubset(source):
                raise ValueError("registered project binding is incomplete")
            project, project_binding = self._registered_project_binding(
                source["project_id"], source["project_binding_id"]
            )
            classification = project_binding["classification"]
            strategy = project_binding.get("install_strategy", "none")
            result = {
                **dict(source),
                "root": project["local_root"],
                "relative_path": project_binding["relative_path"],
                "classification": classification,
                "install_strategy": strategy,
                "managed_block_id": project_binding.get("managed_block_id"),
            }
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise ValueError("registered binding has invalid classification")
        if strategy not in ALLOWED_STRATEGIES:
            raise ValueError("registered binding is not installable")
        return result

    def _registered_project_binding(
        self, project_id: Any, project_binding_id: Any
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        matches = [
            project for project in self.registry.projects()
            if project["project_id"] == project_id
        ]
        if len(matches) != 1:
            raise ValueError("project binding references an unregistered project")
        project = matches[0]
        if not project["active"] or not project.get("local_root"):
            raise ValueError("project binding requires an active local_root")
        bindings = [
            binding for binding in project["rule_bindings"]
            if binding["binding_id"] == project_binding_id
        ]
        if len(bindings) != 1:
            raise ValueError("project rule binding is not registered")
        return project, bindings[0]

    def _registered_revision(
        self, artifact_id: str, revision_id: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any], bytes]:
        if not isinstance(artifact_id, str) or not isinstance(revision_id, str):
            raise ValueError("artifact_id and revision_id must be strings")
        registry = self.registry._read_registry()
        matching_events = [
            event for event in registry["events"]
            if event["operation"] == "artifact-revision"
            and event["artifact_id"] == artifact_id
            and event["revision_id"] == revision_id
        ]
        if len(matching_events) != 1:
            raise ValueError("artifact revision is not registered exactly once")
        event = matching_events[0]
        artifact = self.registry._read_relative_json(
            event["artifact_path"], "artifact_path"
        )
        revision = self.registry._read_relative_json(
            event["revision_path"], "revision_path"
        )
        self.registry._validate_artifact(artifact)
        self.registry._validate_revision(revision)
        if artifact["artifact_id"] != artifact_id or revision["artifact_id"] != artifact_id:
            raise ValueError("registered artifact/revision identity mismatch")
        if revision["revision_id"] != revision_id:
            raise ValueError("registered revision identity mismatch")
        self.registry._verify_revision_hash(revision)
        self.registry._verify_object(revision)
        object_path = self.registry._resolve_relative(
            revision["object_path"], "object_path"
        )
        content = self._read_regular_file(object_path)
        return artifact, revision, content

    def _resolve_target(self, binding: Dict[str, Any]) -> Path:
        root_text = binding.get("root")
        relative_text = binding.get("relative_path")
        if not isinstance(root_text, str) or not root_text:
            raise ValueError("target binding has no local root")
        if not isinstance(relative_text, str) or not relative_text:
            raise ValueError("target binding has no relative path")
        normalized = relative_text.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "." in pure.parts
            or normalized.startswith("//")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise ValueError("target binding relative path is unsafe")
        supplied_root = Path(root_text).expanduser()
        if is_link_like(supplied_root):
            raise ValueError("target binding root symlinks are forbidden")
        root = supplied_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("target binding root must be a real directory")
        candidate = root.joinpath(*pure.parts)
        if is_link_like(candidate):
            raise ValueError("target path symlinks are forbidden")
        parent = candidate.parent
        while parent != root:
            if is_link_like(parent):
                raise ValueError("target parent symlinks are forbidden")
            parent = parent.parent
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("target path escapes registered root") from error
        if not candidate.exists() or not candidate.is_file():
            raise ValueError("target must be an existing regular file")
        return candidate

    @staticmethod
    def _markers(block_id: str) -> Tuple[bytes, bytes]:
        begin = f"<!-- memory-wuxian:managed-block:{block_id}:begin -->".encode()
        end = f"<!-- memory-wuxian:managed-block:{block_id}:end -->".encode()
        return begin, end

    def _extract_block(
        self, document: bytes, block_id: str
    ) -> Tuple[bytes, bytes, bytes]:
        begin, end = self._markers(block_id)
        if document.count(begin) != 1 or document.count(end) != 1:
            raise ValueError("managed block marker must occur exactly once")
        start = document.index(begin)
        finish = document.index(end)
        if finish < start + len(begin):
            raise ValueError("managed block markers are reversed")
        block_end = finish + len(end)
        return document[start:block_end], document[: start + len(begin)], document[finish:]

    def _replace_block(self, local: bytes, remote: bytes, block_id: str) -> bytes:
        _, local_prefix, local_suffix = self._extract_block(local, block_id)
        remote_block, remote_prefix, remote_suffix = self._extract_block(
            remote, block_id
        )
        begin, end = self._markers(block_id)
        remote_body = remote_block[len(begin) : -len(end)]
        if not remote_prefix.endswith(begin) or not remote_suffix.startswith(end):
            raise ValueError("remote managed block structure is invalid")
        return local_prefix + remote_body + local_suffix

    def _assert_outside_unchanged(
        self, before: bytes, after: bytes, block_id: str
    ) -> None:
        _, before_prefix, before_suffix = self._extract_block(before, block_id)
        _, after_prefix, after_suffix = self._extract_block(after, block_id)
        if before_prefix != after_prefix or before_suffix != after_suffix:
            raise ValueError("bytes outside the managed block changed")

    @staticmethod
    def _three_way(local: bytes, base: bytes, remote: bytes) -> Tuple[str, str]:
        if local == remote:
            return "no-change", "local equals remote"
        if remote == base:
            return "no-change", "remote equals base"
        if local == base:
            return "update", "local equals base and remote changed"
        return "conflict", "local and remote both changed from the recorded base"

    def _write_candidate(self, target: Path, payload: bytes, mode: int) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.memory-wuxian.", suffix=".candidate", dir=target.parent
        )
        candidate = Path(name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, mode)
            handle = os.fdopen(descriptor, "wb")
            descriptor = -1
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if not hasattr(os, "fchmod"):
                os.chmod(candidate, mode)
            return candidate
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            if candidate.exists():
                candidate.unlink()
            raise

    def _validate_candidate(
        self,
        candidate: Path,
        *,
        expected_bytes: bytes,
        expected_mode: int,
        strategy: str,
        block_id: Optional[str],
    ) -> None:
        if is_link_like(candidate) or not candidate.is_file():
            raise ValueError("candidate is not a regular file")
        actual = candidate.read_bytes()
        actual.decode("utf-8")
        if actual != expected_bytes or _sha256(actual) != _sha256(expected_bytes):
            raise ValueError("candidate content verification failed")
        if stat.S_IMODE(candidate.stat().st_mode) != expected_mode:
            raise ValueError("candidate would expand or change permissions")
        if strategy == "managed-block":
            if block_id is None:
                raise ValueError("candidate block identifier is missing")
            self._extract_block(actual, block_id)

    def _replace_bytes(self, target: Path, payload: bytes, mode: int) -> None:
        candidate = self._write_candidate(target, payload, mode)
        try:
            self._validate_candidate(
                candidate,
                expected_bytes=payload,
                expected_mode=mode,
                strategy="whole-file",
                block_id=None,
            )
            os.replace(candidate, target)
            _fsync_directory(target.parent)
        finally:
            if candidate.exists():
                candidate.unlink()

    @staticmethod
    def _read_regular_file(path: Path) -> bytes:
        if is_link_like(path) or not path.is_file():
            raise ValueError("path must be a non-symlink regular file")
        return path.read_bytes()

    def _create_transaction(
        self,
        *,
        resolved: Dict[str, Any],
        artifact_id: str,
        revision_id: str,
        target_binding: str,
    ) -> Path:
        transaction_id = f"rule-{uuid.uuid4().hex}"
        transaction_dir = self.transactions_dir / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)
        _fsync_directory(transaction_dir.parent)
        rollback_path = transaction_dir / "rollback.bin"
        _atomic_bytes(rollback_path, resolved["local_bytes"], 0o600)
        target_canonical_path = str(resolved["target_path"].resolve(strict=True))
        timestamp = _now_iso()
        transaction = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "artifact_id": artifact_id,
            "revision_id": revision_id,
            "target_binding": target_binding,
            "target_node_id": self.target_node_id,
            "target_canonical_path": target_canonical_path,
            "target_path_sha256": _sha256(target_canonical_path.encode("utf-8")),
            "binding_sha256": self._binding_sha256(resolved["binding"]),
            "base_revision_id": resolved["base_revision_id"],
            "base_content_sha256": resolved["base_content_sha256"],
            "content_sha256": resolved["content_sha256"],
            "candidate_sha256": resolved["candidate_sha256"],
            "original_sha256": _sha256(resolved["local_bytes"]),
            "original_mode": resolved["original_mode"],
            "strategy": resolved["strategy"],
            "managed_block_id": resolved.get("block_id"),
            "rollback_object": "rollback.bin",
            "status": "prepared",
            "receipt_id": None,
            "receipt_sha256": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "finished_at": None,
        }
        self._write_transaction_record(transaction_dir, transaction)
        return transaction_dir

    @staticmethod
    def _binding_sha256(binding: Dict[str, Any]) -> str:
        return _sha256(_canonical_json(binding))

    @staticmethod
    def _transaction_metadata_sha256(transaction: Dict[str, Any]) -> str:
        payload = dict(transaction)
        payload.pop("metadata_sha256", None)
        return _sha256(_canonical_json(payload))

    def _write_transaction_record(
        self, transaction_dir: Path, transaction: Dict[str, Any]
    ) -> None:
        value = dict(transaction)
        value["metadata_sha256"] = self._transaction_metadata_sha256(value)
        _atomic_json(transaction_dir / "transaction.json", value)
        _fsync_directory(transaction_dir)

    def _load_transaction(self, transaction_dir: Path) -> Dict[str, Any]:
        if is_link_like(transaction_dir) or not transaction_dir.is_dir():
            raise ValueError("transaction directory is unsafe")
        path = transaction_dir / "transaction.json"
        if is_link_like(path) or not path.is_file():
            raise ValueError("transaction record is missing or unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "schema_version",
            "transaction_id",
            "artifact_id",
            "revision_id",
            "target_binding",
            "target_node_id",
            "target_canonical_path",
            "target_path_sha256",
            "binding_sha256",
            "base_revision_id",
            "base_content_sha256",
            "content_sha256",
            "candidate_sha256",
            "original_sha256",
            "original_mode",
            "strategy",
            "managed_block_id",
            "rollback_object",
            "status",
            "receipt_id",
            "receipt_sha256",
            "created_at",
            "updated_at",
            "finished_at",
            "metadata_sha256",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("transaction fields are invalid")
        if value["schema_version"] != 1:
            raise ValueError("transaction schema_version is unsupported")
        if value["transaction_id"] != transaction_dir.name or not re.fullmatch(
            r"rule-[0-9a-f]{32}", value["transaction_id"]
        ):
            raise ValueError("transaction identity mismatch")
        if value["target_node_id"] != self.target_node_id:
            raise ValueError("transaction belongs to a different node")
        for field in (
            "artifact_id",
            "target_binding",
            "target_node_id",
            "target_canonical_path",
        ):
            if not isinstance(value[field], str) or not value[field]:
                raise ValueError(f"transaction {field} is invalid")
        if not REVISION_ID_RE.fullmatch(value["revision_id"]) or not REVISION_ID_RE.fullmatch(
            value["base_revision_id"]
        ):
            raise ValueError("transaction revision identity is invalid")
        for field in (
            "target_path_sha256",
            "binding_sha256",
            "base_content_sha256",
            "content_sha256",
            "candidate_sha256",
            "original_sha256",
            "metadata_sha256",
        ):
            if not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field]):
                raise ValueError(f"transaction {field} is invalid")
        if value["rollback_object"] != "rollback.bin":
            raise ValueError("transaction rollback object is invalid")
        if type(value["original_mode"]) is not int or not 0 <= value["original_mode"] <= 0o7777:
            raise ValueError("transaction original_mode is invalid")
        if value["strategy"] not in ALLOWED_STRATEGIES:
            raise ValueError("transaction strategy is invalid")
        if value["strategy"] == "managed-block":
            if not isinstance(value["managed_block_id"], str) or not BLOCK_ID_RE.fullmatch(
                value["managed_block_id"]
            ):
                raise ValueError("transaction managed_block_id is invalid")
        elif value["managed_block_id"] is not None:
            raise ValueError("whole-file transaction cannot name a managed block")
        if value["status"] not in {
            "prepared",
            "replaced",
            "installed",
            "rolled-back",
            "aborted",
            "recovery-failed",
        }:
            raise ValueError("transaction status is invalid")
        if value["receipt_id"] is not None and not RECEIPT_ID_RE.fullmatch(
            value["receipt_id"]
        ):
            raise ValueError("transaction receipt_id is invalid")
        if value["receipt_sha256"] is not None and not SHA256_RE.fullmatch(
            value["receipt_sha256"]
        ):
            raise ValueError("transaction receipt_sha256 is invalid")
        for field in ("created_at", "updated_at"):
            try:
                datetime.fromisoformat(value[field].replace("Z", "+00:00"))
            except (AttributeError, ValueError) as error:
                raise ValueError(f"transaction {field} is invalid") from error
        if value["finished_at"] is not None:
            try:
                datetime.fromisoformat(value["finished_at"].replace("Z", "+00:00"))
            except (AttributeError, ValueError) as error:
                raise ValueError("transaction finished_at is invalid") from error
        if self._transaction_metadata_sha256(value) != value["metadata_sha256"]:
            raise ValueError("transaction metadata hash mismatch")
        return value

    def _set_transaction_status(self, transaction_dir: Path, status: str) -> None:
        transaction = self._load_transaction(transaction_dir)
        transaction["status"] = status
        transaction["updated_at"] = _now_iso()
        transaction["metadata_sha256"] = self._transaction_metadata_sha256(transaction)
        self._write_transaction_record(transaction_dir, transaction)

    def _abort_transaction(self, transaction_dir: Path) -> None:
        transaction = self._load_transaction(transaction_dir)
        transaction["status"] = "aborted"
        transaction["updated_at"] = _now_iso()
        transaction["finished_at"] = transaction["updated_at"]
        transaction["metadata_sha256"] = self._transaction_metadata_sha256(transaction)
        self._write_transaction_record(transaction_dir, transaction)

    def _complete_transaction(
        self,
        transaction_dir: Path,
        *,
        status: str,
        receipt: Dict[str, Any],
    ) -> None:
        receipt_path = self.receipts_dir / f"{receipt['receipt_id']}.json"
        if is_link_like(receipt_path) or not receipt_path.is_file():
            raise ValueError("verified receipt is not durable")
        persisted_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self._validate_receipt(persisted_receipt)
        if persisted_receipt != receipt:
            raise ValueError("persisted receipt does not match transaction receipt")
        transaction = self._load_transaction(transaction_dir)
        transaction["status"] = status
        transaction["receipt_id"] = receipt["receipt_id"]
        transaction["receipt_sha256"] = _sha256(receipt_path.read_bytes())
        transaction["updated_at"] = _now_iso()
        transaction["finished_at"] = transaction["updated_at"]
        transaction["metadata_sha256"] = self._transaction_metadata_sha256(transaction)
        self._write_transaction_record(transaction_dir, transaction)

    def _delete_rollback_object(self, transaction_dir: Path) -> None:
        rollback_path = transaction_dir / "rollback.bin"
        if rollback_path.exists():
            if is_link_like(rollback_path) or not rollback_path.is_file():
                raise ValueError("rollback object is unsafe")
            rollback_path.unlink()
            _fsync_directory(transaction_dir)

    def _read_rollback_object(
        self, transaction_dir: Path, transaction: Dict[str, Any]
    ) -> bytes:
        rollback_path = transaction_dir / "rollback.bin"
        if is_link_like(rollback_path) or not rollback_path.is_file():
            raise ValueError("rollback object is missing or unsafe")
        original = rollback_path.read_bytes()
        if _sha256(original) != transaction["original_sha256"]:
            raise ValueError("rollback object hash mismatch")
        return original

    def _restore_from_transaction(
        self,
        transaction_dir: Path,
        transaction: Dict[str, Any],
        target_path: Optional[Path],
    ) -> None:
        if target_path is None:
            raise ValueError("transaction target could not be resolved")
        original = self._read_rollback_object(transaction_dir, transaction)
        current = self._read_regular_file(target_path)
        current_hash = _sha256(current)
        if current_hash not in {
            transaction["candidate_sha256"],
            transaction["original_sha256"],
        }:
            raise ValueError("target changed after transaction; refusing overwrite")
        if current_hash != transaction["original_sha256"]:
            self._replace_bytes(target_path, original, transaction["original_mode"])
        if (
            self._read_regular_file(target_path) != original
            or stat.S_IMODE(target_path.stat().st_mode)
            != transaction["original_mode"]
        ):
            raise ValueError("durable rollback verification failed")

    def _recover_transaction(
        self, transaction_dir: Path, transaction: Dict[str, Any]
    ) -> str:
        binding, target_path = self._validate_recovery_context(transaction)
        original = self._read_rollback_object(transaction_dir, transaction)
        target_bytes = self._read_regular_file(target_path)
        target_hash = _sha256(target_bytes)
        target_mode = stat.S_IMODE(target_path.stat().st_mode)
        if transaction["status"] == "prepared":
            if (
                target_hash == transaction["original_sha256"]
                and target_mode == transaction["original_mode"]
            ):
                self._abort_transaction(transaction_dir)
                self._delete_rollback_object(transaction_dir)
                return "aborted"
            if (
                target_hash == transaction["candidate_sha256"]
                and target_mode == transaction["original_mode"]
            ):
                self._set_transaction_status(transaction_dir, "replaced")
                transaction = self._load_transaction(transaction_dir)
            else:
                raise RuleInstallationError(
                    "prepared transaction target no longer matches original or candidate"
                )

        installed_receipt = self._find_transaction_receipt(
            transaction, result="installed"
        )
        if installed_receipt is not None:
            if (
                target_hash != transaction["candidate_sha256"]
                or target_mode != transaction["original_mode"]
            ):
                raise RuleInstallationError(
                    "installed receipt exists but target does not match candidate"
                )
            self._complete_transaction(
                transaction_dir, status="installed", receipt=installed_receipt
            )
            self._delete_rollback_object(transaction_dir)
            return "installed"

        rolled_back_receipt = self._find_transaction_receipt(
            transaction, result="rolled-back"
        )
        if rolled_back_receipt is not None:
            if (
                target_hash != transaction["original_sha256"]
                or target_mode != transaction["original_mode"]
            ):
                raise RuleInstallationError(
                    "rolled-back receipt exists but target is not original"
                )
            self._complete_transaction(
                transaction_dir,
                status="rolled-back",
                receipt=rolled_back_receipt,
            )
            self._delete_rollback_object(transaction_dir)
            return "rolled_back"

        self._restore_from_transaction(transaction_dir, transaction, target_path)
        context = {
            "artifact_id": transaction["artifact_id"],
            "revision_id": transaction["revision_id"],
            "target_binding": transaction["target_binding"],
            "previous_installed_sha256": transaction["original_sha256"],
            "content_sha256": transaction["content_sha256"],
            "rehearsal": {
                "transaction_id": transaction["transaction_id"],
                "recovery": True,
                "strategy": transaction["strategy"],
                "binding_sha256": self._binding_sha256(binding),
            },
        }
        receipt = self._receipt(
            context,
            result="rolled-back",
            final_installed_sha256=transaction["original_sha256"],
            rollback={
                "attempted": True,
                "succeeded": True,
                "recovered_after_crash": True,
            },
            error="incomplete replaced transaction recovered",
        )
        self._persist_receipt(receipt)
        self._complete_transaction(
            transaction_dir, status="rolled-back", receipt=receipt
        )
        self._delete_rollback_object(transaction_dir)
        if self._read_regular_file(target_path) != original:
            raise RuleInstallationError("rollback changed after receipt commit")
        return "rolled_back"

    def _validate_recovery_context(
        self, transaction: Dict[str, Any], *, require_current_revision: bool = True
    ) -> Tuple[Dict[str, Any], Path]:
        binding_id = transaction["target_binding"]
        if binding_id not in self.registered_bindings:
            raise RuleInstallationError("transaction binding is no longer registered")
        binding = self._validate_registered_binding(
            binding_id, self.registered_bindings[binding_id]
        )
        if self._binding_sha256(binding) != transaction["binding_sha256"]:
            raise RuleInstallationError("registered binding or recorded base changed")
        if require_current_revision:
            registry_state = self.registry._read_registry()
            current_entry = registry_state["current_artifacts"].get(
                transaction["artifact_id"]
            )
            if (
                not isinstance(current_entry, dict)
                or current_entry.get("revision_id") != transaction["revision_id"]
            ):
                raise RuleInstallationError(
                    "registry current revision changed during transaction"
                )
        artifact, revision, _ = self._registered_revision(
            transaction["artifact_id"], transaction["revision_id"]
        )
        if revision["content_sha256"] != transaction["content_sha256"]:
            raise RuleInstallationError("registered revision content changed")
        if revision["base_revision_id"] != transaction["base_revision_id"]:
            raise RuleInstallationError("registered revision base changed")
        _, base_revision, base_bytes = self._registered_revision(
            transaction["artifact_id"], transaction["base_revision_id"]
        )
        if (
            base_revision["content_sha256"] != transaction["base_content_sha256"]
            or _sha256(base_bytes) != transaction["base_content_sha256"]
        ):
            raise RuleInstallationError("registered base hash changed")
        if artifact["object_class"] not in {"global-rule", "project-rule"}:
            raise RuleInstallationError("transaction artifact is not a rule")
        if artifact["scope"] != binding["scope"]:
            raise RuleInstallationError("transaction binding scope changed")
        if (
            binding["scope"] == "project"
            and artifact["project_id"] != binding["project_id"]
        ):
            raise RuleInstallationError("transaction project binding changed")
        target_path = self._resolve_target(binding)
        canonical_path = str(target_path.resolve(strict=True))
        if (
            canonical_path != transaction["target_canonical_path"]
            or _sha256(canonical_path.encode("utf-8"))
            != transaction["target_path_sha256"]
        ):
            raise RuleInstallationError("transaction target binding changed")
        return binding, target_path

    def _find_transaction_receipt(
        self, transaction: Dict[str, Any], *, result: str
    ) -> Optional[Dict[str, Any]]:
        if result not in {"installed", "rolled-back"}:
            raise ValueError("unsupported transaction receipt result")
        matches = []
        if not self.receipts_dir.exists():
            return None
        for receipt_path in sorted(self.receipts_dir.glob("*.json")):
            if is_link_like(receipt_path) or not receipt_path.is_file():
                continue
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self._validate_receipt(receipt)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                continue
            if (
                receipt_path.name == f"{receipt['receipt_id']}.json"
                and receipt["result"] == result
                and receipt["artifact_id"] == transaction["artifact_id"]
                and receipt["revision_id"] == transaction["revision_id"]
                and receipt["content_sha256"] == transaction["content_sha256"]
                and receipt["target_node_id"] == transaction["target_node_id"]
                and receipt["target_binding"] == transaction["target_binding"]
                and receipt["previous_installed_sha256"]
                == transaction["original_sha256"]
                and receipt["final_installed_sha256"]
                == (
                    transaction["candidate_sha256"]
                    if result == "installed"
                    else transaction["original_sha256"]
                )
                and receipt["rehearsal"].get("transaction_id")
                == transaction["transaction_id"]
                and (
                    (
                        result == "installed"
                        and receipt["rollback"].get("attempted") is False
                        and receipt["rollback"].get("succeeded") is False
                    )
                    or (
                        result == "rolled-back"
                        and receipt["rollback"].get("attempted") is True
                        and receipt["rollback"].get("succeeded") is True
                    )
                )
            ):
                matches.append(receipt)
        if len(matches) > 1:
            raise RuleInstallationError(
                f"multiple {result} receipts match one transaction"
            )
        return matches[0] if matches else None

    def _cleanup_terminal_rollback(
        self, transaction_dir: Path, transaction: Dict[str, Any]
    ) -> None:
        _, target_path = self._validate_recovery_context(
            transaction, require_current_revision=False
        )
        self._read_rollback_object(transaction_dir, transaction)
        target_hash = _sha256(self._read_regular_file(target_path))
        target_mode = stat.S_IMODE(target_path.stat().st_mode)
        expected_hash = (
            transaction["candidate_sha256"]
            if transaction["status"] == "installed"
            else transaction["original_sha256"]
        )
        if target_hash != expected_hash or target_mode != transaction["original_mode"]:
            raise RuleInstallationError(
                "terminal transaction target no longer matches committed state"
            )
        if transaction["status"] in {"installed", "rolled-back"}:
            receipt = self._load_terminal_receipt(transaction)
            expected_result = (
                "installed"
                if transaction["status"] == "installed"
                else "rolled-back"
            )
            if receipt["result"] != expected_result:
                raise RuleInstallationError(
                    "terminal transaction receipt result mismatch"
                )
        self._delete_rollback_object(transaction_dir)

    def _load_terminal_receipt(
        self, transaction: Dict[str, Any]
    ) -> Dict[str, Any]:
        if transaction["receipt_id"] is None or transaction["receipt_sha256"] is None:
            raise RuleInstallationError("terminal transaction has no receipt")
        path = self.receipts_dir / f"{transaction['receipt_id']}.json"
        if is_link_like(path) or not path.is_file():
            raise RuleInstallationError("terminal transaction receipt is missing")
        if _sha256(path.read_bytes()) != transaction["receipt_sha256"]:
            raise RuleInstallationError("terminal transaction receipt hash mismatch")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self._validate_receipt(receipt)
        if (
            receipt["receipt_id"] != transaction["receipt_id"]
            or receipt["artifact_id"] != transaction["artifact_id"]
            or receipt["revision_id"] != transaction["revision_id"]
            or receipt["target_binding"] != transaction["target_binding"]
            or receipt["rehearsal"].get("transaction_id")
            != transaction["transaction_id"]
        ):
            raise RuleInstallationError("terminal transaction receipt mismatch")
        return receipt

    def _discard_incomplete_prepared_directory(self, transaction_dir: Path) -> None:
        entries = list(transaction_dir.iterdir())
        if any(
            entry.name not in {"rollback.bin"} or is_link_like(entry)
            for entry in entries
        ):
            raise RuleInstallationError(
                "incomplete transaction directory contains unexpected entries"
            )
        for entry in entries:
            entry.unlink()
        transaction_dir.rmdir()
        _fsync_directory(self.transactions_dir)

    def _receipt(
        self,
        context: Dict[str, Any],
        *,
        result: str,
        final_installed_sha256: Optional[str],
        rollback: Dict[str, Any],
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        receipt = {
            "schema_version": 1,
            "receipt_id": f"rule-{uuid.uuid4().hex}",
            "artifact_id": str(context.get("artifact_id", "unknown")),
            "revision_id": (
                context["revision_id"]
                if isinstance(context.get("revision_id"), str)
                and REVISION_ID_RE.fullmatch(context["revision_id"])
                else "rev:" + "0" * 64
            ),
            "content_sha256": (
                context["content_sha256"]
                if isinstance(context.get("content_sha256"), str)
                and SHA256_RE.fullmatch(context["content_sha256"])
                else "0" * 64
            ),
            "target_node_id": self.target_node_id,
            "target_binding": str(context.get("target_binding", "unknown")),
            "previous_installed_sha256": context.get("previous_installed_sha256"),
            "final_installed_sha256": final_installed_sha256,
            "rehearsal": {
                **context.get("rehearsal", {}),
                **({"error": error} if error is not None else {}),
            },
            "result": result,
            "rollback": rollback,
            "created_at": _now_iso(),
        }
        self._validate_receipt(receipt)
        return receipt

    def _persist_receipt(self, receipt: Dict[str, Any]) -> None:
        self._validate_receipt(receipt)
        _atomic_json(self.receipts_dir / f"{receipt['receipt_id']}.json", receipt)

    @staticmethod
    def _validate_receipt(receipt: Dict[str, Any]) -> None:
        required = {
            "schema_version",
            "receipt_id",
            "artifact_id",
            "revision_id",
            "content_sha256",
            "target_node_id",
            "target_binding",
            "previous_installed_sha256",
            "final_installed_sha256",
            "rehearsal",
            "result",
            "rollback",
            "created_at",
        }
        if set(receipt) != required:
            raise ValueError("receipt fields do not match schema")
        if receipt["schema_version"] != 1:
            raise ValueError("receipt schema_version is unsupported")
        if not RECEIPT_ID_RE.fullmatch(receipt["receipt_id"]):
            raise ValueError("receipt_id is invalid")
        for field in ("artifact_id", "target_node_id", "target_binding"):
            if not isinstance(receipt[field], str) or len(receipt[field]) < (
                3 if field != "target_binding" else 1
            ):
                raise ValueError(f"receipt {field} is invalid")
        if not REVISION_ID_RE.fullmatch(receipt["revision_id"]):
            raise ValueError("receipt revision_id is invalid")
        if not SHA256_RE.fullmatch(receipt["content_sha256"]):
            raise ValueError("receipt content_sha256 is invalid")
        for field in ("previous_installed_sha256", "final_installed_sha256"):
            value = receipt[field]
            if value is not None and (
                not isinstance(value, str) or not SHA256_RE.fullmatch(value)
            ):
                raise ValueError(f"receipt {field} is invalid")
        if not isinstance(receipt["rehearsal"], dict):
            raise ValueError("receipt rehearsal must be an object")
        if receipt["result"] not in {"installed", "failed", "rolled-back"}:
            raise ValueError("receipt result is invalid")
        if not isinstance(receipt["rollback"], dict):
            raise ValueError("receipt rollback must be an object")
        try:
            datetime.fromisoformat(receipt["created_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError("receipt created_at is invalid") from error

    def _after_replace(self, target_path: Path) -> None:
        """Test hook called after atomic replacement and before final verification."""

    def _before_replace(self, target_path: Path) -> None:
        """Test hook called after durable prepare and before target replacement."""
