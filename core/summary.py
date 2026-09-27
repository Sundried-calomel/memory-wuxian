"""Small SummaryService assembly over the parent ArchiveStore contract.

This module writes only summaries/*.json. It does not import the installed Skill,
legacy Summary V2 bridge, query module, or backup module.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from storage import atomic_write_json, safe_target, exclusive_lock

FORMAT = "memory-wuxian-summary-v1"
DEFAULT_INPUT_BUDGET = 48_000
MAX_SUMMARY_TEXT_BYTES = 256_000


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _summary_path(root: Path, summary_id: str) -> Path:
    if not summary_id.startswith("sum-") or any(c not in "0123456789abcdef-" for c in summary_id[4:]):
        raise ValueError("unsafe summary id")
    return safe_target(root, f"summaries/{summary_id}.json")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_json(path, value)


def _summary_hash(record: dict[str, Any]) -> str:
    return _sha256(_json_bytes({key: value for key, value in record.items() if key != "summary_sha256"}))


def _summary_id(record: dict[str, Any]) -> str:
    identity = {key: value for key, value in record.items() if key not in {"id", "summary_sha256"}}
    return "sum-" + _sha256(_json_bytes(identity))[:32]


def _validate_raw_records(records: Any, conversation_id: str) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        records = list(records)
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    checked = []
    required = {"message_id", "sequence", "conversation_id", "speaker", "text", "timestamp",
                "completes_round", "round_number", "content_sha256"}
    for record in records:
        if not isinstance(record, dict) or required - set(record):
            raise ValueError("ArchiveStore returned a malformed raw record")
        message_id = record["message_id"]
        sequence = record["sequence"]
        if not isinstance(message_id, str) or not message_id or not isinstance(sequence, int) or isinstance(sequence, bool):
            raise ValueError("raw message identity is malformed")
        if record["conversation_id"] != conversation_id or not isinstance(record["speaker"], str) or not isinstance(record["text"], str):
            raise ValueError("raw record belongs to another conversation or has invalid text")
        if not isinstance(record["timestamp"], str) or not isinstance(record["completes_round"], bool):
            raise ValueError("raw record timestamp/round completion is malformed")
        if not isinstance(record["round_number"], int) or isinstance(record["round_number"], bool):
            raise ValueError("raw record round number is malformed")
        digest = record["content_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("raw record content hash is malformed")
        if message_id in seen_ids or sequence in seen_sequences:
            raise ValueError("ArchiveStore returned duplicate raw identities")
        seen_ids.add(message_id)
        seen_sequences.add(sequence)
        checked.append(dict(record))
    checked.sort(key=lambda item: item["sequence"])
    return checked


def _raw_source_hash(records: list[dict[str, Any]]) -> str:
    return _sha256(_json_bytes([
        {"message_id": row["message_id"], "sequence": row["sequence"],
         "conversation_id": row["conversation_id"], "content_sha256": row["content_sha256"]}
        for row in records
    ]))

def _prompt_record(record):
    return {key:record[key] for key in ('message_id','sequence','conversation_id','speaker','text',
        'timestamp','round_number','completes_round','content_sha256')}


class CodexCLIModel:
    """Explicit Codex CLI adapter using the existing ephemeral, read-only invoke flags."""

    def __init__(self, executable: str | Path, model: str, *, timeout_seconds: int = 900,
                 max_input_bytes: int = DEFAULT_INPUT_BUDGET):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("an explicit Codex model name is required")
        if not 0 < timeout_seconds <= 900:
            raise ValueError("Codex timeout must be between 1 and 900 seconds")
        self.executable = str(executable)
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.max_input_bytes = max_input_bytes

    def __call__(self, source: dict[str, Any]) -> dict[str, Any]:
        schema = {"type": "object", "additionalProperties": False,
                  "required": ["text", "source_refs"],
                  "properties": {"text": {"type": "string", "minLength": 1},
                                 "source_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1}}}
        prompt = (
            "Summarize only the supplied source. Return JSON with exactly text and source_refs. "
            "Preserve explicit decisions, proposals, unresolved tasks, rule scope, and corrections separately; "
            "never promote a proposal into an adopted rule or treat mere recency as supersession. "
            "source_refs must contain only IDs from source.allowed_refs. Keep claims grounded in those sources.\n\n"
            + _json_bytes(source).decode("utf-8")
        )
        if len(prompt.encode("utf-8")) > self.max_input_bytes:
            raise ValueError("Codex summary prompt exceeds the configured byte budget")
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-summary-") as temporary:
            root = Path(temporary)
            schema_path = root / "summary-result.schema.json"
            output_path = root / "candidate.json"
            schema_path.write_bytes(_json_bytes(schema))
            command = [self.executable, "exec", "--ephemeral", "--ignore-user-config",
                       "-c", 'model_reasoning_effort="medium"', "--skip-git-repo-check",
                       "--sandbox", "read-only", "--output-schema", str(schema_path),
                       "--model", self.model, "--output-last-message", str(output_path), "-"]
            completed = subprocess.run(command, input=prompt, text=True, encoding="utf-8",
                                       errors="replace", capture_output=True,
                                       timeout=self.timeout_seconds, check=False, cwd=root,
                                       **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
                                          if os.name == "nt" else {}))
            if completed.returncode != 0:
                raise RuntimeError(f"Codex summary call failed ({completed.returncode}): {completed.stderr[-2000:]}")
            if not output_path.is_file() or output_path.stat().st_size > MAX_SUMMARY_TEXT_BYTES:
                raise ValueError("Codex summary output is missing or exceeds the response limit")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("Codex summary output must be a JSON object")
            return result


class SummaryService:
    """Generate, commit, list, and compose summaries over an ArchiveStore."""

    def __init__(self, store, model=None):
        self.store = store
        self.model = model

    def _root(self) -> Path:
        root = Path(self.store.root).resolve()
        if root == Path(root.anchor):
            raise ValueError("archive root cannot be a filesystem root")
        return root

    def _read_summaries_unlocked(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        directory = self._root() / "summaries"
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.resolve().is_relative_to(self._root()):
            raise ValueError("summary directory escapes archive root")
        output = []
        if conversation_id is None:
            paths = sorted(directory.glob('sum-*.json'))
        else:
            with self.store.connection() as db:
                paths = [safe_target(self._root(),'summaries/'+row[0]+'.json') for row in
                    db.execute('SELECT id FROM summary_index WHERE conversation=? ORDER BY id',(conversation_id,))]
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"summary file cannot be a symbolic link: {path.name}")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("format") != FORMAT or value.get("id") != path.stem:
                raise ValueError(f"invalid summary record: {path.name}")
            if value.get("summary_sha256") != _summary_hash(value):
                raise ValueError(f"summary content hash mismatch: {path.name}")
            if value.get("id") != _summary_id(value):
                raise ValueError(f"summary identity does not match its content: {path.name}")
            if conversation_id is None or value.get("conversation_id") == conversation_id:
                output.append(value)
        return output

    def list(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        with self.store.lock():
            return self._read_summaries_unlocked(conversation_id)

    def _raw_records(self, conversation_id: str, source_ids: list[str] | None) -> list[dict[str, Any]]:
        if source_ids is None:
            records = _validate_raw_records(self.store.records(conversation_id, after_sequence=0), conversation_id)
            completed = {row["round_number"] for row in records if row["completes_round"]}
            return [row for row in records if row["round_number"] in completed]
        if not isinstance(source_ids, list) or not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("source_ids must be a non-empty unique list")
        selected = []
        for message_id in source_ids:
            row = self.store.message_by_id(message_id)
            if row is None:
                raise ValueError("source_ids contain missing records")
            selected.append(row)
        return _validate_raw_records(selected, conversation_id)

    def _resolve_children(self, conversation_id: str, children: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(children, list) or not children:
            raise ValueError("children must be a non-empty list")
        available = {item["id"]: item for item in self._read_summaries_unlocked(conversation_id)}
        resolved = []
        seen = set()
        for child in children:
            child_id = child if isinstance(child, str) else child.get("id") if isinstance(child, dict) else None
            if not isinstance(child_id, str) or child_id in seen or child_id not in available:
                raise ValueError("child summary is missing, duplicated, or belongs to another conversation")
            seen.add(child_id)
            current = available[child_id]
            if isinstance(child, dict) and child.get("summary_sha256") != current["summary_sha256"]:
                raise ValueError("child summary changed after selection")
            resolved.append(current)
        levels = {item["level"] for item in resolved}
        if len(levels) != 1:
            raise ValueError("parent summary children must have the same level")
        return resolved

    def _source(self, conversation_id: str, records=None, children=None) -> dict[str, Any]:
        if children is not None:
            resolved = self._resolve_children(conversation_id, children)
            allowed = [item["id"] for item in resolved]
            return {"kind": "children", "conversation_id": conversation_id,
                    "level": max(item["level"] for item in resolved) + 1,
                    "allowed_refs": allowed, "children": [
                        {"id": item["id"], "level": item["level"], "text": item["text"],
                         "source_refs": item["source_refs"], "raw_source_ids": item["raw_source_ids"],
                         "source_sha256": item["source_sha256"], "summary_sha256": item["summary_sha256"]}
                        for item in resolved],
                    "source_sha256": _sha256(_json_bytes([{
                        "id": item["id"], "summary_sha256": item["summary_sha256"]} for item in resolved]))}
        if not records:
            raise ValueError("no source records were selected")
        ids = [row["message_id"] for row in records]
        return {"kind": "raw", "conversation_id": conversation_id, "level": 1,
                "allowed_refs": ids, "records": [_prompt_record(r) for r in records], "source_sha256": _raw_source_hash(records)}

    def _budget(self) -> int:
        value = getattr(self.model, "max_input_bytes", DEFAULT_INPUT_BUDGET)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1024:
            raise ValueError("model max_input_bytes must be at least 1024")
        # Leave room for the adapter's fixed instructions around the source JSON.
        return value - 512 if isinstance(self.model, CodexCLIModel) else value

    def _partition(self, conversation_id: str, records=None, children=None) -> list[list[Any]]:
        items = records if records is not None else children
        groups: list[list[Any]] = []
        current: list[Any] = []
        budget = self._budget()
        for item in items:
            candidate = current + [item]
            source = self._source(conversation_id,
                                  records=candidate if records is not None else None,
                                  children=candidate if children is not None else None)
            if len(_json_bytes(source)) > budget:
                if not current:
                    raise ValueError("one source item exceeds the model input budget")
                groups.append(current)
                current = [item]
                source = self._source(conversation_id,
                                      records=current if records is not None else None,
                                      children=current if children is not None else None)
                if len(_json_bytes(source)) > budget:
                    raise ValueError("one source item exceeds the model input budget")
            else:
                current = candidate
        if current:
            groups.append(current)
        return groups

    def _verify_source_locked(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        if source["kind"] == "raw":
            if source.get("level") != 1 or not isinstance(source.get("allowed_refs"), list):
                raise ValueError("raw summary source shape is invalid")
            current = self._raw_records(source["conversation_id"], source["allowed_refs"])
            if [_prompt_record(r) for r in current] != source.get("records") or _raw_source_hash(current) != source["source_sha256"]:
                raise ValueError("raw source changed while the model was running")
            return current
        children = self._resolve_children(source["conversation_id"], source["allowed_refs"])
        expected = self._source(source["conversation_id"], children=source["allowed_refs"])
        if source.get("level") != expected["level"] or source != expected:
            raise ValueError("child summaries changed while the model was running")
        return children

    def commit(self, source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(source, dict) or source.get("kind") not in {"raw", "children"}:
            raise ValueError("invalid summary source")
        checked = self._call_model_result(source, result)
        with self.store.lock():
            source_items = self._verify_source_locked(source)
            refs = checked["source_refs"]
            if source["kind"] == "raw":
                raw_ids = refs
            else:
                selected = {item["id"]: item for item in source_items}
                raw_ids = sorted({raw_id for ref in refs for raw_id in selected[ref]["raw_source_ids"]})
            body = {"format": FORMAT, "conversation_id": source["conversation_id"],
                    "level": source["level"], "text": checked["text"], "source_refs": refs,
                    "raw_source_ids": raw_ids, "source_sha256": source["source_sha256"]}
            # Model citations can be a subset. Track consumed input separately so
            # uncited input is not repeatedly summarized on subsequent ticks.
            body['input_source_ids'] = list(source['allowed_refs'])
            content_key = _sha256(_json_bytes(body))
            record = {"id": "sum-" + content_key[:32], **body}
            record["summary_sha256"] = _summary_hash(record)
            path = _summary_path(self._root(), record["id"])
            if path.exists():
                if path.is_symlink():
                    raise ValueError("summary identity is occupied by a symbolic link")
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != record:
                    raise ValueError("summary identity collision with different content")
                self.store.index_summary(existing)
                return existing
            _atomic_json(path, record)
            self.store.index_summary(record)
            return record

    @staticmethod
    def _call_model_result(source: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict) or set(result) != {"text", "source_refs"}:
            raise ValueError("model must return exactly {text, source_refs}")
        text, refs = result["text"], result["source_refs"]
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_SUMMARY_TEXT_BYTES:
            raise ValueError("model summary text is empty or too large")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("model source_refs must be a non-empty string list")
        if len(refs) != len(set(refs)) or any(ref not in source["allowed_refs"] for ref in refs):
            raise ValueError("model source_refs contain duplicates or references outside the supplied source")
        return {"text": text.strip(), "source_refs": refs}

    def _generate_one(self, conversation_id: str, *, records=None, children=None) -> dict[str, Any]:
        source = self._source(conversation_id, records=records, children=children)
        lock = safe_target(self._root(), 'summary-locks/' + source['source_sha256'] + '.lock')
        lock.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(lock):
            with self.store.lock():
                for existing in self._read_summaries_unlocked(conversation_id):
                    if existing.get("source_sha256") == source["source_sha256"] and existing.get("level") == source["level"]:
                        self.store.index_summary(existing)
                        return existing
            if self.model is None:
                raise RuntimeError("No summary model is configured; provide a callable or CodexCLIModel explicitly")
            result = self.model(source)
            return self.commit(source, result)

    def _reduce(self, conversation_id: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        while len(nodes) > 1:
            groups = self._partition(conversation_id, children=nodes)
            next_level = [self._generate_one(conversation_id, children=group) for group in groups]
            if len(next_level) >= len(nodes):
                raise ValueError("summary model did not reduce an over-budget source hierarchy")
            nodes = next_level
        return nodes[0]

    def generate(self, conversation_id: str, source_ids: list[str] | None = None,
                 children: list[str | dict[str, Any]] | None = None) -> dict[str, Any]:
        if conversation_id in self.store.excluded_conversations():
            raise ValueError('internal task is excluded from user memory')
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("conversation_id is required")
        if children is not None and source_ids is not None:
            raise ValueError("choose raw source_ids or child summaries, not both")
        with self.store.lock():
            if children is not None:
                source_children = self._resolve_children(conversation_id, children)
                units = source_children
                kind = "children"
            else:
                units = self._raw_records(conversation_id, source_ids)
                kind = "raw"
        groups = self._partition(conversation_id,
                                 records=units if kind == "raw" else None,
                                 children=units if kind == "children" else None)
        if len(groups) == 1:
            return self._generate_one(conversation_id,
                                      records=groups[0] if kind == "raw" else None,
                                      children=groups[0] if kind == "children" else None)
        partials = [self._generate_one(conversation_id,
                                       records=group if kind == "raw" else None,
                                       children=group if kind == "children" else None)
                    for group in groups]
        return self._reduce(conversation_id, partials)

    def context(self, conversation_id: str) -> dict[str, Any]:
        with self.store.lock():
            records = self.store.records(conversation_id)
            summaries = self._read_summaries_unlocked(conversation_id)
            referenced = {ref for item in summaries if item["level"] > 1
                          for ref in item["source_refs"] if ref.startswith("sum-")}
            roots = [item for item in summaries if item["id"] not in referenced]
            covered = {raw_id for item in roots for raw_id in item["raw_source_ids"]}
            uncovered = [row for row in records if row["message_id"] not in covered]
            return {"conversation_id": conversation_id,
                    "summaries": sorted(roots, key=lambda item: (-item["level"], item["id"])),
                    "uncovered_records": uncovered}
