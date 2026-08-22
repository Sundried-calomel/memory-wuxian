from __future__ import annotations

import plistlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


class RecordingRunner:
    """Test-only command recorder with normalized string arguments."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def record(self, arguments: Iterable[object]) -> list[str]:
        command = [str(item) for item in arguments]
        self.calls.append(command)
        return command

    @staticmethod
    def completed(
        command: list[str],
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def temporary_root(
    *, ignore_cleanup_errors: bool = False
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(
        ignore_cleanup_errors=ignore_cleanup_errors
    )
    return temporary, Path(temporary.name)


def write_launch_agent_plist(
    home: Path, label: str, payload: dict[str, Any]
) -> Path:
    path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload))
    return path
