from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from archive import ArchiveStore
from legacy_summaries import _parse_v1_markdown, convert_files
from storage import atomic_write_json, safe_target
from summary import _summary_hash, _summary_id


DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
DEFAULT_CONFIG = DEFAULT_CODEX_HOME / "skills" / "memory-wuxian" / "config.yaml"
ACTIVE_ROOT_POINTER = DEFAULT_CODEX_HOME / "memory-wuxian-active-root.txt"
REPORT_RELATIVE = "legacy/summary-migration.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_authorized_config(path: Path) -> dict[str, Any]:
    """Read only the memory root and Summary V2 root bindings from the configured YAML."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    result: dict[str, Any] = {"memory": {}, "summary_v2": {"bundle_roots": {}}}
    section = None
    bundle_roots = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        if indent == 0:
            section = key if key in {"memory", "summary_v2"} else None
            bundle_roots = False
            continue
        if section == "memory" and indent == 2 and key == "root_directory":
            result["memory"][key] = _yaml_scalar(value)
        elif section == "summary_v2" and indent == 2 and key == "runtime_root":
            result["summary_v2"][key] = _yaml_scalar(value)
        elif section == "summary_v2" and indent == 2 and key == "bundle_roots":
            bundle_roots = True
        elif section == "summary_v2" and bundle_roots and indent == 4:
            result["summary_v2"]["bundle_roots"][key] = _yaml_scalar(value)
        elif section == "summary_v2" and indent == 2:
            bundle_roots = False
    return result


def _no_links(path: Path, *, include_leaf: bool = True) -> None:
    path = Path(path).absolute()
    targets = [path, *path.parents] if include_leaf else list(path.parents)
    for item in targets:
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ValueError(f"linked path is not permitted: {item}")


def _resolve_binding(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or "\\" in relative or ":" in relative or any(p in {"", ".", ".."} for p in pure.parts):
        raise ValueError("unsafe Summary V2 bundle relative_path")
    base = Path(root).expanduser().absolute()
    _no_links(base)
    base = base.resolve()
    candidate = base.joinpath(*pure.parts)
    _no_links(candidate)
    if not candidate.resolve().is_relative_to(base):
        raise ValueError("Summary V2 bundle escaped its configured root")
    return candidate


def _read_v1_files(old_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    directory = safe_target(old_root, "summaries")
    if not directory.exists():
        return [], []
    files, failures = [], []
    for current, directories, names in os.walk(directory, followlinks=False):
        current_path = Path(current)
        for child in list(directories):
            child_path = current_path / child
            if child_path.is_symlink() or getattr(child_path, "is_junction", lambda: False)():
                relative = child_path.relative_to(old_root).as_posix()
                failures.append({"path": relative, "error": "linked summary directory is not permitted"})
                directories.remove(child)
        for name in sorted(names):
            path = current_path / name
            if path.suffix.lower() != ".md":
                continue
            relative = path.relative_to(old_root).as_posix()
            try:
                _no_links(path)
                data = path.read_bytes()
                metadata = _parse_v1_markdown(data)
                files.append({"kind": "v1", "paths": [(relative, data)], "metadata": metadata,
                              "hashes": {relative: _sha(data)}, "aliases": {"v1": [metadata.get("summary_id")]}})
            except Exception as exc:
                failures.append({"path": relative, "error": f"{type(exc).__name__}: {exc}"})
    return files, failures


def _read_v2_files(old_root: Path, bindings: dict[str, Path]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    directory = safe_target(old_root, "summary-v2/completions")
    if not directory.exists():
        return [], []
    files, failures = [], []
    for completion_path in sorted(directory.glob("*.json")):
        relative_completion = completion_path.relative_to(old_root).as_posix()
        try:
            _no_links(completion_path)
            completion_bytes = completion_path.read_bytes()
            completion = json.loads(completion_bytes.decode("utf-8"))
            bundle = completion["bundle"]
            binding_id = bundle["root_binding_id"]
            if binding_id not in bindings:
                raise ValueError(f"completion uses unconfigured bundle binding: {binding_id}")
            bundle_path = _resolve_binding(bindings[binding_id], bundle["relative_path"])
            sidecar_path, markdown_path = bundle_path / "summary.json", bundle_path / "summary.md"
            _no_links(sidecar_path); _no_links(markdown_path)
            sidecar_bytes, markdown_bytes = sidecar_path.read_bytes(), markdown_path.read_bytes()
            alias = completion.get("target_summary_id")
            label = hashlib.sha256((relative_completion + "\0" + str(alias)).encode("utf-8")).hexdigest()[:24]
            names = [f"v2-{label}/summary.json", f"v2-{label}/summary.md", f"v2-{label}/completion.json"]
            payloads = [sidecar_bytes, markdown_bytes, completion_bytes]
            files.append({"kind": "v2", "paths": list(zip(names, payloads)), "completion": completion,
                          "sidecar": json.loads(sidecar_bytes.decode("utf-8")),
                          "hashes": {relative_completion: _sha(completion_bytes),
                                     str(bundle_path / "summary.json"): _sha(sidecar_bytes),
                                     str(bundle_path / "summary.md"): _sha(markdown_bytes)},
                          "aliases": {"v2": [completion.get("target_summary_id"),
                                             json.loads(sidecar_bytes.decode("utf-8")).get("summary_v2_id")]}})
        except Exception as exc:
            failures.append({"path": relative_completion, "error": f"{type(exc).__name__}: {exc}"})
    return files, failures


def _component_groups(nodes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent = list(range(len(nodes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    owners: dict[tuple[str, str], int] = {}
    for index, node in enumerate(nodes):
        kind = node["kind"]
        for alias in node.get("aliases", {}).get(kind, []):
            if isinstance(alias, str):
                key = (kind, alias)
                if key in owners:
                    union(index, owners[key])
                else:
                    owners[key] = index
    for index, node in enumerate(nodes):
        metadata = node.get("metadata", {})
        sidecar = node.get("sidecar", {})
        if node["kind"] == "v1":
            children = metadata.get("source_summaries") or []
            namespace = "v1"
        else:
            source = sidecar.get("source", {})
            manifest = source.get("source_manifest", {})
            children = source.get("children") or manifest.get("children") or []
            if not children:
                children = [{"summary_v2_id": item.get("source_ref")}
                            for item in source.get("ref_catalog", []) if isinstance(item, dict)]
            children = [item.get("target_summary_id") or item.get("summary_v2_id") or item.get("parallel_summary_id")
                        for item in children if isinstance(item, dict)]
            namespace = "v2"
        for alias in children:
            child = owners.get((namespace, alias))
            if child is not None:
                union(index, child)
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, node in enumerate(nodes):
        groups.setdefault(find(index), []).append(node)
    return list(groups.values())


def _publish_component(store: ArchiveStore, summaries: list[dict[str, Any]]) -> None:
    created: list[Path] = []
    with store.lock():
        for item in summaries:
            if item.get("id") != _summary_id(item) or item.get("summary_sha256") != _summary_hash(item):
                raise ValueError("converted summary identity/hash is invalid")
        paths = [(item, safe_target(store.root, f"summaries/{item['id']}.json")) for item in summaries]
        for item, path in paths:
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != item:
                    raise ValueError(f"destination summary ID is occupied by different content: {item['id']}")
        try:
            with store.connection() as db:
                for item, path in paths:
                    if not path.exists():
                        atomic_write_json(path, item)
                        created.append(path)
                    db.execute("INSERT OR IGNORE INTO summary_index VALUES(?,?,?)",
                               (item["id"], item["conversation_id"], item["text"]))
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise


def migrate_summaries(new_root: Path, *, config_path: Path = DEFAULT_CONFIG,
                      source_root: Path | None = None) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    config = read_authorized_config(config_path)
    if source_root is None:
        if not ACTIVE_ROOT_POINTER.is_file():
            raise FileNotFoundError(f"active archive root pointer is missing: {ACTIVE_ROOT_POINTER}")
        source_root = Path(ACTIVE_ROOT_POINTER.read_text(encoding="utf-8").strip())
    old_root = Path(source_root).expanduser().absolute()
    new_root = Path(new_root).expanduser().absolute()
    _no_links(old_root); _no_links(new_root)
    old_root = old_root.resolve()
    new_root = new_root.resolve()
    if old_root == new_root or old_root.is_relative_to(new_root) or new_root.is_relative_to(old_root):
        raise ValueError("source and destination archive roots must be disjoint")
    bindings = {}
    if config["summary_v2"].get("runtime_root"):
        runtime_root = Path(config["summary_v2"]["runtime_root"]).expanduser().absolute()
        _no_links(runtime_root)
        bindings["runtime"] = runtime_root.resolve()
    for name, value in config["summary_v2"].get("bundle_roots", {}).items():
        if name == "runtime":
            raise ValueError("runtime binding cannot be overridden")
        binding = Path(value).expanduser().absolute()
        _no_links(binding)
        bindings[name] = binding.resolve()
    store = ArchiveStore(new_root)
    raw_count = store.status()['total_messages']
    nodes, read_failures = _read_v1_files(old_root)
    v2_nodes, v2_failures = _read_v2_files(old_root, bindings)
    nodes.extend(v2_nodes)
    components = _component_groups(nodes)
    def conversations(component):
        return tuple(sorted({node.get('metadata', node.get('sidecar', {})).get('conversation_id', '')
                             for node in component}))
    components.sort(key=conversations)
    previous_conversations, raws = None, []
    results = []
    for index, component in enumerate(components, 1):
        names = [name for node in component for name, _ in node["paths"]]
        inputs = [pair for node in component for pair in node["paths"]]
        try:
            selected = conversations(component)
            if not selected or any(not item for item in selected):
                raise ValueError('summary has no explicit conversation identity')
            if selected != previous_conversations:
                raws = [row for conversation in selected for row in store.records(conversation)]
                previous_conversations = selected
            converted = convert_files(inputs, raws)
            _publish_component(store, converted)
            results.append({"component": index, "status": "completed", "input_files": names,
                            "input_sha256": {name: _sha(data) for name, data in inputs},
                            "summary_ids": [item["id"] for item in converted]})
        except Exception as exc:
            results.append({"component": index, "status": "failed", "input_files": names,
                            "input_sha256": {name: _sha(data) for name, data in inputs},
                            "error": f"{type(exc).__name__}: {exc}"})
    failures = read_failures + v2_failures
    overall = "completed" if not failures and all(item["status"] == "completed" for item in results) else "partial"
    if not nodes and not failures:
        overall = "no-input"
    report = {"format": "memory-wuxian-summary-migration-v1", "status": overall,
              "source_root": str(old_root), "destination_root": str(new_root),
              "config_path": str(config_path), "raw_records_available": raw_count,
              "components": results, "read_failures": failures,
              "completed_summary_count": sum(len(item.get("summary_ids", [])) for item in results if item["status"] == "completed"),
              "failed_component_count": sum(item["status"] == "failed" for item in results),
              "failed_input_count": len(failures) + sum(item["status"] == "failed" for item in results)}
    report_path = safe_target(new_root, REPORT_RELATIVE)
    with store.lock():
        atomic_write_json(report_path, report)
    return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Migrate explicitly bound legacy summaries without model calls.")
    parser.add_argument("--new-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args(argv)
    report = migrate_summaries(args.new_root, config_path=args.config, source_root=args.source_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] in {"completed", "no-input"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
