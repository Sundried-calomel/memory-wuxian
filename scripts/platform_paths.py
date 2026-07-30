#!/usr/bin/env python3
"""Cross-platform path safety helpers."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


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
