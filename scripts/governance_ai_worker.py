#!/usr/bin/env python3
"""Run one bounded, ephemeral governance AI batch and return a draft JSON result."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from memory_cli import nested_get
from platform_process import no_window_kwargs


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODEX = Path(
    "codex.exe" if os.name == "nt" else "/Applications/ChatGPT.app/Contents/Resources/codex"
)


def governor_root(store: Any) -> Path:
    configured = str(
        nested_get(
            store.config,
            ["governance_ai", "governor_skill_path"],
            "~/.codex/skills/work-system-governor",
        )
    )
    root = Path(configured).expanduser().resolve()
    required = (
        root / "references" / "ai-orchestration.md",
        root / "schemas" / "governance-ai-result.schema.json",
    )
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"governance AI contract is unavailable: {path}")
    return root


def build_prompt(store: Any, batch: dict[str, Any]) -> str:
    root = governor_root(store)
    contract = (root / "references" / "ai-orchestration.md").read_text(
        encoding="utf-8"
    )
    payload = {
        "batch_id": batch["batch_id"],
        "task_kind": batch["task_kind"],
        "owner_id": batch["owner_id"],
        "source_item_ids": batch["item_ids"],
        "items": [
            {key: value for key, value in item.items() if key != "_path"}
            for item in batch["items"]
        ],
    }
    return (
        contract
        + "\n\nReturn only JSON matching the supplied output schema. "
        + "Use only the evidence in this batch. Every fact, interpretation, "
        + "recommendation, and classification must cite supplied evidence IDs. "
        + "Do not claim acceptance, installation, remediation, or hidden reasoning.\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    )


def run_batch(store: Any, batch: dict[str, Any]) -> dict[str, Any]:
    root = governor_root(store)
    codex_key = "codex_cli_path_windows" if os.name == "nt" else "codex_cli_path"
    codex_path = Path(
        os.environ.get(
            "MEMORY_WUXIAN_CODEX",
            str(nested_get(store.config, ["governance_ai", codex_key], DEFAULT_CODEX)),
        )
    ).expanduser()
    timeout = int(nested_get(store.config, ["governance_ai", "timeout_seconds"], 900))
    model = str(nested_get(store.config, ["governance_ai", "model"], "")).strip()
    command = [
        str(codex_path),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(root / "schemas" / "governance-ai-result.schema.json"),
    ]
    if model:
        command.extend(["--model", model])
    with tempfile.TemporaryDirectory(prefix="memory-wuxian-governance-ai-") as temporary:
        output = Path(temporary) / "result.json"
        command.extend(["--output-last-message", str(output), "-"])
        completed = subprocess.run(
            command,
            input=build_prompt(store, batch),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            check=False,
            cwd=temporary,
            **no_window_kwargs(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"one-shot governance AI failed ({completed.returncode}): "
                + completed.stderr[-2000:]
            )
        text = output.read_text(encoding="utf-8").strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("governance AI output must be an object")
        return value
