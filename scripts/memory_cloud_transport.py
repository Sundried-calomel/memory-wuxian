#!/usr/bin/env python3
"""Encrypted, asynchronous folder transport for Memory Wuxian federation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol

from platform_process import no_window_kwargs
from platform_lock import exclusive_lock
from platform_paths import is_link_like
from memory_exchange_contract import ExchangeStreamFacade, ExchangeStreamPort
from memory_federation import (
    PROTOCOL_VERSION,
    atomic_write_json,
    bytes_sha256,
    now_iso,
    read_json,
    safe_node_id,
)


def filesystem_native_path(path: Path) -> str:
    value = str(path.resolve())
    if (
        os.name != "nt"
        or value.startswith("\\\\?\\")
        or len(value) < 240
    ):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def display_path(path: str | Path) -> str:
    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


CLOUD_FORMAT_VERSION = 1
ACK_FORMAT = "memory-wuxian-cloud-ack-v1"
DEFAULT_MERGE_WINDOW_SECONDS = 900
DEFAULT_EARLY_FLUSH_BYTES = 1024 * 1024
DEFAULT_MAXIMUM_PENDING_SECONDS = 3600
DEFAULT_CLEANUP_GRACE_SECONDS = 24 * 60 * 60
MAX_CLOUD_QUEUE_ENTRIES = 4096
MAX_CLOUD_SCAN_SECONDS = 3.0
ENVELOPE_PATTERN = re.compile(
    r"^(?P<from_sequence>[0-9]{20})-"
    r"(?P<to_sequence>[0-9]{20})-"
    r"(?P<bundle_id>mwb-[0-9a-f]{32})-"
    r"(?P<bundle_sha256>[0-9a-f]{64})[.]mwxe$"
)
ACK_PATTERN = re.compile(
    r"^ack-(?P<sequence>[0-9]{20})-"
    r"(?P<bundle_id>mwb-[0-9a-f]{32})[.]mwxa$"
)


_AUTHENTICATED_OPEN_AUTHORITY = object()


class AuthenticatedOpenResult(dict):
    """One-shot evidence emitted only after the native crypto helper succeeds."""

    def __init__(self, authority: object, value: Dict[str, Any]):
        if authority is not _AUTHENTICATED_OPEN_AUTHORITY:
            raise TypeError("authenticated open results are issued by CommandCrypto")
        super().__init__(value)
        self._consumed = False

    def consume_stream_binding(self) -> tuple[str, str, str]:
        if self._consumed:
            raise ValueError("authenticated open result was already consumed")
        self._consumed = True
        return (
            safe_node_id(str(self["origin_node_id"])),
            safe_node_id(str(self["target_node_id"])),
            str(self["payload_sha256"]),
        )

    def consume_environment_binding(self) -> tuple[str, str, str]:
        """Compatibility alias for the original Environment-only API."""
        return self.consume_stream_binding()


class CryptoAdapter(Protocol):
    def init_identity(
        self, identity_private_path: Path, node_id: str
    ) -> Dict[str, str]:
        ...

    def show_identity(self, identity_private_path: Path) -> Dict[str, str]:
        ...

    def seal(
        self,
        source: Path,
        destination: Path,
        identity_private_path: Path,
        recipients: Iterable[str],
        kind: str,
        origin_node_id: str,
        target_node_id: str,
    ) -> Dict[str, Any]:
        ...

    def open(
        self,
        source: Path,
        destination: Path,
        identity_private_path: Path,
        signing_public_key: str,
        kind: str,
        origin_node_id: str,
        target_node_id: str,
    ) -> Dict[str, Any]:
        ...


class TransientCloudArtifactError(RuntimeError):
    """A synchronized artifact is visible but not locally readable yet."""


class CommandCrypto:
    """Adapter for the memory-wuxian-envelope command-line helper."""

    def __init__(self, binary: Path):
        candidate = Path(binary)
        if os.name == "nt" and candidate.suffix.lower() != ".exe":
            candidate = Path(f"{candidate}.exe")
        self.binary = candidate

    def _run(
        self, arguments: List[str], invalid_envelope_on_failure: bool = False
    ) -> Dict[str, Any]:
        completed = subprocess.run(
            [str(self.binary), *arguments],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=600,
            **no_window_kwargs(),
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            error = (
                f"memory-wuxian-envelope failed with exit code "
                f"{completed.returncode}: {detail}"
            )
            if invalid_envelope_on_failure and any(
                marker in detail.casefold()
                for marker in (
                    "resource deadlock avoided",
                    "temporarily unavailable",
                    "operation would block",
                )
            ):
                raise TransientCloudArtifactError(error)
            if invalid_envelope_on_failure:
                raise ValueError(error)
            raise RuntimeError(error)
        output = completed.stdout.strip()
        if not output:
            return {}
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Envelope helper returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Envelope helper JSON output must be an object")
        return value

    def show_identity(self, identity_private_path: Path) -> Dict[str, str]:
        result = self._run(["show-identity", "--path", str(identity_private_path)])
        return _validated_identity(result, "local")

    def init_identity(
        self, identity_private_path: Path, node_id: str
    ) -> Dict[str, str]:
        result = self._run(
            [
                "init-identity",
                "--path",
                str(identity_private_path),
                "--node-id",
                safe_node_id(node_id),
            ]
        )
        return _validated_identity(result, "local")

    def seal(
        self,
        source: Path,
        destination: Path,
        identity_private_path: Path,
        recipients: Iterable[str],
        kind: str,
        origin_node_id: str,
        target_node_id: str,
    ) -> Dict[str, Any]:
        arguments = [
            "seal",
            "--identity",
            str(identity_private_path),
            "--input",
            str(source),
            "--output",
            str(destination),
            "--kind",
            kind,
            "--origin-node-id",
            origin_node_id,
            "--target-node-id",
            target_node_id,
        ]
        for recipient in recipients:
            arguments.extend(["--recipient", str(recipient)])
        return self._run(arguments)

    def open(
        self,
        source: Path,
        destination: Path,
        identity_private_path: Path,
        signing_public_key: str,
        kind: str,
        origin_node_id: str,
        target_node_id: str,
    ) -> AuthenticatedOpenResult:
        result = self._run(
            [
                "open",
                "--identity",
                str(identity_private_path),
                f"--signing-public-key={signing_public_key}",
                "--input",
                str(source),
                "--output",
                str(destination),
                "--expected-kind",
                kind,
                "--expected-origin-node-id",
                origin_node_id,
                "--expected-target-node-id",
                target_node_id,
            ],
            invalid_envelope_on_failure=True,
        )
        return AuthenticatedOpenResult(_AUTHENTICATED_OPEN_AUTHORITY, result)


def _validated_identity(value: Dict[str, Any], label: str) -> Dict[str, str]:
    required = (
        "encryption_public_key",
        "signing_public_key",
        "fingerprint",
    )
    missing = [key for key in required if not str(value.get(key, "")).strip()]
    if missing:
        raise ValueError(
            f"{label.capitalize()} cloud identity is missing: {', '.join(missing)}"
        )
    return {key: str(value[key]).strip() for key in required}


def _empty_marker() -> Dict[str, Any]:
    empty = {"size": 0, "mtime_ns": 0}
    return {
        "completed_rounds": 0,
        "raw_today": dict(empty),
        "summary_registry": dict(empty),
        "title_index": dict(empty),
    }


def _default_cursor() -> Dict[str, Any]:
    return {
        "last_event_sequence": 0,
        "last_bundle_id": None,
        "last_bundle_sha256": None,
        "acknowledged_at": None,
    }


def _default_config() -> Dict[str, Any]:
    return {
        "format_version": CLOUD_FORMAT_VERSION,
        "enabled": False,
        "exchange_root": "",
        "identity_private_path": "",
        "envelope_binary": "",
        "merge_window_seconds": DEFAULT_MERGE_WINDOW_SECONDS,
        "early_flush_bytes": DEFAULT_EARLY_FLUSH_BYTES,
        "maximum_pending_seconds": DEFAULT_MAXIMUM_PENDING_SECONDS,
        "cleanup_grace_seconds": DEFAULT_CLEANUP_GRACE_SECONDS,
        "schedule": {
            "pending_since": None,
            "last_attempt_at": None,
            "observed": _empty_marker(),
            "published": _empty_marker(),
        },
        "outbound": {},
    }


class CloudFolderTransport:
    """Exchange encrypted federation deltas through a synchronized folder."""

    def __init__(
        self,
        manager: ExchangeStreamFacade,
        crypto: Optional[CryptoAdapter] = None,
        config_path: Optional[Path] = None,
        clock: Optional[Any] = None,
        stream_id: Optional[str] = None,
    ):
        if not isinstance(manager, ExchangeStreamFacade):
            raise TypeError("cloud transport requires an ExchangeStreamFacade")
        self.port: ExchangeStreamPort = manager.exchange_port()
        self.manager = manager
        self.store = self.port.store
        self.archive_root = self.port.root
        self.stream_id = stream_id
        if stream_id is not None and not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{2,63}", stream_id
        ):
            raise ValueError("invalid cloud stream_id")
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else self.port.metadata_root / "cloud.json"
        )
        self.quarantine_root = self.port.metadata_root / "cloud-quarantine"
        self.clock = clock or time.time
        self.config = self._load_config()
        binary = self.config.get("envelope_binary") or self._default_binary()
        self.crypto = crypto or CommandCrypto(Path(str(binary)))

    def _default_binary(self) -> Path:
        filename = "memory-wuxian-envelope.exe" if os.name == "nt" else "memory-wuxian-envelope"
        return Path(__file__).resolve().parent.parent / "bin" / filename

    def _load_config(self) -> Dict[str, Any]:
        config = _default_config()
        if self.config_path.exists():
            loaded = read_json(self.config_path)
            if int(loaded.get("format_version", 0)) != CLOUD_FORMAT_VERSION:
                raise ValueError("Unsupported cloud configuration format version")
            config.update(loaded)
            schedule = dict(_default_config()["schedule"])
            schedule.update(loaded.get("schedule") or {})
            config["schedule"] = schedule
            config["outbound"] = dict(loaded.get("outbound") or {})
        return config

    def save_config(self) -> None:
        atomic_write_json(self.config_path, self.config)

    def configure(
        self,
        exchange_root: Path,
        identity_private_path: Path,
        envelope_binary: Optional[Path] = None,
        enabled: bool = True,
        merge_window_seconds: int = DEFAULT_MERGE_WINDOW_SECONDS,
        early_flush_bytes: int = DEFAULT_EARLY_FLUSH_BYTES,
        maximum_pending_seconds: int = DEFAULT_MAXIMUM_PENDING_SECONDS,
        cleanup_grace_seconds: int = DEFAULT_CLEANUP_GRACE_SECONDS,
    ) -> Dict[str, Any]:
        if min(
            int(merge_window_seconds),
            int(cleanup_grace_seconds),
        ) < 0:
            raise ValueError("Cloud timing values must not be negative")
        if min(int(early_flush_bytes), int(maximum_pending_seconds)) < 1:
            raise ValueError("Cloud byte and maximum-pending limits must be positive")
        resolved_exchange_root = self._provider_root(exchange_root)
        if not resolved_exchange_root.is_dir():
            raise ValueError(
                f"Cloud synchronization directory does not exist: {resolved_exchange_root}"
            )
        resolved_identity_path = Path(identity_private_path).expanduser().resolve()
        archive_root = self.archive_root.resolve()
        replica_root = self.port.replica_root.resolve()
        for protected_root, label in (
            (archive_root, "primary archive"),
            (replica_root, "federation replica cache"),
        ):
            if (
                resolved_exchange_root == protected_root
                or resolved_exchange_root.is_relative_to(protected_root)
                or protected_root.is_relative_to(resolved_exchange_root)
            ):
                raise ValueError(
                    f"Cloud synchronization directory must be separate from the {label}"
                )
        if (
            resolved_identity_path == resolved_exchange_root
            or resolved_identity_path.is_relative_to(resolved_exchange_root)
            or resolved_identity_path.is_relative_to(archive_root)
            or resolved_identity_path.is_relative_to(replica_root)
        ):
            raise ValueError(
                "Cloud private identity must stay outside cloud, archive, and replica directories"
            )
        self.config.update(
            {
                "enabled": bool(enabled),
                "exchange_root": str(resolved_exchange_root),
                "identity_private_path": str(resolved_identity_path),
                "envelope_binary": str(
                    Path(envelope_binary).expanduser().resolve()
                    if envelope_binary
                    else self._default_binary()
                ),
                "merge_window_seconds": int(merge_window_seconds),
                "early_flush_bytes": int(early_flush_bytes),
                "maximum_pending_seconds": int(maximum_pending_seconds),
                "cleanup_grace_seconds": int(cleanup_grace_seconds),
            }
        )
        self.save_config()
        if isinstance(self.crypto, CommandCrypto):
            self.crypto = CommandCrypto(Path(self.config["envelope_binary"]))
        return {"status": "configured", "config_path": str(self.config_path)}

    def initialize_identity(self) -> Dict[str, str]:
        path = self._identity_private_path()
        if path.exists():
            return _validated_identity(
                self.crypto.show_identity(path), "local"
            )
        return _validated_identity(
            self.crypto.init_identity(path, self._local_node_id()), "local"
        )

    def public_identity(self) -> Dict[str, str]:
        return _validated_identity(
            self.crypto.show_identity(self._identity_private_path()), "local"
        )

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        self.config["enabled"] = bool(enabled)
        self.save_config()
        return {
            "status": "enabled" if enabled else "disabled",
            "config_path": str(self.config_path),
        }

    def status(self) -> Dict[str, Any]:
        configured = bool(
            str(self.config.get("exchange_root", "")).strip()
            and str(self.config.get("identity_private_path", "")).strip()
        )
        peers = []
        for peer in self.port.status().get("devices", []):
            identity = peer.get("cloud_identity")
            outbound = (self.config.get("outbound") or {}).get(
                peer["node_id"], {}
            )
            peers.append(
                {
                    "node_id": peer["node_id"],
                    "display_name": peer["display_name"],
                    "trusted": peer["trusted"],
                    "ssh_transport": peer["transport"] == "ssh",
                    "cloud_ready": isinstance(identity, dict),
                    "cloud_fingerprint": (
                        str(identity.get("fingerprint", ""))[:16]
                        if isinstance(identity, dict)
                        else None
                    ),
                    "acknowledged": (outbound or {}).get("acknowledged"),
                    "outstanding": (outbound or {}).get("outstanding"),
                    "last_sync_at": peer.get("last_sync_at"),
                }
            )
        return {
            "enabled": bool(self.config.get("enabled")),
            "configured": configured,
            "encrypted": configured,
            "exchange_root": str(self.config.get("exchange_root", "")),
            "exchange_provider_path_configured": bool(
                str(self.config.get("exchange_root", "")).strip()
            ),
            "identity_ready": (
                self._identity_private_path().is_file() if configured else False
            ),
            "schedule": self.config.get("schedule"),
            "peers": peers,
            "stream_id": self.stream_id or "archive-v1",
        }

    def _exchange_root(self) -> Path:
        value = str(self.config.get("exchange_root", "")).strip()
        if not value:
            raise ValueError("Cloud exchange_root is not configured")
        root = self._provider_root(value) / "MemoryWuxianExchange" / "v1"
        if self.stream_id is not None:
            root = root / self.stream_id
        provider_root = self._provider_root(value)
        current = root
        while current != provider_root and current != current.parent:
            if current.exists() and is_link_like(current):
                raise ValueError("Cloud exchange queue contains a link or junction")
            current = current.parent
        return Path(filesystem_native_path(root)) if os.name == "nt" else root

    def _queue_path(self, *parts: str, for_write: bool = False) -> Path:
        root = self._exchange_root()
        candidate = root.joinpath(*parts)
        try:
            candidate.relative_to(root)
            candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError as error:
            raise ValueError("Cloud exchange path escapes its queue root") from error
        current = candidate
        while True:
            if current.exists() and is_link_like(current):
                raise ValueError("Cloud exchange path contains a link or junction")
            if current == root:
                break
            current = current.parent
        native_candidate = (
            Path(filesystem_native_path(candidate)) if os.name == "nt" else candidate
        )
        if not for_write and not native_candidate.exists():
            raise ValueError("Cloud exchange path does not exist")
        return native_candidate

    def _assert_queue_path(self, path: Path, *, for_write: bool = False) -> Path:
        root = self._exchange_root()
        try:
            relative = Path(display_path(path)).relative_to(Path(display_path(root)))
        except ValueError as error:
            raise ValueError("Cloud exchange path is outside its queue root") from error
        return self._queue_path(*relative.parts, for_write=for_write)

    def _bounded_queue_entries(self, root: Path, label: str) -> List[Path]:
        if not root.exists():
            return []
        started = time.monotonic()
        paths: List[Path] = []
        with os.scandir(root) as entries:
            for entry in entries:
                if time.monotonic() - started > MAX_CLOUD_SCAN_SECONDS:
                    raise ValueError(f"{label} exceeded its scan time limit")
                paths.append(Path(entry.path))
                if len(paths) > MAX_CLOUD_QUEUE_ENTRIES:
                    raise ValueError(f"{label} exceeded its entry limit")
        paths.sort(key=lambda path: path.name)
        return paths

    @staticmethod
    def _provider_root(value: str | Path) -> Path:
        """Normalize either a provider root or the fixed queue directory."""
        # Persisted Windows paths may carry the native ``\\?\`` prefix. Strip
        # it before inspecting the final component so legacy queue roots are
        # not mistaken for provider roots and appended a second time.
        resolved = Path(display_path(value)).expanduser().resolve()
        if resolved.name.casefold() == "memorywuxianexchange":
            return resolved.parent
        return resolved

    def _envelope_kind(self, kind: str) -> str:
        return f"{self.stream_id}-{kind}" if self.stream_id is not None else kind

    def _ack_format(self) -> str:
        return (
            f"memory-wuxian-cloud-ack-{self.stream_id}"
            if self.stream_id is not None
            else ACK_FORMAT
        )

    def _identity_private_path(self) -> Path:
        value = str(self.config.get("identity_private_path", "")).strip()
        if not value:
            raise ValueError("Cloud identity_private_path is not configured")
        return Path(value).expanduser().resolve()

    def _local_node_id(self) -> str:
        return safe_node_id(str(self.port.node()["node_id"]))

    def _trusted_cloud_peers(self) -> Dict[str, Dict[str, Any]]:
        peers: Dict[str, Dict[str, Any]] = {}
        for peer in self.port.peers():
            node_id = safe_node_id(str(peer.get("node_id", "")))
            if not peer.get("trusted"):
                continue
            identity = peer.get("cloud_identity")
            if not isinstance(identity, dict):
                continue
            peers[node_id] = {
                **peer,
                "cloud_identity": _validated_identity(identity, f"peer {node_id}"),
            }
        return peers

    def _self_node_root(self) -> Path:
        return self._queue_path("nodes", self._local_node_id(), for_write=True)

    def _outbox(self, target_node_id: str) -> Path:
        return self._queue_path(
            "nodes", self._local_node_id(), "outbox", safe_node_id(target_node_id),
            for_write=True,
        )

    def _ack_outbox(self, origin_node_id: str) -> Path:
        return self._queue_path(
            "nodes", self._local_node_id(), "acks", safe_node_id(origin_node_id),
            for_write=True,
        )

    def _incoming_outbox(self, origin_node_id: str) -> Path:
        return self._queue_path(
            "nodes", safe_node_id(origin_node_id), "outbox", self._local_node_id(),
            for_write=True,
        )

    def _incoming_acks(self, acknowledging_node_id: str) -> Path:
        return self._queue_path(
            "nodes", safe_node_id(acknowledging_node_id), "acks", self._local_node_id(),
            for_write=True,
        )

    def _peer_state(self, node_id: str) -> Dict[str, Any]:
        outbound = self.config.setdefault("outbound", {})
        state = outbound.setdefault(
            safe_node_id(node_id),
            {"acknowledged": _default_cursor(), "outstanding": None},
        )
        acknowledged = dict(_default_cursor())
        acknowledged.update(state.get("acknowledged") or {})
        state["acknowledged"] = acknowledged
        state.setdefault("outstanding", None)
        return state

    def _observation(self, timestamp: float) -> Dict[str, Any]:
        return self.port.exchange_observation(timestamp)

    @staticmethod
    def _eligible_change(current: Dict[str, Any], published: Dict[str, Any]) -> bool:
        if int(current.get("completed_rounds", 0)) > int(
            published.get("completed_rounds", 0)
        ):
            return True
        for key in (
            "summary_registry",
            "title_index",
            "personal_environment_profiles",
        ):
            if current.get(key) != published.get(key):
                return True
        return False

    @staticmethod
    def _estimated_new_bytes(
        current: Dict[str, Any], published: Dict[str, Any]
    ) -> int:
        total = 0
        for key in (
            "raw_today",
            "summary_registry",
            "title_index",
            "personal_environment_profiles",
        ):
            current_size = int((current.get(key) or {}).get("size", 0))
            previous_size = int((published.get(key) or {}).get("size", 0))
            total += max(0, current_size - previous_size)
        return total

    def _schedule_due(
        self, observation: Dict[str, Any], timestamp: float, force: bool
    ) -> Dict[str, Any]:
        schedule = self.config.setdefault("schedule", _default_config()["schedule"])
        published = schedule.get("published") or _empty_marker()
        changed = self._eligible_change(observation, published)
        if changed and schedule.get("pending_since") is None:
            schedule["pending_since"] = timestamp
        pending_since = schedule.get("pending_since")
        pending_age = (
            max(0.0, timestamp - float(pending_since))
            if pending_since is not None
            else 0.0
        )
        estimated_bytes = self._estimated_new_bytes(observation, published)
        due = bool(
            force
            or (
                changed
                and (
                    pending_age >= int(self.config["merge_window_seconds"])
                    or estimated_bytes >= int(self.config["early_flush_bytes"])
                    or pending_age >= int(self.config["maximum_pending_seconds"])
                )
            )
        )
        schedule["observed"] = observation
        return {
            "changed": changed,
            "due": due,
            "pending_age_seconds": pending_age,
            "estimated_new_bytes": estimated_bytes,
        }

    def _atomic_seal(
        self,
        plaintext: Path,
        destination: Path,
        recipients: Iterable[str],
        kind: str,
        target_node_id: str,
    ) -> None:
        destination = self._assert_queue_path(destination, for_write=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.parent / (
            f".mw-partial-{os.getpid()}-{uuid.uuid4().hex[:16]}.tmp"
        )
        try:
            self.crypto.seal(
                plaintext,
                partial,
                self._identity_private_path(),
                recipients,
                kind,
                self._local_node_id(),
                safe_node_id(target_node_id),
            )
            if not partial.is_file() or partial.stat().st_size == 0:
                raise RuntimeError("Envelope helper did not create a nonempty output")
            with partial.open("rb+") as handle:
                os.fsync(handle.fileno())
            self._assert_queue_path(destination, for_write=True)
            os.replace(
                filesystem_native_path(partial),
                filesystem_native_path(destination),
            )
            published_path = Path(filesystem_native_path(destination))
            visibility_deadline = time.monotonic() + 1.0
            while (
                not published_path.is_file()
                or published_path.stat().st_size == 0
            ):
                if time.monotonic() >= visibility_deadline:
                    raise RuntimeError(
                        f"Published cloud envelope is not visible: {destination}"
                    )
                time.sleep(0.01)
            try:
                directory_descriptor = os.open(
                    str(destination.parent), getattr(os, "O_DIRECTORY", 0)
                )
            except OSError:
                directory_descriptor = None
            if directory_descriptor is not None:
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        finally:
            partial.unlink(missing_ok=True)

    def _migrate_outstanding_path(
        self,
        peer_id: str,
        result: Dict[str, Any],
    ) -> None:
        state = self._peer_state(peer_id)
        outstanding = state.get("outstanding")
        if not isinstance(outstanding, dict):
            return
        source = Path(
            filesystem_native_path(Path(str(outstanding.get("path", ""))))
        )
        if not source.is_file() or source.stat().st_size == 0:
            return
        match = ENVELOPE_PATTERN.fullmatch(source.name)
        if (
            not match
            or match.group("bundle_id") != outstanding.get("bundle_id")
            or match.group("bundle_sha256") != outstanding.get("bundle_sha256")
            or int(match.group("from_sequence"))
            != int(outstanding.get("from_event_sequence", -1))
            or int(match.group("to_sequence"))
            != int(outstanding.get("to_event_sequence", -1))
        ):
            raise RuntimeError("Outstanding cloud envelope metadata is inconsistent")
        destination = self._outbox(peer_id) / source.name
        if source.resolve() == destination.resolve():
            return
        source_sha256 = bytes_sha256(source.read_bytes())
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if (
                not destination.is_file()
                or bytes_sha256(destination.read_bytes()) != source_sha256
            ):
                raise RuntimeError(
                    "Canonical cloud outbox contains a conflicting envelope"
                )
        else:
            partial = destination.parent / (
                f".mw-partial-{os.getpid()}-{uuid.uuid4().hex[:16]}.tmp"
            )
            try:
                with source.open("rb") as reader, partial.open("xb") as writer:
                    while True:
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if bytes_sha256(partial.read_bytes()) != source_sha256:
                    raise RuntimeError("Migrated cloud envelope SHA-256 mismatch")
                os.replace(
                    filesystem_native_path(partial),
                    filesystem_native_path(destination),
                )
            finally:
                partial.unlink(missing_ok=True)
        outstanding["path"] = display_path(destination)
        result["migrated"].append(
            {
                "peer": peer_id,
                "from": display_path(source),
                "to": display_path(destination),
                "envelope_sha256": source_sha256,
            }
        )

    def _quarantine(
        self,
        source: Path,
        peer_node_id: str,
        artifact_type: str,
        reason: str,
    ) -> Dict[str, Any]:
        try:
            digest = bytes_sha256(source.read_bytes())
        except OSError:
            digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        record = {
            "format_version": 1,
            "observed_at": now_iso(),
            "peer_node_id": safe_node_id(peer_node_id),
            "artifact_type": artifact_type,
            "source_path": str(source),
            "source_sha256": digest,
            "reason": reason,
        }
        destination = self.quarantine_root / f"{artifact_type}-{digest}.json"
        if self.port.resolve_quarantine_path is not None:
            destination = self.port.resolve_quarantine_path(destination)
        else:
            current = destination
            while True:
                if current.exists() and is_link_like(current):
                    raise ValueError("cloud quarantine path contains a link or junction")
                if current == self.port.metadata_root:
                    break
                current = current.parent
        atomic_write_json(destination, record)
        return record

    def _stable_candidate(self, path: Path) -> bool:
        if path.name.endswith(".partial") or ".partial." in path.name:
            return False
        try:
            self._assert_queue_path(path)
            return path.is_file() and path.stat().st_size > 0
        except (OSError, ValueError):
            return False

    @staticmethod
    def _materialize_candidate(path: Path) -> None:
        """Trigger cloud-provider hydration without reading the whole envelope."""
        try:
            with path.open("rb") as handle:
                if not handle.read(1):
                    raise OSError("Cloud artifact is empty")
        except OSError as exc:
            raise TransientCloudArtifactError(
                f"Cloud artifact is not locally readable yet: {exc}"
            ) from exc

    def _read_ack(
        self,
        path: Path,
        peer_node_id: str,
        peer_identity: Dict[str, str],
    ) -> Dict[str, Any]:
        self._materialize_candidate(path)
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-cloud-ack-") as temp:
            plaintext = Path(temp) / "ack.json"
            self.crypto.open(
                path,
                plaintext,
                self._identity_private_path(),
                peer_identity["signing_public_key"],
                self._envelope_kind("ack"),
                peer_node_id,
                self._local_node_id(),
            )
            ack = json.loads(plaintext.read_text(encoding="utf-8"))
        if ack.get("format") != self._ack_format():
            raise ValueError("Unsupported cloud acknowledgement format")
        if int(ack.get("protocol_version", 0)) != PROTOCOL_VERSION:
            raise ValueError("Unsupported cloud acknowledgement protocol")
        if safe_node_id(str(ack.get("origin_node_id", ""))) != peer_node_id:
            raise ValueError("Ack origin does not match its cloud writer")
        if safe_node_id(str(ack.get("target_node_id", ""))) != self._local_node_id():
            raise ValueError("Ack is addressed to another node")
        if int(ack.get("last_event_sequence", 0)) < 1:
            raise ValueError("Ack sequence is invalid")
        if not re.fullmatch(r"mwb-[0-9a-f]{32}", str(ack.get("last_bundle_id", ""))):
            raise ValueError("Ack bundle ID is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(ack.get("last_bundle_sha256", ""))):
            raise ValueError("Ack bundle SHA-256 is invalid")
        return ack

    def _process_acks(
        self,
        peers: Dict[str, Dict[str, Any]],
        result: Dict[str, Any],
    ) -> None:
        for peer_id, peer in peers.items():
            incoming = self._incoming_acks(peer_id)
            try:
                paths = self._bounded_queue_entries(incoming, "cloud ack queue")
            except (OSError, ValueError) as exc:
                result["transient"].append(
                    {"peer": peer_id, "type": "ack-scan", "reason": str(exc)}
                )
                continue
            for discovered_path in paths:
                path = Path(filesystem_native_path(discovered_path))
                if ".partial" in path.name:
                    result["transient"].append(
                        {"peer": peer_id, "type": "ack", "path": display_path(path)}
                    )
                    continue
                if path.suffix != ".mwxa":
                    continue
                if not ACK_PATTERN.fullmatch(path.name) or not self._stable_candidate(path):
                    result["transient"].append(
                        {"peer": peer_id, "type": "ack", "path": display_path(path)}
                    )
                    continue
                try:
                    ack = self._read_ack(
                        path, peer_id, peer["cloud_identity"]
                    )
                    state = self._peer_state(peer_id)
                    current = state["acknowledged"]
                    sequence = int(ack["last_event_sequence"])
                    if sequence < int(current["last_event_sequence"]):
                        continue
                    outstanding = state.get("outstanding")
                    if sequence == int(current["last_event_sequence"]):
                        if sequence > 0 and (
                            ack["last_bundle_id"] != current["last_bundle_id"]
                            or ack["last_bundle_sha256"]
                            != current["last_bundle_sha256"]
                        ):
                            raise ValueError(
                                "Ack conflicts with the acknowledged bundle cursor"
                            )
                        continue
                    if sequence > int(current["last_event_sequence"]):
                        if not outstanding:
                            raise ValueError("Ack advances beyond any outstanding bundle")
                        if (
                            sequence != int(outstanding["to_event_sequence"])
                            or ack["last_bundle_id"] != outstanding["bundle_id"]
                            or ack["last_bundle_sha256"]
                            != outstanding["bundle_sha256"]
                        ):
                            raise ValueError("Ack does not match the outstanding bundle")
                        state["acknowledged"] = {
                            "last_event_sequence": sequence,
                            "last_bundle_id": ack["last_bundle_id"],
                            "last_bundle_sha256": ack["last_bundle_sha256"],
                            "acknowledged_at": ack["acknowledged_at"],
                        }
                        state["outstanding"] = None
                        result["acks"].append(
                            {
                                "peer": peer_id,
                                "last_event_sequence": sequence,
                                "bundle_id": ack["last_bundle_id"],
                            }
                        )
                except (OSError, RuntimeError) as exc:
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "ack",
                            "path": display_path(path),
                            "reason": str(exc),
                        }
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    record = self._quarantine(
                        path, peer_id, "ack", str(exc)
                    )
                    result["quarantined"].append(record)

    def _write_ack(
        self,
        peer_id: str,
        peer_identity: Dict[str, str],
        local_identity: Dict[str, str],
        manifest: Dict[str, Any],
        bundle_sha256: str,
    ) -> Path:
        ack = {
            "format": self._ack_format(),
            "protocol_version": PROTOCOL_VERSION,
            "origin_node_id": self._local_node_id(),
            "target_node_id": peer_id,
            "last_event_sequence": int(manifest["to_event_sequence"]),
            "last_bundle_id": manifest["bundle_id"],
            "last_bundle_sha256": bundle_sha256,
            "acknowledged_at": now_iso(),
        }
        destination = self._ack_outbox(peer_id) / (
            f"ack-{int(manifest['to_event_sequence']):020d}-"
            f"{manifest['bundle_id']}.mwxa"
        )
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-cloud-ack-") as temp:
            plaintext = Path(temp) / "ack.json"
            plaintext.write_text(
                json.dumps(ack, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._atomic_seal(
                plaintext,
                destination,
                [
                    local_identity["encryption_public_key"],
                    peer_identity["encryption_public_key"],
                ],
                self._envelope_kind("ack"),
                peer_id,
            )
        return destination

    def _process_bundles(
        self,
        peers: Dict[str, Dict[str, Any]],
        local_identity: Dict[str, str],
        result: Dict[str, Any],
    ) -> None:
        for peer_id, peer in peers.items():
            incoming = self._incoming_outbox(peer_id)
            try:
                paths = self._bounded_queue_entries(incoming, "cloud bundle queue")
            except (OSError, ValueError) as exc:
                result["transient"].append(
                    {"peer": peer_id, "type": "bundle-scan", "reason": str(exc)}
                )
                continue
            replica_state = self.port.replica_state(peer_id)
            current_sequence = int(replica_state.get("last_event_sequence", 0))
            expected_sequence = current_sequence + 1
            candidates = []
            for discovered_path in paths:
                path = Path(filesystem_native_path(discovered_path))
                if ".partial" in path.name:
                    result["transient"].append(
                        {"peer": peer_id, "type": "bundle", "path": display_path(path)}
                    )
                    continue
                if path.suffix != ".mwxe":
                    continue
                match = ENVELOPE_PATTERN.fullmatch(path.name)
                if not match or not self._stable_candidate(path):
                    result["transient"].append(
                        {"peer": peer_id, "type": "bundle", "path": display_path(path)}
                    )
                    continue
                from_sequence = int(match.group("from_sequence"))
                if from_sequence < expected_sequence:
                    overlaps_expected = (
                        self.stream_id == "environment-v1"
                        and
                        int(match.group("to_sequence")) >= expected_sequence
                    )
                    is_current_replay = (
                        int(match.group("to_sequence")) == current_sequence
                        and match.group("bundle_id")
                        == replica_state.get("last_bundle_id")
                        and match.group("bundle_sha256")
                        == replica_state.get("last_bundle_sha256")
                    )
                    if not is_current_replay and not overlaps_expected:
                        continue
                else:
                    is_current_replay = False
                candidates.append(
                    (
                        from_sequence,
                        int(match.group("to_sequence")),
                        path.stat().st_mtime_ns,
                        is_current_replay,
                        path,
                        match,
                    )
                )
            candidates.sort(
                key=lambda item: (
                    item[0],
                    -item[1],
                    -item[2],
                    item[4].name,
                )
            )
            for (
                from_sequence,
                _to_sequence,
                _mtime_ns,
                is_current_replay,
                path,
                match,
            ) in candidates:
                if (
                    _to_sequence < expected_sequence
                    and not (
                        is_current_replay
                        and expected_sequence == current_sequence + 1
                    )
                ):
                    continue
                if (
                    from_sequence < expected_sequence
                    and not is_current_replay
                    and _to_sequence < expected_sequence
                ):
                    continue
                if from_sequence > expected_sequence:
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "bundle-gap",
                            "path": display_path(path),
                            "expected_sequence": expected_sequence,
                        }
                    )
                    continue
                try:
                    self._materialize_candidate(path)
                    with tempfile.TemporaryDirectory(
                        prefix="memory-wuxian-cloud-bundle-"
                    ) as temp:
                        bundle = Path(temp) / "delta.mwxb"
                        open_result = self.crypto.open(
                            path,
                            bundle,
                            self._identity_private_path(),
                            peer["cloud_identity"]["signing_public_key"],
                            self._envelope_kind("bundle"),
                            peer_id,
                            self._local_node_id(),
                        )
                        actual_sha256 = bytes_sha256(bundle.read_bytes())
                        if (
                            not isinstance(open_result, dict)
                            or open_result.get("origin_node_id") != peer_id
                            or open_result.get("target_node_id") != self._local_node_id()
                            or open_result.get("payload_sha256") != actual_sha256
                            or int(open_result.get("payload_length", -1))
                            != bundle.stat().st_size
                        ):
                            raise ValueError("Cloud crypto-open result binding mismatch")
                        if actual_sha256 != match.group("bundle_sha256"):
                            raise ValueError(
                                "Cloud envelope filename bundle SHA-256 mismatch"
                            )
                        manifest = self.port.read_bundle_manifest(bundle)
                        if manifest.get("bundle_id") != match.group("bundle_id"):
                            raise ValueError(
                                "Cloud envelope filename bundle ID mismatch"
                            )
                        if int(manifest["from_event_sequence"]) != from_sequence:
                            raise ValueError(
                                "Cloud envelope filename sequence mismatch"
                            )
                        with exclusive_lock(self.port.exchange_lock_path):
                            imported = self.port.import_bundle(
                                bundle,
                                expected_node_id=peer_id,
                                authenticated_open_result=open_result,
                            )
                        ack_path = self._write_ack(
                            peer_id,
                            peer["cloud_identity"],
                            local_identity,
                            manifest,
                            actual_sha256,
                        )
                    result["imports"].append(
                        {
                            "peer": peer_id,
                            "status": imported["status"],
                            "bundle_id": manifest["bundle_id"],
                            "ack": str(ack_path),
                        }
                    )
                    expected_sequence = int(manifest["to_event_sequence"]) + 1
                except (OSError, RuntimeError) as exc:
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "bundle",
                            "path": display_path(path),
                            "reason": str(exc),
                        }
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    record = self._quarantine(
                        path, peer_id, "bundle", str(exc)
                    )
                    result["quarantined"].append(record)

    def _publish_peer(
        self,
        peer_id: str,
        peer: Dict[str, Any],
        local_identity: Dict[str, str],
        timestamp: float,
        result: Dict[str, Any],
    ) -> str:
        state = self._peer_state(peer_id)
        outstanding = state.get("outstanding")
        if not outstanding:
            acknowledged_sequence = int(
                state["acknowledged"]["last_event_sequence"]
            )
            candidates = []
            for path in self._bounded_queue_entries(
                self._outbox(peer_id), "cloud outbox recovery"
            ):
                if path.suffix != ".mwxe":
                    continue
                match = ENVELOPE_PATTERN.fullmatch(path.name)
                if (
                    match
                    and self._stable_candidate(path)
                    and int(match.group("from_sequence"))
                    == acknowledged_sequence + 1
                ):
                    candidates.append((path, match))
            if candidates:
                path, match = candidates[0]
                outstanding = {
                    "path": display_path(path),
                    "bundle_id": match.group("bundle_id"),
                    "bundle_sha256": match.group("bundle_sha256"),
                    "from_event_sequence": int(match.group("from_sequence")),
                    "to_event_sequence": int(match.group("to_sequence")),
                    "published_at": path.stat().st_mtime,
                }
                state["outstanding"] = outstanding
                if len(candidates) > 1:
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "outbox-recovery",
                            "reason": "multiple unacknowledged envelopes",
                        }
                    )
        if outstanding:
            path = self._assert_queue_path(
                Path(str(outstanding.get("path", ""))), for_write=True
            )
            if path.is_file() and path.stat().st_size > 0:
                result["waiting_ack"].append(
                    {"peer": peer_id, "bundle_id": outstanding["bundle_id"]}
                )
                return "waiting-ack"
            state["outstanding"] = None
        acknowledged = state["acknowledged"]
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-cloud-export-") as temp:
            bundle = Path(temp) / "delta.mwxb"
            with exclusive_lock(self.port.exchange_lock_path):
                exported = self.port.export_delta(
                    bundle,
                    after_event_sequence=int(acknowledged["last_event_sequence"]),
                    target_node_id=peer_id,
                    previous_bundle_sha256=acknowledged["last_bundle_sha256"],
                )
            if exported["status"] == "no-change":
                return "no-change"
            destination = self._outbox(peer_id) / (
                f"{int(exported['from_event_sequence']):020d}-"
                f"{int(exported['to_event_sequence']):020d}-"
                f"{exported['bundle_id']}-{exported['sha256']}.mwxe"
            )
            self._atomic_seal(
                bundle,
                destination,
                [
                    local_identity["encryption_public_key"],
                    peer["cloud_identity"]["encryption_public_key"],
                ],
                self._envelope_kind("bundle"),
                peer_id,
            )
        native_destination = filesystem_native_path(destination)
        state["outstanding"] = {
            "path": native_destination,
            "bundle_id": exported["bundle_id"],
            "bundle_sha256": exported["sha256"],
            "from_event_sequence": int(exported["from_event_sequence"]),
            "to_event_sequence": int(exported["to_event_sequence"]),
            "published_at": timestamp,
        }
        result["published"].append(
            {
                "peer": peer_id,
                "path": native_destination,
                "display_path": display_path(destination),
                "bundle_id": exported["bundle_id"],
                "from_event_sequence": int(exported["from_event_sequence"]),
                "to_event_sequence": int(exported["to_event_sequence"]),
                "has_more": bool(exported.get("has_more")),
            }
        )
        return "published"

    def _cleanup(self, peers: Dict[str, Dict[str, Any]], timestamp: float) -> List[str]:
        removed: List[str] = []
        grace = int(self.config["cleanup_grace_seconds"])
        for peer_id in peers:
            acknowledged = self._peer_state(peer_id)["acknowledged"]
            last_sequence = int(acknowledged["last_event_sequence"])
            if last_sequence > 0:
                outbox = self._outbox(peer_id)
                for path in self._bounded_queue_entries(outbox, "cloud cleanup outbox"):
                    if path.suffix != ".mwxe":
                        continue
                    match = ENVELOPE_PATTERN.fullmatch(path.name)
                    if not match:
                        continue
                    if int(match.group("to_sequence")) > last_sequence:
                        continue
                    try:
                        age = timestamp - path.stat().st_mtime
                    except OSError:
                        continue
                    if age < grace:
                        continue
                    self._assert_queue_path(path)
                    path.unlink(missing_ok=True)
                    removed.append(display_path(path))
            acknowledgements = [
                path
                for path in self._bounded_queue_entries(
                    self._ack_outbox(peer_id), "cloud cleanup acknowledgements"
                )
                if path.suffix == ".mwxa" and ACK_PATTERN.fullmatch(path.name)
            ]
            for path in acknowledgements[:-1]:
                try:
                    age = timestamp - path.stat().st_mtime
                except OSError:
                    continue
                if age < grace:
                    continue
                self._assert_queue_path(path)
                path.unlink(missing_ok=True)
                removed.append(display_path(path))
        return removed

    def sync(self, force: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
        """Run one short import/ack/export pass and return structured status."""
        timestamp = float(self.clock() if now is None else now)
        result: Dict[str, Any] = {
            "status": "disabled" if not self.config.get("enabled") else "ok",
            "stream_id": self.stream_id or "archive-v1",
            "acks": [],
            "imports": [],
            "published": [],
            "waiting_ack": [],
            "transient": [],
            "quarantined": [],
            "cleaned": [],
            "migrated": [],
        }
        if not self.config.get("enabled"):
            return result
        local_identity = _validated_identity(
            self.crypto.show_identity(self._identity_private_path()), "local"
        )
        peers = self._trusted_cloud_peers()
        for peer_id in peers:
            try:
                self._migrate_outstanding_path(peer_id, result)
            except (OSError, RuntimeError) as exc:
                result["transient"].append(
                    {
                        "peer": peer_id,
                        "type": "outbox-path-migration",
                        "reason": str(exc),
                    }
                )
        self._process_acks(peers, result)
        self._process_bundles(peers, local_identity, result)
        observation = self._observation(timestamp)
        schedule_state = self._schedule_due(observation, timestamp, force)
        result["schedule"] = schedule_state
        if schedule_state["due"]:
            all_handled = True
            for peer_id, peer in peers.items():
                try:
                    publish_status = self._publish_peer(
                        peer_id, peer, local_identity, timestamp, result
                    )
                    all_handled = (
                        publish_status in {"published", "no-change"}
                        and all_handled
                    )
                except (OSError, RuntimeError) as exc:
                    all_handled = False
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "publish",
                            "reason": str(exc),
                        }
                    )
            self.config["schedule"]["last_attempt_at"] = timestamp
            if all_handled and peers:
                self.config["schedule"]["published"] = observation
                self.config["schedule"]["pending_since"] = None
        try:
            result["cleaned"] = self._cleanup(peers, timestamp)
        except (OSError, ValueError) as exc:
            result["transient"].append(
                {"type": "cleanup-scan", "reason": str(exc)}
            )
        self.save_config()
        result["counts"] = {
            "acknowledged": len(result["acks"]),
            "imported": sum(
                item.get("status") == "imported" for item in result["imports"]
            ),
            "no_change": sum(
                item.get("status") == "no-change" for item in result["imports"]
            ),
            "published": len(result["published"]),
            "waiting_ack": len(result["waiting_ack"]),
            "transient": len(result["transient"]),
            "quarantined": len(result["quarantined"]),
        }
        if result["transient"] or result["quarantined"]:
            result["status"] = "degraded"
        elif result["waiting_ack"]:
            result["status"] = "waiting-ack"
        for peer_id in peers:
            imported_count = sum(
                item["peer"] == peer_id and item.get("status") == "imported"
                for item in result["imports"]
            )
            transient_count = sum(
                item.get("peer") == peer_id for item in result["transient"]
            )
            quarantined_count = sum(
                item.get("peer") == peer_id for item in result["quarantined"]
            )
            peer_activity = (
                any(item["peer"] == peer_id for item in result["published"])
                or imported_count > 0
                or any(item["peer"] == peer_id for item in result["acks"])
                or transient_count > 0
                or quarantined_count > 0
            )
            if peer_activity:
                self.port.log_sync(
                    "cloud-folder-sync",
                    peer_id,
                    {
                        "published": sum(
                            item["peer"] == peer_id for item in result["published"]
                        ),
                        "imported": imported_count,
                        "acknowledged": sum(
                            item["peer"] == peer_id for item in result["acks"]
                        ),
                        "transient": transient_count,
                        "quarantined": quarantined_count,
                        "stream_id": result["stream_id"],
                    },
                )
        return result
