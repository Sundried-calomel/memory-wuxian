#!/usr/bin/env python3
"""Check, verify, and stage official MemoryWuxian release updates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence


REPOSITORY = "Sundried-calomel/memory-wuxian"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
WINDOWS_RUN_ONCE = r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce"
WINDOWS_RUN_ONCE_VALUE = "MemoryWuxianUpdate"


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.group(1).split("."))


def current_version(skill_root: Path) -> str:
    text = (skill_root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml does not declare a project version")
    return match.group(1)


def asset_names(version: str, system: str) -> tuple[str, str]:
    normalized = version.removeprefix("v")
    if system == "Windows":
        package = f"MemoryWuxian-{normalized}-Windows-x64-Setup.exe"
    elif system == "Darwin":
        package = f"MemoryWuxian-{normalized}-macOS-universal.pkg"
    else:
        raise ValueError(f"Automatic release updates are unsupported on {system}")
    return package, f"{package}.sha256"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "MemoryWuxian-AutoUpdater"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "MemoryWuxian-AutoUpdater"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)


def verify_checksum(package: Path, checksum: Path) -> str:
    fields = checksum.read_text(encoding="utf-8").strip().split()
    if len(fields) < 2 or fields[-1].lstrip("*") != package.name:
        raise ValueError("Release checksum does not name the downloaded package")
    expected = fields[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Release checksum is not SHA-256")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError(f"Release checksum mismatch: expected {expected}, got {digest}")
    return digest


def stage_install(package: Path, system: str) -> str:
    if system == "Windows":
        command = f'"{package}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
        subprocess.run(
            ["reg.exe", "ADD", WINDOWS_RUN_ONCE, "/V", WINDOWS_RUN_ONCE_VALUE,
             "/T", "REG_SZ", "/D", command, "/F"],
            check=True,
            capture_output=True,
        )
        return "staged-for-next-login"
    return "downloaded-awaiting-macos-authorization"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--state-file", default="~/.codex/memory-wuxian-update.json")
    parser.add_argument("--download-directory", default="~/.codex/updates/memory-wuxian")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--release-json", help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    skill_root = Path(args.skill_root).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    download_root = Path(args.download_directory).expanduser().resolve()
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = int(time.time())
    if not args.force and now - int(state.get("checked_at_epoch", 0)) < args.interval_seconds:
        print(json.dumps({"status": "not-due", **state}, ensure_ascii=False, indent=2))
        return 0
    try:
        release = json.loads(Path(args.release_json).read_text(encoding="utf-8")) if args.release_json else fetch_json(LATEST_RELEASE_API)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("GitHub latest release is not a stable published release")
        installed = current_version(skill_root)
        latest = str(release["tag_name"]).removeprefix("v")
        result: dict[str, Any] = {
            "checked_at_epoch": now,
            "installed_version": installed,
            "latest_version": latest,
        }
        if version_tuple(latest) <= version_tuple(installed):
            result["status"] = "up-to-date"
        elif args.check_only:
            result["status"] = "update-available"
        else:
            package_name, checksum_name = asset_names(latest, platform.system())
            assets = {str(item.get("name")): str(item.get("browser_download_url")) for item in release.get("assets", [])}
            if not assets.get(package_name) or not assets.get(checksum_name):
                raise ValueError("Release is missing the platform package or SHA-256 file")
            version_dir = download_root / latest
            version_dir.mkdir(parents=True, exist_ok=True)
            package = version_dir / package_name
            checksum = version_dir / checksum_name
            download(assets[package_name], package)
            download(assets[checksum_name], checksum)
            digest = verify_checksum(package, checksum)
            result.update({
                "status": stage_install(package, platform.system()),
                "package": str(package),
                "sha256": digest,
            })
        atomic_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        failure = {"status": "failed", "checked_at_epoch": now, "error": str(error)}
        atomic_json(state_path, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
