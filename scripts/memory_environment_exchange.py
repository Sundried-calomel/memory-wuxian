"""Independent environment-v1 exchange stream for Memory無限 2.0."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from memory_environment import EnvironmentRegistry, canonical_bytes, read_json
from memory_environment_evolution import ProductEvolutionStore
from memory_environment_governance import GovernanceProposalStore
from memory_environment_skills import (
    EnvironmentSkillInstaller,
    MAX_SKILL_PACKAGE_BYTES,
    skill_package_contract_bytes,
)
from memory_environment_profiles import EnvironmentProfileManager
from memory_federation import (
    FederationManager,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    bytes_sha256,
    canonical_sha256,
    now_iso,
    read_jsonl,
    safe_node_id,
    safe_relative_path,
)
from platform_paths import is_link_like


ENVIRONMENT_BUNDLE_FORMAT = "memory-wuxian-environment-bundle-v1"
ENVIRONMENT_PROTOCOL_VERSION = 1
MAX_ARTIFACTS = 256
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_FILES = 2
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_REPLICA_EVENTS = 100000
MAX_REPLICA_LEDGER_BYTES = 64 * 1024 * 1024


def _sealed_transaction_marker(marker: Dict[str, Any]) -> Dict[str, Any]:
    sealed = dict(marker)
    sealed.pop("marker_sha256", None)
    sealed["marker_sha256"] = canonical_sha256(sealed)
    return sealed


def _file_marker(path: Path) -> Dict[str, int]:
    try:
        stat = path.stat()
        return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
    except OSError:
        return {"size": 0, "mtime_ns": 0}


class EnvironmentExchangeManager:
    """Stage authenticated peer Environment revisions without auto-installing them."""

    requires_authenticated_transport = True

    def __init__(self, store: Any):
        self.store = store
        self.root = store.root
        self.registry = EnvironmentRegistry(self.root)
        self.federation = FederationManager(store)
        self.governance = GovernanceProposalStore(store)
        self.evolution = ProductEvolutionStore(store)
        self.profiles = EnvironmentProfileManager(self.root)
        self.metadata_root = self.registry.root / "exchange"
        self.export_state_path = self.metadata_root / "export-state.json"
        self.export_ledger_path = self.metadata_root / "export-ledger.jsonl"
        self.sync_log_path = self.metadata_root / "sync-log.jsonl"
        self.replica_root = self.registry.root / "replicas"
        self.exchange_lock_path = (
            self.registry.locks_dir / "environment-exchange.lock"
        )

    def init_layout(self) -> None:
        self.registry.init()
        self.governance.init()
        self.evolution.init()
        directories = (
            ("exchange", "Environment exchange metadata root"),
            ("replicas/peers", "Environment replica peer root"),
            ("staging/incoming", "Environment incoming staging root"),
        )
        for relative, label in directories:
            directory = self.registry._resolve_relative(
                relative, label, for_write=True
            )
            directory.mkdir(parents=True, exist_ok=True)
        self.metadata_root = self.registry._resolve_relative(
            "exchange", "Environment exchange metadata root"
        )
        self.replica_root = self.registry._resolve_relative(
            "replicas", "Environment replica root", for_write=True
        )
        self.export_state_path = self.registry._resolve_relative(
            "exchange/export-state.json", "Environment export state", for_write=True
        )
        self.export_ledger_path = self.registry._resolve_relative(
            "exchange/export-ledger.jsonl", "Environment export ledger", for_write=True
        )
        self.sync_log_path = self.registry._resolve_relative(
            "exchange/sync-log.jsonl", "Environment sync log", for_write=True
        )
        if not self.export_state_path.exists():
            atomic_write_json(
                self.export_state_path,
                {
                    "format_version": 1,
                    "next_event_sequence": 1,
                    "source_events": {},
                },
            )
        if not self.export_ledger_path.exists():
            atomic_write_bytes(self.export_ledger_path, b"")
        if not self.sync_log_path.exists():
            atomic_write_bytes(self.sync_log_path, b"")

    def node(self) -> Dict[str, Any]:
        return self.federation.node()

    def peers(self) -> List[Dict[str, Any]]:
        return self.federation.peers()

    def status(self) -> Dict[str, Any]:
        base = self.federation.status()
        recent_sync = []
        for record in read_jsonl(self.sync_log_path)[-20:]:
            details = record.get("details")
            if not isinstance(details, dict):
                details = {}
            recent_sync.append(
                {
                    "timestamp": record.get("timestamp"),
                    "event": record.get("event"),
                    "node_id": record.get("node_id"),
                    "published": int(
                        record.get("published", details.get("published", 0))
                    ),
                    "imported": int(
                        record.get("imported", details.get("imported", 0))
                    ),
                    "acknowledged": int(
                        record.get(
                            "acknowledged", details.get("acknowledged", 0)
                        )
                    ),
                }
            )
        base.update(
            {
                "stream_id": "environment-v1",
                "local_event_sequence": max(
                    (
                        int(item["event_sequence"])
                        for item in read_jsonl(self.export_ledger_path)
                    ),
                    default=0,
                ),
                "recent_sync": recent_sync,
            }
        )
        return base

    def log_sync(self, event: str, node_id: str, details: Dict[str, Any]) -> None:
        self.init_layout()
        record = {
            "timestamp": now_iso(),
            "event": event,
            "node_id": safe_node_id(node_id),
            **details,
        }
        with self.sync_log_path.open(
            "a", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    def exchange_observation(self, _timestamp: float) -> Dict[str, Any]:
        registry = (
            read_json(self.registry.registry_path)
            if self.registry.registry_path.exists()
            else {"events": []}
        )
        return {
            "completed_rounds": len(registry.get("events") or []),
            "raw_today": _file_marker(self.registry.registry_path),
            "summary_registry": _file_marker(self.export_ledger_path),
            "title_index": _file_marker(self.registry.state_path),
            "governance_proposals": {
                "count": len(self.governance.local_events()),
                "directory": _file_marker(self.governance.local_root),
            },
            "personal_environment_profiles": _file_marker(self.profiles.events_path),
        }

    def refresh_export_ledger(self) -> List[Dict[str, Any]]:
        self.init_layout()
        local_node_id = self.node()["node_id"]
        state = read_json(self.export_state_path)
        known = dict(state.get("source_events") or {})
        ledger = read_jsonl(self.export_ledger_path)
        exported_revisions = {
            (item.get("artifact_id"), item.get("revision_id"))
            for item in ledger
            if item.get("event_kind") in (None, "artifact-revision")
        }
        exported_projects = {
            (
                item.get("project_id"),
                canonical_sha256((item.get("payload") or {}).get("project")),
            )
            for item in ledger
            if item.get("event_kind") == "project-registration"
        }
        for item in ledger:
            identity = self._ledger_source_identity(item)
            if identity:
                known.setdefault(identity, int(item["event_sequence"]))
        next_sequence = int(state.get("next_event_sequence", 1))
        registry = self.registry._read_registry()
        for event in registry["events"]:
            if event.get("operation") != "artifact-revision":
                continue
            source_event_id = self._registry_source_identity(event)
            revision_identity = (event["artifact_id"], event["revision_id"])
            if source_event_id in known or revision_identity in exported_revisions:
                known.setdefault(
                    source_event_id,
                    next(
                        int(item["event_sequence"])
                        for item in ledger
                        if (
                            item.get("artifact_id"),
                            item.get("revision_id"),
                        )
                        == revision_identity
                    ),
                )
                continue
            artifact = self.registry._read_relative_json(
                event["artifact_path"], "artifact_path"
            )
            revision = self.registry._read_relative_json(
                event["revision_path"], "revision_path"
            )
            if revision["origin_node_id"] != local_node_id:
                continue
            if self._superseded_skill_without_package(
                registry, artifact, revision
            ):
                continue
            self.registry._verify_object(revision)
            object_path = self.registry._resolve_relative(
                revision["object_path"], "object_path"
            )
            content = object_path.read_bytes()
            package_attachment = self._skill_package_attachment(
                artifact, revision, content
            )
            payload = {
                "artifact": artifact,
                "revision": revision,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "package_attachment": package_attachment,
            }
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "artifact-revision",
                    "artifact_id": artifact["artifact_id"],
                    "revision_id": revision["revision_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        for event in registry["events"]:
            if event.get("operation") != "project-registration":
                continue
            source_event_id = self._registry_source_identity(event)
            if source_event_id in known:
                continue
            project = self.registry._read_relative_json(
                event["project_path"], "project_path"
            )
            project_identity = (
                project["project_id"],
                canonical_sha256(project),
            )
            if project_identity in exported_projects:
                continue
            payload = {"project": project}
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "project-registration",
                    "project_id": project["project_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        for event in self.governance.local_events():
            source_event_id = event["source_event_id"]
            if source_event_id in known:
                continue
            payload = event["payload"]
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "governance-proposal",
                    "proposal_id": event["proposal_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        for event in self.evolution.local_events():
            source_event_id = event["source_event_id"]
            if source_event_id in known:
                continue
            payload = event["payload"]
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "product-evolution",
                    "record_id": event["record_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        for event in self.profiles.local_events():
            source_event_id = event["source_event_id"]
            if source_event_id in known:
                continue
            payload = event["generation"]
            ledger.append(
                {
                    "event_sequence": next_sequence,
                    "source_event_id": source_event_id,
                    "event_kind": "personal-environment-profile",
                    "profile_id": event["profile_id"],
                    "payload_sha256": canonical_sha256(payload),
                    "payload": payload,
                }
            )
            known[source_event_id] = next_sequence
            next_sequence += 1
        atomic_write_jsonl(self.export_ledger_path, ledger)
        atomic_write_json(
            self.export_state_path,
            {
                "format_version": 1,
                "next_event_sequence": next_sequence,
                "source_events": known,
            },
        )
        return ledger

    @staticmethod
    def _registry_source_identity(event: Dict[str, Any]) -> str:
        event_id = str(event["event_id"])
        if event["operation"] == "artifact-revision":
            return (
                f"{event_id}:artifact-revision:"
                f"{event['artifact_id']}:{event['revision_id']}"
            )
        if event["operation"] == "project-registration":
            return (
                f"{event_id}:project-registration:"
                f"{event['project_id']}:{event['project_path']}"
            )
        raise ValueError("unsupported Environment Registry event")

    @staticmethod
    def _ledger_source_identity(item: Dict[str, Any]) -> Optional[str]:
        source = item.get("source_event_id")
        if not isinstance(source, str) or not source:
            return None
        if ":" in source.removeprefix("evt-"):
            return source
        if item.get("event_kind") in (None, "artifact-revision"):
            artifact_id = item.get("artifact_id")
            revision_id = item.get("revision_id")
            if isinstance(artifact_id, str) and isinstance(revision_id, str):
                return (
                    f"{source}:artifact-revision:{artifact_id}:{revision_id}"
                )
        if item.get("event_kind") == "project-registration":
            project = (item.get("payload") or {}).get("project") or {}
            project_id = item.get("project_id")
            project_path = project.get("local_root")
            if isinstance(project_id, str) and isinstance(project_path, str):
                return (
                    f"{source}:project-registration:{project_id}:{project_path}"
                )
        return None

    def _superseded_skill_without_package(
        self,
        registry: Dict[str, Any],
        artifact: Dict[str, Any],
        revision: Dict[str, Any],
    ) -> bool:
        """Skip only replaced local Skill candidates that never passed package verification."""

        if not artifact["object_class"].endswith("-skill"):
            return False
        current = registry["current_artifacts"].get(artifact["artifact_id"])
        if (
            not isinstance(current, dict)
            or current.get("revision_id") == revision["revision_id"]
        ):
            return False
        reference_path = (
            self.registry.root
            / "packages"
            / "by-revision"
            / f"{revision['revision_id'].split(':', 1)[1]}.json"
        )
        return not reference_path.is_file() or reference_path.is_symlink()

    def export_delta(
        self,
        output: Path,
        after_event_sequence: int = 0,
        target_node_id: Optional[str] = None,
        previous_bundle_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        node = self.node()
        ledger = self.refresh_export_ledger()
        after = int(after_event_sequence)
        latest = max((int(item["event_sequence"]) for item in ledger), default=0)
        if after < 0 or after > latest:
            raise ValueError("environment export cursor is invalid")
        if after and not (
            isinstance(previous_bundle_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", previous_bundle_sha256)
        ):
            raise ValueError("noninitial environment export requires predecessor hash")
        if not after and previous_bundle_sha256 is not None:
            raise ValueError("initial environment export cannot name a predecessor")
        pending = [item for item in ledger if int(item["event_sequence"]) > after]
        if not pending:
            return {
                "status": "no-change",
                "origin_node_id": node["node_id"],
                "after_event_sequence": after,
            }
        selected = []
        size = 0
        for item in pending:
            encoded = canonical_bytes(item) + b"\n"
            if len(encoded) > MAX_PAYLOAD_BYTES:
                raise ValueError("environment artifact exceeds bundle limit")
            if len(selected) >= MAX_ARTIFACTS or size + len(encoded) > MAX_PAYLOAD_BYTES:
                break
            selected.append(item)
            size += len(encoded)
        payload = b"".join(canonical_bytes(item) + b"\n" for item in selected)
        manifest_base = {
            "format": ENVIRONMENT_BUNDLE_FORMAT,
            "protocol_version": ENVIRONMENT_PROTOCOL_VERSION,
            "stream_id": "environment-v1",
            "origin_node_id": node["node_id"],
            "target_node_id": (
                safe_node_id(target_node_id) if target_node_id else None
            ),
            "base_event_sequence": after,
            "previous_bundle_sha256": previous_bundle_sha256,
            "from_event_sequence": int(selected[0]["event_sequence"]),
            "to_event_sequence": int(selected[-1]["event_sequence"]),
            "artifact_count": len(selected),
            "payload_path": "payload/environment.jsonl",
            "payload_bytes": len(payload),
            "payload_sha256": bytes_sha256(payload),
        }
        manifest = {
            **manifest_base,
            "bundle_id": f"mwb-{canonical_sha256(manifest_base)[:32]}",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".part", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        manifest, ensure_ascii=False, indent=2, sort_keys=True
                    ).encode("utf-8")
                    + b"\n",
                )
                archive.writestr("payload/environment.jsonl", payload)
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "status": "created",
            "bundle": str(output),
            "bundle_id": manifest["bundle_id"],
            "origin_node_id": node["node_id"],
            "from_event_sequence": manifest["from_event_sequence"],
            "to_event_sequence": manifest["to_event_sequence"],
            "artifact_count": manifest["artifact_count"],
            "has_more": manifest["to_event_sequence"] < latest,
            "sha256": bytes_sha256(output.read_bytes()),
        }

    def read_bundle_manifest(self, bundle: Path) -> Dict[str, Any]:
        if not bundle.is_file():
            raise ValueError("environment bundle does not exist")
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_BUNDLE_FILES or len(names) != len(set(names)):
                raise ValueError("environment bundle file inventory is invalid")
            for name in names:
                safe_relative_path(name)
            if set(names) != {"manifest.json", "payload/environment.jsonl"}:
                raise ValueError("environment bundle contains unexpected files")
            inventory = {item.filename: item for item in infos}
            manifest_info = inventory["manifest.json"]
            payload_info = inventory["payload/environment.jsonl"]
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("environment bundle manifest exceeds size limit")
            if payload_info.file_size > MAX_PAYLOAD_BYTES:
                raise ValueError("environment bundle payload exceeds size limit")
            for info in infos:
                if info.file_size and info.compress_size == 0:
                    raise ValueError("environment bundle compression metadata is invalid")
                if (
                    info.compress_size
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("environment bundle compression ratio exceeds limit")
            manifest = json.loads(archive.read("manifest.json"))
        return manifest

    def read_bundle(
        self, bundle: Path
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        manifest = self.read_bundle_manifest(bundle)
        with zipfile.ZipFile(bundle) as archive:
            payload = archive.read("payload/environment.jsonl")
        if manifest.get("format") != ENVIRONMENT_BUNDLE_FORMAT:
            raise ValueError("unsupported environment bundle format")
        if manifest.get("stream_id") != "environment-v1":
            raise ValueError("environment bundle stream mismatch")
        if int(manifest.get("protocol_version", 0)) != ENVIRONMENT_PROTOCOL_VERSION:
            raise ValueError("unsupported environment protocol")
        if int(manifest.get("payload_bytes", -1)) != len(payload):
            raise ValueError("environment payload size mismatch")
        if manifest.get("payload_sha256") != bytes_sha256(payload):
            raise ValueError("environment payload hash mismatch")
        base_manifest = {
            key: value for key, value in manifest.items() if key != "bundle_id"
        }
        if manifest.get("bundle_id") != (
            f"mwb-{canonical_sha256(base_manifest)[:32]}"
        ):
            raise ValueError("environment bundle ID mismatch")
        artifact_count = manifest.get("artifact_count")
        if type(artifact_count) is not int or not 0 <= artifact_count <= MAX_ARTIFACTS:
            raise ValueError("environment artifact count exceeds limit")
        lines = [line for line in payload.splitlines() if line]
        if len(lines) != artifact_count:
            raise ValueError("environment artifact count mismatch")
        records = [json.loads(line) for line in lines]
        start = int(manifest["from_event_sequence"])
        if any(
            int(item.get("event_sequence", -1)) != start + offset
            for offset, item in enumerate(records)
        ):
            raise ValueError("environment event sequence is not contiguous")
        if int(manifest["base_event_sequence"]) + 1 != start:
            raise ValueError("environment bundle base sequence mismatch")
        for item in records:
            self._validate_payload_record(item)
        return manifest, records

    def replica_state(self, node_id: str) -> Dict[str, Any]:
        peer_root = self._peer_root(node_id)
        path = self.registry._resolve_relative(
            (peer_root / "replica-state.json").relative_to(self.registry.root).as_posix(),
            "environment replica state",
            for_write=True,
        )
        if path.exists():
            return read_json(path)
        return {
                "format_version": 1,
                "stream_id": "environment-v1",
                "origin_node_id": safe_node_id(node_id),
                "last_event_sequence": 0,
                "last_bundle_id": None,
                "last_bundle_sha256": None,
                "last_sync_at": None,
            }

    def _replica_events_path(self, node_id: str) -> Path:
        path = self._peer_root(node_id) / "replica-events.jsonl"
        return self.registry._resolve_relative(
            path.relative_to(self.registry.root).as_posix(),
            "environment replica event ledger",
            for_write=True,
        )

    def _read_replica_records(
        self, root: Path, pattern: str, label: str
    ) -> List[Dict[str, Any]]:
        return [
            read_json(path)
            for path in self._bounded_safe_paths(
                root, pattern, label, maximum=MAX_ARTIFACTS * 4
            )
        ]

    def _bounded_safe_paths(
        self, root: Path, pattern: str, label: str, *, maximum: int
    ) -> List[Path]:
        paths = []
        for candidate in root.glob(pattern):
            paths.append(
                self.registry._resolve_relative(
                    candidate.relative_to(self.registry.root).as_posix(), label
                )
            )
            if len(paths) > maximum:
                raise ValueError(f"{label} count exceeds limit")
        return paths

    def _read_replica_events(self, origin: str) -> List[Dict[str, Any]]:
        path = self._replica_events_path(origin)
        if not path.exists():
            return []
        try:
            if path.stat().st_size > MAX_REPLICA_LEDGER_BYTES:
                raise ValueError("environment replica event ledger exceeds size limit")
        except OSError as error:
            raise ValueError("environment replica event ledger is unreadable") from error
        records = read_jsonl(path)
        if len(records) > MAX_REPLICA_EVENTS:
            raise ValueError("environment replica event ledger count exceeds limit")
        self._require_strict_event_sequences(
            records, "environment replica event ledger"
        )
        return records

    @staticmethod
    def _require_strict_event_sequences(
        records: List[Dict[str, Any]], label: str
    ) -> None:
        previous = 0
        for item in records:
            try:
                sequence = int(item["event_sequence"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{label} has an invalid event sequence") from error
            if sequence <= previous:
                raise ValueError(
                    f"{label} event sequences must be positive and strictly increasing"
                )
            previous = sequence

    @staticmethod
    def _merged_replica_events(
        persisted_records: List[Dict[str, Any]],
        incoming_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        EnvironmentExchangeManager._require_strict_event_sequences(
            persisted_records, "persisted environment replica event ledger"
        )
        EnvironmentExchangeManager._require_strict_event_sequences(
            incoming_records, "incoming environment event ledger"
        )
        persisted = {
            int(item["event_sequence"]): item for item in persisted_records
        }
        for item in incoming_records:
            sequence = int(item["event_sequence"])
            existing = persisted.get(sequence)
            if (
                existing is not None
                and canonical_sha256(existing) != canonical_sha256(item)
            ):
                raise ValueError(
                    "environment replica event ledger conflicts with import"
                )
            persisted[sequence] = item
        if len(persisted) > MAX_REPLICA_EVENTS:
            raise ValueError("environment replica event ledger count exceeds limit")
        return [persisted[key] for key in sorted(persisted)]

    def _legacy_replica_event_matches(
        self, origin: str, item: Dict[str, Any]
    ) -> bool:
        sequence = int(item["event_sequence"])
        peer_root = self._peer_root(origin)
        event_kind = item.get("event_kind")
        if event_kind == "governance-proposal":
            root = self.registry._resolve_relative(
                (peer_root / "governance-proposals").relative_to(self.registry.root).as_posix(),
                "legacy governance proposal replica root",
            )
            return any(
                int(record.get("event_sequence", -1)) == sequence
                and record.get("proposal") == item["payload"]
                for record in self._read_replica_records(
                    root, "*.json", "legacy governance proposal replica"
                )
            )
        if event_kind == "product-evolution":
            root = self.registry._resolve_relative(
                (peer_root / "product-evolution").relative_to(self.registry.root).as_posix(),
                "legacy product evolution replica root",
            )
            return any(
                int(record.get("event_sequence", -1)) == sequence
                and record.get("product_evolution") == item["payload"]
                for record in self._read_replica_records(
                    root, "*.json", "legacy product evolution replica"
                )
            )
        if event_kind == "project-registration":
            root = self.registry._resolve_relative(
                (peer_root / "projects").relative_to(self.registry.root).as_posix(),
                "legacy project replica root",
            )
            return any(
                int(record.get("event_sequence", -1)) == sequence
                and record.get("project") == item["payload"]["project"]
                for record in self._read_replica_records(
                    root, "*.json", "legacy project replica"
                )
            )
        if event_kind == "personal-environment-profile":
            root = self.registry._resolve_relative(
                (peer_root / "profiles").relative_to(self.registry.root).as_posix(),
                "legacy Environment profile replica root",
            )
            return any(
                int(record.get("event_sequence", -1)) == sequence
                and record.get("generation") == item["payload"]
                for record in self._read_replica_records(
                    root, "*.json", "legacy Environment profile replica"
                )
            )
        staging_root = self.registry._resolve_relative(
            f"staging/incoming/{origin}",
            "legacy incoming Environment staging root",
            for_write=True,
        )
        records = self._read_replica_records(
            staging_root,
            f"{sequence:020d}-*.json",
            "legacy incoming Environment staging record",
        )
        if len(records) != 1:
            return False
        stored = records[0]
        payload = item["payload"]
        return all(
            (
                stored.get("artifact") == payload.get("artifact"),
                stored.get("revision") == payload.get("revision"),
                stored.get("content_base64") == payload.get("content_base64"),
                stored.get("package_attachment")
                == payload.get("package_attachment"),
            )
        )

    def _verify_overlap_records(
        self,
        origin: str,
        records: List[Dict[str, Any]],
        current_sequence: int,
    ) -> None:
        persisted = {
            int(item["event_sequence"]): item
            for item in self._read_replica_events(origin)
        }
        for item in records:
            sequence = int(item["event_sequence"])
            if sequence > current_sequence:
                break
            existing = persisted.get(sequence)
            if existing is not None:
                matches = canonical_sha256(existing) == canonical_sha256(item)
            else:
                matches = self._legacy_replica_event_matches(origin, item)
            if not matches:
                raise ValueError(
                    "environment overlap conflicts with persisted replica event"
                )

    @staticmethod
    def _receipt_outputs(outputs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        return sorted(
            [
                {
                    "relative_path": str(output["relative_path"]),
                    "sha256": str(output["sha256"]),
                }
                for output in outputs
            ],
            key=lambda output: output["relative_path"],
        )

    def _validate_committed_receipt(
        self,
        receipt: Any,
        *,
        origin: str,
        expected_bundle_id: str,
        expected_bundle_sha256: str,
        expected_manifest: Optional[Dict[str, Any]] = None,
        verify_state_ledger: bool = True,
    ) -> Dict[str, Any]:
        required = {
            "format_version", "stream_id", "bundle_sha256", "manifest",
            "overlap_recovery", "received_at", "state_sha256",
            "ledger_sha256", "ledger_count", "outputs", "outputs_sha256",
        }
        if (
            not isinstance(receipt, dict)
            or set(receipt) != required
            or receipt.get("format_version") != 1
            or receipt.get("stream_id") != "environment-v1"
            or receipt.get("bundle_sha256") != expected_bundle_sha256
            or not isinstance(receipt.get("manifest"), dict)
            or receipt["manifest"].get("bundle_id") != expected_bundle_id
            or receipt["manifest"].get("origin_node_id") != origin
            or type(receipt.get("overlap_recovery")) is not bool
            or type(receipt.get("ledger_count")) is not int
            or not isinstance(receipt.get("outputs"), list)
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("state_sha256"))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("ledger_sha256"))) is None
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("outputs_sha256"))) is None
            or (expected_manifest is not None and receipt["manifest"] != expected_manifest)
        ):
            raise ValueError("environment import receipt is invalid")
        if any(
            not isinstance(output, dict)
            or set(output) != {"relative_path", "sha256"}
            for output in receipt["outputs"]
        ):
            raise ValueError("environment import receipt output manifest is invalid")
        outputs = self._receipt_outputs(receipt["outputs"])
        if outputs != receipt["outputs"] or canonical_sha256(outputs) != receipt["outputs_sha256"]:
            raise ValueError("environment import receipt output manifest is invalid")
        allowed_prefixes = (
            f"staging/incoming/{origin}/",
            f"replicas/peers/{origin}/governance-proposals/",
            f"replicas/peers/{origin}/product-evolution/",
            f"replicas/peers/{origin}/projects/",
            f"replicas/peers/{origin}/profiles/",
        )
        for output in outputs:
            if (
                not output["relative_path"].startswith(allowed_prefixes)
                or re.fullmatch(r"[0-9a-f]{64}", output["sha256"]) is None
            ):
                raise ValueError("environment import receipt output is invalid")
            try:
                target = self.registry._resolve_relative(
                    output["relative_path"], "environment receipt output"
                )
            except ValueError as error:
                raise ValueError(
                    "environment import receipt output is missing or changed"
                ) from error
            if not target.is_file() or bytes_sha256(target.read_bytes()) != output["sha256"]:
                raise ValueError("environment import receipt output is missing or changed")
        if verify_state_ledger:
            state = self.replica_state(origin)
            ledger = self._read_replica_events(origin)
            if (
                canonical_sha256(state) != receipt["state_sha256"]
                or len(ledger) != receipt["ledger_count"]
                or canonical_sha256(ledger) != receipt["ledger_sha256"]
            ):
                raise ValueError("environment import receipt state or ledger is inconsistent")
        return receipt

    def _persist_replica_events(
        self, origin: str, records: List[Dict[str, Any]]
    ) -> None:
        raw_path = self._replica_events_path(origin)
        path = self.registry._resolve_relative(
            raw_path.relative_to(self.registry.root).as_posix(),
            "environment replica event ledger",
            for_write=True,
        )
        merged = self._merged_replica_events(
            self._read_replica_events(origin), records
        )
        atomic_write_jsonl(path, merged)

    def _import_authenticated_delta(
        self,
        bundle: Path,
        expected_node_id: Optional[str] = None,
        *,
        authenticated_open_result: Any,
    ) -> Dict[str, Any]:
        self.init_layout()
        local_node = self.node()
        manifest, records = self.read_bundle(bundle)
        origin = safe_node_id(str(manifest["origin_node_id"]))
        if origin == local_node["node_id"]:
            raise ValueError("refusing own environment bundle")
        if expected_node_id and origin != safe_node_id(expected_node_id):
            raise ValueError("environment bundle origin mismatch")
        target = manifest.get("target_node_id")
        if target and safe_node_id(str(target)) != local_node["node_id"]:
            raise ValueError("environment bundle targets another node")
        peers = {item["node_id"]: item for item in self.peers()}
        if origin not in peers or not peers[origin].get("trusted"):
            raise ValueError("environment bundle peer is not trusted")
        bundle_hash = bytes_sha256(bundle.read_bytes())
        from memory_cloud_transport import AuthenticatedOpenResult

        if not isinstance(authenticated_open_result, AuthenticatedOpenResult):
            raise TypeError("authenticated environment import requires crypto-open evidence")
        verified_origin_node_id, verified_target_node_id, verified_bundle_sha256 = (
            authenticated_open_result.consume_environment_binding()
        )
        if (
            safe_node_id(verified_origin_node_id) != origin
            or safe_node_id(verified_target_node_id) != local_node["node_id"]
            or verified_bundle_sha256 != bundle_hash
        ):
            raise ValueError("authenticated environment transport binding mismatch")
        peer_root = self.registry._resolve_relative(
            f"replicas/peers/{origin}",
            "environment peer replica root",
            for_write=True,
        )
        receipt_path = (
            peer_root / "receipts" / f"{manifest['bundle_id']}.json"
        )
        receipt_path = self.registry._resolve_relative(
            receipt_path.relative_to(self.registry.root).as_posix(),
            "environment import receipt",
            for_write=True,
        )
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            self._validate_committed_receipt(
                receipt,
                origin=origin,
                expected_bundle_id=str(manifest["bundle_id"]),
                expected_bundle_sha256=bundle_hash,
                expected_manifest=manifest,
            )
            self._verify_overlap_records(
                origin, records, int(manifest["to_event_sequence"])
            )
            return {
                "status": "no-change",
                "bundle_id": manifest["bundle_id"],
                "origin_node_id": origin,
                "to_event_sequence": manifest["to_event_sequence"],
            }
        transaction_root = (
            peer_root / "transactions" / str(manifest["bundle_id"])
        )
        marker_path = transaction_root / "transaction.json"
        marker_path = self.registry._resolve_relative(
            marker_path.relative_to(self.registry.root).as_posix(),
            "environment import transaction marker",
            for_write=True,
        )
        if marker_path.exists():
            self._recover_import_transaction(
                marker_path=marker_path,
                receipt_path=receipt_path,
                expected_bundle_id=str(manifest["bundle_id"]),
            )
            if receipt_path.exists():
                return {
                    "status": "no-change",
                    "bundle_id": manifest["bundle_id"],
                    "origin_node_id": origin,
                    "to_event_sequence": manifest["to_event_sequence"],
                }
        state = self.replica_state(origin)
        current_sequence = int(state["last_event_sequence"])
        expected_sequence = current_sequence + 1
        from_sequence = int(manifest["from_event_sequence"])
        to_sequence = int(manifest["to_event_sequence"])
        if from_sequence > expected_sequence:
            raise ValueError("environment replica sequence gap")
        overlap_recovery = from_sequence < expected_sequence <= to_sequence
        if to_sequence < expected_sequence:
            raise ValueError("environment bundle is older than replica cursor")
        if overlap_recovery:
            self._verify_overlap_records(origin, records, current_sequence)
        elif current_sequence:
            if manifest["previous_bundle_sha256"] != state["last_bundle_sha256"]:
                raise ValueError("environment predecessor bundle mismatch")
        elif manifest["previous_bundle_sha256"] is not None:
            raise ValueError("initial environment import names a predecessor")
        prior_outputs: List[Dict[str, str]] = []
        if current_sequence:
            prior_receipt_path = self.registry._resolve_relative(
                f"replicas/peers/{origin}/receipts/{state['last_bundle_id']}.json",
                "previous environment import receipt",
            )
            prior_receipt = self._validate_committed_receipt(
                read_json(prior_receipt_path),
                origin=origin,
                expected_bundle_id=str(state["last_bundle_id"]),
                expected_bundle_sha256=str(state["last_bundle_sha256"]),
                verify_state_ledger=not overlap_recovery,
            )
            prior_outputs = list(prior_receipt["outputs"])
        new_records = [
            item
            for item in records
            if int(item["event_sequence"]) >= expected_sequence
        ]
        incoming = self.registry._resolve_relative(
            f"staging/incoming/{origin}",
            "incoming Environment staging root",
            for_write=True,
        )
        proposal_replica_root = self.registry._resolve_relative(
            f"replicas/peers/{origin}/governance-proposals",
            "peer governance proposal replica root",
            for_write=True,
        )
        evolution_replica_root = self.registry._resolve_relative(
            f"replicas/peers/{origin}/product-evolution",
            "peer product evolution replica root",
            for_write=True,
        )
        project_replica_root = self.registry._resolve_relative(
            f"replicas/peers/{origin}/projects",
            "peer project replica root",
            for_write=True,
        )
        profile_replica_root = self.registry._resolve_relative(
            (peer_root / "profiles").relative_to(self.registry.root).as_posix(),
            "peer Environment profile replica root",
            for_write=True,
        )
        staged_artifacts = 0
        staged_projects = 0
        staged_governance_proposals = 0
        staged_product_evolution_records = 0
        staged_profiles = 0
        prepared: List[Dict[str, Any]] = []

        incoming_profiles = [
            self.profiles.validate_generation(item["payload"])
            for item in new_records
            if item.get("event_kind") == "personal-environment-profile"
        ]
        incoming_generation_ids = [item["generation_id"] for item in incoming_profiles]
        if len(set(incoming_generation_ids)) != len(incoming_generation_ids):
            raise ValueError("duplicate incoming Environment profile generation")
        if incoming_profiles:
            existing_profiles = []
            if profile_replica_root.is_dir() and any(profile_replica_root.iterdir()):
                existing, _ = self.profiles.load_peer_profile_records(
                    origin, profile_replica_root
                )
                existing_profiles.extend(existing.values())
            by_generation = {}
            for generation in [*existing_profiles, *incoming_profiles]:
                existing = by_generation.get(generation["generation_id"])
                if existing is not None and existing != generation:
                    raise ValueError("peer Environment profile generation conflicts with existing content")
                by_generation[generation["generation_id"]] = generation
            self.profiles.validate_generation_chain(list(by_generation.values()))

        def prepare_json(path: Path, value: Dict[str, Any]) -> None:
            relative = path.relative_to(self.registry.root).as_posix()
            path = self.registry._resolve_relative(
                relative, "environment import target", for_write=True
            )
            payload = canonical_bytes(value) + b"\n"
            if path.exists():
                if is_link_like(path) or not path.is_file():
                    raise ValueError("environment import target path is unsafe")
                if path.read_bytes() != payload:
                    raise ValueError(
                        "environment import target conflicts with existing content"
                    )
                return
            prepared.append(
                {
                    "relative_path": relative,
                    "sha256": bytes_sha256(payload),
                    "content_base64": base64.b64encode(payload).decode("ascii"),
                }
            )

        for item in new_records:
            if item.get("event_kind") == "personal-environment-profile":
                generation = self.profiles.validate_generation(item["payload"])
                profile = generation["profile"]
                profile_record = {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": item["event_sequence"],
                    "generation": generation,
                    "received_bundle_id": manifest["bundle_id"],
                    "automatic_activation": False,
                }
                generation_sha256 = generation["generation_id"].split(":", 1)[1]
                profile_path = profile_replica_root / f"{generation_sha256}.json"
                prepare_json(profile_path, profile_record)
                staged_profiles += 1
                continue
            if item.get("event_kind") == "governance-proposal":
                envelope = self.governance.validate_envelope(
                    item["payload"], expected_origin=origin
                )
                proposal_id = envelope["proposal_id"]
                digest = envelope["content_sha256"]
                conflicts = self._bounded_safe_paths(
                    proposal_replica_root,
                    f"{proposal_id}-*.json",
                    "peer governance proposal conflict",
                    maximum=1,
                )
                proposal_record = {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": item["event_sequence"],
                    "proposal": envelope,
                    "received_bundle_id": manifest["bundle_id"],
                    "automatic_acceptance": False,
                }
                if conflicts:
                    if (
                        len(conflicts) != 1
                        or read_json(conflicts[0]) != proposal_record
                    ):
                        raise ValueError(
                            "peer governance proposal ID conflicts with existing content"
                        )
                else:
                    prepare_json(
                        proposal_replica_root
                        / f"{proposal_id}-{digest}.json",
                        proposal_record,
                    )
                staged_governance_proposals += 1
                continue
            if item.get("event_kind") == "product-evolution":
                envelope = self.evolution.validate_envelope(
                    item["payload"], expected_origin=origin
                )
                record_id = envelope["record_id"]
                digest = envelope["content_sha256"]
                conflicts = self._bounded_safe_paths(
                    evolution_replica_root,
                    f"{record_id}-*.json",
                    "peer product evolution conflict",
                    maximum=1,
                )
                replica_record = {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": item["event_sequence"],
                    "product_evolution": envelope,
                    "received_bundle_id": manifest["bundle_id"],
                    "automatic_remediation": False,
                    "automatic_governance_acceptance": False,
                }
                if conflicts:
                    if len(conflicts) != 1 or read_json(conflicts[0]) != replica_record:
                        raise ValueError(
                            "peer product evolution ID conflicts with existing content"
                        )
                else:
                    prepare_json(
                        evolution_replica_root / f"{record_id}-{digest}.json",
                        replica_record,
                    )
                staged_product_evolution_records += 1
                continue
            if item.get("event_kind") == "project-registration":
                project = self.registry._validate_project(item["payload"]["project"])
                project_record = {
                    "schema_version": 1,
                    "stream_id": "environment-v1",
                    "origin_node_id": origin,
                    "event_sequence": item["event_sequence"],
                    "project": project,
                    "received_bundle_id": manifest["bundle_id"],
                    "automatic_registration": False,
                }
                project_path = project_replica_root / (
                    f"{project['project_id']}-{canonical_sha256(project)}.json"
                )
                conflicts = self._bounded_safe_paths(
                    project_replica_root,
                    f"{project['project_id']}-*.json",
                    "peer project conflict",
                    maximum=1,
                )
                if conflicts:
                    if len(conflicts) != 1 or read_json(conflicts[0]) != project_record:
                        raise ValueError(
                            "peer project registration conflicts with existing content"
                        )
                else:
                    prepare_json(project_path, project_record)
                staged_projects += 1
                continue
            payload = item["payload"]
            content = base64.b64decode(payload["content_base64"], validate=True)
            revision = payload["revision"]
            if bytes_sha256(content) != revision["content_sha256"]:
                raise ValueError("staged Environment object hash mismatch")
            stage_record = {
                "schema_version": 1,
                "stream_id": "environment-v1",
                "origin_node_id": origin,
                "event_sequence": item["event_sequence"],
                "artifact": payload["artifact"],
                "revision": revision,
                "content_base64": payload["content_base64"],
                "package_attachment": payload["package_attachment"],
                "received_bundle_id": manifest["bundle_id"],
            }
            stage_path = incoming / (
                f"{int(item['event_sequence']):020d}-"
                f"{canonical_sha256(item['artifact_id'])[:16]}.json"
            )
            stage_path = self.registry._resolve_relative(
                stage_path.relative_to(self.registry.root).as_posix(),
                "staged Environment event",
                for_write=True,
            )
            if stage_path.exists() and read_json(stage_path) != stage_record:
                raise ValueError("staged Environment event conflicts with existing event")
            prepare_json(stage_path, stage_record)
            staged_artifacts += 1
        peer_root.mkdir(parents=True, exist_ok=True)
        new_state = {
            "format_version": 1,
            "stream_id": "environment-v1",
            "origin_node_id": origin,
            "last_event_sequence": int(manifest["to_event_sequence"]),
            "last_bundle_id": manifest["bundle_id"],
            "last_bundle_sha256": bundle_hash,
            "last_sync_at": now_iso(),
        }
        previous_ledger = self._read_replica_events(origin)
        new_ledger = self._merged_replica_events(previous_ledger, records)
        cumulative_by_path = {
            output["relative_path"]: output["sha256"] for output in prior_outputs
        }
        for output in self._receipt_outputs(prepared):
            existing_sha256 = cumulative_by_path.get(output["relative_path"])
            if existing_sha256 is not None and existing_sha256 != output["sha256"]:
                raise ValueError("environment cumulative output path conflicts")
            cumulative_by_path[output["relative_path"]] = output["sha256"]
        cumulative_outputs = [
            {"relative_path": path, "sha256": cumulative_by_path[path]}
            for path in sorted(cumulative_by_path)
        ]
        marker = {
            "format_version": 1,
            "stream_id": "environment-v1",
            "bundle_id": manifest["bundle_id"],
            "bundle_sha256": bundle_hash,
            "status": "prepared",
            "previous_state": state,
            "new_state": new_state,
            "previous_ledger_sha256": canonical_sha256(previous_ledger),
            "previous_ledger_count": len(previous_ledger),
            "new_ledger_sha256": canonical_sha256(new_ledger),
            "new_ledger_count": len(new_ledger),
            "outputs": prepared,
            "cumulative_outputs": cumulative_outputs,
            "created_at": now_iso(),
        }
        marker = _sealed_transaction_marker(marker)
        atomic_write_json(marker_path, marker)
        try:
            for output in prepared:
                target = self.registry._resolve_relative(
                    str(output["relative_path"]),
                    "environment transaction output",
                    for_write=True,
                )
                payload = base64.b64decode(
                    output["content_base64"], validate=True
                )
                if bytes_sha256(payload) != output["sha256"]:
                    raise ValueError("environment transaction output hash mismatch")
                atomic_write_bytes(target, payload)
            marker["status"] = "outputs-written"
            marker = _sealed_transaction_marker(marker)
            atomic_write_json(marker_path, marker)
            state_path = self.registry._resolve_relative(
                (peer_root / "replica-state.json").relative_to(self.registry.root).as_posix(),
                "environment replica state",
                for_write=True,
            )
            atomic_write_json(state_path, new_state)
            marker["status"] = "state-written"
            marker = _sealed_transaction_marker(marker)
            atomic_write_json(marker_path, marker)
            self._persist_replica_events(origin, records)
            marker["status"] = "ledger-written"
            marker = _sealed_transaction_marker(marker)
            atomic_write_json(marker_path, marker)
            receipt_outputs = cumulative_outputs
            atomic_write_json(
                receipt_path,
                {
                    "format_version": 1,
                    "stream_id": "environment-v1",
                    "bundle_sha256": bundle_hash,
                    "manifest": manifest,
                    "overlap_recovery": overlap_recovery,
                    "received_at": new_state["last_sync_at"],
                    "state_sha256": canonical_sha256(new_state),
                    "ledger_sha256": canonical_sha256(new_ledger),
                    "ledger_count": len(new_ledger),
                    "outputs": receipt_outputs,
                    "outputs_sha256": canonical_sha256(receipt_outputs),
                },
            )
            marker["status"] = "committed"
            marker["outputs"] = [
                {
                    "relative_path": output["relative_path"],
                    "sha256": output["sha256"],
                }
                for output in prepared
            ]
            marker = _sealed_transaction_marker(marker)
            atomic_write_json(marker_path, marker)
        except Exception:
            committed = self._recover_import_transaction(
                marker_path=marker_path,
                receipt_path=receipt_path,
                expected_bundle_id=str(manifest["bundle_id"]),
            )
            if not committed:
                raise
        return {
            "status": "imported",
            "bundle_id": manifest["bundle_id"],
            "origin_node_id": origin,
            "to_event_sequence": manifest["to_event_sequence"],
            "overlap_recovery": overlap_recovery,
            "staged_artifacts": staged_artifacts,
            "staged_projects": staged_projects,
            "staged_governance_proposals": staged_governance_proposals,
            "staged_product_evolution_records": staged_product_evolution_records,
            "staged_profiles": staged_profiles,
        }

    def _recover_import_transaction(
        self,
        *,
        marker_path: Path,
        receipt_path: Path,
        expected_bundle_id: str,
    ) -> bool:
        marker = read_json(marker_path)
        if not isinstance(marker, dict):
            raise ValueError("environment import transaction marker is invalid")
        marker_hash = marker.get("marker_sha256")
        unsealed = dict(marker)
        unsealed.pop("marker_sha256", None)
        if (
            set(marker) != {
                "format_version", "stream_id", "bundle_id", "bundle_sha256",
                "status", "previous_state", "new_state", "outputs",
                "cumulative_outputs",
                "previous_ledger_sha256", "previous_ledger_count",
                "new_ledger_sha256", "new_ledger_count", "created_at",
                "marker_sha256",
            }
            or marker.get("format_version") != 1
            or marker.get("stream_id") != "environment-v1"
            or marker.get("bundle_id") != expected_bundle_id
            or not isinstance(marker.get("outputs"), list)
            or not isinstance(marker.get("cumulative_outputs"), list)
            or marker.get("status") not in {
                "prepared", "outputs-written", "state-written",
                "ledger-written", "committed",
            }
            or marker_hash != canonical_sha256(unsealed)
            or re.fullmatch(
                r"[0-9a-f]{64}", str(marker.get("previous_ledger_sha256"))
            ) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", str(marker.get("new_ledger_sha256"))
            ) is None
            or type(marker.get("previous_ledger_count")) is not int
            or type(marker.get("new_ledger_count")) is not int
            or not 0 <= marker["previous_ledger_count"] <= marker["new_ledger_count"] <= MAX_REPLICA_EVENTS
        ):
            raise ValueError("environment import transaction marker is invalid")
        previous_state = marker.get("previous_state")
        new_state = marker.get("new_state")
        state_fields = {
            "format_version", "stream_id", "origin_node_id",
            "last_event_sequence", "last_bundle_id", "last_bundle_sha256",
            "last_sync_at",
        }
        if (
            not isinstance(previous_state, dict)
            or not isinstance(new_state, dict)
            or set(previous_state) != state_fields
            or set(new_state) != state_fields
            or previous_state.get("format_version") != 1
            or new_state.get("format_version") != 1
            or previous_state.get("stream_id") != "environment-v1"
            or new_state.get("stream_id") != "environment-v1"
            or previous_state.get("origin_node_id") != new_state.get("origin_node_id")
            or new_state.get("last_bundle_id") != expected_bundle_id
            or new_state.get("last_bundle_sha256") != marker.get("bundle_sha256")
            or re.fullmatch(r"[0-9a-f]{64}", str(marker.get("bundle_sha256"))) is None
            or type(previous_state.get("last_event_sequence")) is not int
            or type(new_state.get("last_event_sequence")) is not int
            or new_state["last_event_sequence"] < previous_state["last_event_sequence"]
        ):
            raise ValueError("environment import transaction state is invalid")
        origin = safe_node_id(str(previous_state["origin_node_id"]))
        allowed_prefixes = (
            f"staging/incoming/{origin}/",
            f"replicas/peers/{origin}/governance-proposals/",
            f"replicas/peers/{origin}/product-evolution/",
            f"replicas/peers/{origin}/projects/",
            f"replicas/peers/{origin}/profiles/",
        )
        for output in marker["outputs"]:
            if (
                not isinstance(output, dict)
                or set(output) not in (
                    {"relative_path", "sha256", "content_base64"},
                    {"relative_path", "sha256"},
                )
                or not isinstance(output.get("relative_path"), str)
                or not output["relative_path"].startswith(allowed_prefixes)
                or re.fullmatch(r"[0-9a-f]{64}", str(output.get("sha256"))) is None
            ):
                raise ValueError("environment import transaction output is invalid")
        if (
            any(
                not isinstance(output, dict)
                or set(output) != {"relative_path", "sha256"}
                for output in marker["cumulative_outputs"]
            )
            or marker["cumulative_outputs"]
            != self._receipt_outputs(marker["cumulative_outputs"])
        ):
            raise ValueError("environment import cumulative output manifest is invalid")
        current_state_path = self.registry._resolve_relative(
            f"replicas/peers/{origin}/replica-state.json",
            "environment recovery state",
            for_write=True,
        )
        current_state = (
            read_json(current_state_path) if current_state_path.exists() else None
        )
        if current_state not in (previous_state, new_state):
            raise ValueError("environment recovery state drifted from transaction")
        current_ledger = self._read_replica_events(origin)
        current_ledger_sha256 = canonical_sha256(current_ledger)
        if current_ledger_sha256 not in {
            marker["previous_ledger_sha256"], marker["new_ledger_sha256"]
        }:
            raise ValueError("environment recovery event ledger drifted from transaction")
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            self._validate_committed_receipt(
                receipt,
                origin=origin,
                expected_bundle_id=expected_bundle_id,
                expected_bundle_sha256=str(marker["bundle_sha256"]),
            )
            if (
                current_state != new_state
                or current_ledger_sha256 != marker["new_ledger_sha256"]
                or receipt["state_sha256"] != canonical_sha256(new_state)
                or receipt["ledger_sha256"] != marker["new_ledger_sha256"]
                or receipt["ledger_count"] != marker["new_ledger_count"]
                or receipt["outputs"] != marker["cumulative_outputs"]
            ):
                raise ValueError("environment committed receipt is inconsistent")
            marker_path.unlink(missing_ok=True)
            try:
                marker_path.parent.rmdir()
            except OSError:
                pass
            return True
        for output in marker["outputs"]:
            target = self.registry._resolve_relative(
                str(output["relative_path"]),
                "environment recovery output",
                for_write=True,
            )
            if not target.exists():
                continue
            if (
                is_link_like(target)
                or not target.is_file()
                or bytes_sha256(target.read_bytes()) != output["sha256"]
            ):
                raise ValueError(
                    "environment import transaction output changed during recovery"
                )
            target.unlink()
        if current_state == new_state:
            atomic_write_json(current_state_path, previous_state)
        event_path = self.registry._resolve_relative(
            self._replica_events_path(origin).relative_to(self.registry.root).as_posix(),
            "environment recovery event ledger",
            for_write=True,
        )
        previous_sequence = int(previous_state.get("last_event_sequence", 0))
        if current_ledger_sha256 == marker["new_ledger_sha256"]:
            retained = [
                item
                for item in current_ledger
                if int(item.get("event_sequence", 0)) <= previous_sequence
            ]
            if (
                len(retained) != marker["previous_ledger_count"]
                or canonical_sha256(retained) != marker["previous_ledger_sha256"]
            ):
                raise ValueError("environment recovery ledger boundary mismatch")
            atomic_write_jsonl(event_path, retained)
        marker_path.unlink(missing_ok=True)
        try:
            marker_path.parent.rmdir()
        except OSError:
            pass
        return False

    def _peer_root(self, node_id: str) -> Path:
        return self.registry._resolve_relative(
            f"replicas/peers/{safe_node_id(node_id)}",
            "environment peer replica root",
            for_write=True,
        )

    def _validate_payload_record(self, item: Dict[str, Any]) -> None:
        legacy_required = {
            "event_sequence",
            "source_event_id",
            "artifact_id",
            "revision_id",
            "payload_sha256",
            "payload",
        }
        if not isinstance(item, dict):
            raise ValueError("environment payload event fields are invalid")
        event_kind = item.get("event_kind")
        if event_kind == "governance-proposal":
            required = {
                "event_sequence",
                "source_event_id",
                "event_kind",
                "proposal_id",
                "payload_sha256",
                "payload",
            }
            if set(item) != required:
                raise ValueError("governance proposal event fields are invalid")
            payload = self.governance.validate_envelope(item["payload"])
            if item["proposal_id"] != payload["proposal_id"]:
                raise ValueError("governance proposal event identity mismatch")
            if item["payload_sha256"] != canonical_sha256(payload):
                raise ValueError("governance proposal event hash mismatch")
            return
        if event_kind == "product-evolution":
            required = {
                "event_sequence", "source_event_id", "event_kind", "record_id",
                "payload_sha256", "payload",
            }
            if set(item) != required:
                raise ValueError("product evolution event fields are invalid")
            payload = self.evolution.validate_envelope(item["payload"])
            if item["record_id"] != payload["record_id"]:
                raise ValueError("product evolution event identity mismatch")
            if item["payload_sha256"] != canonical_sha256(payload):
                raise ValueError("product evolution event hash mismatch")
            return
        if event_kind == "personal-environment-profile":
            required = {
                "event_sequence", "source_event_id", "event_kind", "profile_id",
                "payload_sha256", "payload",
            }
            if set(item) != required:
                raise ValueError("personal Environment profile event fields are invalid")
            payload = self.profiles.validate_generation(item["payload"])
            if item["profile_id"] != payload["profile"]["profile_id"]:
                raise ValueError("personal Environment profile event identity mismatch")
            if item["payload_sha256"] != canonical_sha256(payload):
                raise ValueError("personal Environment profile event hash mismatch")
            return
        if event_kind == "project-registration":
            required = {
                "event_sequence",
                "source_event_id",
                "event_kind",
                "project_id",
                "payload_sha256",
                "payload",
            }
            if set(item) != required:
                raise ValueError("project registration event fields are invalid")
            payload = item["payload"]
            if not isinstance(payload, dict) or set(payload) != {"project"}:
                raise ValueError("project registration payload is invalid")
            project = self.registry._validate_project(payload["project"])
            if item["project_id"] != project["project_id"]:
                raise ValueError("project registration identity mismatch")
            if item["payload_sha256"] != canonical_sha256(payload):
                raise ValueError("project registration event hash mismatch")
            return
        if event_kind == "artifact-revision":
            required = legacy_required | {"event_kind"}
        elif event_kind is None:
            required = legacy_required
        else:
            raise ValueError("environment payload event kind is unsupported")
        if set(item) != required:
            raise ValueError("environment payload event fields are invalid")
        payload = item["payload"]
        if item["payload_sha256"] != canonical_sha256(payload):
            raise ValueError("environment payload event hash mismatch")
        if not isinstance(payload, dict) or set(payload) != {
            "artifact",
            "revision",
            "content_base64",
            "package_attachment",
        }:
            raise ValueError("environment payload is invalid")
        artifact = self.registry._validate_artifact(payload["artifact"])
        revision = self.registry._validate_revision(payload["revision"])
        if artifact["artifact_id"] != item["artifact_id"]:
            raise ValueError("environment artifact identity mismatch")
        if revision["revision_id"] != item["revision_id"]:
            raise ValueError("environment revision identity mismatch")
        if revision["artifact_id"] != artifact["artifact_id"]:
            raise ValueError("environment revision artifact mismatch")
        try:
            content = base64.b64decode(payload["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("environment object base64 is invalid") from error
        if bytes_sha256(content) != revision["content_sha256"]:
            raise ValueError("environment object content hash mismatch")
        attachment = payload["package_attachment"]
        if artifact["object_class"].endswith("-skill"):
            self._validate_skill_attachment(attachment, revision, content)
        elif attachment is not None:
            raise ValueError("rule payload cannot carry a Skill package attachment")

    def _skill_package_attachment(
        self,
        artifact: Dict[str, Any],
        revision: Dict[str, Any],
        contract: bytes,
    ) -> Optional[Dict[str, str]]:
        if not artifact["object_class"].endswith("-skill"):
            return None
        try:
            reference_path = self.registry._resolve_relative(
                "packages/by-revision/"
                f"{revision['revision_id'].split(':', 1)[1]}.json",
                "Skill package reference",
            )
        except ValueError as error:
            raise ValueError(
                "registered Skill revision has no verified package attachment"
            ) from error
        if not reference_path.is_file():
            raise ValueError(
                "registered Skill revision has no verified package attachment"
            )
        reference = read_json(reference_path)
        required = {
            "schema_version",
            "artifact_id",
            "revision_id",
            "package_sha256",
            "package_path",
            "package_contract_sha256",
            "verified_at",
        }
        if not isinstance(reference, dict) or set(reference) != required:
            raise ValueError("Skill package reference is invalid")
        if (
            reference["artifact_id"] != artifact["artifact_id"]
            or reference["revision_id"] != revision["revision_id"]
        ):
            raise ValueError("Skill package reference identity mismatch")
        if reference["package_contract_sha256"] != bytes_sha256(contract):
            raise ValueError("Skill package reference contract hash mismatch")
        expected_package_path = (
            f"packages/sha256/{reference['package_sha256'][:2]}/"
            f"{reference['package_sha256'][2:]}"
        )
        if reference["package_path"] != expected_package_path:
            raise ValueError("Skill package reference path is not content-addressed")
        package_path = self.registry._resolve_relative(
            reference["package_path"], "package_path"
        )
        verified_package = EnvironmentSkillInstaller.verify_package_archive(
            package_path
        )
        if verified_package["package_sha256"] != reference["package_sha256"]:
            raise ValueError("Skill package attachment hash mismatch")
        package = package_path.read_bytes()
        attachment = {
            "package_sha256": reference["package_sha256"],
            "content_base64": base64.b64encode(package).decode("ascii"),
        }
        self._validate_skill_attachment(attachment, revision, contract)
        return attachment

    @staticmethod
    def _validate_skill_attachment(
        attachment: Any,
        revision: Dict[str, Any],
        contract: bytes,
    ) -> None:
        if not isinstance(attachment, dict) or set(attachment) != {
            "package_sha256",
            "content_base64",
        }:
            raise ValueError("Skill package attachment is invalid")
        try:
            package = base64.b64decode(attachment["content_base64"], validate=True)
        except Exception as error:
            raise ValueError("Skill package attachment base64 is invalid") from error
        if not isinstance(attachment["package_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", attachment["package_sha256"]
        ):
            raise ValueError("Skill package attachment hash is invalid")
        if bytes_sha256(package) != attachment["package_sha256"]:
            raise ValueError("Skill package attachment hash mismatch")
        if len(package) > MAX_SKILL_PACKAGE_BYTES:
            raise ValueError("Skill package attachment exceeds size limit")
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-skill-verify-") as temp:
            package_path = Path(temp) / "package.zip"
            package_path.write_bytes(package)
            verified_package = EnvironmentSkillInstaller.verify_package_archive(
                package_path
            )
        manifest = verified_package["manifest"]
        if manifest.get("source_revision") != revision["revision_id"]:
            raise ValueError("Skill package attachment revision mismatch")
        if skill_package_contract_bytes(manifest) != contract:
            raise ValueError("Skill package attachment contract mismatch")
