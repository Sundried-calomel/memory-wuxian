"""Selected environment files over the authenticated core-v1 files channel.

This adapter has no legacy protocol fallback. Incoming files are applied only
through device-local EnvironmentService bindings; queue receipt alone never
changes a target and never produces an applied ACK.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
from pathlib import Path, PurePosixPath
from typing import Mapping

from storage import atomic_write_json, bytes_sha256, canonical_json_bytes, exclusive_lock, safe_target


SELECTION_FILE = Path(__file__).with_name("environment-selection.json")
SELECTIONS = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))["items"]
BY_ARTIFACT = {item["artifact_id"]: (name, item) for name, item in SELECTIONS.items()}
CORE_EXCLUDED_MODULES = {"legacy_cloud.py", "transport.py"}
CORE_REQUIRED_MODULES = {
    "archive.py", "backup.py", "core_sync.py", "environment.py",
    "environment_sync.py", "install.py", "live.py", "storage.py", "bootstrap_core.py", "collector.py",
}
CORE_REQUIRED_FILES = {"SKILL.md", "core/dashboard.html", "core/environment-selection.json"}
MAX_FILES = 4096
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
_LOCAL_PATH = re.compile(rb"(?i)(?:[a-z]:\\users\\[^\\\s]+|/(?:users|home)/[^/\s]+)")
_PRIVATE_KEY = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_TOKEN = re.compile(rb"(?i)\bsk-[A-Za-z0-9_-]{20,}\b")


def _safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("environment file path must be a nonempty portable relative path")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("environment file path escapes its selected package")
    if any(part.casefold() in {"live-config", "keys", "diagnostics", "scripts", "installer"} for part in path.parts):
        raise ValueError("device configuration, legacy scripts, installers and diagnostics are excluded")
    return name


def _validate_content(name: str, data: bytes) -> None:
    if not isinstance(data, bytes):
        raise TypeError("environment file content must be exact bytes")
    if _LOCAL_PATH.search(data):
        raise ValueError(f"device-local absolute path found in selected file: {name}")
    if _PRIVATE_KEY.search(data) or _TOKEN.search(data):
        raise ValueError(f"credential material found in selected file: {name}")


def validate_files(selection_id: str, files: Mapping[str, bytes]) -> dict[str, bytes]:
    if selection_id not in SELECTIONS or not isinstance(files, Mapping) or not files:
        raise ValueError("unknown selection or empty files mapping")
    normalized: dict[str, bytes] = {}
    total = 0
    for name, data in files.items():
        name = _safe_relative(name)
        if name in normalized:
            raise ValueError("duplicate environment file path")
        _validate_content(name, data)
        total += len(data)
        if total > MAX_PACKAGE_BYTES or len(normalized) >= MAX_FILES:
            raise ValueError("selected environment package exceeds its size or file bound")
        normalized[name] = data

    if selection_id == "global-codex-agents":
        if set(normalized) != {"AGENTS.md"}:
            raise ValueError("global AGENTS selection is a single whole-file item")
        normalized["AGENTS.md"].decode("utf-8")
        return normalized

    if not CORE_REQUIRED_FILES <= set(normalized):
        raise ValueError("new-core package requires its Skill entry, dashboard and selection manifest")
    module_paths = {name for name in normalized if name.startswith("core/") and name.endswith(".py")}
    if any(PurePosixPath(name).parent != PurePosixPath("core") for name in module_paths):
        raise ValueError("new-core modules must be direct children of core/")
    module_names = {PurePosixPath(name).name for name in module_paths}
    if not CORE_REQUIRED_MODULES <= module_names:
        raise ValueError("new-core package is missing a required module")
    if any(PurePosixPath(name).name in CORE_EXCLUDED_MODULES for name in module_paths):
        raise ValueError("retired machine-specific/legacy transport modules are excluded")
    if any(name not in CORE_REQUIRED_FILES and name not in module_paths
           for name in normalized):
        raise ValueError("the portable Skill bundle accepts only SKILL.md and core source files")
    for name, data in normalized.items():
        if name.endswith(".py"):
            ast.parse(data.decode("utf-8"), filename=name)
    normalized["SKILL.md"].decode("utf-8")
    normalized["core/dashboard.html"].decode("utf-8")
    json.loads(normalized["core/environment-selection.json"].decode("utf-8"))
    return normalized


def collect_core_files(core_root, skill_entry) -> dict[str, bytes]:
    """Collect only the direct new-core Python modules and two explicit entries.

    ``skill_entry`` is passed explicitly because a finalized new-core SKILL.md
    is supplied by the package builder, not inferred from the installed client.
    """
    root = Path(core_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("explicit new-core source directory required")
    entry = Path(skill_entry)
    dashboard = root / "dashboard.html"
    selection = root / "environment-selection.json"
    if any(path.is_symlink() or not path.is_file() for path in (entry, dashboard, selection)):
        raise ValueError("selected Skill entry, dashboard or selection manifest is unavailable")
    files = {"SKILL.md": entry.read_bytes(),
             "core/dashboard.html": dashboard.read_bytes(),
             "core/environment-selection.json": selection.read_bytes()}
    for path in sorted(root.iterdir()):
        if path.name in CORE_EXCLUDED_MODULES or path.suffix != ".py":
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("new-core module source is not a regular file")
        files["core/" + path.name] = path.read_bytes()
    return validate_files("memory-wuxian-core", files)


def collect_selected_files(selection_id: str, local_sources: Mapping[str, str]) -> dict[str, bytes]:
    """Read only an explicitly selected local source set; paths never enter payloads."""
    if selection_id == "global-codex-agents":
        path = Path(local_sources["agents_file"])
        if path.is_symlink() or not path.is_file():
            raise ValueError("selected local global AGENTS.md is unavailable")
        return validate_files(selection_id, {"AGENTS.md": path.read_bytes()})
    if selection_id == "memory-wuxian-core":
        return collect_core_files(local_sources["core_directory"], local_sources["skill_entry"])
    raise ValueError("unknown local environment selection")


def revision_id(selection_id: str, files: Mapping[str, bytes]) -> str:
    files = validate_files(selection_id, files)
    identity = {"artifact_id": SELECTIONS[selection_id]["artifact_id"],
                "files": [{"path": name, "sha256": bytes_sha256(data)}
                          for name, data in sorted(files.items())]}
    return "rev:" + bytes_sha256(canonical_json_bytes(identity))


def publish_selected(core_sync, selection_id: str, files: Mapping[str, bytes]) -> dict:
    selected = SELECTIONS[selection_id]
    files = validate_files(selection_id, files)
    return core_sync.publish_files(files, artifact_id=selected["artifact_id"],
                                   revision_id=revision_id(selection_id, files))


def _dependency_check(selection_id: str, files: Mapping[str, bytes]) -> bool:
    if selection_id == "global-codex-agents":
        return True
    try:
        for name, data in files.items():
            if name.endswith(".py"):
                ast.parse(data.decode("utf-8"), filename=name)
        return bool(files.get("SKILL.md")) and bool(files.get("core/dashboard.html"))
    except (UnicodeError, SyntaxError):
        return False


def run_once(store, service, config) -> dict:
    """Publish selected items, apply authenticated incoming files, then ACK.

    ``service`` is a local EnvironmentService. ``config['transport']`` is a
    configured CoreSyncService. ``config['environment']`` contains local-only
    source paths and selected IDs; neither paths nor config values are copied
    into status evidence or remote payload metadata.
    """
    attempted = dt.datetime.now(dt.timezone.utc).isoformat()
    status = {"schema": 1, "protocol": "memory-wuxian-core-v1", "channel": "files",
              "attempted_at": attempted, "state": "running", "published": [], "received": []}
    path = safe_target(store.root, "environment-status.json")
    lock = safe_target(store.root, ".environment-status.lock")
    try:
        core_sync = config["transport"]
        environment_config = config["environment"]
        for selection_id in environment_config.get("outbound_selection_ids", []):
            try:
                files = collect_selected_files(selection_id, environment_config)
                result = publish_selected(core_sync, selection_id, files)
                status["published"].append({"selection": selection_id,
                    "state": result.get("status", "queued"),
                    "revision_id": revision_id(selection_id, files)})
            except Exception as exc:
                status["published"].append({"selection": selection_id,
                                            "state": "error", "error_type": type(exc).__name__})

        for received in core_sync.receive_files(limit=config.get("receive_limit", 32)):
            verified = received.get("verified", {})
            artifact = verified.get("artifact_id")
            match = BY_ARTIFACT.get(artifact)
            if match is None:
                status["received"].append({"state": "rejected", "error_type": "UnknownArtifact"})
                continue
            selection_id, selected = match
            try:
                files = validate_files(selection_id, verified.get("files", {}))
                if verified.get("revision_id") != revision_id(selection_id, files):
                    raise ValueError("selected package revision mismatch")
                binding = service._read()["bindings"].get(selected["binding_name"])
                if binding is None:
                    status["received"].append({"selection": selection_id,
                        "state": "pending-binding", "revision_id": verified["revision_id"],
                        "acknowledged": False})
                    continue
                if binding.get("strategy") != selected["strategy"]:
                    raise ValueError("local binding strategy does not match selected package")
                check = (lambda items, sid=selection_id: _dependency_check(sid, items)) \
                    if selected["strategy"] == "skill-tree" else None
                apply_files = {"": files["AGENTS.md"]} if selected["strategy"] == "whole-file" else files
                applied = service.apply(selected["binding_name"], apply_files,
                                        dependency_check=check)
                core_sync.acknowledge_files(received)
                status["received"].append({"selection": selection_id,
                    "state": applied.get("status", "applied"),
                    "revision_id": verified["revision_id"], "acknowledged": True})
            except Exception as exc:
                status["received"].append({"selection": selection_id,
                    "state": "conflict" if isinstance(exc, RuntimeError) else "error",
                    "error_type": type(exc).__name__, "error": str(exc), "acknowledged": False})
        errors = any(x["state"] in {"error", "conflict", "rejected", "pending-binding"}
                     for x in status["published"] + status["received"])
        status["state"] = "partial" if errors else "completed"
    except Exception as exc:
        status["state"] = "error"
        status["error_type"] = type(exc).__name__
    with exclusive_lock(lock):
        atomic_write_json(path, status)
    return status
