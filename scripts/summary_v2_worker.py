#!/usr/bin/env python3
"""Run one explicit summary-v2 model call and persist only an external sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from console_encoding import configure_unicode_stdio
from memory_atoms import read_json
from memory_cli import load_simple_yaml, nested_get
from memory_summary_v2 import (
    MAX_SIDECAR_BYTES,
    SummaryV2Error,
    SOURCE_CHILDREN,
    build_level_1_source,
    build_parent_source,
    normalize_model_candidate,
    persist_sidecar,
    project,
    public_source,
    validate_sidecar,
)
from platform_process import no_window_kwargs


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODEX = Path(
    "codex.exe" if os.name == "nt" else "/Applications/ChatGPT.app/Contents/Resources/codex"
)
DEFAULT_PROMPT_LIMIT = 900_000
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024


def parse_result(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_CANDIDATE_BYTES:
        raise SummaryV2Error(
            f"summary-v2 model output exceeds {MAX_CANDIDATE_BYTES} bytes"
        )
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise SummaryV2Error("summary-v2 model output must be a JSON object")
    return value


def build_prompt(source: dict[str, Any]) -> str:
    prompt_name = (
        "summarize-v2-parent.md"
        if source["source_kind"] == SOURCE_CHILDREN
        else "summarize-v2.md"
    )
    instructions = (ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
    public = public_source(source)
    payload = {
        "task": {
            "format_version": 2,
            "job_id": source["job_id"],
            "summary_level": source["summary_level"],
            "conversation_id": source["conversation_id"],
            "source_kind": source["source_kind"],
            "source_sha256": source["source_sha256"],
            "source_refs": source["source_refs"],
            "source_ref_catalog": source["ref_catalog"],
            "required_locators": source["required_locators"],
        },
        "source_manifest": public["source_manifest"],
        "source_payload": source["prompt_payload"],
    }
    return instructions + "\n\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"


def codex_command(
    config: dict[str, Any], source: dict[str, Any]
) -> tuple[list[str], int, int]:
    codex_key = "codex_cli_path_windows" if os.name == "nt" else "codex_cli_path"
    codex = Path(
        os.environ.get(
            "MEMORY_WUXIAN_CODEX",
            str(nested_get(config, ["ai_summary", codex_key], DEFAULT_CODEX)),
        )
    ).expanduser()
    timeout = int(nested_get(config, ["ai_summary", "timeout_seconds"], 900))
    prompt_limit = min(
        DEFAULT_PROMPT_LIMIT,
        int(nested_get(config, ["ai_summary", "max_prompt_utf8_bytes"], DEFAULT_PROMPT_LIMIT)),
    )
    model = str(nested_get(config, ["ai_summary", "model"], "")).strip()
    schema_name = (
        "summary-v2-parent-result.schema.json"
        if source["source_kind"] == SOURCE_CHILDREN
        else "summary-v2-result.schema.json"
    )
    command = [
        str(codex),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(ROOT / "schemas" / schema_name),
    ]
    if model:
        command.extend(["--model", model])
    return command, timeout, prompt_limit


def invoke_codex(command: list[str], timeout: int, prompt: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-summary-v2-") as temporary:
        output = Path(temporary) / "candidate.json"
        completed = subprocess.run(
            [*command, "--output-last-message", str(output), "-"],
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            check=False,
            cwd=tempfile.gettempdir(),
            **no_window_kwargs(),
        )
        if completed.returncode != 0:
            raise SummaryV2Error(
                f"one-shot summary-v2 model call failed ({completed.returncode}): "
                + completed.stderr[-2000:]
            )
        return parse_result(output)


def run_source(
    source: dict[str, Any],
    output_directory: Path,
    archive_root: Path,
    *,
    config_path: Path | None = None,
    candidate: dict[str, Any] | None = None,
    dry_run: bool = False,
    invoker: Callable[[list[str], int, str], dict[str, Any]] = invoke_codex,
) -> dict[str, Any]:
    prompt = build_prompt(source)
    prompt_bytes = len(prompt.encode("utf-8"))
    command: list[str] = []
    timeout = 0
    prompt_limit = DEFAULT_PROMPT_LIMIT
    if candidate is None:
        if config_path is None:
            raise SummaryV2Error("--config is required when no candidate file is supplied")
        config = load_simple_yaml(Path(config_path))
        command, timeout, prompt_limit = codex_command(config, source)
    if prompt_bytes > prompt_limit:
        raise SummaryV2Error(
            f"summary-v2 prompt exceeds the {prompt_limit}-byte staged limit; no partial compression is allowed"
        )
    if dry_run:
        return {
            "status": "dry-run",
            "job_id": source["job_id"],
            "summary_level": source["summary_level"],
            "source_ref_count": len(source["source_refs"]),
            "prompt_utf8_bytes": prompt_bytes,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "command": command,
        }
    model_called = candidate is None
    if candidate is None:
        candidate = invoker(command, timeout, prompt)
    candidate = normalize_model_candidate(candidate, source)
    sidecar = project(source, candidate)
    bundle, status = persist_sidecar(sidecar, output_directory, archive_root)
    return {
        "status": status,
        "bundle": str(bundle),
        "summary_v2_id": sidecar["summary_v2_id"],
        "summary_level": sidecar["summary_level"],
        "model_called": model_called,
        "prompt_utf8_bytes": prompt_bytes,
        "projection_sha256": sidecar["projection_sha256"],
    }


def load_sidecar(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_dir():
        path = path / "summary.json"
    return validate_sidecar(read_json(path, MAX_SIDECAR_BYTES))


def main() -> int:
    configure_unicode_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    l1 = commands.add_parser("l1")
    l1.add_argument("--job", required=True)
    parent = commands.add_parser("parent")
    parent.add_argument("--child", action="append", required=True)
    for command in (l1, parent):
        command.add_argument("--config")
        command.add_argument("--candidate")
        command.add_argument("--output-dir", required=True)
        command.add_argument("--archive-root", required=True)
        command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "l1":
            source = build_level_1_source(
                read_json(Path(args.job), 16 * 1024 * 1024)
            )
        else:
            source = build_parent_source(load_sidecar(Path(path)) for path in args.child)
        candidate = (
            read_json(Path(args.candidate), 8 * 1024 * 1024)
            if args.candidate
            else None
        )
        result = run_source(
            source,
            Path(args.output_dir),
            Path(args.archive_root),
            config_path=Path(args.config) if args.config else None,
            candidate=candidate,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (SummaryV2Error, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"memory-wuxian summary-v2 worker: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
