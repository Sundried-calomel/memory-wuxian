"""Shared atomic writes, confined paths, hashes and reentrant process locks."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

BeforeReplace = Callable[[Path, Path], None]

def canonical_json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def bytes_sha256(value):
    return hashlib.sha256(value).hexdigest()

def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def safe_target(root, relative):
    root = Path(root).absolute()
    relative = str(relative)
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).as_posix() != relative or relative.startswith('/') or '\\' in relative or ':' in relative or any(p in {'..', '.'} or p.endswith((' ', '.')) for p in parts):
        raise ValueError('unsafe relative path')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if any(p.split('.')[0].upper() in reserved for p in parts):
        raise ValueError('reserved target path')
    target = root.joinpath(*parts)
    for part in [root, *[root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]]:
        if part.is_symlink() or (part.exists() and getattr(part.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400):
            raise ValueError('linked target path')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('target escaped root')
    return target

# Source: platform_atomic.py:17-22 (native_filesystem_path)
def native_filesystem_path(path: Path) -> Path:
    """Use extended Windows paths for file operations beyond MAX_PATH."""
    value = str(path.resolve())
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return Path(value)
    return Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)

# Source: platform_atomic.py:25-30 (ParentSync)
class ParentSync(str, Enum):
    """Durability policy for the destination parent directory."""

    NONE = "none"
    BEST_EFFORT = "best-effort"
    REQUIRED = "required"

# Source: platform_atomic.py:33-54 (sync_directory)
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

# Source: platform_atomic.py:57-100 (atomic_replace_bytes)
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

# Source: platform_atomic.py:103-118 (durable_replace)
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

# Source: platform_atomic.py:121-124 (atomic_write_json)
def atomic_write_json(path: Path, payload, *, parent_sync: ParentSync = ParentSync.NONE) -> None:
    """Persist standard product JSON through the shared exact-byte writer."""
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_replace_bytes(path, data, parent_sync=parent_sync)

# Source: platform_lock.py:18-44 (exclusive_lock)
@contextlib.contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            # Windows permits locking a byte range beyond EOF. Avoid writing an
            # initialization byte here: concurrent first users can otherwise
            # race and one write fails with EACCES after the other acquires it.
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EDEADLK, errno.EAGAIN}:
                        raise
                    time.sleep(0.05)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

_locks = {}
_locks_guard = threading.Lock()
_held = threading.local()

@contextlib.contextmanager
def exclusive_lock(path):
    path = Path(path).absolute()
    key = str(path).casefold() if os.name == 'nt' else str(path)
    with _locks_guard:
        mutex = _locks.setdefault(key, threading.RLock())
    with mutex:
        active = getattr(_held, 'paths', set())
        if key in active:
            yield
            return
        with _file_lock(path):
            _held.paths = active | {key}
            try:
                yield
            finally:
                _held.paths = active
