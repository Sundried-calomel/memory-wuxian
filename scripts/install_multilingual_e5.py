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


MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
FILES = (
    ("config.json", 655, "69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959"),
    ("sentencepiece.bpe.model", 5069051, "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"),
    ("special_tokens_map.json", 167, "d05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7"),
    ("tokenizer.json", 17082730, "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
    ("tokenizer_config.json", 443, "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b"),
    (
        "onnx/model_qint8_avx512_vnni.onnx",
        118346824,
        "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
    ),
)
RUNTIME_PACKAGES = ("onnxruntime==1.28.0", "transformers==4.57.6")


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
    parser.add_argument(
        "--model-root",
        default=str(Path.home() / ".codex/models/memory-wuxian/multilingual-e5-small"),
    )
    parser.add_argument(
        "--runtime-dir",
        default=str(Path.home() / ".codex/runtimes/memory-wuxian-e5-py312"),
    )
    args = parser.parse_args()
    model_dir = Path(args.model_root).expanduser().resolve() / MODEL_REVISION
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    python = runtime_python(runtime_dir)
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(runtime_dir)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", *RUNTIME_PACKAGES],
        check=True,
    )
    artifacts = []
    for remote_name, expected_size, expected_sha256 in FILES:
        local_name = Path(remote_name).name
        destination = model_dir / local_name
        valid = (
            destination.exists()
            and destination.stat().st_size == expected_size
            and sha256(destination) == expected_sha256
        )
        if not valid:
            url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{remote_name}"
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
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "runtime_packages": list(RUNTIME_PACKAGES),
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
