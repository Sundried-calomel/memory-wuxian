#!/usr/bin/env python3
"""Build, validate, and activate the offline Windows Python runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Sequence
import urllib.request
import zipfile


LOCK_FIELDS = frozenset({"schema_version", "python", "packages"})
PYTHON_FIELDS = frozenset({"version", "filename", "url", "sha256"})
PACKAGE_FIELDS = frozenset({"name", "version", "artifact"})
MANIFEST_FIELDS = frozenset(
    {"schema_version", "bundle_id", "lock_sha256", "python_version", "interpreter", "packages", "files"}
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class RuntimeBundleError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _closed(value: Any, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise RuntimeBundleError(f"{label} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeBundleError("bundle path must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeBundleError(f"unsafe bundle path: {value}")
    return Path(*path.parts)


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def load_lock(path: Path) -> dict[str, Any]:
    value = _closed(json.loads(path.read_text(encoding="utf-8")), LOCK_FIELDS, "runtime lock")
    if value["schema_version"] != 1:
        raise RuntimeBundleError("unsupported runtime lock schema")
    python = _closed(value["python"], PYTHON_FIELDS, "runtime lock python")
    if not SHA256_RE.fullmatch(python["sha256"]):
        raise RuntimeBundleError("runtime lock Python hash is invalid")
    packages = value["packages"]
    if not isinstance(packages, list) or not packages:
        raise RuntimeBundleError("runtime lock packages must be non-empty")
    identities: list[str] = []
    for index, item in enumerate(packages):
        package = _closed(item, PACKAGE_FIELDS, f"runtime lock package {index}")
        if package["artifact"] not in {"wheel", "sdist"}:
            raise RuntimeBundleError("runtime package artifact is unsupported")
        if not all(isinstance(package[field], str) and package[field] for field in ("name", "version")):
            raise RuntimeBundleError("runtime package identity is invalid")
        identities.append(normalized_name(package["name"]))
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise RuntimeBundleError("runtime packages must be unique and sorted")
    return value


def _archive_member_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    return _safe_relative(normalized)


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            relative = _archive_member_path(info.filename)
            target = destination / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)


def _extract_proxy_tools(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        members = []
        for member in source.getmembers():
            path = _archive_member_path(member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeBundleError("proxy-tools sdist contains a link or device")
            members.append((member, path))
        roots = {path.parts[0] for _, path in members}
        if len(roots) != 1:
            raise RuntimeBundleError("proxy-tools sdist has an unexpected layout")
        root = next(iter(roots))
        copied = False
        for member, path in members:
            parts = path.parts
            if len(parts) < 2 or parts[0] != root or parts[1] != "proxy_tools" or not member.isfile():
                continue
            target = destination.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            reader = source.extractfile(member)
            if reader is None:
                raise RuntimeBundleError("proxy-tools sdist member is unreadable")
            with reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            copied = True
        if not copied:
            raise RuntimeBundleError("proxy-tools package is missing from its sdist")


def _artifact_for(package: dict[str, str], asset_directory: Path) -> Path:
    name = normalized_name(package["name"])
    version = package["version"].lower()
    matches = []
    for path in asset_directory.iterdir():
        if not path.is_file():
            continue
        filename = path.name.lower().replace("_", "-")
        if filename.startswith(f"{name}-{version}-") or filename == f"{name}-{version}.tar.gz":
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeBundleError(f"expected one asset for {package['name']}=={package['version']}, found {len(matches)}")
    return matches[0]


def _file_entries(root: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "runtime-manifest.json"):
        entries.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


def _install_no_bytecode_guard(python_root: Path) -> None:
    site_packages = python_root / "Lib" / "site-packages"
    source = site_packages / "sitecustomize.py"
    source.write_text(
        "import sys\nsys.dont_write_bytecode = True\n",
        encoding="utf-8",
        newline="\n",
    )
    command = (
        "import importlib.util,py_compile,sys;"
        "p=sys.argv[1];"
        "py_compile.compile(p,cfile=importlib.util.cache_from_source(p),"
        "dfile='sitecustomize.py',doraise=True,"
        "invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)"
    )
    result = subprocess.run(
        [str(python_root / "python.exe"), "-I", "-S", "-c", command, str(source)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    cache = source.parent / "__pycache__"
    compiled = list(cache.glob("sitecustomize.*.pyc")) if cache.is_dir() else []
    if result.returncode or len(compiled) != 1:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeBundleError(detail or "runtime no-bytecode guard compilation failed")


def assemble_bundle(lock_path: Path, asset_directory: Path, output: Path) -> dict[str, Any]:
    lock = load_lock(lock_path)
    python_archive = asset_directory / lock["python"]["filename"]
    if not python_archive.is_file() or sha256_file(python_archive) != lock["python"]["sha256"]:
        raise RuntimeBundleError("Python embeddable archive is missing or has hash drift")
    if output.exists():
        raise RuntimeBundleError("runtime bundle output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent)))
    try:
        python_root = staging / "python"
        python_root.mkdir()
        _extract_zip(python_archive, python_root)
        site_packages = python_root / "Lib" / "site-packages"
        site_packages.mkdir(parents=True)
        package_records = []
        for package in lock["packages"]:
            asset = _artifact_for(package, asset_directory)
            if package["artifact"] == "wheel":
                _extract_zip(asset, site_packages)
            else:
                _extract_proxy_tools(asset, site_packages)
            package_records.append({"name": package["name"], "version": package["version"], "asset": asset.name, "sha256": sha256_file(asset)})
        pth_files = list(python_root.glob("python*._pth"))
        if len(pth_files) != 1:
            raise RuntimeBundleError("embeddable Python _pth file is missing or ambiguous")
        pth_files[0].write_text("python314.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8", newline="\n")
        _install_no_bytecode_guard(python_root)
        shutil.copy2(lock_path, staging / "runtime-lock.json")
        files = _file_entries(staging)
        identity = {"lock_sha256": sha256_file(staging / "runtime-lock.json"), "files": files}
        bundle_id = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        manifest = {
            "schema_version": 1,
            "bundle_id": bundle_id,
            "lock_sha256": identity["lock_sha256"],
            "python_version": lock["python"]["version"],
            "interpreter": "python/python.exe",
            "packages": package_records,
            "files": files,
        }
        (staging / "runtime-manifest.json").write_bytes(canonical_bytes(manifest))
        os.replace(staging, output)
        return validate_bundle(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def validate_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest_path = root / "runtime-manifest.json"
    manifest = _closed(json.loads(manifest_path.read_text(encoding="utf-8")), MANIFEST_FIELDS, "runtime manifest")
    if manifest["schema_version"] != 1 or not SHA256_RE.fullmatch(manifest["bundle_id"]):
        raise RuntimeBundleError("runtime manifest identity is invalid")
    if manifest_path.read_bytes() != canonical_bytes(manifest):
        raise RuntimeBundleError("runtime manifest is not canonical JSON")
    entries = manifest["files"]
    if not isinstance(entries, list):
        raise RuntimeBundleError("runtime manifest files must be an array")
    expected_paths = []
    for entry in entries:
        item = _closed(entry, frozenset({"path", "bytes", "sha256"}), "runtime file")
        relative = _safe_relative(item["path"])
        path = root / relative
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise RuntimeBundleError(f"runtime file drift: {item['path']}")
        expected_paths.append(item["path"])
    actual_paths = [item.relative_to(root).as_posix() for item in sorted(root.rglob("*")) if item.is_file() and item != manifest_path]
    if expected_paths != actual_paths:
        raise RuntimeBundleError("runtime bundle contains missing, extra, or unordered files")
    lock_path = root / "runtime-lock.json"
    if sha256_file(lock_path) != manifest["lock_sha256"]:
        raise RuntimeBundleError("runtime lock hash drift")
    expected_id = hashlib.sha256(canonical_bytes({"lock_sha256": manifest["lock_sha256"], "files": entries})).hexdigest()
    if manifest["bundle_id"] != expected_id:
        raise RuntimeBundleError("runtime bundle identity drift")
    interpreter = root / _safe_relative(manifest["interpreter"])
    if not interpreter.is_file():
        raise RuntimeBundleError("runtime interpreter is missing")
    return manifest


def activate_bundle(source: Path, target_parent: Path, *, probe: bool = True) -> dict[str, Any]:
    manifest = validate_bundle(source)
    target = target_parent.resolve() / manifest["bundle_id"]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(source, temporary)
        validate_bundle(temporary)
        try:
            os.replace(temporary, target)
        except OSError:
            if not target.exists():
                raise
            shutil.rmtree(temporary, ignore_errors=True)
    installed = validate_bundle(target)
    interpreter = target / _safe_relative(installed["interpreter"])
    if probe:
        environment = os.environ.copy()
        environment["PATH"] = ""
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [str(interpreter), "-I", "-c", "import yaml, webview, psutil; print('runtime-ready')"],
            check=False, capture_output=True, text=True, timeout=30, env=environment,
        )
        if result.returncode or result.stdout.strip() != "runtime-ready":
            raise RuntimeBundleError(f"isolated runtime import probe failed: {(result.stderr or result.stdout).strip()}")
        validate_bundle(target)
    return {"status": "ready", "bundle_id": installed["bundle_id"], "runtime_root": str(target), "python_executable": str(interpreter), "manifest": str(target / "runtime-manifest.json"), "lock": str(target / "runtime-lock.json")}


def fetch_assets(lock_path: Path, output: Path, pip_python: Path) -> None:
    lock = load_lock(lock_path)
    output.mkdir(parents=True, exist_ok=True)
    python_target = output / lock["python"]["filename"]
    urllib.request.urlretrieve(lock["python"]["url"], python_target)
    if sha256_file(python_target) != lock["python"]["sha256"]:
        raise RuntimeBundleError("downloaded Python archive hash mismatch")
    for package in lock["packages"]:
        command = [str(pip_python), "-m", "pip", "download", "--disable-pip-version-check", "--no-deps", "--dest", str(output)]
        if package["artifact"] == "wheel":
            command.extend(["--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.14", "--implementation", "cp", "--abi", "cp314"])
        else:
            command.append("--no-binary=:all:")
        command.append(f"{package['name']}=={package['version']}")
        subprocess.run(command, check=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--lock", required=True)
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--pip-python", required=True)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("--lock", required=True)
    assemble.add_argument("--assets", required=True)
    assemble.add_argument("--output", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle-root", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--bundle-root", required=True)
    activate.add_argument("--target-parent", required=True)
    activate.add_argument("--no-probe", action="store_true", help=argparse.SUPPRESS)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "fetch":
        fetch_assets(Path(args.lock), Path(args.output), Path(args.pip_python))
        value = {"status": "fetched"}
    elif args.command == "assemble":
        value = assemble_bundle(Path(args.lock), Path(args.assets), Path(args.output))
    elif args.command == "validate":
        value = validate_bundle(Path(args.bundle_root))
    else:
        value = activate_bundle(Path(args.bundle_root), Path(args.target_parent), probe=not args.no_probe)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
