#!/usr/bin/env python3
"""Canonical application assembly for independent cloud exchange streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple

from memory_cloud_transport import CloudFolderTransport
from memory_environment_exchange import EnvironmentExchangeManager
from memory_federation import FederationManager
from memory_project_attachments import ProjectAttachmentExchangeManager
from memory_project_evidence import ProjectEvidenceExchangeManager


ManagerFactory = Callable[[Any], Any]
TransportFactory = Callable[..., CloudFolderTransport]


@dataclass(frozen=True)
class CloudStreamDefinition:
    stream_id: str
    result_key: str
    manager_factory: ManagerFactory
    transport_namespace: Optional[str]
    bootstrap_readiness: str

    def __post_init__(self) -> None:
        if self.bootstrap_readiness not in {
            "none",
            "configured-status",
            "exchange-root",
        }:
            raise ValueError("invalid cloud bootstrap readiness policy")
        if (self.stream_id == "archive-v1") != (self.transport_namespace is None):
            raise ValueError("only archive-v1 may use the unnamespaced transport")


DEFAULT_STREAM_DEFINITIONS: Tuple[CloudStreamDefinition, ...] = (
    CloudStreamDefinition(
        stream_id="archive-v1",
        result_key="archive",
        manager_factory=FederationManager,
        transport_namespace=None,
        bootstrap_readiness="none",
    ),
    CloudStreamDefinition(
        stream_id="environment-v1",
        result_key="environment",
        manager_factory=EnvironmentExchangeManager,
        transport_namespace="environment-v1",
        bootstrap_readiness="configured-status",
    ),
    CloudStreamDefinition(
        stream_id="project-evidence-v1",
        result_key="project_evidence",
        manager_factory=ProjectEvidenceExchangeManager,
        transport_namespace="project-evidence-v1",
        bootstrap_readiness="configured-status",
    ),
    CloudStreamDefinition(
        stream_id="project-attachment-v1",
        result_key="project_attachments",
        manager_factory=ProjectAttachmentExchangeManager,
        transport_namespace="project-attachment-v1",
        bootstrap_readiness="exchange-root",
    ),
)


@dataclass(frozen=True)
class CloudStreamRegistry:
    definitions: Tuple[CloudStreamDefinition, ...] = DEFAULT_STREAM_DEFINITIONS
    _by_id: Mapping[str, CloudStreamDefinition] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        definitions = tuple(self.definitions)
        if not definitions or definitions[0].stream_id != "archive-v1":
            raise ValueError("cloud stream registry must begin with archive-v1")
        by_id = {item.stream_id: item for item in definitions}
        if len(by_id) != len(definitions):
            raise ValueError("cloud stream ids must be unique")
        if len({item.result_key for item in definitions}) != len(definitions):
            raise ValueError("cloud stream result keys must be unique")
        object.__setattr__(self, "definitions", definitions)
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def get(self, stream_id: str) -> CloudStreamDefinition:
        try:
            return self._by_id[stream_id]
        except KeyError as error:
            raise ValueError(f"unknown cloud stream: {stream_id}") from error


@dataclass(frozen=True)
class CloudApplicationService:
    store: Any
    registry: CloudStreamRegistry = field(default_factory=CloudStreamRegistry)
    transport_factory: TransportFactory = CloudFolderTransport

    def transport(
        self,
        stream_id: str,
        *,
        archive_transport: Optional[CloudFolderTransport] = None,
        bootstrap: bool = False,
    ) -> CloudFolderTransport:
        definition = self.registry.get(stream_id)
        manager = definition.manager_factory(self.store)
        if definition.transport_namespace is None:
            transport = self.transport_factory(manager)
        else:
            transport = self.transport_factory(
                manager,
                config_path=manager.metadata_root / "cloud.json",
                stream_id=definition.transport_namespace,
            )
        if bootstrap and definition.transport_namespace is not None:
            if archive_transport is None:
                raise ValueError("cloud bootstrap requires the archive transport")
            self._bootstrap_transport(definition, transport, archive_transport)
        return transport

    def transports(
        self,
        *,
        bootstrap: bool,
        archive_transport: Optional[CloudFolderTransport] = None,
    ) -> Mapping[str, CloudFolderTransport]:
        archive = archive_transport or self.transport("archive-v1")
        assembled = {"archive-v1": archive}
        for definition in self.registry.definitions[1:]:
            assembled[definition.stream_id] = self.transport(
                definition.stream_id,
                archive_transport=archive,
                bootstrap=bootstrap,
            )
        return MappingProxyType(assembled)

    def sync_all(
        self,
        *,
        force: bool = False,
        post_sync: Optional[Mapping[str, Callable[[], Any]]] = None,
    ) -> dict[str, Any]:
        """Synchronize every registered stream without cross-stream failure."""
        result: dict[str, Any] = {"status": "ok", "streams": {}}
        archive_transport: Optional[CloudFolderTransport] = None
        for definition in self.registry.definitions:
            try:
                transport = self.transport(
                    definition.stream_id,
                    archive_transport=archive_transport,
                    bootstrap=definition.stream_id != "archive-v1",
                )
                if definition.stream_id == "archive-v1":
                    archive_transport = transport
                stream_result = transport.sync(force=force)
            except (OSError, RuntimeError, ValueError) as error:
                stream_result = {
                    "status": "failed",
                    "stream_id": definition.stream_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            callback = (post_sync or {}).get(definition.stream_id)
            if callback is not None and stream_result.get("status") != "failed":
                try:
                    stream_result["incoming"] = callback()
                except (OSError, RuntimeError, ValueError) as error:
                    stream_result["incoming"] = {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                    stream_result["status"] = "degraded"
            result[definition.result_key] = stream_result
            result["streams"][definition.stream_id] = stream_result
            if stream_result.get("status") in {"degraded", "failed"}:
                result["status"] = "degraded"
        return result

    @staticmethod
    def _bootstrap_transport(
        definition: CloudStreamDefinition,
        transport: CloudFolderTransport,
        archive_transport: CloudFolderTransport,
    ) -> None:
        if transport.config_path.exists():
            return
        source = archive_transport.config
        if definition.bootstrap_readiness == "configured-status":
            ready = bool(archive_transport.status()["configured"])
        elif definition.bootstrap_readiness == "exchange-root":
            ready = isinstance(source, dict) and bool(
                str(source.get("exchange_root", "")).strip()
            )
        else:
            ready = False
        if not ready:
            return
        transport.configure(
            Path(str(source["exchange_root"])),
            Path(str(source["identity_private_path"])),
            (
                Path(str(source["envelope_binary"]))
                if source.get("envelope_binary")
                else None
            ),
            enabled=bool(source.get("enabled")),
            merge_window_seconds=int(source.get("merge_window_seconds", 900)),
            early_flush_bytes=int(source.get("early_flush_bytes", 1024 * 1024)),
            maximum_pending_seconds=int(
                source.get("maximum_pending_seconds", 3600)
            ),
            cleanup_grace_seconds=int(
                source.get("cleanup_grace_seconds", 24 * 60 * 60)
            ),
        )
