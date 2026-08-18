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
import time
from pathlib import Path
from typing import Any, Callable

from console_encoding import configure_unicode_stdio
from memory_atoms import read_json
from memory_cli import load_simple_yaml, nested_get
from memory_summary_v2 import (
    MAX_SIDECAR_BYTES,
    SummaryV2Error,
    SOURCE_CHILDREN,
    SOURCE_RESCUE_MAPS,
    SOURCE_PARENT_RESCUE_MAPS,
    build_level_1_source,
    build_parent_source,
    normalize_model_candidate,
    persist_sidecar,
    project,
    public_source,
    validate_sidecar,
)
from platform_process import no_window_kwargs
from platform_transaction import atomic_write_canonical_json


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODEX = Path(
    "codex.exe" if os.name == "nt" else "/Applications/ChatGPT.app/Contents/Resources/codex"
)
DEFAULT_PROMPT_LIMIT = 900_000
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024


class CodexInvocationError(SummaryV2Error):
    """A one-shot invocation failed with structured diagnostic evidence."""

    def __init__(self, message: str, diagnostic: dict[str, Any]):
        super().__init__(message)
        self.diagnostic = diagnostic


def _text_evidence(value: str) -> dict[str, Any]:
    encoded = value.encode("utf-8", errors="replace")
    return {
        "utf8_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "head": value[:2000],
        "tail": value[-8000:],
    }


def _compact_parent_child(sidecar: dict[str, Any]) -> dict[str, Any]:
    """Keep model-useful parent evidence while retaining hashes for full lookup."""
    return {
        "summary_v2_id": sidecar["summary_v2_id"],
        "summary_level": sidecar["summary_level"],
        "projection_sha256": sidecar["projection_sha256"],
        "source_sha256": sidecar["source"]["source_sha256"],
        "overview": sidecar["overview"],
        "scenes": sidecar["scenes"],
        "atoms": sidecar["atoms"],
        "relations": sidecar["relations"],
        "omissions": sidecar["omissions"],
        "coverage": {
            "source_ref_count": sidecar["coverage"]["source_ref_count"],
            "represented_source_refs": sidecar["coverage"]["represented_source_refs"],
            "omitted_source_refs": sidecar["coverage"]["omitted_source_refs"],
            "raw_message_count": sidecar["coverage"]["raw_message_count"],
            "silent_loss_count": sidecar["coverage"]["silent_loss_count"],
        },
    }


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
        else (
            "summarize-v2-parent-rescue-reduce.md"
            if source["source_kind"] == SOURCE_PARENT_RESCUE_MAPS
            else
            "summarize-v2-rescue-reduce.md"
            if source["source_kind"] == SOURCE_RESCUE_MAPS
            else "summarize-v2.md"
        )
    )
    instructions = (ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
    public = public_source(source)
    rescue_source = source["source_kind"] in {
        SOURCE_RESCUE_MAPS,
        SOURCE_PARENT_RESCUE_MAPS,
    }
    source_payload = source["prompt_payload"]
    if source["source_kind"] == SOURCE_CHILDREN and source.get("compact_parent_prompt"):
        source_payload = {
            "child_sidecars": [
                _compact_parent_child(sidecar)
                for sidecar in source_payload.get("child_sidecars", [])
            ]
        }
    if rescue_source:
        source_payload = {
            "map_sidecars": [
                {
                    "summary_level": sidecar["summary_level"],
                    "source": {
                        "source_kind": sidecar["source"]["source_kind"],
                        "raw_message_ids": sidecar["source"]["raw_message_ids"],
                    },
                    "overview": sidecar["overview"],
                    "scenes": sidecar["scenes"],
                    "atoms": sidecar["atoms"],
                    "relations": sidecar["relations"],
                    "omissions": sidecar["omissions"],
                }
                for sidecar in source_payload.get("map_sidecars", [])
            ]
        }
    required_locator_refs = list(
        dict.fromkeys(locator["source_ref"] for locator in source["required_locators"])
    )
    task = {
        "format_version": 2,
        "job_id": source["job_id"],
        "summary_level": source["summary_level"],
        "conversation_id": source["conversation_id"],
        "source_kind": source["source_kind"],
        "source_sha256": source["source_sha256"],
        "source_refs": source["source_refs"],
        "source_ref_catalog": source["ref_catalog"],
        "required_locators": source["required_locators"],
    }
    if source["source_kind"] == SOURCE_CHILDREN and source.get("compact_parent_prompt"):
        task["source_ref_catalog"] = [
            {
                "source_ref": item["source_ref"],
                "source_message_count": len(item["source_message_ids"]),
                "source_message_ids_sha256": hashlib.sha256(
                    json.dumps(
                        item["source_message_ids"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            for item in source["ref_catalog"]
        ]
    if rescue_source:
        task["required_locators"] = []
        task["deterministic_locator_count"] = len(source["required_locators"])
        task["deterministic_locator_source_refs"] = required_locator_refs
    payload = {
        "task": task,
        "source_manifest": (
            {
                "kind": public["source_manifest"]["kind"],
                "validated_record_count": len(public["raw_message_manifest"]),
            }
            if rescue_source
            else public["source_manifest"]
        ),
        "source_payload": source_payload,
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
        if source["source_kind"] in {SOURCE_CHILDREN, SOURCE_PARENT_RESCUE_MAPS}
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
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [*command, "--output-last-message", str(output), "-"],
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
                check=False,
                cwd=tempfile.gettempdir(),
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            raise CodexInvocationError(
                f"one-shot summary-v2 model call timed out after {timeout}s",
                {
                    "classification": "infra-timeout",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "returncode": None,
                    "stdout": _text_evidence(stdout),
                    "stderr": _text_evidence(stderr),
                    "candidate_exists": output.exists(),
                    "candidate_sha256": hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None,
                },
            ) from exc
        if completed.returncode != 0:
            raise CodexInvocationError(
                f"one-shot summary-v2 model call failed ({completed.returncode})",
                {
                    "classification": "model-process-failure",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "returncode": completed.returncode,
                    "stdout": _text_evidence(completed.stdout),
                    "stderr": _text_evidence(completed.stderr),
                    "candidate_exists": output.exists(),
                    "candidate_sha256": hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None,
                },
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
    rejected_candidate_path: Path | None = None,
    diagnostic_path: Path | None = None,
    invocation_context: dict[str, Any] | None = None,
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
        try:
            candidate = invoker(command, timeout, prompt)
        except Exception as exc:
            if diagnostic_path is not None:
                diagnostic = {
                    "status": "failed",
                    "model_called": True,
                    "job_id": source["job_id"],
                    "summary_level": source["summary_level"],
                    "source_sha256": source["source_sha256"],
                    "prompt_utf8_bytes": prompt_bytes,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                    **(invocation_context or {}),
                }
                if isinstance(exc, CodexInvocationError):
                    diagnostic.update(exc.diagnostic)
                Path(diagnostic_path).parent.mkdir(parents=True, exist_ok=True)
                atomic_write_canonical_json(Path(diagnostic_path), diagnostic)
            raise
    candidate = normalize_model_candidate(candidate, source)
    try:
        sidecar = project(source, candidate)
    except Exception as exc:
        if rejected_candidate_path is not None:
            rejected_candidate_path = Path(rejected_candidate_path)
            rejected_candidate_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_canonical_json(rejected_candidate_path, candidate)
        if diagnostic_path is not None:
            candidate_bytes = json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            Path(diagnostic_path).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_canonical_json(
                Path(diagnostic_path),
                {
                    "status": "failed",
                    "classification": "candidate-validation-failure",
                    "model_called": model_called,
                    "job_id": source["job_id"],
                    "summary_level": source["summary_level"],
                    "source_sha256": source["source_sha256"],
                    "prompt_utf8_bytes": prompt_bytes,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "candidate_utf8_bytes": len(candidate_bytes),
                    "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                    "exception_type": type(exc).__name__,
                    "error": str(exc),
                    **(invocation_context or {}),
                },
            )
        raise
    bundle, status = persist_sidecar(sidecar, output_directory, archive_root)
    if diagnostic_path is not None:
        Path(diagnostic_path).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_canonical_json(
            Path(diagnostic_path),
            {
                "status": "completed",
                "model_called": model_called,
                "job_id": source["job_id"],
                "summary_level": source["summary_level"],
                "source_sha256": source["source_sha256"],
                "prompt_utf8_bytes": prompt_bytes,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "projection_sha256": sidecar["projection_sha256"],
                **(invocation_context or {}),
            },
        )
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
