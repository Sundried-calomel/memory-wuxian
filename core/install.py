"""Explicit changed-file application with a small persistent rollback journal."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from storage import atomic_replace_bytes, atomic_write_json, exclusive_lock, file_sha256, safe_target


class ChangedFileInstaller:
    def __init__(self, target):
        self.target = Path(target).absolute()
        self.journal = safe_target(self.target, ".assembly-apply-journal.json")
        self.lock = safe_target(self.target, ".assembly-apply.lock")

    @staticmethod
    def _digest(path):
        return file_sha256(path) if path.is_file() else None

    def apply(self, files: Mapping[str, bytes], load_check: Callable | None = None) -> dict:
        if not isinstance(files, Mapping) or any(not isinstance(v, bytes) for v in files.values()):
            raise ValueError("files must map relative paths to exact bytes")
        self.target.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.lock):
            if self.journal.exists():
                raise RuntimeError("unfinished apply journal exists; call recover() first")
            changed, entries, seen = {}, [], set()
            for relative, content in files.items():
                if str(relative).casefold() in {'.assembly-apply.lock', '.assembly-apply-journal.json'}:
                    raise ValueError('package cannot overwrite transaction control files')
                path = safe_target(self.target, relative)
                identity = str(path).casefold() if os.name == 'nt' else str(path)
                if identity in seen:
                    raise ValueError('multiple package names refer to the same target')
                seen.add(identity)
                if path.exists() and not path.is_file():
                    raise ValueError('file replacement target is not a regular file')
                old = path.read_bytes() if path.is_file() else None
                if old == content:
                    continue
                mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else None
                changed[path] = content
                entries.append({"relative": relative, "old_exists": old is not None,
                                "old_base64": base64.b64encode(old).decode("ascii") if old is not None else None,
                                "old_mode": mode,
                                "old_sha256": hashlib.sha256(old).hexdigest() if old is not None else None,
                                "new_sha256": hashlib.sha256(content).hexdigest()})
            if not changed:
                return {"status": "unchanged", "changed": []}
            atomic_write_json(self.journal, {"schema": 1, "root": str(self.target), "entries": entries})
            try:
                for (path, content), entry in zip(changed.items(), entries):
                    atomic_replace_bytes(path, content, mode=entry['old_mode'])
                if load_check is not None and load_check(self.target, tuple(changed)) is False:
                    raise RuntimeError("single entry load check rejected applied files")
            except BaseException as original:
                try:
                    self._recover_locked()
                except BaseException as rollback_error:
                    raise RuntimeError(f"apply failed and recovery is pending: {rollback_error}") from original
                raise
            self.journal.unlink()
            return {"status": "applied", "changed": [str(p) for p in changed],
                    "sha256": {str(p): file_sha256(p) for p in changed}}

    def recover(self) -> dict:
        self.target.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.lock):
            return self._recover_locked()

    def _recover_locked(self) -> dict:
        if not self.journal.exists():
            return {"status": "clean", "restored": []}
        try:
            journal = json.loads(self.journal.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("apply journal is unreadable; no targets changed") from exc
        if (not isinstance(journal, dict) or journal.get("schema") != 1
                or journal.get("root") != str(self.target) or not isinstance(journal.get("entries"), list)):
            raise RuntimeError("apply journal identity/schema mismatch; no targets changed")
        checked = []
        for item in journal["entries"]:
            if not isinstance(item, dict) or not isinstance(item.get("relative"), str):
                raise RuntimeError("invalid journal entry; no targets changed")
            path = safe_target(self.target, item["relative"])
            current_hash = self._digest(path)
            old_hash = item.get("old_sha256") if item.get("old_exists") else None
            if current_hash not in {old_hash, item.get("new_sha256")}:
                raise RuntimeError(f"target no longer matches this transaction; recovery refused: {item['relative']}")
            if item.get("old_exists"):
                try:
                    old_bytes = base64.b64decode(item["old_base64"], validate=True)
                except Exception as exc:
                    raise RuntimeError("invalid journal backup bytes; no targets changed") from exc
                if hashlib.sha256(old_bytes).hexdigest() != old_hash:
                    raise RuntimeError("journal backup hash mismatch; no targets changed")
            elif item.get("old_base64") is not None:
                raise RuntimeError("invalid absent-file journal entry; no targets changed")
            checked.append((path, item, current_hash))
        for path, item, current_hash in reversed(checked):
            old_hash = item.get("old_sha256") if item.get("old_exists") else None
            if current_hash == old_hash:
                continue
            if item["old_exists"]:
                old_bytes = base64.b64decode(item["old_base64"], validate=True)
                atomic_replace_bytes(path, old_bytes, mode=item.get("old_mode"))
            else:
                path.unlink(missing_ok=True)
        self.journal.unlink()
        return {"status": "recovered", "restored": [item["relative"] for _, item, _ in checked]}


def register_windows_task(task_name: str, xml_path, *, runner=subprocess.run) -> None:
    """Perform explicit Windows Task Scheduler registration through injected runner."""
    if os.name != "nt":
        raise RuntimeError("Windows task registration requires Windows")
    runner(["schtasks.exe", "/Create", "/TN", task_name, "/XML", str(Path(xml_path).absolute()), "/F"], check=True)


def register_macos_job(domain: str, plist_path, *, runner=subprocess.run) -> None:
    """Perform explicit launchd registration through injected runner."""
    if not domain or not domain.startswith("gui/"):
        raise ValueError("launchd GUI domain is required")
    runner(["/bin/launchctl", "bootstrap", domain, str(Path(plist_path).absolute())], check=True)
