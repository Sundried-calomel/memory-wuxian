#!/usr/bin/env python3
"""Single mutation owner for the unified Windows installer transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any, Callable, Protocol, Sequence
import uuid

try:
    from platform_transaction import atomic_write_canonical_json, read_canonical_json
except ModuleNotFoundError:
    from scripts.platform_transaction import atomic_write_canonical_json, read_canonical_json

try:
    from windows_install_manifest import WindowsInstallManifest
except ModuleNotFoundError:
    from scripts.windows_install_manifest import WindowsInstallManifest


class InstallerExit(IntEnum):
    SUCCESS = 0
    INVALID_MANIFEST = 30
    PREPARE_FAILED = 31
    APPLY_FAILED_ROLLED_BACK = 32
    APPLY_FAILED_ROLLBACK_INCOMPLETE = 33
    EFFECT_VERIFICATION_FAILED = 34
    COMMIT_FAILED = 35


@dataclass(frozen=True)
class TransactionToken:
    transaction_id: str
    secret: str


class PreparedMutation(Protocol):
    name: str
    resource_id: str
    compensation: str
    owned_paths: tuple[str, ...]
    owned_tasks: tuple[str, ...]
    owned_registry_values: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    def prepare(self, token: TransactionToken) -> dict[str, Any]: ...
    def restore_prepare(self, token: TransactionToken, evidence: dict[str, Any]) -> None: ...
    def discard_prepare(self, token: TransactionToken) -> None: ...
    def apply(self, token: TransactionToken) -> dict[str, Any]: ...
    def verify(self, token: TransactionToken) -> dict[str, Any]: ...
    def commit(self, token: TransactionToken) -> dict[str, Any]: ...
    def rollback(self, token: TransactionToken) -> dict[str, Any]: ...
    def rollback_verify(self, token: TransactionToken) -> dict[str, Any]: ...


class TransactionAdapter(Protocol):
    def build(
        self, manifest: WindowsInstallManifest, token: TransactionToken
    ) -> Sequence[PreparedMutation]: ...


@dataclass(frozen=True)
class TransactionResult:
    exit_code: InstallerExit
    phase: str
    journal_path: Path
    transaction_id: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _manifest_binding(manifest: WindowsInstallManifest) -> tuple[dict[str, Any], str]:
    document = manifest.to_document()
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return document, hashlib.sha256(payload).hexdigest()


class WindowsInstallerTransaction:
    """Own ordering, durable checkpoints, verification, commit, and rollback."""

    def __init__(
        self,
        *,
        journal_path: Path,
        adapters: Sequence[TransactionAdapter],
        identity_validator: Callable[[WindowsInstallManifest], None] | None = None,
    ) -> None:
        self.journal_path = journal_path.resolve()
        self.adapters = tuple(adapters)
        self.identity_validator = identity_validator

    def _write(self, journal: dict[str, Any]) -> None:
        journal["updated_at"] = _now()
        atomic_write_canonical_json(self.journal_path, journal)

    def _rollback(
        self,
        journal: dict[str, Any],
        applied: list[tuple[int, PreparedMutation]],
        token: TransactionToken,
        success_code: InstallerExit,
    ) -> InstallerExit:
        journal["phase"] = "rolling-back"
        self._write(journal)
        rollback_errors: list[str] = []
        for index, mutation in reversed(applied):
            try:
                evidence = mutation.rollback(token)
                journal["mutations"][index]["status"] = "rollback-applied"
                journal["mutations"][index]["rollback_evidence"] = evidence
                self._write(journal)
                verification = mutation.rollback_verify(token)
                journal["mutations"][index]["status"] = "rollback-verified"
                journal["mutations"][index]["rollback_verify_evidence"] = verification
            except BaseException as rollback_exc:
                rollback_errors.append(f"{mutation.name}: {rollback_exc}")
                journal["mutations"][index]["status"] = "rollback-failed"
            self._write(journal)
        if rollback_errors:
            journal["phase"] = "rollback-incomplete"
            journal["rollback_errors"] = rollback_errors
            code = InstallerExit.APPLY_FAILED_ROLLBACK_INCOMPLETE
        else:
            journal["phase"] = "rollback-verified"
            self._write(journal)
            journal["phase"] = "rolled-back"
            code = success_code
        self._write(journal)
        return code

    @staticmethod
    def _terminal(phase: str) -> bool:
        return phase in {"committed", "rolled-back", "rollback-incomplete", "prepare-failed", "invalid-manifest"}

    def _build(self, manifest: WindowsInstallManifest, token: TransactionToken) -> list[PreparedMutation]:
        mutations: list[PreparedMutation] = []
        for adapter in self.adapters:
            mutations.extend(adapter.build(manifest, token))
        names = [item.name for item in mutations]
        resources = [item.resource_id for item in mutations]
        if len(names) != len(set(names)) or len(resources) != len(set(resources)):
            raise RuntimeError("prepared mutation names and resources must be unique")
        for item in mutations:
            for field in ("owned_paths", "owned_tasks", "owned_registry_values", "forbidden_paths"):
                value = getattr(item, field, None)
                if not isinstance(value, tuple) or any(not isinstance(entry, str) or not entry for entry in value):
                    raise RuntimeError(f"{item.name} has no closed {field} declaration")
        return mutations

    def _resume(
        self,
        manifest: WindowsInstallManifest,
        journal: dict[str, Any],
        *,
        failure_point: str | None,
    ) -> TransactionResult:
        manifest_document, manifest_sha256 = _manifest_binding(manifest)
        if journal.get("manifest") != manifest_document or journal.get("manifest_sha256") != manifest_sha256:
            raise RuntimeError("unfinished transaction manifest does not match the request")
        if self.identity_validator is not None:
            self.identity_validator(manifest)
        token_data = journal.get("transaction_token") or {}
        token = TransactionToken(str(journal["transaction_id"]), str(token_data.get("secret", "")))
        if not token.secret:
            raise RuntimeError("unfinished transaction has no recovery token")
        mutations = self._build(manifest, token)
        entries = journal.get("mutations") or []
        if [
            (
                item.name, item.resource_id, list(item.owned_paths),
                list(item.owned_tasks), list(item.owned_registry_values),
                list(item.forbidden_paths),
            )
            for item in mutations
        ] != [
            (
                entry.get("name"), entry.get("resource_id"), entry.get("owned_paths"),
                entry.get("owned_tasks"), entry.get("owned_registry_values"),
                entry.get("forbidden_paths"),
            )
            for entry in entries
        ]:
            raise RuntimeError("unfinished transaction mutation set drifted")
        if journal.get("phase") in {"planned", "preparing", "prepared"}:
            for mutation, entry in zip(mutations, entries):
                if entry.get("status") == "prepared":
                    mutation.restore_prepare(token, entry["prepare_evidence"])
                    continue
                entry["status"] = "preparing"
                journal["phase"] = "preparing"
                self._write(journal)
                entry["prepare_evidence"] = mutation.prepare(token)
                entry["status"] = "prepared"
                self._write(journal)
            journal["phase"] = "prepared"
            self._write(journal)
            return self._apply_verify_commit(
                manifest, journal, mutations, token, failure_point=failure_point
            )
        for mutation, entry in zip(mutations, entries):
            mutation.restore_prepare(token, entry["prepare_evidence"])
        applied = [
            (index, mutation) for index, (mutation, entry) in enumerate(zip(mutations, entries))
            if entry.get("status") not in {"preparing", "prepared", "rollback-verified"}
        ]
        if journal.get("phase") == "committing":
            return self._commit(manifest, journal, applied, token)
        code = self._rollback(
            journal, applied, token, InstallerExit.APPLY_FAILED_ROLLED_BACK
        )
        return TransactionResult(code, journal["phase"], self.journal_path, token.transaction_id)

    def _apply_verify_commit(
        self,
        manifest: WindowsInstallManifest,
        journal: dict[str, Any],
        mutations: list[PreparedMutation],
        token: TransactionToken,
        *,
        failure_point: str | None,
    ) -> TransactionResult:
        applied: list[tuple[int, PreparedMutation]] = []
        try:
            journal["phase"] = "applying"
            self._write(journal)
            for index, mutation in enumerate(mutations):
                if failure_point == f"before-apply:{mutation.name}":
                    raise RuntimeError(f"injected failure before {mutation.name}")
                applied.append((index, mutation))
                journal["mutations"][index]["status"] = "applying"
                self._write(journal)
                evidence = mutation.apply(token)
                journal["mutations"][index]["status"] = "applied"
                journal["mutations"][index]["apply_evidence"] = evidence
                self._write(journal)
            journal["phase"] = "applied"
            self._write(journal)
            journal["phase"] = "verifying-effects"
            self._write(journal)
            for index, mutation in applied:
                if failure_point == f"before-verify:{mutation.name}":
                    raise RuntimeError(f"injected verification failure for {mutation.name}")
                evidence = mutation.verify(token)
                journal["mutations"][index]["status"] = "effect-verified"
                journal["mutations"][index]["verify_evidence"] = evidence
                self._write(journal)
            journal["phase"] = "effect-verified"
            self._write(journal)
        except BaseException as exc:
            journal["error"] = str(exc)
            failure_code = InstallerExit.EFFECT_VERIFICATION_FAILED if journal.get("phase") == "verifying-effects" else InstallerExit.APPLY_FAILED_ROLLED_BACK
            code = self._rollback(journal, applied, token, failure_code)
            return TransactionResult(code, journal["phase"], self.journal_path, token.transaction_id)
        return self._commit(manifest, journal, applied, token)

    def _commit(
        self,
        manifest: WindowsInstallManifest,
        journal: dict[str, Any],
        applied: list[tuple[int, PreparedMutation]],
        token: TransactionToken,
    ) -> TransactionResult:
        journal["phase"] = "committing"
        self._write(journal)
        for index, mutation in reversed(applied):
            entry = journal["mutations"][index]
            if entry.get("status") == "committed":
                continue
            entry["status"] = "commit-intent"
            self._write(journal)
            try:
                evidence = mutation.commit(token)
            except BaseException as exc:
                journal["error"] = str(exc)
                code = self._rollback(journal, applied, token, InstallerExit.COMMIT_FAILED)
                return TransactionResult(code, journal["phase"], self.journal_path, token.transaction_id)
            entry["commit_evidence"] = evidence
            entry["status"] = "committed"
            try:
                self._write(journal)
            except BaseException:
                return TransactionResult(InstallerExit.COMMIT_FAILED, "committing", self.journal_path, token.transaction_id)
        journal["phase"] = "committed"
        self._write(journal)
        return TransactionResult(InstallerExit.SUCCESS, journal["phase"], self.journal_path, token.transaction_id)

    def execute(
        self,
        manifest: WindowsInstallManifest,
        *,
        failure_point: str | None = None,
    ) -> TransactionResult:
        if self.journal_path.is_file():
            existing = read_canonical_json(self.journal_path)
            phase = str(existing.get("phase", ""))
            manifest_document, manifest_sha256 = _manifest_binding(manifest)
            if phase == "rollback-incomplete":
                return TransactionResult(InstallerExit.APPLY_FAILED_ROLLBACK_INCOMPLETE, phase, self.journal_path, str(existing["transaction_id"]))
            if not self._terminal(phase):
                try:
                    return self._resume(
                        manifest, existing, failure_point=failure_point
                    )
                except (RuntimeError, ValueError, OSError):
                    return TransactionResult(InstallerExit.INVALID_MANIFEST, "recovery-request-mismatch", self.journal_path, str(existing["transaction_id"]))
            if phase == "committed" and existing.get("manifest") == manifest_document and existing.get("manifest_sha256") == manifest_sha256:
                if self.identity_validator is not None:
                    self.identity_validator(manifest)
                return TransactionResult(InstallerExit.SUCCESS, phase, self.journal_path, str(existing["transaction_id"]))
            rotated = self.journal_path.with_name(
                f"{self.journal_path.stem}.{existing.get('transaction_id', 'terminal')}{self.journal_path.suffix}"
            )
            self.journal_path.replace(rotated)
        if self.identity_validator is not None:
            try:
                self.identity_validator(manifest)
            except BaseException as exc:
                rejected = {
                    "schema_version": 1,
                    "transaction_id": str(uuid.uuid4()),
                    "phase": "invalid-manifest",
                    "source_entrypoint": manifest.source_entrypoint,
                    "operation": manifest.operation,
                    "package": {"version": manifest.package.version, "sha256": manifest.package.sha256},
                    "mutations": [],
                    "created_at": _now(),
                    "updated_at": _now(),
                    "error": str(exc),
                }
                self._write(rejected)
                return TransactionResult(InstallerExit.INVALID_MANIFEST, "invalid-manifest", self.journal_path, rejected["transaction_id"])
        token = TransactionToken(str(uuid.uuid4()), secrets.token_hex(32))
        manifest_document, manifest_sha256 = _manifest_binding(manifest)
        mutations = self._build(manifest, token)
        journal: dict[str, Any] = {
            "schema_version": 1,
            "transaction_id": token.transaction_id,
            "transaction_token": {"secret": token.secret},
            "phase": "planned",
            "source_entrypoint": manifest.source_entrypoint,
            "operation": manifest.operation,
            "package": {
                "version": manifest.package.version,
                "sha256": manifest.package.sha256,
            },
            "manifest": manifest_document,
            "manifest_sha256": manifest_sha256,
            "mutations": [
                {
                    "name": item.name, "resource_id": item.resource_id,
                    "compensation": item.compensation, "prepare_evidence": None,
                    "owned_paths": list(item.owned_paths),
                    "owned_tasks": list(item.owned_tasks),
                    "owned_registry_values": list(item.owned_registry_values),
                    "forbidden_paths": list(item.forbidden_paths),
                    "status": "pending", "apply_evidence": None,
                    "verify_evidence": None, "commit_evidence": None,
                    "rollback_evidence": None, "rollback_verify_evidence": None,
                }
                for item in mutations
            ],
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
            "test_envelope": {"failure_point": failure_point} if failure_point else None,
        }
        self._write(journal)
        prepared: list[PreparedMutation] = []
        try:
            for item, entry in zip(mutations, journal["mutations"]):
                journal["phase"] = "preparing"
                entry["status"] = "preparing"
                self._write(journal)
                entry["prepare_evidence"] = item.prepare(token)
                entry["status"] = "prepared"
                prepared.append(item)
                self._write(journal)
            journal["phase"] = "prepared"
            self._write(journal)
        except BaseException as exc:
            for mutation in reversed(prepared):
                try:
                    mutation.discard_prepare(token)
                except BaseException:
                    pass
            journal["phase"] = "prepare-failed"
            journal["error"] = str(exc)
            self._write(journal)
            return TransactionResult(InstallerExit.PREPARE_FAILED, journal["phase"], self.journal_path, token.transaction_id)

        return self._apply_verify_commit(
            manifest, journal, mutations, token, failure_point=failure_point
        )
