#!/usr/bin/env python3
"""Validate Memory Wuxian source ownership and dependency boundaries."""

from __future__ import annotations

import ast
import fnmatch
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "module-architecture.json"


def normalized(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3] + "/")
    return fnmatch.fnmatchcase(path, pattern)


def production_files(contract: dict) -> list[str]:
    files: list[str] = []
    for source in contract["source_roots"]:
        root = ROOT / source["path"]
        extensions = set(source["extensions"])
        files.extend(
            normalized(path)
            for path in root.rglob("*")
            if path.is_file() and path.suffix in extensions
        )
    return sorted(files)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def validate() -> list[str]:
    contract = json.loads(MANIFEST.read_text(encoding="utf-8"))
    modules = contract["modules"]
    errors: list[str] = []
    owner_by_path: dict[str, str] = {}

    for path in production_files(contract):
        owners = [
            module["id"]
            for module in modules
            if any(matches(path, pattern) for pattern in module["patterns"])
        ]
        if not owners:
            errors.append(f"unowned production file: {path}")
        elif len(owners) > 1:
            errors.append(f"multiple owners for {path}: {', '.join(owners)}")
        else:
            owner_by_path[path] = owners[0]

    declared_ids = [module["id"] for module in modules]
    if len(declared_ids) != len(set(declared_ids)):
        errors.append("module ids must be unique")

    forbidden = contract["python_dependency_rules"]["forbidden_imports"]
    for path, owner in owner_by_path.items():
        if not path.endswith(".py"):
            continue
        imports = imported_modules(ROOT / path)
        violations = sorted(imports.intersection(forbidden.get(owner, [])))
        for imported in violations:
            errors.append(f"forbidden dependency: {path} ({owner}) -> {imported}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Architecture contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Architecture contract passed: every production file has one owner.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
