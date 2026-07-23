#!/usr/bin/env python3
"""Resolve persisted conversations from Codex and archive titles."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


THREAD_ID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
THREAD_ID_SEARCH_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
RUNTIME_TITLE_CACHE: tuple[float, dict[str, str]] = (0.0, {})


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def codex_runtime_titles() -> dict[str, str]:
    global RUNTIME_TITLE_CACHE
    if time.monotonic() - RUNTIME_TITLE_CACHE[0] < 60:
        return RUNTIME_TITLE_CACHE[1]
    bundled_codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    windows_codex = Path.home() / ".codex/.sandbox-bin/codex.exe"
    executable = next(
        (str(candidate) for candidate in (bundled_codex, windows_codex) if candidate.exists()),
        shutil.which("codex"),
    )
    if not executable:
        return {}
    process = None
    killer = None
    titles: dict[str, str] = {}
    try:
        process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        killer = threading.Timer(3, process.terminate)
        killer.start()
        assert process.stdin and process.stdout
        for request in (
            {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "memory-wuxian", "version": "1.0"}}},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "thread/list", "params": {"limit": 100, "archived": False}},
        ):
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            response = json.loads(line)
            if response.get("id") != 2:
                continue
            for thread in (response.get("result") or {}).get("data", []):
                title = thread.get("title") or thread.get("name")
                if thread.get("id") and title:
                    titles[str(thread["id"])] = str(title).strip()
            break
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError):
        titles = {}
    finally:
        if killer:
            killer.cancel()
        if process and process.poll() is None:
            process.terminate()
        if process:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
    RUNTIME_TITLE_CACHE = (time.monotonic(), titles)
    return titles


def codex_thread_title_aliases() -> dict[str, list[str]]:
    runtime_titles = codex_runtime_titles()
    sidebar_candidates: dict[str, list[str]] = defaultdict(list)
    try:
        state = json.loads((Path.home() / ".codex/.codex-global-state.json").read_text(encoding="utf-8"))

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                object_id = str(value.get("id", ""))
                object_title = value.get("title")
                if THREAD_ID_PATTERN.fullmatch(object_id) and isinstance(object_title, str):
                    sidebar_candidates[object_id].append(object_title.strip())
                for key, child in value.items():
                    key_text = str(key)
                    embedded_id = THREAD_ID_SEARCH_PATTERN.search(key_text)
                    candidate_id = key_text if THREAD_ID_PATTERN.fullmatch(key_text) else (
                        embedded_id.group(0) if embedded_id and "title" in key_text.casefold() else None
                    )
                    if candidate_id and isinstance(child, str):
                        candidate = child.strip()
                        if candidate and "\\" not in candidate and "/" not in candidate and len(candidate) <= 120:
                            sidebar_candidates[candidate_id].append(candidate)
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(state)
    except (OSError, json.JSONDecodeError):
        pass

    database_titles: dict[str, str] = {}
    database = Path.home() / ".codex/state_5.sqlite"
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            database_titles = {
                str(thread_id): str(title).strip()
                for thread_id, title in connection.execute("SELECT id, title FROM threads")
                if str(title).strip()
            }
        finally:
            connection.close()
    except sqlite3.Error:
        pass
    aliases: dict[str, list[str]] = {}
    for thread_id in set(runtime_titles) | set(database_titles) | set(sidebar_candidates):
        candidates = [
            runtime_titles.get(thread_id, ""),
            database_titles.get(thread_id, ""),
            *sidebar_candidates.get(thread_id, []),
        ]
        aliases[thread_id] = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    return aliases


def codex_thread_titles() -> dict[str, str]:
    return {
        thread_id: candidates[0]
        for thread_id, candidates in codex_thread_title_aliases().items()
        if candidates
    }


def codex_thread_metadata() -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    database = Path.home() / ".codex/state_5.sqlite"
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
        try:
            for thread_id, archived, cwd in connection.execute(
                "SELECT id, archived, cwd FROM threads"
            ):
                metadata[str(thread_id)] = {
                    "archived": bool(archived),
                    "cwd": str(cwd or ""),
                }
        finally:
            connection.close()
    except sqlite3.Error:
        pass

    try:
        state = json.loads((Path.home() / ".codex/.codex-global-state.json").read_text(encoding="utf-8"))
        persisted = state.get("electron-persisted-atom-state") or {}
        projects = state.get("local-projects") or persisted.get("local-projects") or {}
        assignments = (
            state.get("thread-project-assignments")
            or persisted.get("thread-project-assignments")
            or {}
        )
        project_by_root = {
            str(root).rstrip("/\\"): str(project.get("name") or "").strip()
            for project in projects.values()
            if isinstance(project, dict)
            for root in project.get("rootPaths", [])
            if str(project.get("name") or "").strip()
        }
        for thread_id, assignment in assignments.items():
            if not isinstance(assignment, dict):
                continue
            project = projects.get(str(assignment.get("projectId"))) or {}
            project_name = str(project.get("name") or "").strip()
            entry = metadata.setdefault(str(thread_id), {"archived": False, "cwd": ""})
            if project_name:
                entry["project"] = project_name
        for entry in metadata.values():
            cwd = str(entry.get("cwd") or "").rstrip("/\\")
            if not entry.get("project") and cwd in project_by_root:
                entry["project"] = project_by_root[cwd]
    except (OSError, json.JSONDecodeError):
        pass

    for entry in metadata.values():
        if not entry.get("project"):
            cwd = str(entry.get("cwd") or "").rstrip("/\\")
            entry["project"] = Path(cwd).name if cwd else "Unassigned"
    return metadata


def archive_conversation_titles(records: Iterable[dict[str, Any]]) -> dict[str, str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("conversation_id", ""))].append(record)
    codex_titles = codex_thread_titles()
    titles: dict[str, str] = {}
    for conversation_id, items in grouped.items():
        native_id = conversation_id.removeprefix("codex:")
        source_title = next(
            (str(item.get("source", {}).get("conversation_title")).strip() for item in items if item.get("source", {}).get("conversation_title")),
            "",
        )
        first_user = next(
            (str(item.get("text", "")).strip().replace("\n", " ")[:72] for item in items if item.get("speaker") == "user"),
            conversation_id,
        )
        titles[conversation_id] = codex_titles.get(native_id, source_title or first_user)
    return titles


def archive_conversation_title_aliases(records: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    items = list(records)
    canonical = archive_conversation_titles(items)
    codex_aliases = codex_thread_title_aliases()
    aliases: dict[str, list[str]] = {}
    for conversation_id, title in canonical.items():
        source_titles = [
            str(record.get("source", {}).get("conversation_title")).strip()
            for record in items
            if str(record.get("conversation_id")) == conversation_id
            and record.get("source", {}).get("conversation_title")
        ]
        first_user = next(
            (
                str(record.get("text", "")).strip().replace("\n", " ")[:72]
                for record in items
                if str(record.get("conversation_id")) == conversation_id
                and record.get("speaker") == "user"
            ),
            "",
        )
        candidates = [
            title,
            *codex_aliases.get(conversation_id.removeprefix("codex:"), []),
            *source_titles,
            first_user,
        ]
        aliases[conversation_id] = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    return aliases


def resolve_conversation_title(
    title: str,
    titles: dict[str, list[str]],
    excluded_conversation_ids: Iterable[str] = (),
) -> tuple[str, str]:
    target = normalize_title(title)
    if not target:
        raise ValueError("Conversation title must not be empty")
    excluded = {str(value) for value in excluded_conversation_ids}
    exact = [
        (conversation_id, value)
        for conversation_id, values in titles.items()
        if conversation_id not in excluded
        for value in values
        if normalize_title(value) == target
    ]
    matches = exact or [
        (conversation_id, value)
        for conversation_id, values in titles.items()
        if conversation_id not in excluded
        for value in values
        if target in normalize_title(value)
    ]
    matches = list({conversation_id: (conversation_id, value) for conversation_id, value in matches}.values())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No archived conversation matched title: {title}")
    candidates = "; ".join(f"{value} [{conversation_id}]" for conversation_id, value in sorted(matches))
    raise ValueError(f"Conversation title is ambiguous: {title}. Candidates: {candidates}")
