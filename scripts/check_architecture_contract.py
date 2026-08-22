#!/usr/bin/env python3
"""Validate Memory Wuxian source ownership and dependency boundaries."""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "module-architecture.json"
REQUIRED_CAPTURE_PATTERNS = {
    "scripts/collector_lifecycle.py",
    "native-collector/src/lib.rs",
    "native-collector/src/runtime.rs",
    "native-collector/src/locking.rs",
    "native-collector/src/telemetry.rs",
    "native-collector/src/source/**",
    "native-collector/src/store/**",
    "native-collector/src/bin/memory-wuxian-core-launcher.rs",
}
REQUIRED_CAPTURE_FORBIDDEN_DEPENDENCIES = {
    "product-shell",
    "control-plane",
    "memory-plane",
    "environment-plane",
    "project-evidence-plane",
    "exchange-plane",
}
REQUIRED_CAPTURE_FORBIDDEN_CAPABILITIES = {
    "control",
    "memory",
    "exchange",
    "environment",
    "project",
    "ui",
    "ai",
}


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
        extensions = set(source.get("extensions", []))
        all_files = source.get("all_files") is True
        files.extend(
            normalized(path)
            for path in root.rglob("*")
            if path.is_file() and (all_files or path.suffix in extensions)
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


def imported_rust_symbols(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    symbols: set[str] = set()
    for use_path in re.findall(
        r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([^;]+);",
        source,
    ):
        symbols.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", use_path))
    symbols.update(
        re.findall(
            r"(?m)^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
            source,
        )
    )
    symbols.update(
        re.findall(
            r"(?m)^\s*extern\s+crate\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
            source,
        )
    )
    return symbols


def validate_schema(contract: dict) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") != 2:
        errors.append("schema_version must be 2")

    source_roots = contract.get("source_roots")
    if not isinstance(source_roots, list):
        errors.append("source_roots must be a list")
    else:
        for source in source_roots:
            if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                errors.append("each source root must declare a path")
                continue
            extensions = source.get("extensions")
            if source.get("all_files") is not True and not (
                isinstance(extensions, list)
                and all(isinstance(extension, str) for extension in extensions)
            ):
                errors.append(
                    f"source root {source['path']} must declare extensions or all_files"
                )

    modules = contract.get("modules")
    if not isinstance(modules, list):
        return errors + ["modules must be a list"]

    declared_ids = [module.get("id") for module in modules]
    if len(declared_ids) != len(set(declared_ids)):
        errors.append("module ids must be unique")
    known_ids = {module_id for module_id in declared_ids if isinstance(module_id, str)}

    required_fields = {
        "id": str,
        "architecture_owner": str,
        "criticality": str,
        "failure_domain": str,
        "allowed_dependencies": list,
        "forbidden_dependencies": list,
        "patterns": list,
    }
    for module in modules:
        module_id = module.get("id", "<missing>")
        for field, expected_type in required_fields.items():
            value = module.get(field)
            if not isinstance(value, expected_type) or (
                expected_type is str and not value
            ):
                errors.append(
                    f"module {module_id} field {field} must be a non-empty "
                    f"{expected_type.__name__}"
                )
        if module.get("criticality") not in {"P0", "P1", "P2"}:
            errors.append(f"module {module_id} has invalid criticality")
        allowed = set(module.get("allowed_dependencies", []))
        forbidden = set(module.get("forbidden_dependencies", []))
        unknown = sorted((allowed | forbidden) - known_ids)
        for dependency in unknown:
            errors.append(f"module {module_id} declares unknown dependency: {dependency}")
        overlap = sorted(allowed & forbidden)
        for dependency in overlap:
            errors.append(
                f"module {module_id} both allows and forbids dependency: {dependency}"
            )
        if module_id in allowed or module_id in forbidden:
            errors.append(f"module {module_id} must not declare itself as a dependency")

    capture = next(
        (module for module in modules if module.get("id") == "capture-core"), None
    )
    if capture is None:
        return errors + ["capture-core module is required"]
    if capture.get("criticality") != "P0":
        errors.append("capture-core criticality must be P0")
    if capture.get("failure_domain") != "capture-core":
        errors.append("capture-core must have an independent capture-core failure domain")

    patterns = set(capture.get("patterns", []))
    expected_patterns = set(capture.get("expected_patterns", []))
    for pattern in sorted(REQUIRED_CAPTURE_PATTERNS):
        if pattern not in patterns or pattern not in expected_patterns:
            errors.append(f"capture-core expected pattern is not registered: {pattern}")

    forbidden_dependencies = set(capture.get("forbidden_dependencies", []))
    for dependency in sorted(
        REQUIRED_CAPTURE_FORBIDDEN_DEPENDENCIES - forbidden_dependencies
    ):
        errors.append(f"capture-core must forbid dependency: {dependency}")

    forbidden_capabilities = set(capture.get("forbidden_capabilities", []))
    for capability in sorted(
        REQUIRED_CAPTURE_FORBIDDEN_CAPABILITIES - forbidden_capabilities
    ):
        errors.append(f"capture-core must forbid capability: {capability}")

    rust_symbols = contract.get("rust_capability_rules", {}).get("symbols", {})
    if not isinstance(rust_symbols, dict):
        errors.append("rust_capability_rules.symbols must be an object")
    else:
        for capability in sorted(forbidden_capabilities):
            symbols = rust_symbols.get(capability)
            if not isinstance(symbols, list) or not symbols or not all(
                isinstance(symbol, str) and symbol for symbol in symbols
            ):
                errors.append(
                    "capture-core forbidden capability lacks Rust symbols: "
                    f"{capability}"
                )
    return errors


def local_python_owners(owner_by_path: dict[str, str]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for path, owner in owner_by_path.items():
        if path.endswith(".py"):
            candidates.setdefault(Path(path).stem, set()).add(owner)
    return {
        imported: next(iter(owners))
        for imported, owners in candidates.items()
        if len(owners) == 1
    }


def validate() -> list[str]:
    contract = json.loads(MANIFEST.read_text(encoding="utf-8"))
    modules = contract.get("modules", [])
    errors = validate_schema(contract)
    owner_by_path: dict[str, str] = {}

    for path in production_files(contract):
        owners = [
            module["id"]
            for module in modules
            if isinstance(module.get("patterns"), list)
            and any(matches(path, pattern) for pattern in module["patterns"])
        ]
        if not owners:
            errors.append(f"unowned production file: {path}")
        elif len(owners) > 1:
            errors.append(f"multiple owners for {path}: {', '.join(owners)}")
        else:
            owner_by_path[path] = owners[0]

    module_by_id = {
        module["id"]: module for module in modules if isinstance(module.get("id"), str)
    }
    import_owners = local_python_owners(owner_by_path)
    dependency_rules = contract.get("python_dependency_rules", {})
    forbidden_imports = dependency_rules.get("forbidden_imports", {})
    enforce_allowlist = dependency_rules.get("enforce_module_allowlist", False)
    rust_capability_symbols = contract.get("rust_capability_rules", {}).get(
        "symbols", {}
    )

    for path, owner in owner_by_path.items():
        if not path.endswith(".py"):
            continue
        imports = imported_modules(ROOT / path)
        violations = sorted(imports.intersection(forbidden_imports.get(owner, [])))
        for imported in violations:
            errors.append(f"forbidden dependency: {path} ({owner}) -> {imported}")

        if not enforce_allowlist or owner not in module_by_id:
            continue
        allowed = set(module_by_id[owner].get("allowed_dependencies", []))
        forbidden = set(module_by_id[owner].get("forbidden_dependencies", []))
        for imported in sorted(imports):
            target_owner = import_owners.get(imported)
            if target_owner is None or target_owner == owner:
                continue
            if target_owner in forbidden:
                errors.append(
                    f"forbidden module dependency: {path} ({owner}) -> "
                    f"{imported} ({target_owner})"
                )
            elif target_owner not in allowed:
                errors.append(
                    f"dependency outside allowlist: {path} ({owner}) -> "
                    f"{imported} ({target_owner})"
                )

    for path, owner in owner_by_path.items():
        if not path.endswith(".rs") or owner not in module_by_id:
            continue
        imported = imported_rust_symbols(ROOT / path)
        for capability in module_by_id[owner].get("forbidden_capabilities", []):
            forbidden = set(rust_capability_symbols.get(capability, []))
            for symbol in sorted(imported.intersection(forbidden)):
                errors.append(
                    f"forbidden Rust capability: {path} ({owner}) -> "
                    f"{capability}:{symbol}"
                )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Architecture contract failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Architecture contract passed: schema v2, source ownership, "
        "dependency allowlists, and Rust capability boundaries are valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
