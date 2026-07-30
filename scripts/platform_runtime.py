#!/usr/bin/env python3
"""Platform path helpers shared by MemoryWuxian installers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def executable_entry_path(
    value: str | Path,
    *,
    platform_name: str | None = None,
) -> Path:
    """Return an absolute executable entry without dereferencing macOS symlinks."""

    platform_name = platform_name or sys.platform
    expanded = Path(value).expanduser()
    if platform_name == "darwin":
        return Path(os.path.abspath(os.fspath(expanded)))
    return expanded.resolve()

