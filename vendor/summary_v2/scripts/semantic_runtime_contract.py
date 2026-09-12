#!/usr/bin/env python3
"""Validate, register, and realize the shared E5 semantic runtime contract."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from memory_environment import EnvironmentRegistry, revision_id_for


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "contracts" / "multilingual-e5-small.runtime.json"
ARTIFACT_ID = "global-runtime-contract:memory-wuxian-e5"
SUPPORTED_PLATFORMS = {"macos", "windows", "linux"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_contract(value: Any) -> Dict[str, Any]:
    required = {
        "schema_version",
        "contract_id",
        "interface_version",
        "provider",
        "supported_platforms",
        "model",
        "runtime",
        "embedding",
        "installation",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("semantic runtime contract has unexpected fields")
    if value["schema_version"] != 1 or value["interface_version"] != 1:
        raise ValueError("unsupported semantic runtime contract version")
    if value["contract_id"] != "memory-wuxian-semantic-runtime:multilingual-e5-small":
        raise ValueError("unsupported semantic runtime contract identity")
    if value["provider"] != "multilingual-e5-small":
        raise ValueError("unsupported semantic provider")
    platforms = value["supported_platforms"]
    if (
        not isinstance(platforms, list)
        or not platforms
        or len(platforms) != len(set(platforms))
        or any(item not in SUPPORTED_PLATFORMS for item in platforms)
    ):
        raise ValueError("semantic runtime contract platforms are invalid")
    model = value["model"]
    if set(model) != {"id", "revision", "dimension", "artifacts"}:
        raise ValueError("semantic runtime model contract has unexpected fields")
    if (
        model["id"] != "intfloat/multilingual-e5-small"
        or not re.fullmatch(r"[0-9a-f]{40}", model["revision"])
        or model["dimension"] != 384
    ):
        raise ValueError("semantic runtime model identity is invalid")
    seen_paths = set()
    for artifact in model["artifacts"]:
        if set(artifact) != {"source", "path", "size", "sha256"}:
            raise ValueError("semantic runtime model artifact has unexpected fields")
        if (
            not isinstance(artifact["source"], str)
            or not artifact["source"]
            or not isinstance(artifact["path"], str)
            or Path(artifact["path"]).name != artifact["path"]
            or type(artifact["size"]) is not int
            or artifact["size"] < 1
            or not SHA256_RE.fullmatch(str(artifact["sha256"]))
            or artifact["path"] in seen_paths
        ):
            raise ValueError("semantic runtime model artifact is invalid")
        seen_paths.add(artifact["path"])
    runtime = value["runtime"]
    if set(runtime) != {"packages", "remote_model_code", "offline_inference"}:
        raise ValueError("semantic runtime package contract has unexpected fields")
    if (
        not runtime["packages"]
        or len(runtime["packages"]) != len(set(runtime["packages"]))
        or any("==" not in item for item in runtime["packages"])
        or runtime["remote_model_code"] is not False
        or runtime["offline_inference"] is not True
    ):
        raise ValueError("semantic runtime package contract is invalid")
    if value["embedding"] != {
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "max_length": 512,
        "pooling": "attention-mask-mean",
        "normalize": True,
        "similarity": "dot-product",
    }:
        raise ValueError("semantic embedding interface is unsupported")
    installation = value["installation"]
    if (
        set(installation) != {"installer", "model_root", "runtime_root"}
        or installation["installer"] != "scripts/install_multilingual_e5.py"
    ):
        raise ValueError("semantic runtime installation contract is invalid")
    return json.loads(json.dumps(value))


def load_contract(path: Path | str = CONTRACT_PATH) -> Dict[str, Any]:
    return validate_contract(json.loads(Path(path).read_text(encoding="utf-8")))


def contract_sha256(contract: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()


def environment_manifest(
    origin_node_id: str,
    *,
    contract: Dict[str, Any] | None = None,
    version: int = 1,
    base_revision_id: str | None = None,
) -> Dict[str, Any]:
    value = contract or load_contract()
    content = canonical_json(value) + "\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    artifact = {
        "schema_version": 1,
        "artifact_id": ARTIFACT_ID,
        "object_class": "global-runtime-contract",
        "scope": "global",
        "project_id": None,
        "display_name": "Memory Wuxian multilingual E5 runtime interface",
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    revision = {
        "schema_version": 1,
        "revision_id": "rev:" + "0" * 64,
        "artifact_id": ARTIFACT_ID,
        "origin_node_id": origin_node_id,
        "version": version,
        "base_revision_id": base_revision_id,
        "content_sha256": digest,
        "object_path": f"objects/sha256/{digest[:2]}/{digest[2:]}",
        "supported_platforms": list(value["supported_platforms"]),
        "runtime_requirements": {},
        "provenance": {
            "source": "bundled-semantic-runtime-contract",
            "contract_sha256": contract_sha256(value),
        },
        "lifecycle_state": "discovered",
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    revision["revision_id"] = revision_id_for(revision)
    return {
        "schema_version": 1,
        "artifacts": [{"artifact": artifact, "revision": revision, "content": content}],
        "projects": [],
    }


def local_platform() -> str:
    name = platform.system().lower()
    return {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(name, name)


def local_status(contract: Dict[str, Any], *, model_root: Path, runtime_root: Path) -> Dict[str, Any]:
    model_dir = model_root.expanduser() / contract["model"]["revision"]
    manifest_path = model_dir / "model-manifest.json"
    python = runtime_root.expanduser() / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    artifacts_ok = True
    for item in contract["model"]["artifacts"]:
        path = model_dir / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item["size"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            artifacts_ok = False
            break
    return {
        "contract_sha256": contract_sha256(contract),
        "platform": local_platform(),
        "platform_supported": local_platform() in contract["supported_platforms"],
        "model_dir": str(model_dir),
        "model_manifest": str(manifest_path),
        "model_verified": artifacts_ok and manifest_path.is_file(),
        "runtime_python": str(python),
        "runtime_present": python.is_file(),
        "realized": artifacts_ok and manifest_path.is_file() and python.is_file(),
    }


def registered_contract(registry: EnvironmentRegistry, artifact_id: str = ARTIFACT_ID) -> Dict[str, Any]:
    shown = registry.show(artifact_id)
    revision = shown["revision"]
    object_path = registry.root / revision["object_path"]
    payload = object_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != revision["content_sha256"]:
        raise ValueError("registered semantic runtime contract hash mismatch")
    return load_contract_from_text(payload.decode("utf-8"))


def load_contract_from_text(text: str) -> Dict[str, Any]:
    return validate_contract(json.loads(text))


def realize(
    contract: Dict[str, Any],
    *,
    model_root: Path,
    runtime_root: Path,
    apply: bool,
) -> Dict[str, Any]:
    bundled = load_contract()
    supported = contract == bundled
    preview = {
        "status": "preview",
        "contract_sha256": contract_sha256(contract),
        "supported_by_installed_skill": supported,
        "platform": local_platform(),
        "model_root": str(model_root.expanduser()),
        "runtime_root": str(runtime_root.expanduser()),
        "network_download_may_be_required": True,
        "automatic": False,
    }
    if not apply:
        return preview
    if not supported:
        raise ValueError(
            "registered semantic runtime contract is not supported by this Skill version"
        )
    if local_platform() not in contract["supported_platforms"]:
        raise ValueError("semantic runtime contract does not support this platform")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / contract["installation"]["installer"]),
            "--contract",
            str(CONTRACT_PATH),
            "--model-root",
            str(model_root.expanduser()),
            "--runtime-dir",
            str(runtime_root.expanduser()),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    status = local_status(
        contract,
        model_root=model_root,
        runtime_root=runtime_root,
    )
    if not status["realized"]:
        raise RuntimeError("semantic runtime realization did not pass local verification")
    return {
        **preview,
        "status": "realized",
        "installer_result": json.loads(completed.stdout),
        "verification": status,
    }
