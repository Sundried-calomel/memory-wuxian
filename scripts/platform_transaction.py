#!/usr/bin/env python3
"""Fail-closed canonical JSON transactions for platform-owned state."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from platform_atomic import ParentSync, atomic_replace_bytes


INDEX_GENERATION_FORMAT = "memory-wuxian-index-generation-v1"
INDEX_GENERATION_FORMAT_VERSION = 1
_GENERATION_FIELDS = {
    "generation_id",
    "format",
    "format_version",
    "builder",
    "source_manifest",
    "files",
    "created_at",
    "previous_generation_id",
    "status",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_ID_PATTERN = re.compile(r"^idxgen-[0-9a-f]{64}$")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_BeforeReplace = Callable[[Path, Path], None]


class PlatformTransactionError(ValueError):
    """A platform transaction or pointer failed closed validation."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashing and persistence."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlatformTransactionError(f"value is not canonical JSON: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlatformTransactionError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def read_canonical_json(path: Path) -> Any:
    """Read exact canonical JSON, rejecting malformed or noncanonical bytes."""

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlatformTransactionError(f"canonical JSON is not readable: {path}: {exc}") from exc
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PlatformTransactionError(f"JSON contains unsupported constant: {value}")
            ),
        )
    except PlatformTransactionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformTransactionError(f"canonical JSON is malformed: {path}: {exc}") from exc
    if raw != canonical_json_bytes(value):
        raise PlatformTransactionError(f"JSON bytes are not canonical: {path}")
    return value


def atomic_write_canonical_json(
    path: Path,
    value: Any,
    *,
    before_replace: _BeforeReplace | None = None,
) -> bytes:
    """Fsync canonical bytes in the target directory, then atomically replace."""

    path = Path(path)
    payload = canonical_json_bytes(value)
    atomic_replace_bytes(
        path,
        payload,
        parent_sync=ParentSync.NONE,
        before_replace=before_replace,
    )
    return payload


def _validate_hash(value: Any, location: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PlatformTransactionError(f"{location} must be a lowercase SHA-256")


def _validate_generation_id(
    value: Any, location: str, *, nullable: bool = False
) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or _GENERATION_ID_PATTERN.fullmatch(value) is None:
        raise PlatformTransactionError(f"{location} must be an idxgen SHA-256 identity")


def _validate_relative_path(value: Any, location: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PlatformTransactionError(f"{location} must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PlatformTransactionError(f"{location} must be a safe relative path")
    if re.match(r"^[A-Za-z]:", value):
        raise PlatformTransactionError(f"{location} must not contain a drive prefix")


def _validate_entries(entries: Any, location: str, fields: set[str]) -> None:
    if not isinstance(entries, list):
        raise PlatformTransactionError(f"{location} must be an array")
    paths: list[str] = []
    for index, entry in enumerate(entries):
        item_location = f"{location}/{index}"
        if not isinstance(entry, dict) or set(entry) != fields:
            raise PlatformTransactionError(
                f"{item_location} must contain exactly: {', '.join(sorted(fields))}"
            )
        _validate_relative_path(entry["path"], f"{item_location}/path")
        if (
            not isinstance(entry["byte_length"], int)
            or isinstance(entry["byte_length"], bool)
            or entry["byte_length"] < 0
        ):
            raise PlatformTransactionError(
                f"{item_location}/byte_length must be a non-negative integer"
            )
        _validate_hash(entry["sha256"], f"{item_location}/sha256")
        paths.append(entry["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PlatformTransactionError(f"{location} paths must be unique and sorted")


def index_generation_id(document: Mapping[str, Any]) -> str:
    """Hash all identity fields while excluding the three metadata fields."""

    identity = {
        key: value
        for key, value in document.items()
        if key not in {"generation_id", "created_at", "previous_generation_id"}
    }
    return f"idxgen-{hashlib.sha256(canonical_json_bytes(identity)).hexdigest()}"


def source_manifest_sha256(entries: Any) -> str:
    """Return the digest bound to an exact ordered source-entry list."""

    return hashlib.sha256(canonical_json_bytes({"entries": entries})).hexdigest()


def validate_index_generation(document: Any) -> dict[str, Any]:
    """Validate the canonical closed v1 index-generation manifest."""

    if not isinstance(document, dict) or set(document) != _GENERATION_FIELDS:
        raise PlatformTransactionError(
            "index generation must contain exactly the closed v1 field set"
        )
    if document["format"] != INDEX_GENERATION_FORMAT:
        raise PlatformTransactionError("index generation format is unsupported")
    if document["format_version"] != INDEX_GENERATION_FORMAT_VERSION:
        raise PlatformTransactionError("index generation format version is unsupported")
    if not isinstance(document["builder"], str) or not document["builder"]:
        raise PlatformTransactionError("/builder must be a non-empty string")
    if document["status"] != "complete":
        raise PlatformTransactionError("index generation status must be complete")
    _validate_generation_id(document["generation_id"], "/generation_id")
    _validate_generation_id(
        document["previous_generation_id"],
        "/previous_generation_id",
        nullable=True,
    )
    created_at = document["created_at"]
    if not isinstance(created_at, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(created_at) is None:
        raise PlatformTransactionError("/created_at must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as exc:
        raise PlatformTransactionError(
            "/created_at must be an RFC 3339 UTC timestamp"
        ) from exc
    source_manifest = document["source_manifest"]
    if not isinstance(source_manifest, dict) or set(source_manifest) != {
        "entries",
        "sha256",
    }:
        raise PlatformTransactionError(
            "/source_manifest must contain exactly entries and sha256"
        )
    _validate_entries(
        source_manifest["entries"],
        "/source_manifest/entries",
        {"path", "byte_length", "sha256"},
    )
    _validate_hash(source_manifest["sha256"], "/source_manifest/sha256")
    if source_manifest["sha256"] != source_manifest_sha256(
        source_manifest["entries"]
    ):
        raise PlatformTransactionError("source manifest digest does not match its entries")
    _validate_entries(
        document["files"],
        "/files",
        {"path", "byte_length", "sha256"},
    )
    expected = index_generation_id(document)
    if document["generation_id"] != expected:
        raise PlatformTransactionError("index generation identity does not match its content")
    return document


def read_index_generation(path: Path) -> dict[str, Any]:
    return validate_index_generation(read_canonical_json(path))


def write_index_generation(
    path: Path,
    document: Mapping[str, Any],
    *,
    before_replace: _BeforeReplace | None = None,
) -> bytes:
    validated = validate_index_generation(dict(document))
    return atomic_write_canonical_json(
        path, validated, before_replace=before_replace
    )
