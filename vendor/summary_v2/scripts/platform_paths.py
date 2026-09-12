#!/usr/bin/env python3
"""Cross-platform path safety helpers."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def filesystem_native_path(path: Path) -> str:
    """Return a Windows extended-length path without changing its logical identity."""
    value = str(Path(path).expanduser().resolve())
    if os.name != "nt" or value.startswith("\\\\?\\") or len(value) < 240:
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def is_link_like(path: Path) -> bool:
    """Return true for symbolic links and Windows directory junctions."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    if os.name != "nt" or not path.exists():
        return False
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x400)
