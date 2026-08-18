#!/usr/bin/env python3
"""Run the auditable Memory Wuxian release rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from console_encoding import configure_unicode_stdio


ROOT = Path(__file__).resolve().parent.parent


def validation_profile(version: str) -> tuple[str, list[str]]:
    contract_path = ROOT / "docs" / "work-contracts" / f"v{version}.json"
    if not contract_path.is_file():
        return "full", []
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    profile = str(contract.get("validation_profile", "full"))
    scenarios = contract.get("required_rehearsal_scenarios", [])
    if profile not in {"full", "targeted-patch"}:
        raise ValueError(f"unsupported validation profile: {profile}")
    if profile == "targeted-patch" and (
        not isinstance(scenarios, list)
        or not scenarios
        or not all(isinstance(item, str) and item for item in scenarios)
    ):
        raise ValueError("targeted-patch requires explicit rehearsal scenarios")
    return profile, list(scenarios)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> tuple[str, bool, str]:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1"], cwd=ROOT
    )
    digest_state = hashlib.sha256(revision.encode("ascii") + b"\0")
    digest_state.update(subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT))
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).split(b"\0")
    for encoded_path in sorted(path for path in untracked if path):
        relative = encoded_path.decode("utf-8", errors="surrogateescape")
        path = ROOT / relative
        digest_state.update(encoded_path + b"\0")
        if path.is_symlink():
            digest_state.update(str(path.readlink()).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest_state.update(path.read_bytes())
        digest_state.update(b"\0")
    return revision, not bool(status), digest_state.hexdigest()


def validate_unittest_evidence(path: Path, expected_source_content_sha256: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Reusable unittest evidence is missing: {path}")
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    if not re.search(r"(?m)^Ran [1-9][0-9]* tests? in ", text):
        raise ValueError("Reusable unittest evidence has no completed test count")
    if not re.search(r"(?m)^OK(?: \(|$)", text):
        raise ValueError("Reusable unittest evidence has no final OK result")
    if re.search(r"(?m)^FAILED(?: \(|$)", text):
        raise ValueError("Reusable unittest evidence contains a failed result")
    marker = re.search(r"(?m)^SOURCE_CONTENT_SHA256=([0-9a-f]{64})\r?$", text)
    if marker is None or marker.group(1) != expected_source_content_sha256:
        raise ValueError("Reusable unittest evidence does not match the candidate source content")
    if not re.search(r"(?m)^test_mw29_signature_001_metadata_authenticity_fails_closed .* \.\.\. ok\r?$", text):
        raise ValueError("Reusable unittest evidence does not prove the mandatory update-signature case")
    if not re.search(r"(?m)^test_mw210_01_capture_is_deterministic_and_deduplicated .* \.\.\. ok\r?$", text):
        raise ValueError("Reusable unittest evidence does not prove the mandatory v2.10 profile case")
    if not re.search(r"(?m)^test_mw211_01_continuous_catchup_contract_is_versioned .* \.\.\. ok\r?$", text):
        raise ValueError("Reusable unittest evidence does not prove the mandatory v2.11 catch-up case")
    return digest(path)


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "outputs/rehearsal/latest"))
    parser.add_argument("--scenario-shard-index", type=int)
    parser.add_argument("--scenario-shard-count", type=int)
    parser.add_argument(
        "--exclude-baseline",
        action="store_true",
        help="Exclude full Python and Rust baselines proved by separate CI jobs.",
    )
    parser.add_argument(
        "--reuse-unittest-evidence",
        help=(
            "Reference a successful full unittest evidence file instead of "
            "rerunning focused unittest scenarios."
        ),
    )
    parser.add_argument("--print-source-content-sha256", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--print-validation-profile", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--contract-profile",
        action="store_true",
        help="Run the explicit targeted-patch scenarios in the version work contract.",
    )
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if args.print_source_content_sha256:
        print(source_identity()[2])
        return 0
    profile, profile_scenarios = validation_profile(version)
    if args.print_validation_profile:
        print(profile)
        return 0
    if (args.scenario_shard_index is None) != (args.scenario_shard_count is None):
        parser.error("scenario shard index and count must be supplied together")
    if args.scenario_shard_count is not None and (
        args.scenario_shard_count < 1
        or not 0 <= args.scenario_shard_index < args.scenario_shard_count
    ):
        parser.error("require count >= 1 and 0 <= index < count")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    initial_revision, initial_source_clean, initial_source_content_sha256 = source_identity()
    python = sys.executable
    scenarios = [
        ("python-compile", [python, "-m", "compileall", "-q", "scripts"]),
        (
            "native-format",
            ["cargo", "fmt", "--check", "--manifest-path", "native-collector/Cargo.toml"],
        ),
        ("native-check", ["cargo", "check", "--manifest-path", "native-collector/Cargo.toml"]),
        ("native-tests", ["cargo", "test", "--manifest-path", "native-collector/Cargo.toml"]),
        (
            "bundled-native-version",
            [
                python,
                "-c",
                (
                    "import subprocess,sys,tomllib\n"
                    "from pathlib import Path\n"
                    "version=tomllib.loads(Path('pyproject.toml').read_text('utf-8'))"
                    "['project']['version']\n"
                    "if sys.platform not in ('darwin','win32'):\n"
                    " raise SystemExit(0)\n"
                    "suffix='.exe' if sys.platform=='win32' else ''\n"
                    "for name in ('memory-wuxian-collector','memory-wuxian-envelope'):\n"
                    " path=Path('bin')/(name+suffix)\n"
                    " assert path.is_file(), f'missing bundled executable: {path}'\n"
                    " result=subprocess.run([str(path),'--version'],text=True,"
                    "capture_output=True)\n"
                    " assert result.returncode==0, result.stderr\n"
                    " assert result.stdout.strip()==f'{name} {version}', "
                    "f'{path}: {result.stdout.strip()} != {name} {version}'\n"
                ),
            ],
        ),
        (
            "candidate-native-version",
            [
                python,
                "-c",
                (
                    "import subprocess,sys,tomllib\n"
                    "from pathlib import Path\n"
                    "version=tomllib.loads(Path('pyproject.toml').read_text('utf-8'))"
                    "['project']['version']\n"
                    "suffix='.exe' if sys.platform=='win32' else ''\n"
                    "for name in ('memory-wuxian-collector','memory-wuxian-envelope'):\n"
                    " path=Path('native-collector/target/debug')/(name+suffix)\n"
                    " assert path.is_file(), f'missing candidate executable: {path}'\n"
                    " result=subprocess.run([str(path),'--version'],text=True,capture_output=True)\n"
                    " assert result.returncode==0, result.stderr\n"
                    " assert result.stdout.strip()==f'{name} {version}', "
                    "f'{path}: {result.stdout.strip()} != {name} {version}'\n"
                ),
            ],
        ),
        (
            "macos-bundled-dashboard-signature",
            [
                python,
                "-c",
                (
                    "import subprocess,sys\n"
                    "from pathlib import Path\n"
                    "app=Path('assets/macos/Memory無限操作台.app')\n"
                    "if sys.platform=='darwin':\n"
                    " assert app.is_dir(), f'missing bundled dashboard: {app}'\n"
                    " result=subprocess.run(['codesign','--verify','--deep','--strict',"
                    "'--verbose=2',str(app)],text=True,capture_output=True)\n"
                    " assert result.returncode==0, result.stderr or result.stdout\n"
                ),
            ],
        ),
        (
            "python-regressions",
            [
                python, "-m", "unittest", "discover", "-s", "tests", "-v",
            ],
        ),
        (
            "owner-registration-preview-and-apply",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_owner_refresh_is_explicit_idempotent_and_predecessor_linked",
            ],
        ),
        (
            "stable-file-refresh",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_owner_refresh_rejects_unstable_source",
            ],
        ),
        (
            "no-change-zero-write",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_owner_refresh_is_explicit_idempotent_and_predecessor_linked",
            ],
        ),
        (
            "predecessor-chain",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_predecessor_query_marks_old_generation_as_superseded",
            ],
        ),
        (
            "secret-and-conflict-rejection",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_secret_and_unregistered_predecessor_are_rejected",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_reconstruct_conflict_has_no_partial_writes",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_owner_rejects_missing_linked_and_oversized_sources",
            ],
        ),
        (
            "maintenance-restart-and-idempotency",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_owner_maintenance_restart_is_idempotent",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_bounded_owner_refresh_is_failure_isolated",
            ],
        ),
        (
            "authenticated-project-evidence-exchange",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_independent_stream_imports_read_only_and_old_environment_is_unchanged",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_imported_evidence_does_not_create_owner",
            ],
        ),
        (
            "project-evidence-export-hash",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_independent_stream_imports_read_only_and_old_environment_is_unchanged",
            ],
        ),
        (
            "native-project-evidence-round-trip",
            [
                "cargo", "test", "--manifest-path", "native-collector/Cargo.toml",
                "--bin", "memory-wuxian-envelope",
                "project_evidence_kind_round_trips_and_is_stream_bound",
            ],
        ),
        (
            "native-cross-stream-rejection",
            [
                "cargo", "test", "--manifest-path", "native-collector/Cargo.toml",
                "--bin", "memory-wuxian-envelope",
                "project_evidence_kind_round_trips_and_is_stream_bound",
            ],
        ),
        (
            "authorized-real-encrypted-publish",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cloud_transport.MemoryCloudTransportTest.test_project_evidence_uses_authenticated_encrypted_transport",
            ],
        ),
        (
            "macos-dashboard",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_dashboard_federation.MemoryDashboardFederationTest.test_dashboard_html_keeps_existing_features_and_adds_federation_views",
            ],
        ),
        (
            "windows-installer-and-dashboard",
            [
                python, "-m", "unittest", "-v",
                "tests.test_v214_release_contract.V214ReleaseContractTest.test_cross_platform_surfaces_remain_registered",
            ],
        ),
        (
            "rollback",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_project_evidence.ProjectEvidenceTests.test_rollback_boundary_preserves_existing_evidence",
                "tests.test_macos_transaction.MacosTransactionTest.test_post_switch_failure_restores_old_skill_and_plist",
            ],
        ),
        (
            "archive-red-lines",
            [
                python, "-m", "unittest", "-v", "tests.test_guarded_features",
            ],
        ),
        (
            "v26-index-generation-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_platform_index_transaction",
                "tests.test_index_generations",
            ],
        ),
        (
            "v26-retrieval-benchmark-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_retrieval_v26",
            ],
        ),
        (
            "v26-index-generation-cli-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_index_generation_cli_is_preview_first_for_activation",
            ],
        ),
        (
            "token-usage-ledger",
            [
                python, "-m", "unittest", "-v", "tests.test_token_usage",
            ],
        ),
        (
            "static-contracts",
            [
                python, "-c",
                (
                    "from pathlib import Path;"
                    "w=Path('scripts/install_codex_autosync_windows.py').read_text('utf-8');"
                    "a=Path('scripts/install_auto_update.py').read_text('utf-8');"
                    "d=Path('dashboard/index.html').read_text('utf-8');"
                    "m=Path('scripts/memory_dashboard.py').read_text('utf-8');"
                    "assert '-EncodedCommand' not in w+a;"
                    "assert 'powershell.exe' not in a.lower();"
                    "assert 'endswith((\"powershell.exe\", \"powershell\"))' in w;"
                    "assert '/api/events' in m and \"EventSource('/api/events')\" in d;"
                    "assert 'project-filter' in d and 'source-filter' in d and 'device-filter' in d;"
                    "assert 'reported_total_tokens' in d and 'reported-tokens-' in d"
                ),
            ],
        ),
        (
            "documentation-contract",
            [python, "scripts/check_documentation_contract.py"],
        ),
        (
            "architecture-contract",
            [python, "scripts/check_architecture_contract.py"],
        ),
        (
            "configuration-v1-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_configuration",
                "tests.test_memory_v25_cli",
            ],
        ),
        (
            "device-capability-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_capabilities",
            ],
        ),
        (
            "dashboard-system-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_dashboard_system",
            ],
        ),
        (
            "desktop-dashboard-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_dashboard_shortcut",
            ],
        ),
        (
            "macos-stable-runtime-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_platform_runtime",
                "tests.test_cloud_scheduler.CloudSchedulerTest.test_macos_plist_is_one_shot_and_uses_exact_paths",
            ],
        ),
        (
            "macos-update-transaction-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_macos_transaction",
            ],
        ),
        (
            "macos-routine-auto-update-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_auto_update",
            ],
        ),
        (
            "collector-startup-defers-semantic-worker-contract",
            [
                python, "-c",
                (
                    "from pathlib import Path;"
                    "s=Path('native-collector/src/lib.rs').read_text('utf-8');"
                    "p=s.split('#[cfg(test)]',1)[0];"
                    "assert 'fn sync_startup_batch' in s;"
                    "assert 'self.sync_batch(paths)' in p;"
                    "assert 'store.sync_startup_batch(initial_paths)?' in s;"
                    "assert 'self.maybe_create_level_one_job()?' in p;"
                    "assert 'semantic_dispatch.py' not in p;"
                    "assert 'run_one_shot_summary' not in p;"
                    "assert 'sync_batch_with_semantic_worker' not in p"
                ),
            ],
        ),
        (
            "collector-coalesced-backup-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_semantic_backfill_drains_coalesced_backup_debt_without_summary_jobs",
                "tests.test_memory_cli.MemoryCliTest.test_dashboard_reports_pending_coalesced_backup_as_recoverable_debt",
            ],
        ),
        (
            "collector-health-and-waterline-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_archive_waterline",
                "tests.test_memory_cli.MemoryCliTest.test_dashboard_health_reports_collector_freshness_alerts",
            ],
        ),
        (
            "daily-archive-tooltip-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_dashboard_achievement_settings_are_local_and_hide_empty_levels",
            ],
        ),
        (
            "environment-schema-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_environment_schema_contract",
            ],
        ),
        (
            "semantic-runtime-environment-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_semantic_runtime_contract",
                "tests.test_guarded_features",
                "tests.test_semantic_e5_worker",
            ],
        ),
        (
            "environment-registry-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment",
            ],
        ),
        (
            "environment-dashboard-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_dashboard_environment",
            ],
        ),
        (
            "environment-exchange-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_exchange",
            ],
        ),
        (
            "environment-governance-proposal-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_governance",
            ],
        ),
        (
            "environment-product-evolution-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_evolution",
            ],
        ),
        (
            "environment-governance-ai-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_governance_ai",
                "tests.test_governance_ai_scheduler",
            ],
        ),
        (
            "environment-incoming-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_incoming",
            ],
        ),
        (
            "environment-binding-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_bindings",
            ],
        ),
        (
            "environment-conflict-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_conflicts",
                "tests.test_memory_environment_promotions",
            ],
        ),
        (
            "environment-install-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_environment_rules",
                "tests.test_memory_environment_skills",
            ],
        ),
        (
            "environment-cloud-scheduler-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_cloud_scheduler",
                "tests.test_memory_cloud_transport",
            ],
        ),
        (
            "v27-job-recovery-contract",
            [python, "-m", "unittest", "-v", "tests.test_memory_jobs"],
        ),
        (
            "v27-capture-independence-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_jobs.MaintenanceQueueTests.test_mw27_capture_001_service_telemetry_advances_while_job_quarantined",
                "tests.test_memory_cli.MemoryCliTest.test_native_collector_matches_python_storage_contract",
            ],
        ),
        (
            "v27-diagnostic-redaction-contract",
            [python, "-m", "unittest", "-v", "tests.test_memory_diagnostics"],
        ),
        (
            "v27-no-ai-side-effect-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_semantic_dispatch",
                "tests.test_memory_jobs.MaintenanceQueueTests.test_mw27_round_boundary_and_no_side_effect_001_only_marks_semantic_ready",
            ],
        ),
        (
            "v28-exact-byte-content-contract",
            [python, "-m", "unittest", "-v", "tests.test_memory_content_store"],
        ),
        (
            "v28-resumable-transfer-contract",
            [python, "-m", "unittest", "-v", "tests.test_memory_resumable_sync"],
        ),
        (
            "v28-preview-rollback-cli-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_content_shadow_cli_is_preview_first_and_resumable",
            ],
        ),
        (
            "v29-readonly-interface-parity-contract",
            [python, "-m", "unittest", "-v", "tests.test_readonly_interfaces"],
        ),
        (
            "v29-update-governance-contract",
            [python, "-m", "unittest", "-v", "tests.test_update_governance", "tests.test_auto_update"],
        ),
        (
            "v29-summary-budget-contract",
            [python, "-m", "unittest", "-v", "tests.test_summary_budget"],
        ),
        (
            "v210-personal-environment-profile-contract",
            [python, "-m", "unittest", "-v", "tests.test_memory_environment_profiles"],
        ),
        (
            "v211-continuous-catchup-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_v211_release_contract",
                "tests.test_collector_activation",
                "tests.test_maintenance_scheduler",
                "tests.test_semantic_plan",
                "tests.test_semantic_backfill",
            ],
        ),
        (
            "v2116-runtime-effect-gate-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_runtime_effect_gate",
                "tests.test_dashboard_shortcut",
                "tests.test_v211_release_contract",
                "tests.test_release_workflow_gate",
            ],
        ),
        (
            "v212-federated-daily-metrics-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_v212_release_contract",
                "tests.test_daily_metrics",
                "tests.test_token_usage",
                "tests.test_memory_federation",
                "tests.test_memory_dashboard_federation",
            ],
        ),
        (
            "v2121-token-ledger-v1-upgrade-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_native_collector_matches_python_storage_contract",
                "tests.test_v2121_release_contract",
                "tests.test_token_usage",
                "tests.test_daily_metrics",
            ],
        ),
        (
            "v2122-dashboard-cursor-summary-job-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_v2122_release_contract",
                "tests.test_memory_cli.MemoryCliTest.test_native_collector_matches_python_storage_contract",
                "tests.test_memory_cli.MemoryCliTest.test_pending_parent_jobs_reserve_children_and_repair_legacy_overlap",
                "tests.test_memory_cli.MemoryCliTest.test_dashboard_achievement_settings_are_local_and_hide_empty_levels",
                "tests.test_memory_dashboard_federation",
            ],
        ),
        (
            "v2123-semantic-drain-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_semantic_backfill",
                "tests.test_v2123_release_contract",
            ],
        ),
        (
            "v2124-lossless-audit-lock-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_lossless_summary_payload_round_trip",
                "tests.test_memory_cli.MemoryCliTest.test_lossless_summary_payload_preserves_sparse_and_null_fields",
                "tests.test_memory_cli.MemoryCliTest.test_lossless_parent_summary_payload_round_trip",
                "tests.test_memory_cli.MemoryCliTest.test_lossless_parent_summary_payload_preserves_sparse_and_null_fields",
                "tests.test_memory_cli.MemoryCliTest.test_lossless_payload_rejects_malformed_presence_bitmap",
                "tests.test_memory_cli.MemoryCliTest.test_heartbeat_owns_archive_lock_for_consistent_audit",
                "tests.test_memory_cli.MemoryCliTest.test_heartbeat_cli_does_not_wrap_its_owned_archive_lock",
                "tests.test_semantic_backfill",
                "tests.test_memory_jobs",
                "tests.test_defect_workbook_contract",
                "tests.test_v2124_release_contract",
            ],
        ),
        (
            "v2125-duplicate-pending-round-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_round_recovery_keeps_duplicate_number_pending_per_conversation",
                "tests.test_memory_cli.MemoryCliTest.test_concurrent_conversations_keep_rounds_and_replies_isolated",
                "tests.test_defect_workbook_contract",
                "tests.test_v2125_release_contract",
            ],
        ),
        (
            "v2126-shared-round-completion-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_round_recovery_keeps_duplicate_number_pending_per_conversation",
                "tests.test_memory_cli.MemoryCliTest.test_concurrent_conversations_keep_rounds_and_replies_isolated",
                "tests.test_defect_workbook_contract",
                "tests.test_v2126_release_contract",
            ],
        ),
        (
            "v2127-live-shared-round-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_live_append_waits_for_last_conversation_sharing_round",
                "tests.test_memory_cli.MemoryCliTest.test_round_recovery_keeps_duplicate_number_pending_per_conversation",
                "tests.test_defect_workbook_contract",
                "tests.test_v2127_release_contract",
            ],
        ),
        ("diff-check", ["git", "diff", "--check"]),
    ]
    if args.contract_profile:
        if profile != "targeted-patch":
            parser.error("the current work contract does not declare targeted-patch")
        catalog = {scenario_id: command for scenario_id, command in scenarios}
        unknown = sorted(set(profile_scenarios) - set(catalog))
        if unknown:
            parser.error(f"unknown contract rehearsal scenarios: {', '.join(unknown)}")
        scenarios = [(scenario_id, catalog[scenario_id]) for scenario_id in profile_scenarios]
    baseline_ids = {
        "python-compile",
        "native-format",
        "native-check",
        "native-tests",
        "bundled-native-version",
        "python-regressions",
    }
    unittest_scenario_ids = {
        scenario_id
        for scenario_id, command in scenarios
        if len(command) >= 3 and command[1:3] == ["-m", "unittest"]
    }
    if args.exclude_baseline:
        scenarios = [
            scenario for scenario in scenarios if scenario[0] not in baseline_ids
        ]
    total_scenarios = len(scenarios)
    if args.scenario_shard_count is not None:
        scenarios = scenarios[
            args.scenario_shard_index :: args.scenario_shard_count
        ]
        if not scenarios:
            parser.error("selected scenario shard is empty")
    results = []
    for scenario_id, command in scenarios:
        if (
            args.reuse_unittest_evidence
            and scenario_id in unittest_scenario_ids
        ):
            reused_evidence = Path(args.reuse_unittest_evidence).resolve()
            source_sha256 = validate_unittest_evidence(reused_evidence, initial_source_content_sha256)
            log = output / f"{scenario_id}.log"
            log.write_text(
                "Covered by the successful full unittest suite.\n"
                f"source={reused_evidence}\n"
                f"source_sha256={source_sha256}\n",
                encoding="utf-8",
            )
            results.append({
                "id": scenario_id,
                "status": "passed",
                "command": command,
                "evidence": log.name,
                "evidence_sha256": digest(log),
                "reused_evidence_sha256": source_sha256,
            })
            continue
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        log = output / f"{scenario_id}.log"
        log.write_text(
            f"$ {subprocess.list2cmdline(command)}\n\n"
            f"[stdout]\n{completed.stdout}\n[stderr]\n{completed.stderr}\n",
            encoding="utf-8",
        )
        results.append({
            "id": scenario_id,
            "status": "passed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "command": command,
            "evidence": log.name,
            "evidence_sha256": digest(log),
        })
    for result in results:
        evidence = output / result["evidence"]
        if not evidence.is_file() or digest(evidence) != result["evidence_sha256"]:
            result["status"] = "failed"
            result["evidence_error"] = "missing or mismatched rehearsal evidence"
    revision, source_clean, source_content_sha256 = source_identity()
    if source_content_sha256 != initial_source_content_sha256:
        raise ValueError("candidate source changed during release rehearsal")
    report = {
        "format_version": 1,
        "release_version": version,
        "source_revision": initial_revision,
        "source_clean": initial_source_clean,
        "source_content_sha256": source_content_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(item["status"] == "passed" for item in results) else "failed",
        "required_scenarios": len(scenarios),
        "completed_scenarios": len(results),
        "scenario_catalog_size": total_scenarios,
        "validation_profile": profile if args.contract_profile else "full",
        "scenario_shard_index": args.scenario_shard_index,
        "scenario_shard_count": args.scenario_shard_count,
        "baseline_excluded": args.exclude_baseline,
        "scenarios": results,
    }
    report_path = output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
