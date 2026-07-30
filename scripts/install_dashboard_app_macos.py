#!/usr/bin/env python3
"""Install or verify the current macOS Memory Wuxian dashboard application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from platform_runtime import executable_entry_path
except ModuleNotFoundError:
    from scripts.platform_runtime import executable_entry_path


APP_NAME = "Memory無限操作台.app"
CONFIG_NAME = "memory-wuxian-dashboard-launcher.json"
INSTALLATION_NAME = "memory-wuxian-dashboard-installation.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version(skill_root: Path) -> str:
    import tomllib

    with (skill_root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def app_metadata(app: Path) -> tuple[Path, str]:
    plist = app / "Contents/Info.plist"
    executable = app / "Contents/MacOS/MemoryDashboard"
    if not plist.is_file() or not executable.is_file():
        raise ValueError(f"incomplete dashboard application: {app}")
    with plist.open("rb") as handle:
        payload = plistlib.load(handle)
    return executable, str(payload.get("CFBundleShortVersionString", ""))


def launcher_payload(
    python_executable: Path, skill_root: Path, archive_root: Path
) -> dict:
    return {
        "schema_version": 1,
        "python_executable": str(python_executable),
        "skill_root": str(skill_root),
        "archive_root": str(archive_root),
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_bytes(path, previous)


def replace_app(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.installing"
    previous = destination.parent / f".{destination.name}.previous"
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    shutil.copytree(source, temporary, symlinks=True)
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(temporary)],
        check=True,
        capture_output=True,
        text=True,
    )
    if destination.exists():
        try:
            os.replace(destination, previous)
        except PermissionError:
            subprocess.run(
                ["/usr/bin/ditto", str(temporary), str(destination)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["codesign", "--verify", "--deep", "--strict", str(destination)],
                check=True,
                capture_output=True,
                text=True,
            )
            shutil.rmtree(temporary, ignore_errors=True)
            return
    try:
        os.replace(temporary, destination)
    except Exception:
        if previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def verify(
    *,
    app: Path,
    config_path: Path,
    installation_path: Path,
    expected_version: str,
) -> dict:
    executable, installed_version = app_metadata(app)
    if installed_version != expected_version:
        raise ValueError(
            f"dashboard version mismatch: {installed_version} != {expected_version}"
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported dashboard launcher configuration")
    for key in ("python_executable", "skill_root", "archive_root"):
        value = Path(str(config.get(key, "")))
        if not value.is_absolute() or not value.exists():
            raise ValueError(f"dashboard launcher path is invalid: {key}")
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)],
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment["MEMORY_WUXIAN_DASHBOARD_CONFIG"] = str(config_path)
    self_check = subprocess.run(
        [str(executable), "--self-check"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = json.loads(self_check.stdout)
    if payload.get("status") != "ok":
        raise ValueError("dashboard self-check did not pass")
    result = {
        "status": "ok",
        "version": installed_version,
        "app": str(app),
        "executable_sha256": sha256(executable),
        "config": str(config_path),
        "paths": payload,
    }
    if installation_path.exists():
        recorded = json.loads(installation_path.read_text(encoding="utf-8"))
        if recorded.get("executable_sha256") != result["executable_sha256"]:
            raise ValueError("dashboard installation manifest hash mismatch")
    return result


def install(
    *,
    source: Path,
    destination: Path,
    config_path: Path,
    installation_path: Path,
    python_executable: Path,
    skill_root: Path,
    archive_root: Path,
    version: str,
) -> dict:
    previous_config = config_path.read_bytes() if config_path.is_file() else None
    previous_installation = (
        installation_path.read_bytes() if installation_path.is_file() else None
    )
    rollback_app = destination.parent / f".{destination.name}.transaction-rollback"
    shutil.rmtree(rollback_app, ignore_errors=True)
    if destination.exists():
        shutil.copytree(destination, rollback_app, symlinks=True)
    try:
        replace_app(source, destination)
        atomic_json(
            config_path,
            launcher_payload(python_executable, skill_root, archive_root),
        )
        executable, _ = app_metadata(destination)
        atomic_json(
            installation_path,
            {
                "schema_version": 1,
                "version": version,
                "app": str(destination),
                "executable_sha256": sha256(executable),
                "config": str(config_path),
            },
        )
        return verify(
            app=destination,
            config_path=config_path,
            installation_path=installation_path,
            expected_version=version,
        )
    except Exception:
        if rollback_app.exists():
            if destination.exists():
                subprocess.run(
                    ["/usr/bin/ditto", str(rollback_app), str(destination)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                os.replace(rollback_app, destination)
        else:
            shutil.rmtree(destination, ignore_errors=True)
        restore_file(config_path, previous_config)
        restore_file(installation_path, previous_installation)
        raise
    finally:
        shutil.rmtree(rollback_app, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--source-app")
    parser.add_argument("--destination")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    skill_root = Path(args.skill_root).expanduser().resolve()
    archive_root = Path(args.archive_root).expanduser().resolve()
    python_executable = executable_entry_path(args.python_executable)
    destination = Path(
        args.destination or f"~/Desktop/{APP_NAME}"
    ).expanduser().resolve()
    codex_home = skill_root.parent.parent
    config_path = codex_home / CONFIG_NAME
    installation_path = codex_home / INSTALLATION_NAME
    version = project_version(skill_root)

    if not args.verify_only:
        source = Path(
            args.source_app or skill_root / "assets/macos" / APP_NAME
        ).expanduser().resolve()
        if not source.is_dir():
            raise SystemExit(f"packaged dashboard application does not exist: {source}")
        if not archive_root.is_dir() or not python_executable.is_file():
            raise SystemExit("dashboard runtime paths do not exist")
        result = install(
            source=source,
            destination=destination,
            config_path=config_path,
            installation_path=installation_path,
            python_executable=python_executable,
            skill_root=skill_root,
            archive_root=archive_root,
            version=version,
        )
    else:
        result = verify(
            app=destination,
            config_path=config_path,
            installation_path=installation_path,
            expected_version=version,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
