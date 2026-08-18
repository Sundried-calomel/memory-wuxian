#!/usr/bin/env python3
"""Fail-closed, bounded runtime effect gate for collector lifecycle v2.16."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from collector_lifecycle import ProcessInspector, inspect_process, verify_collector_lifecycle


def _inspect_windows_shortcut(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    shortcut = Path(path)
    return {
        "configured": True,
        "exists": shortcut.is_file(),
        "path_name": shortcut.name,
    }


def check_runtime_effects(
    root: str | Path,
    config_path: str | Path | None = None,
    *,
    now: datetime | None = None,
    supervisor_max_age_seconds: float = 300.0,
    skill_root: str | Path | None = None,
    launcher_config: str | Path | None = None,
    windows_shortcut: str | Path | None = None,
    lifecycle_manifest: str | Path | None = None,
    telemetry_path: str | Path | None = None,
    run_synthetic_probe: bool = False,
    probe_parent: str | Path | None = None,
    process_inspector: ProcessInspector = inspect_process,
) -> dict[str, Any]:
    """Check real collector effect without traversing the archive.

    Legacy arguments remain accepted so callers can migrate independently. They
    do not expand this gate into summary, semantic, backup, or cloud domains.
    """
    del config_path, skill_root, launcher_config
    archive_root = Path(root)
    lifecycle = (
        Path(lifecycle_manifest)
        if lifecycle_manifest is not None
        else archive_root / "imports" / "codex" / "collector-lifecycle.json"
    )
    telemetry = (
        Path(telemetry_path)
        if telemetry_path is not None
        else archive_root / "imports" / "codex" / "collector-telemetry.json"
    )
    collector = verify_collector_lifecycle(
        lifecycle,
        telemetry,
        run_probe=run_synthetic_probe,
        probe_parent=probe_parent,
        process_inspector=process_inspector,
        now=now,
        max_age_seconds=supervisor_max_age_seconds,
    )
    shortcut = _inspect_windows_shortcut(windows_shortcut)
    reasons = list(collector["reason_codes"])
    if shortcut is not None and not shortcut["exists"]:
        reasons.append({"code": "collector-windows-shortcut-missing"})
    return {
        "format": "memory-wuxian-runtime-effect-v2",
        "ok": not reasons,
        "reason_codes": reasons,
        "collector": collector,
        "windows_shortcut": shortcut,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--config")
    parser.add_argument("--collector-lifecycle")
    parser.add_argument("--collector-telemetry")
    parser.add_argument("--synthetic-probe", action="store_true")
    parser.add_argument("--probe-parent")
    parser.add_argument("--supervisor-max-age-seconds", type=float, default=300.0)
    parser.add_argument("--skill-root")
    parser.add_argument("--launcher-config")
    parser.add_argument("--windows-shortcut")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = check_runtime_effects(
        args.root,
        args.config,
        supervisor_max_age_seconds=args.supervisor_max_age_seconds,
        skill_root=args.skill_root,
        launcher_config=args.launcher_config,
        windows_shortcut=args.windows_shortcut,
        lifecycle_manifest=args.collector_lifecycle,
        telemetry_path=args.collector_telemetry,
        run_synthetic_probe=args.synthetic_probe,
        probe_parent=args.probe_parent,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
