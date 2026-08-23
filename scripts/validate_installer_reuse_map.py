#!/usr/bin/env python3
"""Fail closed when the approved installer replan reuse decisions drift."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


DISPOSITIONS = {
    "reuse_exact",
    "reuse_with_extension",
    "reuse_with_correction",
    "integrate_existing",
    "replace_composition",
}


class ReuseMapError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_reuse_map(
    root: Path,
    map_path: Path,
    *,
    step: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = json.loads(map_path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ReuseMapError("unsupported reuse-map schema")
    if step not in document.get("enforced_steps", []):
        raise ReuseMapError(f"step is not governed by the reuse map: {step}")
    receipt = document.get("replan_receipt", {})
    receipt_path = root / str(receipt.get("path", ""))
    if not receipt_path.is_file() or sha256_file(receipt_path) != receipt.get("sha256"):
        raise ReuseMapError("replan receipt is missing or drifted")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ReuseMapError("reuse map contains no artifacts")
    seen: set[str] = set()
    exact_verified: list[str] = []
    corrections_verified: list[str] = []
    composition_changed = False
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {
            "path", "disposition", "baseline_sha256"
        }:
            raise ReuseMapError("reuse artifact must be a closed object")
        relative = artifact["path"]
        disposition = artifact["disposition"]
        baseline = artifact["baseline_sha256"]
        if relative in seen or disposition not in DISPOSITIONS:
            raise ReuseMapError("reuse artifact path or disposition is invalid")
        seen.add(relative)
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ReuseMapError(f"reuse artifact escapes project root: {relative}") from exc
        if not path.is_file():
            raise ReuseMapError(f"reuse artifact is missing: {relative}")
        current = sha256_file(path)
        if disposition == "reuse_exact":
            if current != baseline:
                raise ReuseMapError(f"approved exact-reuse artifact drifted: {relative}")
            exact_verified.append(relative)
        elif disposition == "reuse_with_correction":
            if current == baseline:
                raise ReuseMapError(f"approved correction was not applied: {relative}")
            corrections_verified.append(relative)
        elif disposition == "replace_composition":
            composition_changed = current != baseline
    replacement = document.get("replacement_contract")
    if not isinstance(replacement, dict) or set(replacement) != {
        "path", "forbidden_classes", "required_mutation_classes", "required_methods"
    }:
        raise ReuseMapError("replacement contract is missing or open-ended")
    replacement_path = (root / replacement["path"]).resolve()
    tree = ast.parse(replacement_path.read_text(encoding="utf-8"), filename=str(replacement_path))
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    forbidden = set(replacement["forbidden_classes"])
    if forbidden.intersection(classes):
        raise ReuseMapError("the rejected composed mutation class is still present")
    required_classes = replacement["required_mutation_classes"]
    required_methods = set(replacement["required_methods"])
    if not isinstance(required_classes, list) or len(required_classes) < 2:
        raise ReuseMapError("replacement contract has no bounded mutation set")
    for class_name in required_classes:
        node = classes.get(class_name)
        if node is None:
            raise ReuseMapError(f"required bounded mutation class is missing: {class_name}")
        methods = {item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing_methods = sorted(required_methods - methods)
        if missing_methods:
            raise ReuseMapError(
                f"bounded mutation {class_name} lacks methods: {', '.join(missing_methods)}"
            )
    if not composition_changed:
        raise ReuseMapError("the rejected composed mutation has not been replaced")
    if state is not None:
        invalidated_steps = document.get("invalidated_receipts", [])
        current_index = invalidated_steps.index(step)
        for index, invalidated in enumerate(invalidated_steps):
            entry = state.get("steps", {}).get(invalidated, {})
            if index > current_index and entry.get("status") == "completed":
                raise ReuseMapError(f"invalidated downstream receipt became completed early: {invalidated}")
    return {
        "status": "passed",
        "step": step,
        "exact_reuse_verified": exact_verified,
        "corrections_verified": corrections_verified,
        "composition_changed": True,
        "bounded_mutations": required_classes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state else None
    result = validate_reuse_map(
        args.root.resolve(), args.map_path.resolve(), step=args.step, state=state
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
