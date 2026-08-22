#!/usr/bin/env python3
"""Immutable command metadata for the Memory Wuxian CLI shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    archive_access: str
    external_fs_access: str
    lock_policy: str
    mutation_predicate: str
    output_kind: str
    lifecycle_kind: str


# This order is the public argparse order frozen by the v2.18 CLI snapshot.
COMMAND_NAMES: Tuple[str, ...] = (
    "configuration-compile", "configuration-explain", "environment-capability-status",
    "init", "append", "sync-codex", "token-usage-backfill", "import-chatgpt",
    "status", "context-refresh-status", "context-capsule", "ack-context-refresh",
    "backup", "make-summary-job", "ingest-summary", "retrieve", "readonly-query",
    "readonly-http", "readonly-mcp", "summary-budget-status", "conversation-tail",
    "register-title", "rebuild-state", "rebuild-conversations", "rebuild-indexes",
    "index-generation-build", "index-generation-status", "index-generation-activate",
    "index-generation-rollback", "content-shadow-build", "content-shadow-status",
    "content-shadow-verify", "content-shadow-reconstruct", "content-shadow-disable",
    "content-transfer", "maintenance-enqueue", "maintenance-requeue",
    "maintenance-status", "maintenance-tick", "maintenance-diagnostics", "heartbeat",
    "rebuild-deterministic-indexes", "init-node", "add-peer", "revoke-peer",
    "export-delta", "inspect-bundle", "import-delta", "rebuild-global-index",
    "retrieve-global", "federation-status", "sync-peer", "cloud-configure",
    "cloud-pair-export", "cloud-pair-import", "cloud-sync", "project-attachment-sync",
    "cloud-status", "cloud-enable", "cloud-disable", "project-evidence-build",
    "project-evidence-list", "project-evidence-query", "project-evidence-reconstruct",
    "project-evidence-status", "project-evidence-owner-register",
    "project-evidence-owner-refresh", "project-evidence-owner-status",
    "project-attachment-build", "project-attachment-reconstruct",
    "project-attachment-owner-register", "project-attachment-owner-refresh",
    "project-attachment-status", "project-attachment-owner-status", "migration-preview",
    "migration-apply", "project-package-export", "project-package-import", "as-of",
    "decision-graph", "retrieval-evaluate", "semantic-index-build",
    "semantic-index-clear", "semantic-retrieve", "semantic-runtime-status",
    "environment-register-semantic-runtime", "environment-realize-semantic-runtime",
    "environment-init", "environment-scan", "environment-status", "environment-list",
    "environment-projects", "environment-show", "environment-diff",
    "environment-register", "environment-validate", "environment-export-delta",
    "environment-exchange-status", "environment-profile-capture",
    "environment-profile-status", "environment-profile-current",
    "environment-profile-rebuild-current", "environment-profile-compare",
    "environment-convergence-plan", "environment-incoming-status",
    "environment-process-incoming", "environment-accept-incoming",
    "environment-bindings-status", "environment-register-root",
    "environment-register-project-binding", "environment-register-rule-binding",
    "environment-register-project-rule-binding", "environment-register-skill-binding",
    "environment-discover", "environment-install-rule",
    "environment-recover-rule-installs", "environment-install-skill",
    "environment-recover-skill-installs", "environment-conflict-assess",
    "environment-conflicts", "environment-conflict-resolve",
    "environment-promotion-propose", "environment-promotion-transition",
    "environment-promotions", "environment-governance-propose",
    "environment-governance-proposals", "environment-product-evolution-record",
    "environment-product-evolution-records", "environment-governance-ai-enqueue",
    "environment-governance-ai-configure", "environment-governance-ai-tick",
    "environment-governance-ai-discover", "environment-governance-ai-status",
)

STATELESS_COMMANDS = frozenset({
    "configuration-compile", "configuration-explain", "environment-capability-status",
})

DIRECT_COMMANDS = frozenset({
    "retrieve", "readonly-query", "readonly-http", "readonly-mcp",
    "summary-budget-status", "conversation-tail", "context-refresh-status",
    "context-capsule", "ack-context-refresh", "inspect-bundle", "retrieve-global",
    "federation-status", "cloud-pair-export", "cloud-status", "project-evidence-list",
    "project-evidence-query", "project-evidence-status", "project-evidence-owner-status",
    "project-attachment-status", "project-attachment-owner-status", "migration-preview",
    "as-of", "decision-graph", "semantic-retrieve", "semantic-runtime-status",
    "environment-scan", "environment-status", "environment-list",
    "environment-projects", "environment-show", "environment-diff",
    "environment-validate", "environment-exchange-status", "environment-incoming-status",
    "environment-profile-status", "environment-profile-current",
    "environment-profile-compare", "environment-convergence-plan", "environment-init",
    "environment-register", "environment-profile-capture",
    "environment-profile-rebuild-current", "maintenance-enqueue", "maintenance-requeue",
    "maintenance-status", "maintenance-tick", "maintenance-diagnostics", "heartbeat",
    "content-shadow-status", "content-shadow-verify",
})

APPLY_PREVIEW_COMMANDS = frozenset({
    "token-usage-backfill", "project-evidence-build", "project-evidence-reconstruct",
    "project-evidence-owner-register", "project-evidence-owner-refresh",
    "project-attachment-build", "project-attachment-reconstruct",
    "project-attachment-owner-register", "project-attachment-owner-refresh",
    "content-shadow-build", "content-shadow-reconstruct", "content-shadow-disable",
    "content-transfer", "environment-register-semantic-runtime",
    "environment-realize-semantic-runtime", "environment-process-incoming",
    "environment-accept-incoming", "environment-register-root",
    "environment-register-project-binding", "environment-register-rule-binding",
    "environment-register-project-rule-binding", "environment-register-skill-binding",
    "environment-install-rule", "environment-install-skill",
    "environment-conflict-assess", "environment-conflict-resolve",
    "environment-promotion-propose", "environment-promotion-transition",
    "environment-governance-propose", "environment-product-evolution-record",
    "environment-governance-ai-enqueue", "environment-governance-ai-configure",
    "environment-governance-ai-discover",
})

FEDERATION_LOCK_COMMANDS = frozenset({
    "init-node", "add-peer", "revoke-peer", "import-delta", "rebuild-global-index",
    "sync-peer", "cloud-configure", "cloud-pair-import", "cloud-sync",
    "project-attachment-sync", "cloud-enable", "cloud-disable",
})

EXTERNAL_READ_COMMANDS = frozenset({
    "configuration-compile", "configuration-explain", "environment-capability-status",
    "sync-codex", "token-usage-backfill", "import-chatgpt", "ingest-summary",
    "inspect-bundle", "import-delta", "cloud-pair-import", "project-evidence-build",
    "project-attachment-build", "project-package-import", "retrieval-evaluate",
    "semantic-index-build", "environment-profile-compare", "environment-register",
    "environment-conflict-assess", "environment-promotion-propose",
    "environment-promotion-transition", "environment-governance-propose",
    "environment-product-evolution-record", "environment-governance-ai-enqueue",
    "environment-governance-ai-configure",
})

EXTERNAL_WRITE_COMMANDS = frozenset({
    "export-delta", "cloud-pair-export", "project-evidence-reconstruct",
    "project-attachment-reconstruct", "project-package-export", "as-of",
    "maintenance-diagnostics",
})

ARCHIVE_READ_COMMANDS = frozenset({
    "status", "context-refresh-status", "context-capsule", "retrieve", "readonly-query",
    "readonly-http", "readonly-mcp", "summary-budget-status", "conversation-tail",
    "export-delta", "retrieve-global", "federation-status", "migration-preview", "as-of",
    "decision-graph", "retrieval-evaluate", "semantic-retrieve",
    "semantic-runtime-status", "heartbeat",
})

TEXT_OUTPUT_COMMANDS = frozenset({"retrieve", "retrieve-global"})
SERVICE_OUTPUT_COMMANDS = frozenset({"readonly-http", "readonly-mcp"})
MIXED_OUTPUT_COMMANDS = frozenset({"export-delta"})


def _lock_policy(name: str) -> str:
    if name in STATELESS_COMMANDS or name in DIRECT_COMMANDS:
        return "none"
    if name.startswith("project-evidence-"):
        return "project-evidence-command"
    if name.startswith("project-attachment-") and name != "project-attachment-sync":
        return "project-attachment-command"
    if name.startswith("content-"):
        return "content-store"
    if name == "environment-export-delta":
        return "environment-exchange"
    if name.startswith("environment-"):
        return "none"
    if name in FEDERATION_LOCK_COMMANDS:
        return "federation"
    return "archive"


def _mutation_predicate(name: str) -> str:
    if name in APPLY_PREVIEW_COMMANDS:
        return "apply"
    if name in ARCHIVE_READ_COMMANDS or name in STATELESS_COMMANDS:
        return "never"
    return "always"


def _archive_access(name: str, lock_policy: str) -> str:
    if name in ARCHIVE_READ_COMMANDS:
        return "read"
    if name == "token-usage-backfill":
        return "conditional-write"
    if lock_policy == "archive":
        return "write"
    return "none"


def _external_fs_access(name: str) -> str:
    reads = name in EXTERNAL_READ_COMMANDS
    writes = name in EXTERNAL_WRITE_COMMANDS
    if reads and writes:
        return "read-write"
    if reads:
        return "read"
    if writes:
        return "write"
    return "none"


def _output_kind(name: str) -> str:
    if name in TEXT_OUTPUT_COMMANDS:
        return "text"
    if name in SERVICE_OUTPUT_COMMANDS:
        return "service"
    if name in MIXED_OUTPUT_COMMANDS:
        return "binary-or-json"
    return "json"


def _lifecycle_kind(name: str) -> str:
    if name == "readonly-http":
        return "http-server"
    if name == "readonly-mcp":
        return "mcp-server"
    return "oneshot"


def _build_spec(name: str) -> CommandSpec:
    lock_policy = _lock_policy(name)
    return CommandSpec(
        name=name,
        archive_access=_archive_access(name, lock_policy),
        external_fs_access=_external_fs_access(name),
        lock_policy=lock_policy,
        mutation_predicate=_mutation_predicate(name),
        output_kind=_output_kind(name),
        lifecycle_kind=_lifecycle_kind(name),
    )


COMMAND_SPECS: Tuple[CommandSpec, ...] = tuple(_build_spec(name) for name in COMMAND_NAMES)
COMMAND_REGISTRY: Mapping[str, CommandSpec] = MappingProxyType(
    {spec.name: spec for spec in COMMAND_SPECS}
)


def command_spec(name: str) -> CommandSpec:
    try:
        return COMMAND_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown CLI command: {name}") from exc


def validate_parser_commands(parser_command_names: Tuple[str, ...]) -> None:
    if parser_command_names != COMMAND_NAMES:
        missing = tuple(name for name in COMMAND_NAMES if name not in parser_command_names)
        extra = tuple(name for name in parser_command_names if name not in COMMAND_REGISTRY)
        raise RuntimeError(
            "CLI parser and command registry differ: "
            f"missing={missing!r}, extra={extra!r}, order_matches=False"
        )


def command_lock_path(
    spec: CommandSpec,
    args: Any,
    archive_root: Path,
) -> Optional[Path]:
    if spec.lock_policy == "none":
        return None
    if spec.mutation_predicate == "apply" and not bool(getattr(args, "apply", False)):
        return None
    lock_filenames = {
        "archive": "archive.lock",
        "federation": "federation.lock",
        "environment-exchange": "environment-exchange.lock",
        "project-evidence-command": "project-evidence-command.lock",
        "project-attachment-command": "project-attachment-command.lock",
        "content-store": "content-store.lock",
    }
    try:
        filename = lock_filenames[spec.lock_policy]
    except KeyError as exc:
        raise ValueError(f"Unsupported CLI lock policy: {spec.lock_policy}") from exc
    return archive_root / ".locks" / filename
