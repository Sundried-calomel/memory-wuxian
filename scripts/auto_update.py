#!/usr/bin/env python3
"""Check, verify, and stage official MemoryWuxian release updates."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from platform_atomic import atomic_replace_bytes
from platform_process import no_window_kwargs
from memory_update_governance import apply_delta_bundle, load_signed_metadata, select_release, stage_update


REPOSITORY = "Sundried-calomel/memory-wuxian"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
LATEST_RELEASE_DOWNLOAD = f"https://github.com/{REPOSITORY}/releases/latest/download"
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
    payload_bytes = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_replace_bytes(path, payload_bytes)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "MemoryWuxian-AutoUpdater"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "MemoryWuxian-AutoUpdater"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MemoryWuxian-AutoUpdater"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read(1024 * 1024 * 1024 + 1)


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


def _active_archive_root(skill_root: Path) -> Path:
    pointer = skill_root.parent.parent / "memory-wuxian-active-root.txt"
    if not pointer.is_file():
        raise ValueError("active MemoryWuxian archive pointer is missing")
    archive_root = Path(pointer.read_text(encoding="utf-8").splitlines()[0]).expanduser()
    if not archive_root.is_absolute() or not archive_root.is_dir():
        raise ValueError("active MemoryWuxian archive pointer is invalid")
    return archive_root.resolve()


def _codex_cli() -> Path:
    candidates = (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / ".codex/.sandbox-bin/codex",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise ValueError("Codex CLI executable is unavailable")


def _extract_macos_skill(
    package: Path,
    destination: Path,
    *,
    runner=subprocess.run,
) -> Path:
    pkgutil = shutil.which("pkgutil") or "/usr/sbin/pkgutil"
    cpio = shutil.which("cpio") or "/usr/bin/cpio"
    expanded = destination / "expanded"
    payload_root = destination / "payload"
    runner(
        [pkgutil, "--expand", str(package), str(expanded)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = expanded / "Payload"
    if not payload.is_file():
        raise ValueError("macOS release package has no component payload")
    with gzip.open(payload, "rb") as archive:
        listing = runner(
            [cpio, "-it"],
            stdin=archive,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    if not listing:
        raise ValueError("macOS release package payload is empty")
    for item in listing:
        normalized = item.removeprefix("./")
        path = Path(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("macOS release package contains an unsafe payload path")
    payload_root.mkdir()
    with gzip.open(payload, "rb") as archive:
        runner(
            [cpio, "-idm", "--quiet"],
            stdin=archive,
            cwd=payload_root,
            check=True,
            capture_output=True,
        )
    source_root = (
        payload_root
        / "Library"
        / "Application Support"
        / "MemoryWuxian"
        / "skill"
    )
    if not (source_root / "SKILL.md").is_file():
        raise ValueError("macOS release package is missing the Skill payload")
    return source_root


def stage_install(
    package: Path,
    system: str,
    *,
    skill_root: Optional[Path] = None,
    python_executable: Optional[Path] = None,
    state_path: Optional[Path] = None,
    expected_sha256: Optional[str] = None,
    runner=subprocess.run,
) -> str:
    if system == "Windows":
        if skill_root is None or python_executable is None or state_path is None:
            raise ValueError("Windows staged install requires Skill, Python, and state paths")
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise ValueError("Windows staged install package no longer matches the approved SHA-256")
        pythonw = python_executable.with_name("pythonw.exe")
        launcher = pythonw if pythonw.is_file() else python_executable
        command = subprocess.list2cmdline([
            str(launcher),
            str(skill_root / "scripts" / "auto_update.py"),
            "--execute-windows-staged-installer",
            "--state-file",
            str(state_path),
            "--staged-package",
            str(package),
            "--expected-sha256",
            digest,
        ])
        runner(
            ["reg.exe", "ADD", WINDOWS_RUN_ONCE, "/V", WINDOWS_RUN_ONCE_VALUE,
             "/T", "REG_SZ", "/D", command, "/F"],
            check=True,
            capture_output=True,
            **no_window_kwargs(),
        )
        return "staged-for-next-login"
    if system != "Darwin":
        raise ValueError(f"Automatic release updates are unsupported on {system}")
    if skill_root is None or python_executable is None:
        raise ValueError("macOS user transaction requires Skill and Python paths")
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-release-") as temporary:
        source_root = _extract_macos_skill(
            package,
            Path(temporary),
            runner=runner,
        )
        completed = runner(
            [
                str(python_executable),
                str(source_root / "scripts" / "install_macos_transaction.py"),
                "--source-root",
                str(source_root),
                "--skill-root",
                str(skill_root),
                "--archive-root",
                str(_active_archive_root(skill_root)),
                "--sessions-root",
                str(Path.home() / ".codex/sessions"),
                "--python-executable",
                str(python_executable),
                "--codex-cli",
                str(_codex_cli()),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=1_500,
        )
        result = json.loads(completed.stdout)
        if result.get("status") != "installed":
            raise ValueError("macOS user transaction did not report installation")
    return "installed-user-transaction"


def execute_windows_staged_installer(
    package: Path,
    state_path: Path,
    expected_sha256: str,
    *,
    runner=subprocess.run,
) -> int:
    """Run one approved Setup and persist its exact outer transaction result."""
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = int(time.time())
    digest = hashlib.sha256(package.read_bytes()).hexdigest() if package.is_file() else ""
    if digest != expected_sha256.lower() or state.get("sha256") != expected_sha256.lower():
        state.update({
            "status": "install-failed",
            "installer_exit_code": 30,
            "installer_finished_at_epoch": now,
            "installer_error": "staged package SHA-256 no longer matches approval",
        })
        atomic_json(state_path, state)
        return 30
    state.update({"status": "installing", "installer_started_at_epoch": now})
    atomic_json(state_path, state)
    try:
        completed = runner(
            [
                str(package),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SOURCEENTRYPOINT=auto-update",
            ],
            check=False,
            **no_window_kwargs(),
        )
        exit_code = int(completed.returncode)
        error = None
    except OSError as exc:
        exit_code = 31
        error = str(exc)
    state.update({
        "status": "installed" if exit_code == 0 else "install-failed",
        "installer_exit_code": exit_code,
        "installer_finished_at_epoch": int(time.time()),
    })
    if error is not None:
        state["installer_error"] = error
    else:
        state.pop("installer_error", None)
    atomic_json(state_path, state)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--state-file", default="~/.codex/memory-wuxian-update.json")
    parser.add_argument("--download-directory", default="~/.codex/updates/memory-wuxian")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--approve-install",
        action="store_true",
        help="Install only a previously staged artifact matching expected version and SHA-256",
    )
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--execute-windows-staged-installer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--staged-package", help=argparse.SUPPRESS)
    parser.add_argument("--release-json", help=argparse.SUPPRESS)
    parser.add_argument("--channel", choices=("stable", "beta", "development"), default="stable")
    parser.add_argument("--update-metadata-json")
    parser.add_argument("--update-metadata-signature")
    parser.add_argument("--allowed-signers")
    parser.add_argument("--base-package")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    skill_root = Path(args.skill_root).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve()
    download_root = Path(args.download_directory).expanduser().resolve()
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    now = int(time.time())
    if args.execute_windows_staged_installer:
        if not args.staged_package or not args.expected_sha256:
            return 30
        return execute_windows_staged_installer(
            Path(args.staged_package).expanduser().resolve(),
            state_path,
            args.expected_sha256,
        )
    if args.approve_install:
        try:
            if not args.expected_version or not args.expected_sha256:
                raise ValueError("approval requires --expected-version and --expected-sha256")
            if state.get("status") != "staged-awaiting-user-approval":
                raise ValueError("no verified staged update is awaiting approval")
            if state.get("latest_version") != args.expected_version:
                raise ValueError("staged update version does not match approval")
            if state.get("sha256") != args.expected_sha256.lower():
                raise ValueError("staged update SHA-256 does not match approval")
            package = Path(str(state.get("package", ""))).expanduser().resolve()
            if not package.is_file() or hashlib.sha256(package.read_bytes()).hexdigest() != args.expected_sha256.lower():
                raise ValueError("staged update bytes no longer match approval")
            state["status"] = stage_install(
                package,
                platform.system(),
                skill_root=skill_root,
                python_executable=Path(args.python_executable).expanduser(),
                state_path=state_path,
                expected_sha256=args.expected_sha256,
            )
            state["install_approved"] = True
            state["approved_at_epoch"] = now
            atomic_json(state_path, state)
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return 0
        except Exception as error:
            failure = {"status": "failed", "checked_at_epoch": now, "error": str(error)}
            print(json.dumps(failure, ensure_ascii=False, indent=2))
            return 1
    if not args.force and now - int(state.get("checked_at_epoch", 0)) < args.interval_seconds:
        print(json.dumps({"status": "not-due", **state}, ensure_ascii=False, indent=2))
        return 0
    try:
        if args.release_json and os.environ.get("MEMORY_WUXIAN_TEST_ALLOW_UNSIGNED_RELEASE") != "1":
            raise ValueError("unsigned release fixtures are disabled outside tests")
        installed = current_version(skill_root)
        metadata_path = Path(args.update_metadata_json).expanduser().resolve() if args.update_metadata_json else None
        signature_path = Path(args.update_metadata_signature).expanduser().resolve() if args.update_metadata_signature else None
        if not args.release_json and metadata_path is None:
            platform_label = {"Windows": "windows", "Darwin": "macos"}.get(platform.system())
            if not platform_label:
                raise ValueError(f"Automatic release updates are unsupported on {platform.system()}")
            metadata_root = download_root / "metadata"
            metadata_path = metadata_root / f"memory-wuxian-update-{platform_label}-v1.json"
            signature_path = metadata_path.with_suffix(metadata_path.suffix + ".sig")
            metadata_root.mkdir(parents=True, exist_ok=True)
            download(f"{LATEST_RELEASE_DOWNLOAD}/{metadata_path.name}", metadata_path)
            download(f"{LATEST_RELEASE_DOWNLOAD}/{signature_path.name}", signature_path)
        if metadata_path is not None:
            if signature_path is None:
                signature_path = metadata_path.with_suffix(metadata_path.suffix + ".sig")
            allowed_signers = (
                Path(args.allowed_signers).expanduser().resolve()
                if args.allowed_signers
                else skill_root / "keys/update-allowed-signers"
            )
            metadata = load_signed_metadata(metadata_path, signature_path, allowed_signers)
            selection = select_release(metadata, args.channel, installed)
            if selection["status"] == "up-to-date":
                result = {"checked_at_epoch": now, **selection}
            elif args.check_only:
                result = {"checked_at_epoch": now, **selection, "release": selection["release"]["version"]}
            else:
                release = selection["release"]
                filename = str(release["full"].get("filename", ""))
                if not filename or Path(filename).name != filename:
                    raise ValueError("update filename is unsafe")
                destination = download_root / release["version"] / filename
                base = (
                    Path(args.base_package).expanduser().resolve().read_bytes()
                    if args.base_package
                    else b""
                )
                staged = stage_update(
                    release,
                    installed,
                    destination,
                    fetch_bytes,
                    lambda patch: apply_delta_bundle(base, patch),
                )
                result = {
                    "checked_at_epoch": now,
                    "installed_version": installed,
                    "latest_version": release["version"],
                    "channel": args.channel,
                    "package": staged["path"],
                    "sha256": staged["sha256"],
                    "status": staged["status"],
                    "artifact_kind": staged["artifact_kind"],
                    "attempts": staged["attempts"],
                    "install_approved": False,
                }
            atomic_json(state_path, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.channel != "stable":
            raise ValueError("beta and development channels require --update-metadata-json")
        release = json.loads(Path(args.release_json).read_text(encoding="utf-8")) if args.release_json else fetch_json(LATEST_RELEASE_API)
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("GitHub latest release is not a stable published release")
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
                "status": "staged-awaiting-user-approval",
                "package": str(package),
                "sha256": digest,
                "install_approved": False,
            })
        atomic_json(state_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        failure = {"status": "failed", "checked_at_epoch": now, "error": str(error)}
        staged_package = Path(str(state.get("package", ""))).expanduser()
        staged_digest = str(state.get("sha256", "")).lower()
        if (
            state.get("status") == "staged-awaiting-user-approval"
            and re.fullmatch(r"[0-9a-f]{64}", staged_digest)
            and staged_package.is_file()
            and hashlib.sha256(staged_package.read_bytes()).hexdigest() == staged_digest
        ):
            state["checked_at_epoch"] = now
            state["last_check_failure"] = failure
            atomic_json(state_path, state)
            failure["staged_approval_preserved"] = True
        else:
            atomic_json(state_path, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
