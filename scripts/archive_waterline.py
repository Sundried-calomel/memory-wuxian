#!/usr/bin/env python3
"""Verify that MemoryWuxian covers retained Codex events through a cutoff."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def required_source_size(path: Path, cutoff: datetime) -> int:
    """Return the byte boundary containing every complete event through cutoff."""

    required = 0
    offset = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            offset += len(raw_line)
            try:
                event = json.loads(raw_line)
                timestamp = parse_time(str(event["timestamp"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if timestamp <= cutoff:
                required = offset
    return required


def cursor_by_source(archive_root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in (archive_root / "imports" / "codex").glob("*.json"):
        if path.name == "collector-telemetry.json":
            continue
        try:
            cursor = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = str(cursor.get("source_path") or "")
        if source:
            result[source] = cursor
    return result


def evaluate(
    archive_root: Path,
    sessions_root: Path,
    cutoff: datetime,
) -> dict[str, Any]:
    cursors = cursor_by_source(archive_root)
    lagging = []
    checked = 0
    for source in sorted(sessions_root.rglob("rollout-*.jsonl")):
        required = required_source_size(source, cutoff)
        if required == 0:
            continue
        checked += 1
        cursor = cursors.get(str(source))
        archived_size = int((cursor or {}).get("source_size") or 0)
        if archived_size < required:
            lagging.append(
                {
                    "source_path": str(source),
                    "required_source_size": required,
                    "archived_source_size": archived_size,
                    "missing_bytes_through_cutoff": required - archived_size,
                }
            )
    return {
        "status": "covered" if not lagging else "lagging",
        "cutoff": cutoff.astimezone(timezone.utc).isoformat(),
        "checked_sources": checked,
        "lagging_sources": lagging,
    }


def backfill(
    *,
    collector: Path,
    archive_root: Path,
    config: Path,
    sessions_root: Path,
    sources: Iterable[Path],
) -> None:
    command = [
        str(collector),
        "--archive-root",
        str(archive_root),
        "--config",
        str(config),
        "--sessions-root",
        str(sessions_root),
        "--once",
    ]
    for source in sources:
        command.extend(["--session-file", str(source)])
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--sessions-root", default="~/.codex/sessions")
    parser.add_argument("--cutoff", required=True, help="ISO-8601 report cutoff")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()

    archive_root = Path(args.archive_root).expanduser().resolve()
    sessions_root = Path(args.sessions_root).expanduser().resolve()
    skill_root = Path(args.skill_root).expanduser().resolve()
    cutoff = parse_time(args.cutoff)
    if cutoff.tzinfo is None:
        raise SystemExit("--cutoff must include a timezone")

    result = evaluate(archive_root, sessions_root, cutoff)
    if result["lagging_sources"] and args.backfill:
        backfill(
            collector=skill_root / "bin" / "memory-wuxian-collector",
            archive_root=archive_root,
            config=skill_root / "config.yaml",
            sessions_root=sessions_root,
            sources=[
                Path(item["source_path"]) for item in result["lagging_sources"]
            ],
        )
        result = evaluate(archive_root, sessions_root, cutoff)
        result["backfill_attempted"] = True
    else:
        result["backfill_attempted"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "covered" else 2


if __name__ == "__main__":
    raise SystemExit(main())
