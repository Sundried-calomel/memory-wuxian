#!/usr/bin/env python3
"""Install a pinned offline multilingual-e5-small ONNX runtime and model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path

from semantic_runtime_contract import CONTRACT_PATH, load_contract



def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Memory-Wuxian-E5-Installer"})
        with urllib.request.urlopen(request, timeout=120) as response, open(temporary, "wb") as handle:
            while block := response.read(1024 * 1024):
                handle.write(block)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def runtime_python(runtime_dir: Path) -> Path:
    return runtime_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(CONTRACT_PATH))
    parser.add_argument(
        "--model-root",
        default=None,
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
    )
    args = parser.parse_args()
    contract = load_contract(args.contract)
    model_id = contract["model"]["id"]
    model_revision = contract["model"]["revision"]
    model_root = args.model_root or contract["installation"]["model_root"]
    runtime_root = args.runtime_dir or contract["installation"]["runtime_root"]
    model_dir = Path(model_root).expanduser().resolve() / model_revision
    runtime_dir = Path(runtime_root).expanduser().resolve()
    python = runtime_python(runtime_dir)
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(runtime_dir)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *contract["runtime"]["packages"],
        ],
        check=True,
    )
    artifacts = []
    for item in contract["model"]["artifacts"]:
        remote_name = item["source"]
        local_name = item["path"]
        expected_size = item["size"]
        expected_sha256 = item["sha256"]
        destination = model_dir / local_name
        valid = (
            destination.exists()
            and destination.stat().st_size == expected_size
            and sha256(destination) == expected_sha256
        )
        if not valid:
            url = (
                f"https://huggingface.co/{model_id}/resolve/"
                f"{model_revision}/{remote_name}"
            )
            download(url, destination)
        actual_size = destination.stat().st_size
        actual_sha256 = sha256(destination)
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"Pinned model artifact failed verification: {remote_name}")
        artifacts.append({
            "source": remote_name,
            "path": local_name,
            "size": actual_size,
            "sha256": actual_sha256,
        })
    manifest = {
        "format": "memory-wuxian-e5-model-v1",
        "contract_id": contract["contract_id"],
        "interface_version": contract["interface_version"],
        "model_id": model_id,
        "model_revision": model_revision,
        "runtime_packages": list(contract["runtime"]["packages"]),
        "offline_only": True,
        "artifacts": artifacts,
    }
    manifest_path = model_dir / "model-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "installed",
        "model_dir": str(model_dir),
        "runtime_python": str(python),
        "manifest": str(manifest_path),
        "downloaded_bytes": sum(item["size"] for item in artifacts),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
