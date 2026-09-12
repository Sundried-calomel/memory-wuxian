"""Configure Unicode-safe command output on Windows."""

from __future__ import annotations

import os
import sys
from typing import TextIO


def _configure_stream(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    if stream.isatty():
        reconfigure(errors="backslashreplace")
    else:
        reconfigure(encoding="utf-8", errors="strict")


def configure_unicode_stdio() -> None:
    """Keep redirected output lossless and legacy Windows consoles non-fatal."""
    if os.name != "nt":
        return
    _configure_stream(sys.stdout)
    _configure_stream(sys.stderr)
