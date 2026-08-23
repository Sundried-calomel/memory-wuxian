#!/usr/bin/env python3
"""Minimal allowlisted privilege broker for the Windows installer transaction."""

from __future__ import annotations

import argparse
import csv
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
import uuid


ALLOWED_OPERATIONS = frozenset({"install", "repair", "uninstall"})
REQUEST_FIELDS = frozenset(
    {
        "transaction_id",
        "operation",
        "target_sid",
        "manifest_path",
        "manifest_sha256",
        "controller_path",
        "controller_sha256",
        "nonce",
    }
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SID_RE = re.compile(r"S-1-(?:\d+-)+\d+")
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")


class BrokerExit(IntEnum):
    SUCCESS = 0
    INVALID_REQUEST = 20
    HASH_MISMATCH = 21
    SID_MISMATCH = 22
    PATH_ESCAPE = 23
    ELEVATION_CANCELLED = 24
    ELEVATION_DENIED = 25
    NONCE_REJECTED = 26
    CONTROLLER_FAILED = 27


class BrokerError(RuntimeError):
    def __init__(self, message: str, exit_code: BrokerExit) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise BrokerError("transaction_id must be a string", BrokerExit.INVALID_REQUEST)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise BrokerError("transaction_id is not a UUID", BrokerExit.INVALID_REQUEST) from exc
    if str(parsed) != value.lower():
        raise BrokerError("transaction_id is not canonical", BrokerExit.INVALID_REQUEST)
    return str(parsed)


def _closed_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise BrokerError(f"{field} must be a non-empty string", BrokerExit.INVALID_REQUEST)
    return value


def _trusted_file(value: str, roots: Sequence[Path], label: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise BrokerError(f"{label} must be absolute", BrokerExit.PATH_ESCAPE)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BrokerError(f"{label} is unavailable", BrokerExit.INVALID_REQUEST) from exc
    if not resolved.is_file():
        raise BrokerError(f"{label} is not a file", BrokerExit.INVALID_REQUEST)
    trusted = False
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=True))
            trusted = True
            break
        except (OSError, ValueError):
            continue
    if not trusted:
        raise BrokerError(f"{label} escapes trusted roots", BrokerExit.PATH_ESCAPE)
    return resolved


@dataclass(frozen=True)
class BrokerRequest:
    transaction_id: str
    operation: str
    target_sid: str
    manifest_path: Path
    manifest_sha256: str
    controller_path: Path
    controller_sha256: str
    nonce: str


class NonceLedger:
    """One-time nonce store bound to one transaction and target user SID."""

    def __init__(self, root: Path, *, lifetime: timedelta = timedelta(minutes=5)) -> None:
        self.root = root.resolve()
        self.lifetime = lifetime

    def _path(self, nonce: str, suffix: str) -> Path:
        name = hashlib.sha256(nonce.encode("ascii")).hexdigest()
        return self.root / f"{name}.{suffix}.json"

    def issue(self, transaction_id: str, target_sid: str, *, at: datetime | None = None) -> str:
        transaction_id = _canonical_uuid(transaction_id)
        if not SID_RE.fullmatch(target_sid):
            raise BrokerError("target_sid is invalid", BrokerExit.INVALID_REQUEST)
        issued = at or datetime.now(timezone.utc)
        nonce = secrets.token_urlsafe(32)
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "transaction_id": transaction_id,
            "target_sid": target_sid,
            "issued_at": issued.isoformat(),
            "expires_at": (issued + self.lifetime).isoformat(),
        }
        path = self._path(nonce, "issued")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
        return nonce

    def consume(
        self,
        nonce: str,
        transaction_id: str,
        target_sid: str,
        *,
        at: datetime | None = None,
    ) -> None:
        if not NONCE_RE.fullmatch(nonce):
            raise BrokerError("nonce is malformed", BrokerExit.NONCE_REJECTED)
        issued_path = self._path(nonce, "issued")
        consumed_path = self._path(nonce, "consumed")
        try:
            record = json.loads(issued_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrokerError("nonce is missing, consumed, or corrupt", BrokerExit.NONCE_REJECTED) from exc
        if record.get("transaction_id") != transaction_id:
            raise BrokerError("nonce transaction mismatch", BrokerExit.NONCE_REJECTED)
        if record.get("target_sid") != target_sid:
            raise BrokerError("nonce SID mismatch", BrokerExit.SID_MISMATCH)
        try:
            expires = datetime.fromisoformat(record["expires_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokerError("nonce expiry is invalid", BrokerExit.NONCE_REJECTED) from exc
        current = at or datetime.now(timezone.utc)
        if expires <= current:
            raise BrokerError("nonce expired", BrokerExit.NONCE_REJECTED)
        try:
            os.replace(issued_path, consumed_path)
        except OSError as exc:
            raise BrokerError("nonce could not be consumed", BrokerExit.NONCE_REJECTED) from exc


class AllowlistedUacBroker:
    """Validate one closed request, then call one injected transaction dispatcher."""

    def __init__(
        self,
        *,
        manifest_roots: Sequence[Path],
        controller_roots: Sequence[Path],
        nonce_ledger: NonceLedger,
        dispatcher: Callable[[BrokerRequest], int],
        expected_sid: str | None = None,
    ) -> None:
        if not manifest_roots or not controller_roots:
            raise ValueError("trusted roots must not be empty")
        self.manifest_roots = tuple(manifest_roots)
        self.controller_roots = tuple(controller_roots)
        self.nonce_ledger = nonce_ledger
        self.dispatcher = dispatcher
        self.expected_sid = expected_sid

    def validate(self, payload: Mapping[str, Any], *, consume_nonce: bool = True) -> BrokerRequest:
        fields = frozenset(payload)
        if fields != REQUEST_FIELDS:
            unknown = sorted(fields - REQUEST_FIELDS)
            missing = sorted(REQUEST_FIELDS - fields)
            raise BrokerError(
                f"closed request mismatch; unknown={unknown}, missing={missing}",
                BrokerExit.INVALID_REQUEST,
            )
        transaction_id = _canonical_uuid(payload["transaction_id"])
        operation = _closed_string(payload, "operation")
        if operation not in ALLOWED_OPERATIONS:
            raise BrokerError("operation is not allowlisted", BrokerExit.INVALID_REQUEST)
        target_sid = _closed_string(payload, "target_sid")
        if not SID_RE.fullmatch(target_sid):
            raise BrokerError("target_sid is invalid", BrokerExit.INVALID_REQUEST)
        if self.expected_sid is not None and target_sid != self.expected_sid:
            raise BrokerError("target_sid does not match the current user", BrokerExit.SID_MISMATCH)
        nonce = _closed_string(payload, "nonce")
        manifest_sha256 = _closed_string(payload, "manifest_sha256").lower()
        controller_sha256 = _closed_string(payload, "controller_sha256").lower()
        if not SHA256_RE.fullmatch(manifest_sha256) or not SHA256_RE.fullmatch(controller_sha256):
            raise BrokerError("request hash is invalid", BrokerExit.INVALID_REQUEST)
        manifest_path = _trusted_file(
            _closed_string(payload, "manifest_path"), self.manifest_roots, "manifest_path"
        )
        controller_path = _trusted_file(
            _closed_string(payload, "controller_path"), self.controller_roots, "controller_path"
        )
        if sha256_file(manifest_path) != manifest_sha256:
            raise BrokerError("manifest hash mismatch", BrokerExit.HASH_MISMATCH)
        if sha256_file(controller_path) != controller_sha256:
            raise BrokerError("controller hash mismatch", BrokerExit.HASH_MISMATCH)
        if consume_nonce:
            self.nonce_ledger.consume(nonce, transaction_id, target_sid)
        return BrokerRequest(
            transaction_id=transaction_id,
            operation=operation,
            target_sid=target_sid,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            controller_path=controller_path,
            controller_sha256=controller_sha256,
            nonce=nonce,
        )

    def dispatch(self, payload: Mapping[str, Any]) -> int:
        request = self.validate(payload)
        return_code = self.dispatcher(request)
        return int(return_code)


def classify_elevation_failure(error: BaseException | int) -> BrokerExit:
    code = error if isinstance(error, int) else getattr(error, "winerror", None)
    if code == 1223:
        return BrokerExit.ELEVATION_CANCELLED
    if code in {5, 740}:
        return BrokerExit.ELEVATION_DENIED
    return BrokerExit.CONTROLLER_FAILED


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def current_user_sid(*, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str:
    completed = runner(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    rows = list(csv.reader(completed.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) < 2 or not SID_RE.fullmatch(rows[0][1].strip()):
        raise BrokerError("current user SID could not be resolved", BrokerExit.SID_MISMATCH)
    return rows[0][1].strip()


def build_request(
    manifest_path: Path,
    controller_path: Path,
    ledger: NonceLedger,
    *,
    target_sid: str,
) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    operation = manifest.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        raise BrokerError("manifest operation is not allowlisted", BrokerExit.INVALID_REQUEST)
    transaction_id = str(uuid.uuid4())
    return {
        "transaction_id": transaction_id,
        "operation": str(operation),
        "target_sid": target_sid,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256_file(manifest_path),
        "controller_path": str(controller_path.resolve()),
        "controller_sha256": sha256_file(controller_path),
        "nonce": ledger.issue(transaction_id, target_sid),
    }


def shell_execute_elevated(executable: Path, arguments: Sequence[str]) -> int:
    if os.name != "nt":
        raise OSError(740, "Windows elevation is unavailable")

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong), ("hIconOrMonitor", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400
    info.lpVerb = "runas"
    info.lpFile = str(executable)
    info.lpParameters = subprocess.list2cmdline([str(item) for item in arguments])
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError()
    try:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError()
        return int(exit_code.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)


def launch(
    manifest_path: Path,
    controller_path: Path,
    request_path: Path,
    nonce_root: Path,
    *,
    elevater: Callable[[Path, Sequence[str]], int] = shell_execute_elevated,
) -> int:
    manifest_path = manifest_path.resolve(strict=True)
    controller_path = controller_path.resolve(strict=True)
    target_sid = current_user_sid()
    ledger = NonceLedger(nonce_root)
    payload = build_request(manifest_path, controller_path, ledger, target_sid=target_sid)
    _atomic_write(request_path, _canonical_json(payload))
    request_sha256 = sha256_file(request_path)
    broker = AllowlistedUacBroker(
        manifest_roots=[manifest_path.parent],
        controller_roots=[controller_path.parent],
        nonce_ledger=ledger,
        dispatcher=lambda _request: 0,
        expected_sid=target_sid,
    )
    broker.validate(payload, consume_nonce=False)
    try:
        return elevater(
            Path(sys.executable),
            [
                str(Path(__file__).resolve()),
                "--dispatch-request", str(request_path),
                "--request-sha256", request_sha256,
                "--nonce-root", str(nonce_root),
            ],
        )
    except OSError as error:
        return int(classify_elevation_failure(error))


def dispatch_request(request_path: Path, request_sha256: str, nonce_root: Path) -> int:
    try:
        if not SHA256_RE.fullmatch(request_sha256) or sha256_file(request_path) != request_sha256:
            return int(BrokerExit.HASH_MISMATCH)
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return int(BrokerExit.INVALID_REQUEST)
    target_sid = current_user_sid()

    def run_controller(request: BrokerRequest) -> int:
        try:
            from windows_install_manifest import read_manifest
        except ModuleNotFoundError:
            from scripts.windows_install_manifest import read_manifest
        manifest = read_manifest(request.manifest_path)
        if manifest.operation != request.operation:
            raise BrokerError("request operation does not match manifest", BrokerExit.INVALID_REQUEST)
        if Path(sys.executable).resolve() != manifest.runtime_bundle.python_executable.resolve():
            raise BrokerError("elevated broker runtime does not match manifest", BrokerExit.HASH_MISMATCH)
        completed = subprocess.run(
            [str(manifest.runtime_bundle.python_executable), str(request.controller_path), "--execute-manifest", str(request.manifest_path)],
            check=False,
        )
        return int(completed.returncode)

    broker = AllowlistedUacBroker(
        manifest_roots=[Path(str(payload.get("manifest_path", ""))).resolve().parent],
        controller_roots=[Path(str(payload.get("controller_path", ""))).resolve().parent],
        nonce_ledger=NonceLedger(nonce_root),
        dispatcher=run_controller,
        expected_sid=target_sid,
    )
    try:
        return broker.dispatch(payload)
    except BrokerError as error:
        return int(error.exit_code)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--launch-manifest")
    mode.add_argument("--dispatch-request")
    parser.add_argument("--controller")
    parser.add_argument("--request-output")
    parser.add_argument("--request-sha256")
    parser.add_argument("--nonce-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.launch_manifest:
            if not args.controller or not args.request_output:
                raise BrokerError("broker launch arguments are incomplete", BrokerExit.INVALID_REQUEST)
            return launch(Path(args.launch_manifest), Path(args.controller), Path(args.request_output), Path(args.nonce_root))
        if not args.request_sha256:
            raise BrokerError("broker dispatch hash is missing", BrokerExit.INVALID_REQUEST)
        return dispatch_request(Path(args.dispatch_request), args.request_sha256, Path(args.nonce_root))
    except BrokerError as error:
        return int(error.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
