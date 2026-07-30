#!/usr/bin/env python3
"""Compile and explain Memory Wuxian configuration without mutating state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

import yaml


ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = ROOT / "contracts" / "configuration-v1.defaults.json"
SOURCE_SCHEMA_PATH = ROOT / "schemas" / "configuration-source.schema.json"
EFFECTIVE_SCHEMA_PATH = ROOT / "schemas" / "effective-configuration.schema.json"


class ConfigurationError(ValueError):
    """A closed configuration contract could not be compiled."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False) -> dict:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConfigurationError("YAML mapping keys must be scalar") from exc
        if duplicate:
            raise ConfigurationError(f"YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def canonical_json_bytes(value: Any) -> bytes:
    """Return the stable UTF-8 JSON representation used for contract hashes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"value is not canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_pointer(parts: Sequence[str]) -> str:
    if not parts:
        return "/"
    escaped = (part.replace("~", "~0").replace("/", "~1") for part in parts)
    return "/" + "/".join(escaped)


def _load_json(path: Path, label: str) -> Tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"{label} is not readable: {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if type(value) is not dict:
        raise ConfigurationError(f"{label} must be a JSON object: {path}")
    return value, raw


def _resolve_ref(
    reference: str,
    document_schema: dict,
    schema_path: Path,
) -> Tuple[dict, Path, dict]:
    if reference.startswith("#"):
        target = document_schema
        pointer = reference[1:]
        if pointer:
            for encoded in pointer.lstrip("/").split("/"):
                key = encoded.replace("~1", "/").replace("~0", "~")
                if type(target) is not dict or key not in target:
                    raise ConfigurationError(f"schema has unresolved reference: {reference}")
                target = target[key]
        if type(target) is not dict:
            raise ConfigurationError(f"schema reference is not an object: {reference}")
        return target, schema_path, document_schema

    file_name, separator, fragment = reference.partition("#")
    referenced_path = schema_path.parent / file_name
    referenced, _ = _load_json(referenced_path, "referenced schema")
    if not separator:
        return referenced, referenced_path, referenced
    return _resolve_ref(f"#{fragment}", referenced, referenced_path)


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": type(value) is dict,
        "array": type(value) is list,
        "string": type(value) is str,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "null": value is None,
    }.get(expected, False)


def _validate(
    value: Any,
    schema: dict,
    schema_path: Path,
    path: Tuple[str, ...] = (),
    document_schema: Optional[dict] = None,
) -> None:
    if document_schema is None:
        document_schema = schema
    location = _json_pointer(path)
    if "$ref" in schema:
        target, target_path, target_document = _resolve_ref(
            str(schema["$ref"]), document_schema, schema_path
        )
        _validate(value, target, target_path, path, target_document)
    for component in schema.get("allOf", []):
        _validate(value, component, schema_path, path, document_schema)

    if "const" in schema and value != schema["const"]:
        raise ConfigurationError(f"{location}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ConfigurationError(f"{location}: value is not in the allowed set")

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if type(expected) is str else expected
        if not any(_matches_type(value, item) for item in expected_types):
            rendered = ", ".join(expected_types)
            raise ConfigurationError(f"{location}: expected type {rendered}")

    if type(value) is dict:
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ConfigurationError(f"{location}: missing required key {missing[0]}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if type(key) is not str:
                raise ConfigurationError(f"{location}: mapping keys must be strings")
            if key in properties:
                _validate(
                    child,
                    properties[key],
                    schema_path,
                    path + (key,),
                    document_schema,
                )
            elif additional is False:
                raise ConfigurationError(
                    f"{_json_pointer(path + (key,))}: unknown configuration key"
                )
            elif type(additional) is dict:
                _validate(
                    child,
                    additional,
                    schema_path,
                    path + (key,),
                    document_schema,
                )
        minimum_properties = schema.get("minProperties")
        if minimum_properties is not None and len(value) < minimum_properties:
            raise ConfigurationError(
                f"{location}: expected at least {minimum_properties} properties"
            )

    if type(value) is str:
        if len(value) < schema.get("minLength", 0):
            raise ConfigurationError(f"{location}: string is too short")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise ConfigurationError(f"{location}: string does not match required pattern")

    if type(value) in {int, float}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ConfigurationError(
                f"{location}: value must be at least {schema['minimum']}"
            )
        if "maximum" in schema and value > schema["maximum"]:
            raise ConfigurationError(
                f"{location}: value must be at most {schema['maximum']}"
            )


def _load_yaml_mapping(path: Path) -> Tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"configuration source is not readable: {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"configuration source is not UTF-8: {path}") from exc
    try:
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except ConfigurationError:
        raise
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"configuration source is invalid YAML: {path}: {exc}") from exc
    if value is None:
        value = {}
    if type(value) is not dict:
        raise ConfigurationError("configuration source must contain a mapping")
    return value, raw


def _merge(
    defaults: dict,
    source: dict,
    defaults_label: str,
    source_label: str,
) -> Tuple[dict, dict]:
    missing = object()
    effective = copy.deepcopy(defaults)
    origins: dict[str, dict[str, str]] = {}

    def visit(default_value: Any, source_value: Any, parts: Tuple[str, ...]) -> Any:
        if type(default_value) is dict:
            source_mapping = source_value if type(source_value) is dict else {}
            result = {}
            for key in default_value:
                child_source = source_mapping.get(key, missing)
                result[key] = visit(
                    default_value[key], child_source, parts + (key,)
                )
            return result
        pointer = _json_pointer(parts)
        if source_value is not missing:
            origins[pointer] = {
                "layer": "configuration-source",
                "source": source_label,
            }
            return copy.deepcopy(source_value)
        origins[pointer] = {"layer": "defaults-v1", "source": defaults_label}
        return copy.deepcopy(default_value)

    effective = visit(effective, source, ())
    return effective, dict(sorted(origins.items()))


def _validate_relationships(configuration: Mapping[str, Any]) -> None:
    governance = configuration["governance_ai"]
    if governance["product_min_items"] > governance["product_max_items"]:
        raise ConfigurationError(
            "/governance_ai: product_min_items must not exceed product_max_items"
        )
    if governance["classification_min_items"] > governance["classification_max_items"]:
        raise ConfigurationError(
            "/governance_ai: classification_min_items must not exceed "
            "classification_max_items"
        )
    refresh = configuration["context_refresh"]
    if refresh["utilization_low_percent"] >= refresh["utilization_high_percent"]:
        raise ConfigurationError(
            "/context_refresh: utilization_low_percent must be less than "
            "utilization_high_percent"
        )
    if refresh["soft_max_tokens"] > refresh["absolute_max_tokens"]:
        raise ConfigurationError(
            "/context_refresh: soft_max_tokens must not exceed absolute_max_tokens"
        )
    backup = configuration["backup"]
    if backup["enabled"] and not backup["directory"].strip():
        raise ConfigurationError("/backup/directory: required when backup is enabled")


def _resolve_root(
    configuration: Mapping[str, Any],
    value_sources: Mapping[str, Mapping[str, str]],
    *,
    root_argument: Optional[str],
    environ: Mapping[str, str],
    active_root_pointer_path: Optional[Path],
    skill_root: Path,
) -> dict:
    if root_argument:
        return {
            "path": str(Path(root_argument).expanduser()),
            "layer": "explicit-root",
            "source": "--root",
        }
    environment_root = environ.get("MEMORY_WUXIAN_ROOT")
    if environment_root:
        return {
            "path": str(Path(environment_root).expanduser()),
            "layer": "environment",
            "source": "MEMORY_WUXIAN_ROOT",
        }
    if active_root_pointer_path is None:
        codex_home = Path(environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        active_root_pointer_path = codex_home / "memory-wuxian-active-root.txt"
    if active_root_pointer_path.exists():
        try:
            pointed_root = active_root_pointer_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(
                f"active root pointer is not readable: {active_root_pointer_path}: {exc}"
            ) from exc
        if pointed_root:
            return {
                "path": str(Path(pointed_root).expanduser()),
                "layer": "active-root-pointer",
                "source": str(active_root_pointer_path),
            }
    configured = Path(configuration["memory"]["root_directory"]).expanduser()
    resolved = configured if configured.is_absolute() else skill_root / configured
    origin = value_sources["/memory/root_directory"]
    return {
        "path": str(resolved),
        "layer": origin["layer"],
        "source": origin["source"],
    }


def compile_configuration(
    configuration_path: Path,
    *,
    root_argument: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    active_root_pointer_path: Optional[Path] = None,
    defaults_path: Path = DEFAULTS_PATH,
    source_schema_path: Path = SOURCE_SCHEMA_PATH,
    effective_schema_path: Path = EFFECTIVE_SCHEMA_PATH,
    skill_root: Path = ROOT,
) -> dict:
    """Compile a source file into an effective, explainable configuration."""

    source_path = Path(configuration_path)
    defaults_path = Path(defaults_path)
    source_schema_path = Path(source_schema_path)
    effective_schema_path = Path(effective_schema_path)
    source, source_raw = _load_yaml_mapping(source_path)
    defaults_contract, defaults_raw = _load_json(defaults_path, "defaults contract")
    source_schema, _ = _load_json(source_schema_path, "configuration source schema")

    if set(defaults_contract) != {"schema_version", "contract_id", "configuration"}:
        raise ConfigurationError("defaults contract has unexpected fields")
    if defaults_contract["schema_version"] != 1:
        raise ConfigurationError("defaults contract schema_version is unsupported")
    if defaults_contract["contract_id"] != "memory-wuxian-configuration-defaults-v1":
        raise ConfigurationError("defaults contract_id is unsupported")
    defaults = defaults_contract["configuration"]
    if type(defaults) is not dict:
        raise ConfigurationError("defaults contract configuration must be an object")

    _validate(defaults, source_schema, source_schema_path)
    _validate(source, source_schema, source_schema_path)
    effective, value_sources = _merge(
        defaults,
        source,
        str(defaults_path),
        str(source_path),
    )
    _validate_relationships(effective)
    effective_hash = canonical_sha256(effective)
    root_resolution = _resolve_root(
        effective,
        value_sources,
        root_argument=root_argument,
        environ=os.environ if environ is None else environ,
        active_root_pointer_path=active_root_pointer_path,
        skill_root=Path(skill_root),
    )
    result = {
        "schema_version": 1,
        "contract_id": "memory-wuxian-effective-configuration-v1",
        "effective_configuration": effective,
        "effective_configuration_sha256": effective_hash,
        "source": {"path": str(source_path), "sha256": _file_sha256(source_raw)},
        "defaults": {"path": str(defaults_path), "sha256": _file_sha256(defaults_raw)},
        "root_resolution": root_resolution,
        "value_sources": value_sources,
    }
    effective_schema, _ = _load_json(
        effective_schema_path, "effective configuration schema"
    )
    _validate(result, effective_schema, effective_schema_path)
    return result


def explain_configuration(compiled: Mapping[str, Any]) -> dict:
    """Return the diagnostic subset without changing the compiled contract."""

    return {
        "effective_configuration_sha256": compiled[
            "effective_configuration_sha256"
        ],
        "root_resolution": copy.deepcopy(compiled["root_resolution"]),
        "value_sources": copy.deepcopy(compiled["value_sources"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Memory Wuxian effective-configuration compiler"
    )
    parser.add_argument(
        "command",
        choices=("compile", "explain"),
        help="print the complete effective contract or its source explanation",
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config.yaml"),
        help="YAML source to compile (default: repository config.yaml)",
    )
    parser.add_argument("--root", help="explicit archive root, preserving CLI precedence")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        compiled = compile_configuration(Path(args.config), root_argument=args.root)
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    output = compiled if args.command == "compile" else explain_configuration(compiled)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
