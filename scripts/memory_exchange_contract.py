#!/usr/bin/env python3
"""Shared, policy-free mechanics for independent Memory Wuxian streams."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ExchangeStreamPort:
    """Explicit cloud-transport boundary implemented by one stream facade."""

    stream_id: str
    root: Path
    metadata_root: Path
    replica_root: Path
    exchange_lock_path: Path
    requires_authenticated_transport: bool
    node: Callable[[], Dict[str, Any]]
    peers: Callable[[], List[Dict[str, Any]]]
    status: Callable[[], Dict[str, Any]]
    exchange_observation: Callable[[float], Dict[str, Any]]
    replica_state: Callable[[str], Dict[str, Any]]
    read_bundle_manifest: Callable[[Path], Dict[str, Any]]
    export_delta: Callable[..., Dict[str, Any]]
    import_delta: Callable[..., Dict[str, Any]]
    log_sync: Callable[[str, str, Dict[str, Any]], None]
    resolve_quarantine_path: Optional[Callable[[Path], Path]] = None

    def import_bundle(
        self,
        bundle: Path,
        *,
        expected_node_id: str,
        authenticated_open_result: Any,
    ) -> Dict[str, Any]:
        if self.requires_authenticated_transport:
            return self.import_delta(
                bundle,
                expected_node_id=expected_node_id,
                authenticated_open_result=authenticated_open_result,
            )
        return self.import_delta(bundle, expected_node_id=expected_node_id)


class ExchangeStreamFacade(ABC):
    """Marker contract preventing Cloud transport from accepting ad-hoc objects."""

    @abstractmethod
    def exchange_port(self) -> ExchangeStreamPort:
        raise NotImplementedError


def validate_export_cursor(
    after: int,
    latest: int,
    previous_bundle_sha256: Optional[str],
    *,
    cursor_error: Callable[[int, int], str],
    predecessor_error: str,
    initial_predecessor_error: str,
    predecessor_is_valid: Callable[[Optional[str]], bool],
    initial_predecessor_is_declared: Callable[[Optional[str]], bool],
) -> None:
    if after < 0 or after > latest:
        raise ValueError(cursor_error(after, latest))
    if after and not predecessor_is_valid(previous_bundle_sha256):
        raise ValueError(predecessor_error)
    if not after and initial_predecessor_is_declared(previous_bundle_sha256):
        raise ValueError(initial_predecessor_error)


def select_jsonl_page(
    records: Iterable[Dict[str, Any]],
    *,
    encode: Callable[[Dict[str, Any]], bytes],
    maximum_items: int,
    maximum_bytes: int,
    oversized_item_error: Callable[[Dict[str, Any]], str],
) -> Tuple[List[Dict[str, Any]], bytes]:
    selected: List[Dict[str, Any]] = []
    payload = bytearray()
    for record in records:
        encoded = encode(record)
        if selected and (
            len(selected) >= maximum_items
            or len(payload) + len(encoded) > maximum_bytes
        ):
            break
        if len(encoded) > maximum_bytes:
            raise ValueError(oversized_item_error(record))
        selected.append(record)
        payload.extend(encoded)
    return selected, bytes(payload)


def build_bundle_manifest(
    manifest_identity: Dict[str, Any],
    *,
    canonical_sha256: Callable[[Any], str],
) -> Dict[str, Any]:
    return {
        **manifest_identity,
        "bundle_id": f"mwb-{canonical_sha256(manifest_identity)[:32]}",
    }


def verify_bundle_identity(
    manifest: Dict[str, Any],
    *,
    canonical_sha256: Callable[[Any], str],
    error: str,
) -> None:
    identity = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if manifest.get("bundle_id") != f"mwb-{canonical_sha256(identity)[:32]}":
        raise ValueError(error)


def verify_payload(
    manifest: Dict[str, Any],
    payload: bytes,
    *,
    bytes_sha256: Callable[[bytes], str],
    size_error: str,
    hash_error: str,
    coerce_size: bool = False,
) -> None:
    expected_size = manifest.get("payload_bytes", -1)
    if coerce_size:
        expected_size = int(expected_size)
    if expected_size != len(payload):
        raise ValueError(size_error)
    if manifest.get("payload_sha256") != bytes_sha256(payload):
        raise ValueError(hash_error)


def validate_authenticated_binding(
    binding: Tuple[str, str, str],
    *,
    expected_origin: str,
    expected_target: str,
    expected_payload_sha256: str,
    identity_error: str,
    payload_error: Optional[str] = None,
) -> Tuple[str, str, str]:
    origin, target, payload_sha256 = binding
    if origin != expected_origin or target != expected_target:
        raise ValueError(identity_error)
    if payload_sha256 != expected_payload_sha256:
        raise ValueError(payload_error or identity_error)
    return origin, target, payload_sha256


def validate_strict_replica_continuity(
    manifest: Dict[str, Any],
    state: Dict[str, Any],
    *,
    manifest_sequence_field: str,
    state_offset: int,
    sequence_error: Callable[[int, int], str],
    predecessor_error: str,
    initial_predecessor_error: Optional[str] = None,
) -> None:
    last_sequence = int(state["last_event_sequence"])
    expected_sequence = last_sequence + state_offset
    actual_sequence = int(manifest[manifest_sequence_field])
    if actual_sequence != expected_sequence:
        raise ValueError(sequence_error(expected_sequence, actual_sequence))
    predecessor = manifest.get("previous_bundle_sha256")
    if last_sequence:
        if predecessor != state.get("last_bundle_sha256"):
            raise ValueError(predecessor_error)
    elif initial_predecessor_error is not None and predecessor is not None:
        raise ValueError(initial_predecessor_error)


@dataclass(frozen=True)
class ReplicaWindow:
    expected_sequence: int
    overlap_recovery: bool


def classify_replica_window(
    manifest: Dict[str, Any],
    state: Dict[str, Any],
    *,
    gap_error: str,
    stale_error: str,
) -> ReplicaWindow:
    expected_sequence = int(state["last_event_sequence"]) + 1
    from_sequence = int(manifest["from_event_sequence"])
    to_sequence = int(manifest["to_event_sequence"])
    if from_sequence > expected_sequence:
        raise ValueError(gap_error)
    if to_sequence < expected_sequence:
        raise ValueError(stale_error)
    return ReplicaWindow(
        expected_sequence=expected_sequence,
        overlap_recovery=from_sequence < expected_sequence <= to_sequence,
    )
