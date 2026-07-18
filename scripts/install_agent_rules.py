#!/usr/bin/env python3
"""Install or update Memory无限 rules in a workspace AGENTS.md."""

from __future__ import annotations

import argparse
from pathlib import Path


START = "<!-- memory-wuxian:rules:start -->"
END = "<!-- memory-wuxian:rules:end -->"


def upsert_rules(agents_file: Path, template_file: Path) -> str:
    block = template_file.read_text(encoding="utf-8").strip()
    if not block.startswith(START) or not block.endswith(END):
        raise ValueError("agent rules template is missing its managed markers")

    existing = agents_file.read_text(encoding="utf-8") if agents_file.exists() else ""
    start = existing.find(START)
    end = existing.find(END)
    if (start == -1) != (end == -1):
        raise ValueError("AGENTS.md contains an incomplete memory-wuxian rules block")

    if start != -1:
        end += len(END)
        updated = existing[:start].rstrip() + "\n\n" + block + existing[end:]
        action = "updated"
    else:
        prefix = existing.rstrip()
        updated = (prefix + "\n\n" if prefix else "") + block + "\n"
        action = "installed"

    agents_file.parent.mkdir(parents=True, exist_ok=True)
    agents_file.write_text(updated, encoding="utf-8", newline="\n")
    return action


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents-file", required=True, type=Path)
    args = parser.parse_args()
    template = Path(__file__).resolve().parent.parent / "templates" / "agents-workspace.md"
    action = upsert_rules(args.agents_file.resolve(), template)
    print(f"{action}: {args.agents_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
