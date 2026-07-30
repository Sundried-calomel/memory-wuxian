#!/usr/bin/env python3
"""Install or remove the daily MemoryWuxian release check."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from platform_process import no_window_kwargs
try:
    from platform_runtime import executable_entry_path
except ModuleNotFoundError:
    from scripts.platform_runtime import executable_entry_path


WINDOWS_TASK = "MemoryWuxianAutoUpdate"
WINDOWS_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = "MemoryWuxianAutoUpdate"
MACOS_LABEL = "com.memorywuxian.auto-update"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    skill_root = Path(args.skill_root).expanduser().resolve()
    python = executable_entry_path(args.python_executable)
    updater = skill_root / "scripts/auto_update.py"
    if os.name == "nt":
        if args.uninstall:
            subprocess.run(["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK, "/F"], check=False, **no_window_kwargs())
            subprocess.run(["reg.exe", "DELETE", WINDOWS_RUN_KEY, "/V", WINDOWS_RUN_VALUE, "/F"], check=False, **no_window_kwargs())
            return 0
        pythonw = python.with_name("pythonw.exe")
        if not pythonw.is_file():
            pythonw = python
        command = subprocess.list2cmdline(
            [str(pythonw), str(updater), "--skill-root", str(skill_root)]
        )
        task = subprocess.run(
            ["schtasks.exe", "/Create", "/TN", WINDOWS_TASK, "/SC", "DAILY", "/ST", "03:00",
             "/RL", "LIMITED", "/TR", command, "/F"],
            check=False, capture_output=True, text=True, **no_window_kwargs(),
        )
        if task.returncode == 0:
            print(f"task:{WINDOWS_TASK}")
            return 0
        run_command = command
        subprocess.run(["reg.exe", "ADD", WINDOWS_RUN_KEY, "/V", WINDOWS_RUN_VALUE,
                        "/T", "REG_SZ", "/D", run_command, "/F"], check=True,
                       **no_window_kwargs())
        print(f"run-key:{WINDOWS_RUN_KEY}\\{WINDOWS_RUN_VALUE}")
        return 0
    if sys.platform != "darwin":
        raise SystemExit("Automatic update registration supports Windows and macOS")
    output = Path.home() / f"Library/LaunchAgents/{MACOS_LABEL}.plist"
    if args.uninstall:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(output)], check=False)
        output.unlink(missing_ok=True)
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": MACOS_LABEL,
        "ProgramArguments": [str(python), str(updater), "--skill-root", str(skill_root)],
        "RunAtLoad": True,
        "StartInterval": 86400,
        "ProcessType": "Background",
        "StandardOutPath": str(Path.home() / "Library/Logs/MemoryWuxian-update.log"),
        "StandardErrorPath": str(Path.home() / "Library/Logs/MemoryWuxian-update.error.log"),
    }
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(output)], check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(output)], check=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
