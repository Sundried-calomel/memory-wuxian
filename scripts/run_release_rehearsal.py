#!/usr/bin/env python3
"""Run the auditable Memory Wuxian release rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "outputs/rehearsal/latest"))
    parser.add_argument("--scenario-shard-index", type=int)
    parser.add_argument("--scenario-shard-count", type=int)
    parser.add_argument(
        "--exclude-baseline",
        action="store_true",
        help="Exclude full Python and Rust baselines proved by separate CI jobs.",
    )
    args = parser.parse_args()
    if (args.scenario_shard_index is None) != (args.scenario_shard_count is None):
        parser.error("scenario shard index and count must be supplied together")
    if args.scenario_shard_count is not None and (
        args.scenario_shard_count < 1
        or not 0 <= args.scenario_shard_index < args.scenario_shard_count
    ):
        parser.error("require count >= 1 and 0 <= index < count")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
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
            "archive-red-lines",
            [
                python, "-m", "unittest", "-v", "tests.test_guarded_features",
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
                    "assert 'powershell.exe' not in w.lower()+a.lower();"
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
                    "s=Path('native-collector/src/main.rs').read_text('utf-8');"
                    "assert 'fn sync_startup_batch' in s;"
                    "assert 'self.sync_batch_with_semantic_worker(paths, false)' in s;"
                    "assert 'store.sync_startup_batch(initial_paths)?' in s;"
                    "assert '.arg(\"--no-backup\")' in s"
                ),
            ],
        ),
        (
            "collector-coalesced-backup-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_memory_cli.MemoryCliTest.test_semantic_backfill_drains_coalesced_backup_debt_without_summary_jobs",
                "tests.test_memory_cli.MemoryCliTest.test_dashboard_reports_pending_coalesced_backup",
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
        ("diff-check", ["git", "diff", "--check"]),
    ]
    baseline_ids = {
        "python-compile",
        "native-format",
        "native-check",
        "native-tests",
        "bundled-native-version",
        "python-regressions",
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
    report = {
        "format_version": 1,
        "release_version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(item["status"] == "passed" for item in results) else "failed",
        "required_scenarios": len(scenarios),
        "completed_scenarios": len(results),
        "scenario_catalog_size": total_scenarios,
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
