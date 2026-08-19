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
