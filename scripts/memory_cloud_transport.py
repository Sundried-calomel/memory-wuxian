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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol

from platform_lock import exclusive_lock
from memory_federation import (
    PROTOCOL_VERSION,
    FederationManager,
    atomic_write_json,
    bytes_sha256,
    now_iso,
    read_json,
    safe_node_id,
)


CLOUD_FORMAT_VERSION = 1
ACK_FORMAT = "memory-wuxian-cloud-ack-v1"
DEFAULT_MERGE_WINDOW_SECONDS = 900
DEFAULT_EARLY_FLUSH_BYTES = 1024 * 1024
DEFAULT_MAXIMUM_PENDING_SECONDS = 3600
DEFAULT_CLEANUP_GRACE_SECONDS = 24 * 60 * 60
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
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            error = (
                f"memory-wuxian-envelope failed with exit code "
                f"{completed.returncode}: {detail}"
            )
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
    ) -> Dict[str, Any]:
        return self._run(
            [
                "open",
                "--identity",
                str(identity_private_path),
                "--signing-public-key",
                signing_public_key,
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


def _file_marker(path: Path) -> Dict[str, int]:
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError):
        return {"size": 0, "mtime_ns": 0}
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


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
        manager: FederationManager,
        crypto: Optional[CryptoAdapter] = None,
        config_path: Optional[Path] = None,
        clock: Optional[Any] = None,
    ):
        self.manager = manager
        self.store = manager.store
        self.archive_root = manager.root
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else manager.metadata_root / "cloud.json"
        )
        self.quarantine_root = manager.metadata_root / "cloud-quarantine"
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
        resolved_exchange_root = Path(exchange_root).expanduser().resolve()
        if not resolved_exchange_root.is_dir():
            raise ValueError(
                f"Cloud synchronization directory does not exist: {resolved_exchange_root}"
            )
        resolved_identity_path = Path(identity_private_path).expanduser().resolve()
        archive_root = self.archive_root.resolve()
        replica_root = self.manager.replica_root.resolve()
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
        for peer in self.manager.status().get("devices", []):
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
            "exchange_provider_path_configured": bool(
                str(self.config.get("exchange_root", "")).strip()
            ),
            "identity_ready": (
                self._identity_private_path().is_file() if configured else False
            ),
            "schedule": self.config.get("schedule"),
            "peers": peers,
        }

    def _exchange_root(self) -> Path:
        value = str(self.config.get("exchange_root", "")).strip()
        if not value:
            raise ValueError("Cloud exchange_root is not configured")
        return Path(value).expanduser().resolve() / "MemoryWuxianExchange" / "v1"

    def _identity_private_path(self) -> Path:
        value = str(self.config.get("identity_private_path", "")).strip()
        if not value:
            raise ValueError("Cloud identity_private_path is not configured")
        return Path(value).expanduser().resolve()

    def _local_node_id(self) -> str:
        return safe_node_id(str(self.manager.node()["node_id"]))

    def _trusted_cloud_peers(self) -> Dict[str, Dict[str, Any]]:
        peers: Dict[str, Dict[str, Any]] = {}
        for peer in self.manager.peers():
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
        return self._exchange_root() / "nodes" / self._local_node_id()

    def _outbox(self, target_node_id: str) -> Path:
        return self._self_node_root() / "outbox" / safe_node_id(target_node_id)

    def _ack_outbox(self, origin_node_id: str) -> Path:
        return self._self_node_root() / "acks" / safe_node_id(origin_node_id)

    def _incoming_outbox(self, origin_node_id: str) -> Path:
        return (
            self._exchange_root()
            / "nodes"
            / safe_node_id(origin_node_id)
            / "outbox"
            / self._local_node_id()
        )

    def _incoming_acks(self, acknowledging_node_id: str) -> Path:
        return (
            self._exchange_root()
            / "nodes"
            / safe_node_id(acknowledging_node_id)
            / "acks"
            / self._local_node_id()
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
        state = read_json(self.store.state_path, {})
        completed = int(state.get("completed_rounds", 0)) + len(
            state.get("completed_rounds_out_of_order") or []
        )
        local = datetime.fromtimestamp(timestamp).astimezone()
        raw_today = (
            self.store.raw_dir
            / f"{local.year:04d}"
            / f"{local.month:02d}"
            / f"{local.date().isoformat()}.md"
        )
        return {
            "completed_rounds": completed,
            "raw_today": _file_marker(raw_today),
            "summary_registry": _file_marker(
                self.store.summaries_dir / "registry.jsonl"
            ),
            "title_index": _file_marker(self.store.title_index_path),
        }

    @staticmethod
    def _eligible_change(current: Dict[str, Any], published: Dict[str, Any]) -> bool:
        if int(current.get("completed_rounds", 0)) > int(
            published.get("completed_rounds", 0)
        ):
            return True
        for key in ("summary_registry", "title_index"):
            if current.get(key) != published.get(key):
                return True
        return False

    @staticmethod
    def _estimated_new_bytes(
        current: Dict[str, Any], published: Dict[str, Any]
    ) -> int:
        total = 0
        for key in ("raw_today", "summary_registry", "title_index"):
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
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
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
            os.replace(partial, destination)
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
        atomic_write_json(destination, record)
        return record

    @staticmethod
    def _stable_candidate(path: Path) -> bool:
        if path.name.endswith(".partial") or ".partial." in path.name:
            return False
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _read_ack(
        self,
        path: Path,
        peer_node_id: str,
        peer_identity: Dict[str, str],
    ) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-cloud-ack-") as temp:
            plaintext = Path(temp) / "ack.json"
            self.crypto.open(
                path,
                plaintext,
                self._identity_private_path(),
                peer_identity["signing_public_key"],
                "ack",
                peer_node_id,
                self._local_node_id(),
            )
            ack = json.loads(plaintext.read_text(encoding="utf-8"))
        if ack.get("format") != ACK_FORMAT:
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
                paths = sorted(incoming.iterdir()) if incoming.exists() else []
            except OSError as exc:
                result["transient"].append(
                    {"peer": peer_id, "type": "ack-scan", "reason": str(exc)}
                )
                continue
            for path in paths:
                if ".partial" in path.name:
                    result["transient"].append(
                        {"peer": peer_id, "type": "ack", "path": str(path)}
                    )
                    continue
                if path.suffix != ".mwxa":
                    continue
                if not ACK_PATTERN.fullmatch(path.name) or not self._stable_candidate(path):
                    result["transient"].append(
                        {"peer": peer_id, "type": "ack", "path": str(path)}
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
                            "path": str(path),
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
            "format": ACK_FORMAT,
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
                "ack",
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
                paths = sorted(incoming.iterdir()) if incoming.exists() else []
            except OSError as exc:
                result["transient"].append(
                    {"peer": peer_id, "type": "bundle-scan", "reason": str(exc)}
                )
                continue
            expected_sequence = int(
                self.manager.replica_state(peer_id).get("last_event_sequence", 0)
            ) + 1
            for path in paths:
                if ".partial" in path.name:
                    result["transient"].append(
                        {"peer": peer_id, "type": "bundle", "path": str(path)}
                    )
                    continue
                if path.suffix != ".mwxe":
                    continue
                match = ENVELOPE_PATTERN.fullmatch(path.name)
                if not match or not self._stable_candidate(path):
                    result["transient"].append(
                        {"peer": peer_id, "type": "bundle", "path": str(path)}
                    )
                    continue
                from_sequence = int(match.group("from_sequence"))
                if from_sequence > expected_sequence:
                    result["transient"].append(
                        {
                            "peer": peer_id,
                            "type": "bundle-gap",
                            "path": str(path),
                            "expected_sequence": expected_sequence,
                        }
                    )
                    continue
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="memory-wuxian-cloud-bundle-"
                    ) as temp:
                        bundle = Path(temp) / "delta.mwxb"
                        self.crypto.open(
                            path,
                            bundle,
                            self._identity_private_path(),
                            peer["cloud_identity"]["signing_public_key"],
                            "bundle",
                            peer_id,
                            self._local_node_id(),
                        )
                        actual_sha256 = bytes_sha256(bundle.read_bytes())
                        if actual_sha256 != match.group("bundle_sha256"):
                            raise ValueError(
                                "Cloud envelope filename bundle SHA-256 mismatch"
                            )
                        manifest = self.manager.read_bundle_manifest(bundle)
                        if manifest.get("bundle_id") != match.group("bundle_id"):
                            raise ValueError(
                                "Cloud envelope filename bundle ID mismatch"
                            )
                        if int(manifest["from_event_sequence"]) != from_sequence:
                            raise ValueError(
                                "Cloud envelope filename sequence mismatch"
                            )
                        imported = self.manager.import_delta(
                            bundle, expected_node_id=peer_id
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
                            "path": str(path),
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
    ) -> bool:
        state = self._peer_state(peer_id)
        outstanding = state.get("outstanding")
        if not outstanding:
            acknowledged_sequence = int(
                state["acknowledged"]["last_event_sequence"]
            )
            candidates = []
            for path in sorted(self._outbox(peer_id).glob("*.mwxe")):
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
                    "path": str(path),
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
            path = Path(str(outstanding.get("path", "")))
            if path.is_file() and path.stat().st_size > 0:
                result["waiting_ack"].append(
                    {"peer": peer_id, "bundle_id": outstanding["bundle_id"]}
                )
                return True
            state["outstanding"] = None
        acknowledged = state["acknowledged"]
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-cloud-export-") as temp:
            bundle = Path(temp) / "delta.mwxb"
            with exclusive_lock(self.archive_root / ".locks" / "archive.lock"):
                exported = self.manager.export_delta(
                    bundle,
                    after_event_sequence=int(acknowledged["last_event_sequence"]),
                    target_node_id=peer_id,
                    previous_bundle_sha256=acknowledged["last_bundle_sha256"],
                )
            if exported["status"] == "no-change":
                return True
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
                "bundle",
                peer_id,
            )
        state["outstanding"] = {
            "path": str(destination),
            "bundle_id": exported["bundle_id"],
            "bundle_sha256": exported["sha256"],
            "from_event_sequence": int(exported["from_event_sequence"]),
            "to_event_sequence": int(exported["to_event_sequence"]),
            "published_at": timestamp,
        }
        result["published"].append(
            {
                "peer": peer_id,
                "path": str(destination),
                "bundle_id": exported["bundle_id"],
                "from_event_sequence": int(exported["from_event_sequence"]),
                "to_event_sequence": int(exported["to_event_sequence"]),
                "has_more": bool(exported.get("has_more")),
            }
        )
        return True

    def _cleanup(self, peers: Dict[str, Dict[str, Any]], timestamp: float) -> List[str]:
        removed: List[str] = []
        grace = int(self.config["cleanup_grace_seconds"])
        for peer_id in peers:
            acknowledged = self._peer_state(peer_id)["acknowledged"]
            last_sequence = int(acknowledged["last_event_sequence"])
            if last_sequence > 0:
                outbox = self._outbox(peer_id)
                for path in sorted(outbox.glob("*.mwxe")):
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
                    path.unlink(missing_ok=True)
                    removed.append(str(path))
            acknowledgements = [
                path
                for path in sorted(self._ack_outbox(peer_id).glob("*.mwxa"))
                if ACK_PATTERN.fullmatch(path.name)
            ]
            for path in acknowledgements[:-1]:
                try:
                    age = timestamp - path.stat().st_mtime
                except OSError:
                    continue
                if age < grace:
                    continue
                path.unlink(missing_ok=True)
                removed.append(str(path))
        return removed

    def sync(self, force: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
        """Run one short import/ack/export pass and return structured status."""
        timestamp = float(self.clock() if now is None else now)
        result: Dict[str, Any] = {
            "status": "disabled" if not self.config.get("enabled") else "ok",
            "acks": [],
            "imports": [],
            "published": [],
            "waiting_ack": [],
            "transient": [],
            "quarantined": [],
            "cleaned": [],
        }
        if not self.config.get("enabled"):
            return result
        local_identity = _validated_identity(
            self.crypto.show_identity(self._identity_private_path()), "local"
        )
        peers = self._trusted_cloud_peers()
        self._process_acks(peers, result)
        self._process_bundles(peers, local_identity, result)
        observation = self._observation(timestamp)
        schedule_state = self._schedule_due(observation, timestamp, force)
        result["schedule"] = schedule_state
        if schedule_state["due"]:
            all_handled = True
            for peer_id, peer in peers.items():
                try:
                    all_handled = (
                        self._publish_peer(
                            peer_id, peer, local_identity, timestamp, result
                        )
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
        result["cleaned"] = self._cleanup(peers, timestamp)
        self.save_config()
        for peer_id in peers:
            peer_activity = (
                any(item["peer"] == peer_id for item in result["published"])
                or any(item["peer"] == peer_id for item in result["imports"])
                or any(item["peer"] == peer_id for item in result["acks"])
            )
            if peer_activity:
                self.manager.log_sync(
                    "cloud-folder-sync",
                    peer_id,
                    {
                        "published": sum(
                            item["peer"] == peer_id for item in result["published"]
                        ),
                        "imported": sum(
                            item["peer"] == peer_id for item in result["imports"]
                        ),
                        "acknowledged": sum(
                            item["peer"] == peer_id for item in result["acks"]
                        ),
                    },
                )
        return result
