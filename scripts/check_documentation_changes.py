#!/usr/bin/env python3
"""Require release documentation to accompany functional repository changes."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = {
    "README.md",
    "README.zh-CN.md",
    "README.ja.md",
    "CHANGELOG.md",
    "docs/documentation-contract.json",
}
FUNCTIONAL_PREFIXES = (
    "scripts/",
    "native-collector/",
    "dashboard/",
    "packaging/",
    "prompts/",
    "schemas/",
    "templates/",
)
FUNCTIONAL_FILES = {"config.yaml", "pyproject.toml", "SKILL.md"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()

    output = subprocess.check_output(
        ["git", "diff", "--name-only", f"{args.base}...{args.head}"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    changed = {line.replace("\\", "/") for line in output.splitlines() if line}
    functional = {
        path
        for path in changed
        if path in FUNCTIONAL_FILES or path.startswith(FUNCTIONAL_PREFIXES)
    }
    functional -= {
        "scripts/check_documentation_contract.py",
        "scripts/check_documentation_changes.py",
    }
    if not functional:
        print("Documentation change gate passed: no functional files changed.")
        return 0

    missing = sorted(REQUIRED_DOCS - changed)
    if missing:
        print("Functional files changed:")
        for path in sorted(functional):
            print(f"- {path}")
        print("Required documentation files not changed:")
        for path in missing:
            print(f"- {path}")
        return 1

    print(
        "Documentation change gate passed: functional changes include all "
        "localized READMEs, CHANGELOG.md, and the feature contract."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
