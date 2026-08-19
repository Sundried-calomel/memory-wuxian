#!/usr/bin/env python3
"""Merge missing release defaults into an existing Memory Wuxian config."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from platform_atomic import atomic_replace_bytes


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def merge_missing(current: dict[str, Any], defaults: dict[str, Any], prefix: str = "") -> list[str]:
    added: list[str] = []
    for key, default_value in defaults.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in current:
            current[key] = default_value
            added.append(path)
            continue
        if isinstance(current[key], dict) and isinstance(default_value, dict):
            added.extend(merge_missing(current[key], default_value, path))
    return added


def atomic_write(path: Path, payload: bytes) -> None:
    atomic_replace_bytes(path, payload, create_parent=False)


def migrate_config(current_path: Path, defaults_path: Path, *, apply: bool) -> dict[str, Any]:
    current_path = current_path.resolve()
    defaults_path = defaults_path.resolve()
    current_bytes = current_path.read_bytes()
    defaults_bytes = defaults_path.read_bytes()
    current = yaml.safe_load(current_bytes.decode("utf-8"))
    defaults = yaml.safe_load(defaults_bytes.decode("utf-8"))
    if not isinstance(current, dict) or not isinstance(defaults, dict):
        raise ValueError("current and default configs must contain YAML mappings")
    added = merge_missing(current, defaults)
    output = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).encode("utf-8")
    before_sha = sha256_bytes(current_bytes)
    after_sha = sha256_bytes(output)
    receipt = {
        "format": "memory-wuxian-config-migration-v1",
        "status": "unchanged" if not added else ("applied" if apply else "preview"),
        "added_keys": added,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "defaults_sha256": sha256_bytes(defaults_bytes),
    }
    if apply and added:
        backup = current_path.with_name(f"{current_path.name}.pre-migration-{before_sha[:12]}")
        if not backup.exists():
            atomic_write(backup, current_bytes)
        atomic_write(current_path, output)
        receipt["rollback"] = str(backup)
        receipt_path = current_path.with_name("config-migration-receipt.json")
        atomic_write(
            receipt_path,
            (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        receipt["receipt"] = str(receipt_path)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", required=True)
    parser.add_argument("--defaults", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = migrate_config(Path(args.current), Path(args.defaults), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
