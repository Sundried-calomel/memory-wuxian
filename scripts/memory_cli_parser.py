"""Mechanical argparse owner for the Memory Wuxian CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from memory_cli_contract import validate_parser_commands


def build_parser(
    *,
    default_config: Path,
    maintenance_job_kinds: Sequence[str],
    semantic_runtime_artifact_id: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Memory無限 persistent conversation memory CLI")
    parser.add_argument("--root", help="Memory archive root; defaults to config.yaml")
    parser.add_argument("--config", default=str(default_config), help="Configuration YAML path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "configuration-compile",
        help="Compile and print the effective configuration without creating state",
    )
    subparsers.add_parser(
        "configuration-explain",
        help="Explain effective configuration sources without creating state",
    )
    capability_status = subparsers.add_parser(
        "environment-capability-status",
        help="Show local device capability compatibility without creating state",
    )
    capability_status.add_argument(
        "--peer-offer",
        help="Optional peer device capability offer JSON",
    )
    subparsers.add_parser("init", help="Initialize an archive without overwriting existing records")
    append_parser = subparsers.add_parser("append", help="Append one exact dialogue message")
    append_parser.add_argument("--speaker", required=True, choices=["user", "assistant", "system", "tool"])
    append_parser.add_argument("--text")
    append_parser.add_argument("--text-file")
    append_parser.add_argument("--timestamp", help="ISO-8601 timestamp with timezone")
    append_parser.add_argument("--conversation-id", default="default")
    append_parser.add_argument("--message-id")
    append_parser.add_argument("--reply-to")
    append_parser.add_argument("--allow-secrets", action="store_true", help="Disable configured secret redaction for this message")
    append_parser.add_argument(
        "--nonfinal-assistant",
        action="store_true",
        help="Store a visible assistant update without completing the dialogue round",
    )

    sync_parser = subparsers.add_parser(
        "sync-codex",
        help="Incrementally import visible messages from Codex rollout JSONL files",
    )
    sync_parser.add_argument(
        "--session-file",
        action="append",
        default=[],
        help="Specific Codex rollout JSONL file; may be supplied more than once",
    )
    sync_parser.add_argument(
        "--sessions-root",
        help="Recursively scan a Codex sessions directory for rollout JSONL files",
    )
    sync_parser.add_argument(
        "--since",
        help="When scanning --sessions-root, include files modified at or after this ISO-8601 time",
    )
    token_backfill_parser = subparsers.add_parser(
        "token-usage-backfill",
        help="Preview or persist exact Codex-reported token usage from retained rollout files",
    )
    token_backfill_parser.add_argument(
        "--sessions-root",
        action="append",
        default=[],
        help=(
            "Codex sessions directory to scan; may be repeated. Defaults to both "
            "~/.codex/sessions and ~/.codex/archived_sessions."
        ),
    )
    token_backfill_parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist one derived token-usage ledger per top-level Codex conversation",
    )
    chatgpt_parser = subparsers.add_parser(
        "import-chatgpt",
        help="Import ChatGPT data export ZIP, directory, or conversations.json",
    )
    chatgpt_parser.add_argument(
        "--export",
        required=True,
        help="ChatGPT export ZIP, extracted directory, or conversations.json path",
    )
    chatgpt_parser.add_argument(
        "--conversation-id",
        action="append",
        default=[],
        help="Import only this native ChatGPT conversation ID; may be repeated",
    )

    subparsers.add_parser("status", help="Print archive counters and pending work")
    for name, help_text in (
        ("context-refresh-status", "Check whether the active conversation needs a memory capsule"),
        ("context-capsule", "Render a bounded hierarchical memory capsule for the active conversation"),
        ("ack-context-refresh", "Deprecated compatibility no-op; context capsules require no acknowledgement"),
    ):
        refresh_parser = subparsers.add_parser(name, help=help_text)
        refresh_parser.add_argument("--session-file")
    backup_parser = subparsers.add_parser(
        "backup",
        help="Create one verified external snapshot and prune older snapshots",
    )
    backup_parser.add_argument("--reason", default="manual-backup")
    subparsers.add_parser("make-summary-job", help="Create the next due deterministic summary job")
    ingest_parser = subparsers.add_parser("ingest-summary", help="Validate and persist an Agent-generated summary")
    ingest_parser.add_argument("--job", required=True)
    ingest_parser.add_argument("--summary-json", required=True)
    retrieve_parser = subparsers.add_parser("retrieve", help="Search indexes and verify against raw history")
    retrieve_parser.add_argument("--query", required=True)
    retrieve_parser.add_argument(
        "--mode",
        choices=("historical", "current-policy"),
        default="historical",
        help="Use current-policy to include explicit policy validity and later matching evidence",
    )
    readonly_parser = subparsers.add_parser(
        "readonly-query",
        help="Run the bounded provenance-aware query shared by CLI, HTTP, and MCP",
    )
    readonly_parser.add_argument("--query")
    readonly_parser.add_argument("--mode", default="hybrid")
    readonly_parser.add_argument("--limit", default="20")
    readonly_http = subparsers.add_parser(
        "readonly-http",
        help="Serve the bounded read-only query API on loopback only",
    )
    readonly_http.add_argument("--host", default="127.0.0.1")
    readonly_http.add_argument("--port", type=int, default=8766)
    subparsers.add_parser(
        "readonly-mcp",
        help="Serve the allow-listed read-only memory.query JSON-RPC method on stdio",
    )
    summary_budget = subparsers.add_parser(
        "summary-budget-status",
        help="Evaluate deterministic summary eligibility without invoking AI",
    )
    summary_budget.add_argument("--metrics-json", required=True)
    summary_budget.add_argument("--policy-json", required=True)
    tail_parser = subparsers.add_parser(
        "conversation-tail",
        help="Resolve an archived conversation by title and return its latest visible messages",
    )
    tail_parser.add_argument("--title", required=True)
    tail_parser.add_argument("--messages", type=int, default=20)
    tail_parser.add_argument(
        "--exclude-conversation-id",
        action="append",
        default=[],
        help="Conversation ID that must not satisfy this historical lookup; may be repeated",
    )
    register_title_parser = subparsers.add_parser(
        "register-title",
        help="Persist a user-confirmed title alias for one archived conversation",
    )
    register_title_parser.add_argument("--conversation-id", required=True)
    register_title_parser.add_argument("--title", required=True)
    register_title_parser.add_argument("--source", default="user-confirmed")
    rebuild_state_parser = subparsers.add_parser("rebuild-state", help="Preview or apply state reconstruction from persisted files")
    rebuild_state_parser.add_argument("--apply", action="store_true", help="Back up and replace state.json")
    rebuild_conversations_parser = subparsers.add_parser(
        "rebuild-conversations",
        help="Preview or rebuild one complete transcript per conversation",
    )
    rebuild_conversations_parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and replace derived per-conversation transcripts",
    )
    rebuild_indexes_parser = subparsers.add_parser("rebuild-indexes", help="Preview or apply derived-index reconstruction")
    rebuild_indexes_parser.add_argument("--apply", action="store_true", help="Back up and replace derived indexes")
    subparsers.add_parser(
        "index-generation-build",
        help="Build and verify an immutable shadow index generation without activating it",
    )
    generation_status_parser = subparsers.add_parser(
        "index-generation-status",
        help="Verify the active or selected immutable index generation",
    )
    generation_status_parser.add_argument("--generation-id")
    generation_activate_parser = subparsers.add_parser(
        "index-generation-activate",
        help="Preview or atomically activate one verified index generation",
    )
    generation_activate_parser.add_argument("--generation-id", required=True)
    generation_activate_parser.add_argument("--apply", action="store_true")
    generation_rollback_parser = subparsers.add_parser(
        "index-generation-rollback",
        help="Preview or atomically restore the previous index-generation pointer",
    )
    generation_rollback_parser.add_argument("--apply", action="store_true")
    content_build_parser = subparsers.add_parser(
        "content-shadow-build",
        help="Preview or build one exact-byte shadow manifest from explicit files",
    )
    content_build_parser.add_argument("--source-root", required=True)
    content_build_parser.add_argument("--source-id", required=True)
    content_build_parser.add_argument("--file", action="append", required=True)
    content_build_parser.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "content-shadow-status",
        help="Show removable exact-byte shadow-store status",
    )
    content_verify_parser = subparsers.add_parser(
        "content-shadow-verify",
        help="Verify one shadow manifest and its exact-byte objects",
    )
    content_verify_parser.add_argument("--manifest-id", required=True)
    content_verify_parser.add_argument("--source-root")
    content_reconstruct_parser = subparsers.add_parser(
        "content-shadow-reconstruct",
        help="Preview or reconstruct one verified manifest without overwriting conflicts",
    )
    content_reconstruct_parser.add_argument("--manifest-id", required=True)
    content_reconstruct_parser.add_argument("--destination", required=True)
    content_reconstruct_parser.add_argument("--apply", action="store_true")
    content_disable_parser = subparsers.add_parser(
        "content-shadow-disable",
        help="Preview or disable shadow-store use without changing source history",
    )
    content_disable_parser.add_argument("--apply", action="store_true")
    content_transfer_parser = subparsers.add_parser(
        "content-transfer",
        help="Preview or apply one bounded resumable local shadow transfer range",
    )
    content_transfer_parser.add_argument("--manifest-id", required=True)
    content_transfer_parser.add_argument("--target-archive-root", required=True)
    content_transfer_parser.add_argument("--domain", choices=("archive", "environment"), required=True)
    content_transfer_parser.add_argument("--target-id", required=True)
    content_transfer_parser.add_argument("--start", type=int, required=True)
    content_transfer_parser.add_argument("--count", type=int, required=True)
    content_transfer_parser.add_argument("--apply", action="store_true")
    maintenance_enqueue_parser = subparsers.add_parser(
        "maintenance-enqueue",
        help="Persist one bounded model-free maintenance job",
    )
    maintenance_enqueue_parser.add_argument("--kind", required=True, choices=sorted(maintenance_job_kinds))
    maintenance_enqueue_parser.add_argument("--idempotency-key", required=True)
    maintenance_enqueue_parser.add_argument("--payload-json")
    maintenance_enqueue_parser.add_argument("--max-attempts", type=int, default=3)
    maintenance_requeue_parser = subparsers.add_parser(
        "maintenance-requeue",
        help="Explicitly requeue one quarantined job with an immutable audit receipt",
    )
    maintenance_requeue_parser.add_argument("--job-id", required=True)
    maintenance_requeue_parser.add_argument("--reason", required=True)
    subparsers.add_parser(
        "maintenance-status",
        help="Show desired-versus-actual service and maintenance queue state",
    )
    maintenance_tick_parser = subparsers.add_parser(
        "maintenance-tick",
        help="Run one bounded model-free maintenance pass",
    )
    maintenance_tick_parser.add_argument("--maximum-jobs", type=int, default=20)
    subparsers.add_parser(
        "maintenance-diagnostics",
        help="Create a redacted diagnostic bundle without raw dialogue",
    )
    heartbeat_parser = subparsers.add_parser("heartbeat", help="Validate archive state and recover due work")
    heartbeat_parser.add_argument("--no-create-jobs", action="store_true")
    heartbeat_parser.add_argument("--check-only", action="store_true", help="Validate without creating jobs or repairing files")
    heartbeat_parser.add_argument("--repair", action="store_true", help="Back up and rebuild repairable state or index inconsistencies")
    subparsers.add_parser(
        "rebuild-deterministic-indexes",
        help="Rebuild script-only hybrid indexes from authoritative raw records",
    )
    init_node_parser = subparsers.add_parser(
        "init-node",
        help="Create this archive's stable federation node identity",
    )
    init_node_parser.add_argument("--display-name")
    init_node_parser.add_argument("--node-id")
    add_peer_parser = subparsers.add_parser(
        "add-peer",
        help="Register a trusted Memory Wuxian peer and optional SSH transport",
    )
    add_peer_parser.add_argument("--node-id", required=True)
    add_peer_parser.add_argument("--display-name")
    add_peer_parser.add_argument("--host")
    add_peer_parser.add_argument("--port", type=int, default=22)
    add_peer_parser.add_argument("--remote-root")
    add_peer_parser.add_argument("--remote-config")
    add_peer_parser.add_argument("--remote-cli")
    add_peer_parser.add_argument("--remote-python", default="python3")
    add_peer_parser.add_argument(
        "--remote-shell",
        choices=["posix", "powershell"],
        default="posix",
    )
    revoke_peer_parser = subparsers.add_parser(
        "revoke-peer",
        help="Reject future imports and SSH synchronization from one peer",
    )
    revoke_peer_parser.add_argument("--node-id", required=True)
    export_delta_parser = subparsers.add_parser(
        "export-delta",
        help="Export new local artifacts as one verifiable federation bundle",
    )
    export_delta_parser.add_argument("--output", required=True)
    export_delta_parser.add_argument("--after-event-sequence", type=int, default=0)
    export_delta_parser.add_argument("--previous-bundle-sha256")
    export_delta_parser.add_argument("--target-node-id")
    inspect_bundle_parser = subparsers.add_parser(
        "inspect-bundle",
        help="Validate a federation bundle without importing it",
    )
    inspect_bundle_parser.add_argument("--bundle", required=True)
    import_delta_parser = subparsers.add_parser(
        "import-delta",
        help="Import one trusted peer bundle into its read-only replica",
    )
    import_delta_parser.add_argument("--bundle", required=True)
    import_delta_parser.add_argument("--expected-node-id")
    subparsers.add_parser(
        "rebuild-global-index",
        help="Rebuild cross-device indexes from read-only replicas",
    )
    retrieve_global_parser = subparsers.add_parser(
        "retrieve-global",
        help="Search local authority and all synchronized peer replicas",
    )
    retrieve_global_parser.add_argument("--query", required=True)
    retrieve_global_parser.add_argument("--node")
    subparsers.add_parser(
        "federation-status",
        help="Show this node, trusted peers, replica cursors, and recent synchronization",
    )
    sync_peer_parser = subparsers.add_parser(
        "sync-peer",
        help="Pull and import a peer's next delta over authenticated SSH",
    )
    sync_peer_parser.add_argument("--node-id", required=True)
    cloud_configure_parser = subparsers.add_parser(
        "cloud-configure",
        help="Configure an encrypted iCloud Drive, OneDrive, or compatible folder transport",
    )
    cloud_configure_parser.add_argument("--directory", required=True)
    cloud_configure_parser.add_argument("--identity-path")
    cloud_configure_parser.add_argument("--envelope-binary")
    cloud_pair_export_parser = subparsers.add_parser(
        "cloud-pair-export",
        help="Export this node's public cloud pairing record",
    )
    cloud_pair_export_parser.add_argument("--output")
    cloud_pair_import_parser = subparsers.add_parser(
        "cloud-pair-import",
        help="Trust a peer's public cloud pairing record",
    )
    cloud_pair_import_parser.add_argument("--pairing-file", required=True)
    cloud_pair_import_parser.add_argument("--expected-fingerprint")
    cloud_sync_parser = subparsers.add_parser(
        "cloud-sync",
        help="Run one encrypted bidirectional cloud-folder synchronization pass",
    )
    cloud_sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the merge window for local exports",
    )
    project_attachment_sync_parser = subparsers.add_parser(
        "project-attachment-sync",
        help="Run one encrypted synchronization pass for project attachments only",
    )
    project_attachment_sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the merge window for attachment exports",
    )
    subparsers.add_parser(
        "cloud-status",
        help="Show encrypted cloud-folder configuration and peer delivery state",
    )
    subparsers.add_parser(
        "cloud-enable",
        help="Enable an already configured cloud-folder transport",
    )
    subparsers.add_parser(
        "cloud-disable",
        help="Disable cloud-folder synchronization without deleting data or keys",
    )
    project_evidence_build = subparsers.add_parser(
        "project-evidence-build",
        help="Preview or record one explicit immutable project evidence package",
    )
    project_evidence_build.add_argument("--spec", required=True)
    project_evidence_build.add_argument("--apply", action="store_true")
    project_evidence_list = subparsers.add_parser(
        "project-evidence-list",
        help="List local and read-only peer project evidence packages",
    )
    project_evidence_list.add_argument("--project-id")
    project_evidence_query = subparsers.add_parser(
        "project-evidence-query",
        help="Search bounded local and peer project evidence metadata and text excerpts",
    )
    project_evidence_query.add_argument("--query", required=True)
    project_evidence_query.add_argument("--project-id")
    project_evidence_query.add_argument("--role")
    project_evidence_reconstruct = subparsers.add_parser(
        "project-evidence-reconstruct",
        help="Preview or reconstruct exact project evidence bytes into a destination",
    )
    project_evidence_reconstruct.add_argument("--generation-id", required=True)
    project_evidence_reconstruct.add_argument("--destination", required=True)
    project_evidence_reconstruct.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "project-evidence-status",
        help="Show project evidence package and exchange cursors",
    )
    project_evidence_owner_register = subparsers.add_parser(
        "project-evidence-owner-register",
        help="Preview or register one device-local explicit project evidence owner",
    )
    project_evidence_owner_register.add_argument("--spec", required=True)
    project_evidence_owner_register.add_argument("--apply", action="store_true")
    project_evidence_owner_refresh = subparsers.add_parser(
        "project-evidence-owner-refresh",
        help="Preview or create one changed immutable generation for a registered owner",
    )
    project_evidence_owner_refresh.add_argument("--project-id", required=True)
    project_evidence_owner_refresh.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "project-evidence-owner-status",
        help="Show device-local project evidence owners and their current generation",
    )
    project_attachment_build = subparsers.add_parser(
        "project-attachment-build",
        help="Preview or record one exact-byte project attachment generation",
    )
    project_attachment_build.add_argument("--spec", required=True)
    project_attachment_build.add_argument("--apply", action="store_true")
    project_attachment_reconstruct = subparsers.add_parser(
        "project-attachment-reconstruct",
        help="Preview or reconstruct a complete verified project attachment generation",
    )
    project_attachment_reconstruct.add_argument("--generation-id", required=True)
    project_attachment_reconstruct.add_argument("--destination", required=True)
    project_attachment_reconstruct.add_argument("--apply", action="store_true")
    project_attachment_owner_register = subparsers.add_parser(
        "project-attachment-owner-register",
        help="Preview or register one explicit device-local attachment selection",
    )
    project_attachment_owner_register.add_argument("--spec", required=True)
    project_attachment_owner_register.add_argument("--apply", action="store_true")
    project_attachment_owner_refresh = subparsers.add_parser(
        "project-attachment-owner-refresh",
        help="Preview or refresh one registered attachment selection",
    )
    project_attachment_owner_refresh.add_argument("--project-id", required=True)
    project_attachment_owner_refresh.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "project-attachment-status",
        help="Show local attachment manifests and exchange cursors",
    )
    subparsers.add_parser(
        "project-attachment-owner-status",
        help="Show device-local project attachment owners",
    )
    migration_preview = subparsers.add_parser(
        "migration-preview", help="Preview a verified copy-only archive migration"
    )
    migration_preview.add_argument("--destination", required=True)
    migration_apply = subparsers.add_parser(
        "migration-apply", help="Copy and verify an archive without deleting its source"
    )
    migration_apply.add_argument("--destination", required=True)
    migration_apply.add_argument("--switch-active", action="store_true")
    project_export = subparsers.add_parser(
        "project-package-export", help="Export selected conversations as a readable project package"
    )
    project_export.add_argument("--output", required=True)
    project_export.add_argument("--conversation-id", action="append", required=True)
    project_import = subparsers.add_parser(
        "project-package-import", help="Verify a project package into a read-only replica"
    )
    project_import.add_argument("--package", required=True)
    as_of = subparsers.add_parser(
        "as-of", help="Render a read-only historical view at an exact timestamp"
    )
    as_of.add_argument("--timestamp", required=True)
    as_of.add_argument("--conversation-id")
    as_of.add_argument("--output")
    graph = subparsers.add_parser(
        "decision-graph", help="Render the derived decision and rule lineage graph"
    )
    graph.add_argument("--output")
    evaluation = subparsers.add_parser(
        "retrieval-evaluate", help="Evaluate retrieval against a human-readable JSONL dataset"
    )
    evaluation.add_argument("--dataset", required=True)
    evaluation.add_argument("--top-k", type=int, default=10)
    evaluation.add_argument("--output")
    semantic_build = subparsers.add_parser(
        "semantic-index-build", help="Build an optional disposable local semantic index"
    )
    semantic_build.add_argument("--provider", default="local-hash-v1")
    subparsers.add_parser(
        "semantic-index-clear", help="Delete only the disposable semantic index"
    )
    semantic_retrieve = subparsers.add_parser(
        "semantic-retrieve", help="Search the local semantic index and verify raw backlinks"
    )
    semantic_retrieve.add_argument("--query", required=True)
    semantic_retrieve.add_argument("--top-k", type=int, default=10)
    semantic_runtime_status = subparsers.add_parser(
        "semantic-runtime-status",
        help="Validate the shared E5 interface contract and inspect local realization",
    )
    semantic_runtime_status.add_argument(
        "--artifact-id", default=semantic_runtime_artifact_id
    )
    semantic_runtime_register = subparsers.add_parser(
        "environment-register-semantic-runtime",
        help="Preview or register the bundled E5 interface contract in environment-v1",
    )
    semantic_runtime_register.add_argument("--origin-node-id", required=True)
    semantic_runtime_register.add_argument("--apply", action="store_true")
    semantic_runtime_realize = subparsers.add_parser(
        "environment-realize-semantic-runtime",
        help="Preview or explicitly realize one registered E5 interface contract locally",
    )
    semantic_runtime_realize.add_argument(
        "--artifact-id", default=semantic_runtime_artifact_id
    )
    semantic_runtime_realize.add_argument("--model-root")
    semantic_runtime_realize.add_argument("--runtime-dir")
    semantic_runtime_realize.add_argument("--apply", action="store_true")

    subparsers.add_parser(
        "environment-init",
        help="Initialize the independent Memory Wuxian 2.0 Environment Registry",
    )
    environment_scan = subparsers.add_parser(
        "environment-scan",
        help="Preview only explicitly supplied environment manifests",
    )
    environment_scan.add_argument("--manifest", action="append", default=[])
    environment_scan.add_argument("--scan-root", action="append", default=[])
    subparsers.add_parser(
        "environment-status",
        help="Show Environment Registry counters without changing archive state",
    )
    environment_list = subparsers.add_parser(
        "environment-list",
        help="List current registered environment artifacts",
    )
    environment_list.add_argument(
        "--object-class",
        choices=(
            "global-rule",
            "project-rule",
            "global-skill",
            "project-skill",
            "global-runtime-contract",
        ),
    )
    subparsers.add_parser(
        "environment-projects",
        help="List current registered project bindings",
    )
    environment_show = subparsers.add_parser(
        "environment-show",
        help="Show one environment artifact and its current immutable revision",
    )
    environment_show.add_argument("--artifact-id", required=True)
    environment_diff = subparsers.add_parser(
        "environment-diff",
        help="Preview changes from one explicit environment manifest",
    )
    environment_diff.add_argument("--manifest", required=True)
    environment_register = subparsers.add_parser(
        "environment-register",
        help="Preview or register one explicit environment manifest",
    )
    environment_register.add_argument("--manifest", required=True)
    environment_register.add_argument(
        "--apply",
        action="store_true",
        help="Commit the validated plan using the independent environment lock",
    )
    subparsers.add_parser(
        "environment-validate",
        help="Verify environment registry, revisions, objects, and project bindings",
    )
    environment_export = subparsers.add_parser(
        "environment-export-delta",
        help="Export one independent environment-v1 delta bundle",
    )
    environment_export.add_argument("--output", required=True)
    environment_export.add_argument("--after-event-sequence", type=int, default=0)
    environment_export.add_argument("--previous-bundle-sha256")
    environment_export.add_argument("--target-node-id")
    subparsers.add_parser(
        "environment-exchange-status",
        help="Show the independent environment-v1 stream cursor",
    )
    environment_profile_capture = subparsers.add_parser(
        "environment-profile-capture",
        help="Capture one deterministic path-free personal Environment profile from an explicit specification",
    )
    environment_profile_capture.add_argument("--specification", required=True)
    environment_profile_capture.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-profile-status",
        help="Show immutable personal Environment profile generation counters",
    )
    subparsers.add_parser(
        "environment-profile-current",
        help="Show the current local personal Environment profile evidence",
    )
    environment_profile_rebuild = subparsers.add_parser(
        "environment-profile-rebuild-current",
        help="Preview or atomically rebuild the current pointer from one complete generation chain",
    )
    environment_profile_rebuild.add_argument("--apply", action="store_true")
    environment_profile_compare = subparsers.add_parser(
        "environment-profile-compare",
        help="Compare the current local profile with one trusted read-only peer replica",
    )
    environment_profile_compare.add_argument("--peer-node-id", required=True)
    environment_profile_compare.add_argument("--peer-generation-sha256")
    environment_profile_plan = subparsers.add_parser(
        "environment-convergence-plan",
        help="Preview a bounded convergence plan without activating any capability",
    )
    environment_profile_plan.add_argument("--peer-node-id", required=True)
    environment_profile_plan.add_argument(
        "--artifact-links",
        help=(
            "Optional JSON file conforming to "
            "schemas/environment-convergence-artifact-links.schema.json"
        ),
    )
    subparsers.add_parser(
        "environment-incoming-status",
        help="Show deterministic decisions for staged Environment updates",
    )
    environment_process = subparsers.add_parser(
        "environment-process-incoming",
        help="Preview or process staged Environment updates without AI",
    )
    environment_process.add_argument("--apply", action="store_true")
    environment_process.add_argument(
        "--auto-register-compatible-rules",
        action="store_true",
        help="Register only global Rule fast-forwards; never auto-install Skills",
    )
    environment_process.add_argument("--maximum-events", type=int, default=100)
    environment_process.add_argument("--runtime", action="append", default=[])
    environment_accept = subparsers.add_parser(
        "environment-accept-incoming",
        help="Preview or explicitly register one compatible staged update",
    )
    environment_accept.add_argument("--stage-sha256", required=True)
    environment_accept.add_argument("--runtime", action="append", default=[])
    environment_accept.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-bindings-status",
        help="Show this node's explicit Environment roots and bindings",
    )
    environment_root = subparsers.add_parser(
        "environment-register-root",
        help="Preview or register one explicit global rule or Skill root",
    )
    environment_root.add_argument("--root-id", required=True)
    environment_root.add_argument(
        "--role", required=True, choices=("global-rules", "global-skills")
    )
    environment_root.add_argument("--owner", required=True)
    environment_root.add_argument("--path", required=True)
    environment_root.add_argument("--base-binding-sha256")
    environment_root.add_argument("--apply", action="store_true")
    environment_project = subparsers.add_parser(
        "environment-register-project-binding",
        help="Preview or activate one project already present in Environment Registry",
    )
    environment_project.add_argument("--project-id", required=True)
    environment_project.add_argument("--base-binding-sha256")
    environment_project.add_argument("--apply", action="store_true")
    for command, help_text in (
        ("environment-register-rule-binding", "Register one global rule binding JSON"),
        (
            "environment-register-project-rule-binding",
            "Register one node-local project rule base-state JSON",
        ),
        ("environment-register-skill-binding", "Register one global Skill binding JSON"),
    ):
        binding_parser = subparsers.add_parser(command, help=help_text)
        binding_parser.add_argument("--binding-json", required=True)
        binding_parser.add_argument("--base-binding-sha256")
        binding_parser.add_argument("--apply", action="store_true")
    environment_discover = subparsers.add_parser(
        "environment-discover",
        help="Read-only discovery under one explicitly registered root",
    )
    environment_discover.add_argument(
        "--role", required=True, choices=("global-rules", "global-skills", "project")
    )
    environment_discover.add_argument("--path", required=True)
    environment_discover.add_argument("--project-id")
    environment_rule_install = subparsers.add_parser(
        "environment-install-rule",
        help="Preview or apply one verified registered rule revision",
    )
    environment_rule_install.add_argument("--artifact-id", required=True)
    environment_rule_install.add_argument("--revision-id", required=True)
    environment_rule_install.add_argument("--binding-id", required=True)
    environment_rule_install.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-recover-rule-installs",
        help="Recover interrupted rule-install transactions",
    )
    environment_skill_install = subparsers.add_parser(
        "environment-install-skill",
        help="Preview or apply one verified immutable Skill package",
    )
    environment_skill_install.add_argument("--package", required=True)
    environment_skill_install.add_argument("--artifact-id", required=True)
    environment_skill_install.add_argument("--revision-id", required=True)
    environment_skill_install.add_argument("--binding-id", required=True)
    environment_skill_install.add_argument("--runtime", action="append", default=[])
    environment_skill_install.add_argument("--apply", action="store_true")
    environment_skill_recover = subparsers.add_parser(
        "environment-recover-skill-installs",
        help="Recover interrupted Skill-install transactions",
    )
    environment_skill_recover.add_argument("--runtime", action="append", default=[])
    environment_conflict_assess = subparsers.add_parser(
        "environment-conflict-assess",
        help="Classify one explicit base/local/remote assessment JSON",
    )
    environment_conflict_assess.add_argument("--assessment-json", required=True)
    environment_conflict_assess.add_argument("--apply", action="store_true")
    environment_conflicts = subparsers.add_parser(
        "environment-conflicts",
        help="List effective Environment conflict states",
    )
    environment_conflicts.add_argument("--pending-only", action="store_true")
    environment_conflict_resolve = subparsers.add_parser(
        "environment-conflict-resolve",
        help="Preview or record one explicit conflict resolution",
    )
    environment_conflict_resolve.add_argument("--conflict-id", required=True)
    environment_conflict_resolve.add_argument(
        "--action",
        required=True,
        choices=("take-local", "take-remote", "manual-merge", "reject-remote"),
    )
    environment_conflict_resolve.add_argument("--evidence", required=True)
    environment_conflict_resolve.add_argument("--reviewer", required=True)
    environment_conflict_resolve.add_argument("--apply", action="store_true")
    environment_promotion = subparsers.add_parser(
        "environment-promotion-propose",
        help="Preview or record one project capability promotion candidate",
    )
    environment_promotion.add_argument("--record-json", required=True)
    environment_promotion.add_argument("--apply", action="store_true")
    environment_promotion_transition = subparsers.add_parser(
        "environment-promotion-transition",
        help="Preview or append one reviewed promotion-state transition",
    )
    environment_promotion_transition.add_argument("--promotion-id", required=True)
    environment_promotion_transition.add_argument("--record-json", required=True)
    environment_promotion_transition.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-promotions",
        help="List effective project capability promotion states",
    )
    environment_governance = subparsers.add_parser(
        "environment-governance-propose",
        help="Preview or record one immutable governance insight proposal",
    )
    environment_governance.add_argument("--proposal-json", required=True)
    environment_governance.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-governance-proposals",
        help="List local and imported governance proposals without accepting them",
    )
    environment_evolution = subparsers.add_parser(
        "environment-product-evolution-record",
        help="Preview or record one immutable product evolution report",
    )
    environment_evolution.add_argument("--record-json", required=True)
    environment_evolution.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-product-evolution-records",
        help="List local and imported product evolution reports without remediation",
    )
    governance_ai_enqueue = subparsers.add_parser(
        "environment-governance-ai-enqueue",
        help="Preview or enqueue one source-hashed governance AI work item",
    )
    governance_ai_enqueue.add_argument("--item-json", required=True)
    governance_ai_enqueue.add_argument("--apply", action="store_true")
    governance_ai_configure = subparsers.add_parser(
        "environment-governance-ai-configure",
        help="Preview or persist the local governance AI scheduler policy",
    )
    governance_ai_configure.add_argument("--policy-json", required=True)
    governance_ai_configure.add_argument("--apply", action="store_true")
    governance_ai_tick = subparsers.add_parser(
        "environment-governance-ai-tick",
        help="Check due micro-batches and optionally run ephemeral AI drafts",
    )
    governance_ai_tick.add_argument("--run-ai", action="store_true")
    governance_ai_tick.add_argument("--maximum-batches", type=int, default=1)
    governance_ai_discover = subparsers.add_parser(
        "environment-governance-ai-discover",
        help="Preview or create model-free tasks from registered Environment evidence",
    )
    governance_ai_discover.add_argument("--apply", action="store_true")
    subparsers.add_parser(
        "environment-governance-ai-status",
        help="Show governance AI queue, coordinator, limits, and draft counters",
    )
    subparser_action = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    validate_parser_commands(tuple(subparser_action.choices))
    return parser
