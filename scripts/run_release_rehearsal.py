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
    args = parser.parse_args()
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
            "desktop-dashboard-contract",
            [
                python, "-m", "unittest", "-v",
                "tests.test_dashboard_shortcut",
            ],
        ),
        ("diff-check", ["git", "diff", "--check"]),
    ]
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
            "evidence": str(log),
            "evidence_sha256": digest(log),
        })
    report = {
        "format_version": 1,
        "release_version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all(item["status"] == "passed" for item in results) else "failed",
        "required_scenarios": len(scenarios),
        "completed_scenarios": len(results),
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
