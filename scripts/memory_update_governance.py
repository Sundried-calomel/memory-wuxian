#!/usr/bin/env python3
"""Deterministic update selection and verified staging without installation."""

from __future__ import annotations

import hashlib
import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


CHANNELS = {"stable", "beta", "development"}
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_DELTA_OPERATIONS = 100_000
SIGNING_IDENTITY = "memory-wuxian-update"
SIGNING_NAMESPACE = "memory-wuxian-update-v1"


def verify_metadata_signature(
    metadata_path: Path,
    signature_path: Path,
    allowed_signers_path: Path,
    *,
    runner=subprocess.run,
) -> None:
    executable = shutil.which("ssh-keygen")
    if not executable:
        raise ValueError("ssh-keygen is required to verify update metadata")
    if not metadata_path.is_file() or not signature_path.is_file() or not allowed_signers_path.is_file():
        raise ValueError("signed update metadata inputs are missing")
    completed = runner(
        [
            executable,
            "-Y",
            "verify",
            "-f",
            str(allowed_signers_path),
            "-I",
            SIGNING_IDENTITY,
            "-n",
            SIGNING_NAMESPACE,
            "-s",
            str(signature_path),
        ],
        input=metadata_path.read_bytes(),
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("update metadata signature verification failed")


def load_signed_metadata(
    metadata_path: Path,
    signature_path: Path,
    allowed_signers_path: Path,
) -> dict[str, Any]:
    verify_metadata_signature(metadata_path, signature_path, allowed_signers_path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed update metadata is invalid UTF-8 JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("signed update metadata must be an object")
    return metadata


def _version_key(value: str) -> tuple[int, int, int, int, int]:
    import re
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(?:-beta\.|b)(\d+))?", value)
    if not match:
        raise ValueError("unsupported update version")
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    beta = match.group(4)
    return major, minor, patch, 1 if beta is None else 0, int(beta or 0)


def select_release(metadata: Any, channel: str, installed_version: str) -> dict[str, Any]:
    if channel not in CHANNELS:
        raise ValueError("unsupported update channel")
    if not isinstance(metadata, dict) or set(metadata) != {"schema_version", "releases"} or metadata["schema_version"] != 1:
        raise ValueError("update metadata has an invalid closed field set")
    if not isinstance(metadata["releases"], list) or len(metadata["releases"]) > 100:
        raise ValueError("update metadata release count is invalid")
    candidates = []
    for release in metadata["releases"]:
        required = {"version", "channel", "full", "deltas"}
        if not isinstance(release, dict) or set(release) != required or release["channel"] not in CHANNELS:
            raise ValueError("update release has an invalid closed field set")
        _version_key(release["version"])
        _verify_descriptor(release["full"])
        if not isinstance(release["deltas"], list) or len(release["deltas"]) > 100:
            raise ValueError("update delta count is invalid")
        for delta in release["deltas"]:
            if not isinstance(delta, dict) or set(delta) != {"from_version", "artifact"}:
                raise ValueError("delta descriptor is invalid")
            _version_key(delta["from_version"])
            _verify_descriptor(delta["artifact"])
        if channel == "stable" and release["channel"] != "stable":
            continue
        if channel == "beta" and release["channel"] not in {"stable", "beta"}:
            continue
        if _version_key(release["version"]) > _version_key(installed_version):
            candidates.append(release)
    if not candidates:
        return {"status": "up-to-date", "channel": channel, "installed_version": installed_version}
    selected = max(candidates, key=lambda item: _version_key(item["version"]))
    return {"status": "update-available", "channel": channel, "installed_version": installed_version, "release": selected}


def _verify_descriptor(descriptor: Any) -> tuple[str, str]:
    if not isinstance(descriptor, dict) or set(descriptor) != {"url", "sha256", "filename"}:
        raise ValueError("update artifact descriptor is invalid")
    url, digest = descriptor["url"], descriptor["sha256"]
    filename = descriptor["filename"]
    if (
        not isinstance(url, str)
        or not url
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
    ):
        raise ValueError("update artifact descriptor is invalid")
    int(digest, 16)
    return url, digest.lower()


def apply_delta_bundle(base: bytes, patch: bytes) -> bytes:
    try:
        payload = json.loads(patch.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("delta bundle is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"format", "base_sha256", "target_sha256", "operations"}:
        raise ValueError("delta bundle has an invalid closed field set")
    if payload["format"] != "memory-wuxian-binary-delta-v1" or hashlib.sha256(base).hexdigest() != payload["base_sha256"]:
        raise ValueError("delta base identity mismatch")
    operations = payload["operations"]
    if not isinstance(operations, list) or len(operations) > MAX_DELTA_OPERATIONS:
        raise ValueError("delta operation count is invalid")
    output = bytearray()
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) not in ({"copy"}, {"data"}):
            raise ValueError("delta operation is invalid")
        if "copy" in operation:
            value = operation["copy"]
            if not isinstance(value, list) or len(value) != 2 or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value):
                raise ValueError("delta copy operation is invalid")
            start, length = value
            if start + length > len(base):
                raise ValueError("delta copy exceeds base artifact")
            output.extend(base[start : start + length])
        else:
            try:
                output.extend(base64.b64decode(operation["data"], validate=True))
            except (ValueError, TypeError) as exc:
                raise ValueError("delta data operation is invalid") from exc
        if len(output) > MAX_ARTIFACT_BYTES:
            raise ValueError("delta output exceeds artifact size limit")
    result = bytes(output)
    if hashlib.sha256(result).hexdigest() != payload["target_sha256"]:
        raise ValueError("delta target identity mismatch")
    return result


def stage_update(
    release: dict[str, Any],
    installed_version: str,
    destination: Path,
    fetch: Callable[[str], bytes],
    apply_delta: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Stage verified bytes; never execute or install them."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    attempts = []
    selected_bytes = None
    selected_kind = None
    for delta in release["deltas"]:
        if not isinstance(delta, dict) or set(delta) != {"from_version", "artifact"}:
            raise ValueError("delta descriptor is invalid")
        if delta["from_version"] != installed_version:
            continue
        try:
            url, expected = _verify_descriptor(delta["artifact"])
            patch = fetch(url)
            if hashlib.sha256(patch).hexdigest() != expected:
                raise ValueError("delta hash mismatch")
            selected_bytes = apply_delta(patch)
            _, target_sha256 = _verify_descriptor(release["full"])
            if hashlib.sha256(selected_bytes).hexdigest() != target_sha256:
                raise ValueError("delta output does not match full-package SHA-256")
            selected_kind = "delta"
            attempts.append({"kind": "delta", "status": "verified"})
        except Exception as exc:
            attempts.append({"kind": "delta", "status": "failed", "reason": str(exc)})
        break
    if selected_bytes is None:
        url, expected = _verify_descriptor(release["full"])
        selected_bytes = fetch(url)
        if len(selected_bytes) > MAX_ARTIFACT_BYTES:
            raise ValueError("full package exceeds artifact size limit")
        actual = hashlib.sha256(selected_bytes).hexdigest()
        if actual != expected:
            raise ValueError("full package hash mismatch")
        selected_kind = "full"
        attempts.append({"kind": "full", "status": "verified"})
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(selected_bytes)
    temporary.replace(destination)
    return {
        "schema_version": 1,
        "status": "staged-awaiting-user-approval",
        "version": release["version"],
        "artifact_kind": selected_kind,
        "path": str(destination),
        "sha256": hashlib.sha256(selected_bytes).hexdigest(),
        "attempts": attempts,
        "installed": False,
        "executed": False,
    }
