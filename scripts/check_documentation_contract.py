#!/usr/bin/env python3
"""Fail when public Memory Wuxian features are missing from localized READMEs."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "documentation-contract.json"


def public_cli_commands() -> set[str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from memory_cli import build_parser

    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise RuntimeError("memory_cli.py does not expose an argparse subcommand set")


def main() -> int:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    readmes = [ROOT / name for name in contract["readmes"]]
    failures: list[str] = []
    commands = public_cli_commands()

    for path in readmes:
        text = path.read_text(encoding="utf-8")
        missing_commands = sorted(command for command in commands if command not in text)
        if missing_commands:
            failures.append(
                f"{path.name}: missing CLI commands: {', '.join(missing_commands)}"
            )
        for feature in contract["features"]:
            missing_tokens = [
                token for token in feature["required_tokens"] if token not in text
            ]
            if missing_tokens:
                failures.append(
                    f"{path.name}: feature {feature['id']} is missing: "
                    + ", ".join(missing_tokens)
                )

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    if contract.get("reviewed_version") != version:
        failures.append(
            "docs/documentation-contract.json: reviewed_version "
            f"{contract.get('reviewed_version')!r} does not match package version {version!r}"
        )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version} -" not in changelog:
        failures.append(f"CHANGELOG.md: missing current version heading for {version}")

    if failures:
        print("Documentation contract failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        f"Documentation contract passed: {len(commands)} CLI commands and "
        f"{len(contract['features'])} feature groups in {len(readmes)} READMEs."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
