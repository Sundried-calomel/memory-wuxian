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
import traceback
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


class InstallerDiagnosticError(RuntimeError):
    """A bounded, component-owned failure that is safe to project into CI evidence."""

    def __init__(
        self,
        *,
        error_code: str,
        safe_message: str,
        source: dict[str, Any],
        checks: Sequence[dict[str, Any]],
    ) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message[:2048]
        self.source = dict(source)
        self.checks = [dict(item) for item in checks]

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "InstallerDiagnosticError":
        allowed = {"schema_version", "error_code", "safe_message", "source", "checks"}
        if set(document) != allowed or document.get("schema_version") != 1:
            raise ValueError("installer diagnostic document has an unsupported shape")
        checks = document.get("checks")
        source = document.get("source")
        if not isinstance(checks, list) or len(checks) > 32 or not isinstance(source, dict):
            raise ValueError("installer diagnostic document exceeds its closed bounds")
        if set(source) != {"file", "line", "function"}:
            raise ValueError("installer diagnostic source has an unsupported shape")
        for field in ("file", "function"):
            value = source.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 2048):
                raise ValueError("installer diagnostic source exceeds its string bound")
        if source.get("line") is not None and not isinstance(source.get("line"), int):
            raise ValueError("installer diagnostic source line is not an integer")
        if not isinstance(document.get("error_code"), str) or len(document["error_code"]) > 2048:
            raise ValueError("installer diagnostic error code is invalid")
        if not isinstance(document.get("safe_message"), str) or len(document["safe_message"]) > 2048:
            raise ValueError("installer diagnostic safe message is invalid")
        for item in checks:
            if not isinstance(item, dict) or set(item) != {"id", "passed", "expected", "observed"}:
                raise ValueError("installer diagnostic check has an unsupported shape")
            if not isinstance(item.get("id"), str) or not isinstance(item.get("passed"), bool):
                raise ValueError("installer diagnostic check has invalid identity or status")
            for field in ("expected", "observed"):
                value = item.get(field)
                if value is not None and not isinstance(value, (bool, int, str)):
                    raise ValueError("installer diagnostic check value is not a safe primitive")
                if isinstance(value, str) and len(value) > 2048:
                    raise ValueError("installer diagnostic check value exceeds its bound")
        return cls(
            error_code=str(document["error_code"]),
            safe_message=str(document["safe_message"]),
            source=source,
            checks=checks,
        )


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


def _bounded_source(exc: BaseException) -> dict[str, Any]:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return {"file": None, "line": None, "function": None}
    frame = frames[-1]
    return {
        "file": Path(frame.filename).name[:2048],
        "line": int(frame.lineno),
        "function": frame.name[:2048],
    }


def _failure_document(
    journal: dict[str, Any],
    exc: BaseException,
    mutation: PreparedMutation | None,
) -> dict[str, Any]:
    structured = isinstance(exc, InstallerDiagnosticError)
    return {
        "schema_version": 1,
        "recorded_at": _now(),
        "phase": str(journal.get("phase", "unknown")),
        "operation": str(journal.get("operation", "unknown")),
        "component": mutation.name if mutation is not None else "transaction-controller",
        "resource_id": mutation.resource_id if mutation is not None else "installer-transaction",
        "error_code": exc.error_code if structured else "unclassified-exception",
        "exception_type": type(exc).__name__,
        "safe_message": exc.safe_message if structured else None,
        "source": exc.source if structured else _bounded_source(exc),
        "package": dict(journal.get("package") or {}),
        "checks": list(exc.checks) if structured else [],
        "rollback": {"phase": "not-started", "status": "pending", "error_count": 0},
    }


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

    def _record_failure(
        self,
        journal: dict[str, Any],
        exc: BaseException,
        mutation: PreparedMutation | None,
    ) -> None:
        journal["error"] = str(exc)
        journal["failure"] = _failure_document(journal, exc, mutation)
        self._write(journal)

    def _rollback(
        self,
        journal: dict[str, Any],
        applied: list[tuple[int, PreparedMutation]],
        token: TransactionToken,
        success_code: InstallerExit,
    ) -> InstallerExit:
        journal["phase"] = "rolling-back"
        if journal.get("failure"):
            journal["failure"]["rollback"] = {
                "phase": "rolling-back", "status": "in-progress", "error_count": 0
            }
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
        if journal.get("failure"):
            journal["failure"]["rollback"] = {
                "phase": journal["phase"],
                "status": "incomplete" if rollback_errors else "verified",
                "error_count": len(rollback_errors),
            }
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
        active_mutation: PreparedMutation | None = None
        try:
            journal["phase"] = "applying"
            self._write(journal)
            for index, mutation in enumerate(mutations):
                active_mutation = mutation
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
                active_mutation = mutation
                if failure_point == f"before-verify:{mutation.name}":
                    raise RuntimeError(f"injected verification failure for {mutation.name}")
                evidence = mutation.verify(token)
                journal["mutations"][index]["status"] = "effect-verified"
                journal["mutations"][index]["verify_evidence"] = evidence
                self._write(journal)
            journal["phase"] = "effect-verified"
            self._write(journal)
        except BaseException as exc:
            self._record_failure(journal, exc, active_mutation)
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
                self._record_failure(journal, exc, mutation)
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
            active_mutation = next(
                (item for item, entry in zip(mutations, journal["mutations"]) if entry.get("status") == "preparing"),
                None,
            )
            self._record_failure(journal, exc, active_mutation)
            discard_errors = 0
            for mutation in reversed(prepared):
                try:
                    mutation.discard_prepare(token)
                except BaseException:
                    discard_errors += 1
            journal["phase"] = "prepare-failed"
            journal["failure"]["rollback"] = {
                "phase": "discard-prepare",
                "status": "verified" if discard_errors == 0 else "incomplete",
                "error_count": discard_errors,
            }
            self._write(journal)
            return TransactionResult(InstallerExit.PREPARE_FAILED, journal["phase"], self.journal_path, token.transaction_id)

        return self._apply_verify_commit(
            manifest, journal, mutations, token, failure_point=failure_point
        )
