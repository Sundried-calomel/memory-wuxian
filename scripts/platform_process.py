"""Cross-platform subprocess defaults for Memory Wuxian."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Sequence


def _unique_command_argument(command: Sequence[str], option: str) -> str | None:
    matches = [index for index, value in enumerate(command) if value == option]
    if len(matches) != 1 or matches[0] + 1 >= len(command):
        return None
    return command[matches[0] + 1]


def no_window_kwargs() -> dict[str, Any]:
    """Prevent console flashes for child processes on Windows."""
    if os.name != "nt":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def detached_no_window_kwargs() -> dict[str, Any]:
    """Start a detached background process without creating a console window."""
    if os.name != "nt":
        return {}
    return {
        "creationflags": (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        ),
    }
