#!/usr/bin/env python3
"""Exact-byte atomic replacement primitives owned by Platform Foundation."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from enum import Enum
from pathlib import Path


BeforeReplace = Callable[[Path, Path], None]


class ParentSync(str, Enum):
    """Durability policy for the destination parent directory."""

    NONE = "none"
    BEST_EFFORT = "best-effort"
    REQUIRED = "required"


def sync_directory(path: Path, *, policy: ParentSync) -> None:
    """Synchronize a directory according to an explicit portability policy."""

    policy = ParentSync(policy)
    if policy is ParentSync.NONE or (policy is ParentSync.REQUIRED and os.name == "nt"):
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(Path(path), flags)
    except (AttributeError, OSError):
        if policy is ParentSync.REQUIRED:
            raise
        return
    try:
        os.fsync(descriptor)
    except OSError:
        if policy is ParentSync.REQUIRED:
            raise
    finally:
        os.close(descriptor)


def atomic_replace_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int | None = None,
    parent_sync: ParentSync = ParentSync.NONE,
    before_replace: BeforeReplace | None = None,
    create_parent: bool = True,
) -> None:
    """Write exact bytes, fsync, atomically replace, then sync the parent."""

    path = Path(path)
    parent_sync = ParentSync(parent_sync)
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None and not hasattr(os, "fchmod"):
            os.chmod(temporary, mode)
        if before_replace is not None:
            before_replace(temporary, path)
        os.replace(temporary, path)
        sync_directory(path.parent, policy=parent_sync)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def durable_replace(
    source: Path,
    destination: Path,
    *,
    parent_sync: ParentSync,
) -> None:
    """Replace an existing path and synchronize every changed directory."""

    source = Path(source)
    destination = Path(destination)
    source_parent = source.parent
    destination_parent = destination.parent
    os.replace(source, destination)
    sync_directory(destination_parent, policy=parent_sync)
    if source_parent != destination_parent:
        sync_directory(source_parent, policy=parent_sync)
