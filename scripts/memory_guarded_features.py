#!/usr/bin/env python3
"""Guarded portability, history, graph, evaluation, and semantic features."""

from __future__ import annotations

import datetime as dt
import hashlib
import heapq
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from semantic_runtime_contract import CONTRACT_PATH, load_contract


AUTHORITATIVE_PREFIXES = ("raw/", "summaries/")
AUTHORITATIVE_FILES = {"state.json"}
IGNORED_MIGRATION_PARTS = {".locks", ".DS_Store"}
E5_PROVIDER = "multilingual-e5-small"
MAX_SEMANTIC_RECORDS = 200_000
MAX_SEMANTIC_INDEX_BYTES = 512 * 1024 * 1024
MAX_SEMANTIC_LINE_BYTES = 1024 * 1024
MAX_SEMANTIC_MANIFEST_BYTES = 64 * 1024
MAX_E5_SCORE_BYTES = 16 * 1024 * 1024
SEMANTIC_QUERY_WORKER_TIMEOUT_SECONDS = 120
SEMANTIC_BUILD_WORKER_TIMEOUT_SECONDS = 3600


class SemanticIndexStaleError(ValueError):
    """Raised when a semantic index does not cover the current raw source."""


def raw_record_sha256(record: Dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"_path", "content_sha256"}
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def raw_source_snapshot(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(records, key=lambda item: int(item["sequence"]))
    source_records = []
    for record in ordered:
        sequence = record.get("sequence")
        message_id = record.get("message_id")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or not isinstance(message_id, str)
            or not message_id
        ):
            raise ValueError("Raw source identity fields are malformed")
        source_records.append({
            "sequence": sequence,
            "message_id": message_id,
            "record_sha256": raw_record_sha256(record),
        })
    canonical = json.dumps(
        source_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    high_watermark = source_records[-1] if source_records else None
    return {
        "format": "memory-wuxian-raw-source-snapshot-v1",
        "record_count": len(source_records),
        "high_watermark": high_watermark,
        "identity_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def archive_manifest(root: Path) -> Dict[str, Any]:
    root = root.resolve()
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(part in IGNORED_MIGRATION_PARTS for part in path.relative_to(root).parts):
            continue
        files.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "authoritative": relative in AUTHORITATIVE_FILES
            or relative.startswith(AUTHORITATIVE_PREFIXES),
        })
    canonical = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "format": "memory-wuxian-archive-manifest-v1",
        "root": str(root),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "manifest_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def same_manifest(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return left["manifest_sha256"] == right["manifest_sha256"]


class GuardedFeatures:
    def __init__(self, store: Any):
        self.store = store
        self.root = store.root.resolve()

    def migration_preview(self, destination: Path) -> Dict[str, Any]:
        destination = destination.expanduser().resolve()
        if destination == self.root or self.root in destination.parents or destination in self.root.parents:
            raise ValueError("Migration source and destination must not contain one another")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Migration destination must be absent or empty")
        source = archive_manifest(self.root)
        usage = shutil.disk_usage(destination.parent if destination.parent.exists() else destination.anchor)
        return {
            "status": "preview",
            "source": str(self.root),
            "destination": str(destination),
            "source_manifest_sha256": source["manifest_sha256"],
            "file_count": source["file_count"],
            "required_bytes": source["total_bytes"],
            "free_bytes": usage.free,
            "space_sufficient": usage.free >= source["total_bytes"] * 2,
            "source_will_be_modified": False,
            "source_will_be_deleted": False,
            "pointer_switch_requires": "--switch-active",
        }

    def migration_apply(self, destination: Path, switch_active: bool) -> Dict[str, Any]:
        preview = self.migration_preview(destination)
        if not preview["space_sufficient"]:
            raise ValueError("Destination does not have the required safety headroom")
        destination = Path(preview["destination"])
        partial = destination.with_name(destination.name + ".memory-wuxian-partial")
        if partial.exists():
            raise ValueError(f"Existing partial migration requires manual review: {partial}")
        source_before = archive_manifest(self.root)
        partial.mkdir(parents=True)
        try:
            for item in source_before["files"]:
                source_path = self.root / item["path"]
                target_path = partial / item["path"]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
            target = archive_manifest(partial)
            source_after = archive_manifest(self.root)
            if not same_manifest(source_before, source_after):
                raise RuntimeError("Source changed during migration; no pointer was switched")
            if not same_manifest(source_before, target):
                raise RuntimeError("Destination hash verification failed; no pointer was switched")
            os.replace(partial, destination)
            pointer = None
            if switch_active:
                pointer = Path.home() / ".codex" / "memory-wuxian-active-root.txt"
                temporary = pointer.with_suffix(".tmp")
                pointer.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(str(destination) + "\n", encoding="utf-8")
                os.replace(temporary, pointer)
            return {
                "status": "verified-copy",
                "source": str(self.root),
                "destination": str(destination),
                "manifest_sha256": source_before["manifest_sha256"],
                "source_preserved": True,
                "source_deleted": False,
                "active_root_switched": bool(pointer),
                "active_root_pointer": str(pointer) if pointer else None,
            }
        except Exception:
            raise

    def project_export(self, output: Path, conversation_ids: List[str]) -> Dict[str, Any]:
        selected = [
            record for record in self.store.read_all_raw()
            if str(record.get("conversation_id")) in set(conversation_ids)
        ]
        if not selected:
            raise ValueError("No raw records matched the requested conversations")
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        raw_text = "".join(
            json.dumps({k: v for k, v in record.items() if k != "_path"},
                       ensure_ascii=False, sort_keys=True) + "\n"
            for record in selected
        )
        readme = (
            "# Memory Wuxian project memory package\n\n"
            "This is a read-only, human-readable copy. It is not imported into local raw history.\n"
        )
        artifacts = {
            "README.md": readme.encode("utf-8"),
            "raw/messages.jsonl": raw_text.encode("utf-8"),
        }
        manifest = {
            "format": "memory-wuxian-project-package-v1",
            "conversation_ids": sorted(set(conversation_ids)),
            "record_count": len(selected),
            "artifacts": {
                name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in artifacts.items()
            },
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for name, data in artifacts.items():
                archive.writestr(name, data)
        return {"status": "exported", "output": str(output), **manifest}

    def project_import(self, package: Path) -> Dict[str, Any]:
        package = package.expanduser().resolve()
        replicas = self.root.parent / f"{self.root.name}-project-replicas"
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "memory-wuxian-project-package-v1":
                raise ValueError("Unsupported project package")
            package_id = sha256_file(package)
            target = replicas / package_id
            if target.exists():
                return {"status": "already-imported", "replica": str(target)}
            temporary = replicas / f".{package_id}.partial"
            temporary.mkdir(parents=True, exist_ok=False)
            try:
                for name, expected in manifest["artifacts"].items():
                    data = archive.read(name)
                    if len(data) != expected["size"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                        raise ValueError(f"Project package artifact failed verification: {name}")
                    path = temporary / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                atomic_json(temporary / "manifest.json", manifest)
                os.replace(temporary, target)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        return {
            "status": "imported-read-only-replica",
            "replica": str(target),
            "local_raw_modified": False,
            "package_sha256": package_id,
        }

    @staticmethod
    def _at_or_before(value: Any, cutoff: dt.datetime) -> bool:
        if not value:
            return True
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=cutoff.tzinfo)
        return parsed <= cutoff

    def as_of(self, timestamp: str, conversation_id: Optional[str]) -> Dict[str, Any]:
        cutoff = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError("Time-travel timestamp must include a timezone")
        raw = [
            record for record in self.store.read_all_raw()
            if self._at_or_before(record.get("timestamp"), cutoff)
            and (not conversation_id or record.get("conversation_id") == conversation_id)
        ]
        policies = [
            record for record in self.store.policy_records()
            if self._at_or_before(record.get("end_time"), cutoff)
            and (not conversation_id or record.get("conversation_id") == conversation_id)
        ]
        return {
            "format": "memory-wuxian-time-travel-v1",
            "as_of": cutoff.isoformat(),
            "conversation_id": conversation_id,
            "raw_record_count": len(raw),
            "last_sequence": max((int(item["sequence"]) for item in raw), default=0),
            "raw_records": raw,
            "policy_view": self.store.policy_view(policies),
            "read_only": True,
        }

    def decision_graph(self) -> Dict[str, Any]:
        raw = {str(item["message_id"]): item for item in self.store.read_all_raw()}
        policies = self.store.policy_view()
        nodes = []
        edges = []
        for item in policies:
            sources = []
            for message_id in item.get("source_message_ids", []):
                record = raw.get(str(message_id))
                pointer = self.raw_pointer(record) if record else {}
                sources.append({
                    "message_id": message_id,
                    "raw_path": record.get("_path") if record else None,
                    "record_sha256": record.get("content_sha256") if record else None,
                    **pointer,
                })
            nodes.append({**item, "raw_sources": sources})
            if item.get("supersedes_policy_event_id"):
                edges.append({
                    "from": item["policy_event_id"],
                    "to": item["supersedes_policy_event_id"],
                    "type": item["event_type"],
                })
        return {
            "format": "memory-wuxian-decision-graph-v1",
            "nodes": nodes,
            "edges": edges,
            "derived": True,
            "authoritative_source": "raw messages",
        }

    def raw_pointer(self, record: Dict[str, Any]) -> Dict[str, Any]:
        relative = str(record.get("_path", ""))
        path = self.root / relative
        if not relative or not path.exists():
            return {"raw_line_start": None, "raw_line_end": None}
        message_id = str(record.get("message_id", ""))
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line == "<!-- memory-wuxian-record -->" and index + 2 < len(lines):
                try:
                    candidate = json.loads(lines[index + 2])
                except json.JSONDecodeError:
                    continue
                if str(candidate.get("message_id")) == message_id:
                    return {"raw_line_start": index + 1, "raw_line_end": index + 4}
        return {"raw_line_start": None, "raw_line_end": None}

    def raw_pointer_index(
        self, records: List[Dict[str, Any]]
    ) -> Dict[tuple[str, str], Dict[str, Optional[int]]]:
        requested: Dict[str, Dict[str, str]] = {}
        for record in records:
            relative = str(record.get("_path", ""))
            message_id = str(record.get("message_id", ""))
            if relative and message_id:
                requested.setdefault(relative, {})[message_id] = str(
                    record.get("content_sha256", "")
                )
        pointers: Dict[tuple[str, str], Dict[str, Optional[int]]] = {}
        for relative, message_ids in requested.items():
            path = self.root / relative
            if not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if line != "<!-- memory-wuxian-record -->" or index + 2 >= len(lines):
                    continue
                try:
                    candidate = json.loads(lines[index + 2])
                except json.JSONDecodeError:
                    continue
                message_id = str(candidate.get("message_id", ""))
                if message_id in message_ids:
                    computed = raw_record_sha256(candidate)
                    expected = message_ids[message_id]
                    pointers[(relative, message_id)] = {
                        "raw_line_start": index + 1,
                        "raw_line_end": index + 4,
                        "verified_against_raw": bool(
                            expected
                            and candidate.get("content_sha256") == expected
                            and computed == expected
                        ),
                    }
        return pointers

    def retrieval_evaluate(self, dataset: Path, top_k: int) -> Dict[str, Any]:
        dataset_bytes = dataset.read_bytes()
        records = []
        for line_number, line in enumerate(
            dataset_bytes.decode("utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Retrieval corpus line {line_number} is not valid JSON: {error.msg}"
                ) from error
            if not isinstance(record, dict):
                raise ValueError(
                    f"Retrieval corpus line {line_number} must be a JSON object"
                )
            records.append(record)

        fixed_corpus = bool(
            records
            and records[0].get("format") == "memory-wuxian-retrieval-corpus-v2"
        )
        corpus_header: Optional[Dict[str, Any]] = None
        cases = records
        if fixed_corpus:
            corpus_header = records[0]
            if corpus_header.get("version") != "2.6":
                raise ValueError("Fixed retrieval corpus version must be '2.6'")
            cases = records[1:]
            if not cases:
                raise ValueError("Fixed retrieval corpus must contain at least one case")
            if any("format" in case for case in cases):
                raise ValueError("Fixed retrieval corpus may contain only one header record")

            seen_ids = set()
            allowed_confidence = {
                "verified", "summary-supported", "index-only", "unverified",
            }
            for case in cases:
                case_id = case.get("id")
                if not isinstance(case_id, str) or not re.fullmatch(
                    r"MW26-(?:RET|DELTA)-\d{3}", case_id
                ):
                    raise ValueError(f"Malformed fixed retrieval case ID: {case_id!r}")
                if case_id in seen_ids:
                    raise ValueError(f"Duplicate fixed retrieval case ID: {case_id}")
                seen_ids.add(case_id)
                if not isinstance(case.get("query"), str) or not case["query"].strip():
                    raise ValueError(f"Fixed retrieval case {case_id} has an empty query")
                if case.get("mode", "historical") not in {
                    "historical", "current-policy",
                }:
                    raise ValueError(f"Fixed retrieval case {case_id} has an invalid mode")
                confidence = case.get("expected_confidence")
                if confidence is not None and confidence not in allowed_confidence:
                    raise ValueError(
                        f"Fixed retrieval case {case_id} has an invalid expected_confidence"
                    )
                for field in ("expected_message_ids", "expected_policy_event_ids"):
                    values = case.get(field, [])
                    if not isinstance(values, list) or any(
                        not isinstance(value, str) or not value for value in values
                    ):
                        raise ValueError(
                            f"Fixed retrieval case {case_id} field {field} must be a string array"
                        )
                validity = case.get("expected_policy_validity", {})
                if not isinstance(validity, dict) or any(
                    not isinstance(key, str)
                    or not key
                    or not isinstance(value, str)
                    or not value
                    for key, value in validity.items()
                ):
                    raise ValueError(
                        f"Fixed retrieval case {case_id} expected_policy_validity must be an object"
                    )
                expected_policy_ids = set(case.get("expected_policy_event_ids", []))
                if not set(validity).issubset(expected_policy_ids):
                    raise ValueError(
                        f"Fixed retrieval case {case_id} validity references an unexpected policy event"
                    )
                disambiguation = case.get("expected_disambiguation")
                if disambiguation is not None and not isinstance(disambiguation, dict):
                    raise ValueError(
                        f"Fixed retrieval case {case_id} expected_disambiguation must be an object"
                    )
                comparison = case.get("comparison")
                if comparison is not None:
                    if not isinstance(comparison, dict):
                        raise ValueError(
                            f"Fixed retrieval case {case_id} comparison must be an object"
                        )
                    for field in (
                        "baseline_message_ids",
                        "intended_added_message_ids",
                        "intended_removed_message_ids",
                    ):
                        values = comparison.get(field, [])
                        if not isinstance(values, list) or any(
                            not isinstance(value, str) or not value for value in values
                        ):
                            raise ValueError(
                                f"Fixed retrieval case {case_id} comparison field {field} "
                                "must be a string array"
                            )

        results = []
        started = time.perf_counter()
        for case in cases:
            case_started = time.perf_counter()
            mode = str(case.get("mode", "historical"))
            if fixed_corpus:
                _, metadata = self.store.retrieve(str(case["query"]), mode)
            else:
                _, metadata = self.store.retrieve(str(case["query"]))
            actual = [item["message_id"] for item in metadata["raw_matches"][:top_k]]
            expected = set(map(str, case.get("expected_message_ids", [])))
            hits = len(expected.intersection(actual))
            result = {
                "id": case.get("id"),
                "query": case["query"],
                "expected_message_ids": sorted(expected),
                "actual_message_ids": actual,
                "recall_at_k": hits / len(expected) if expected else 1.0,
                "wrong_citation_count": len([item for item in actual if item not in expected]),
                "latency_ms": round((time.perf_counter() - case_started) * 1000, 3),
            }
            if fixed_corpus:
                expected_policy_ids = sorted(case.get("expected_policy_event_ids", []))
                actual_policy_events = [
                    {
                        "policy_event_id": str(item.get("policy_event_id", "")),
                        "validity": str(item.get("validity", "")),
                    }
                    for item in metadata.get("policy_events", [])
                ]
                actual_policy_ids = sorted(
                    item["policy_event_id"] for item in actual_policy_events
                )
                actual_validity = {
                    item["policy_event_id"]: item["validity"]
                    for item in actual_policy_events
                }
                expected_validity = case.get("expected_policy_validity", {})
                expected_confidence = case.get("expected_confidence")
                expected_disambiguation = case.get("expected_disambiguation")
                confidence_matches = (
                    expected_confidence is None
                    or metadata.get("verification") == expected_confidence
                )
                policy_matches = (
                    actual_policy_ids == expected_policy_ids
                    and all(
                        actual_validity.get(event_id) == validity
                        for event_id, validity in expected_validity.items()
                    )
                )
                disambiguation_matches = (
                    expected_disambiguation is None
                    or metadata.get("disambiguation") == expected_disambiguation
                )
                result.update({
                    "mode": mode,
                    "expected_confidence": expected_confidence,
                    "actual_confidence": metadata.get("verification"),
                    "confidence_matches": confidence_matches,
                    "expected_policy_event_ids": expected_policy_ids,
                    "expected_policy_validity": expected_validity,
                    "actual_policy_events": actual_policy_events,
                    "policy_matches": policy_matches,
                    "expected_disambiguation": expected_disambiguation,
                    "actual_disambiguation": metadata.get("disambiguation"),
                    "disambiguation_matches": disambiguation_matches,
                })
                unexplained_count = 0
                comparison = case.get("comparison")
                if comparison is not None:
                    baseline = set(comparison.get("baseline_message_ids", []))
                    intended_added = set(
                        comparison.get("intended_added_message_ids", [])
                    )
                    intended_removed = set(
                        comparison.get("intended_removed_message_ids", [])
                    )
                    actual_set = set(actual)
                    actual_added = actual_set - baseline
                    actual_removed = baseline - actual_set
                    unexplained = {
                        "unexpected_added_message_ids": sorted(
                            actual_added - intended_added
                        ),
                        "unexpected_removed_message_ids": sorted(
                            actual_removed - intended_removed
                        ),
                        "missing_intended_added_message_ids": sorted(
                            intended_added - actual_added
                        ),
                        "missing_intended_removed_message_ids": sorted(
                            intended_removed - actual_removed
                        ),
                    }
                    unexplained_count = sum(len(values) for values in unexplained.values())
                    result.update({
                        "intended_delta": {
                            "added_message_ids": sorted(intended_added),
                            "removed_message_ids": sorted(intended_removed),
                        },
                        "actual_delta": {
                            "added_message_ids": sorted(actual_added),
                            "removed_message_ids": sorted(actual_removed),
                        },
                        "unexplained_delta": unexplained,
                        "unexplained_delta_count": unexplained_count,
                    })
                result["passed"] = (
                    result["recall_at_k"] == 1.0
                    and result["wrong_citation_count"] == 0
                    and confidence_matches
                    and policy_matches
                    and disambiguation_matches
                    and unexplained_count == 0
                )
            results.append(result)

        if fixed_corpus:
            return {
                "format": "memory-wuxian-retrieval-evaluation-v2",
                "corpus_format": corpus_header["format"] if corpus_header else None,
                "corpus_version": corpus_header["version"] if corpus_header else None,
                "corpus_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
                "case_count": len(results),
                "top_k": top_k,
                "mean_recall_at_k": sum(
                    item["recall_at_k"] for item in results
                ) / len(results),
                "wrong_citation_count": sum(
                    item["wrong_citation_count"] for item in results
                ),
                "unexplained_delta_count": sum(
                    item.get("unexplained_delta_count", 0) for item in results
                ),
                "all_cases_passed": all(item["passed"] for item in results),
                "total_latency_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
                "cases": results,
            }
        return {
            "format": "memory-wuxian-retrieval-evaluation-v1",
            "case_count": len(results),
            "top_k": top_k,
            "mean_recall_at_k": sum(item["recall_at_k"] for item in results) / len(results) if results else 0.0,
            "wrong_citation_count": sum(item["wrong_citation_count"] for item in results),
            "total_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "cases": results,
        }

    @staticmethod
    def _hash_embedding(text: str, dimensions: int = 256) -> List[float]:
        vector = [0.0] * dimensions
        for token in text.casefold().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:2], "big") % dimensions] += -1.0 if digest[2] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def e5_paths() -> tuple[Path, Path]:
        contract = load_contract()
        model_dir = (
            Path(contract["installation"]["model_root"]).expanduser()
            / contract["model"]["revision"]
        )
        runtime_python = Path(
            contract["installation"]["runtime_root"]
        ).expanduser()
        runtime_python /= "Scripts/python.exe" if os.name == "nt" else "bin/python"
        return model_dir, runtime_python

    def validate_e5_install(self) -> tuple[Path, Path]:
        contract = load_contract()
        model_dir, runtime_python = self.e5_paths()
        manifest_path = model_dir / "model-manifest.json"
        if not runtime_python.exists() or not manifest_path.exists():
            raise ValueError(
                "multilingual-e5-small is not installed; run scripts/install_multilingual_e5.py"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("format") != "memory-wuxian-e5-model-v1"
            or manifest.get("model_id") != contract["model"]["id"]
            or manifest.get("model_revision") != contract["model"]["revision"]
            or manifest.get("runtime_packages") != contract["runtime"]["packages"]
            or manifest.get("offline_only") is not True
        ):
            raise ValueError("multilingual-e5-small manifest does not match the pinned provider")
        expected_artifacts = {
            item["path"]: item for item in contract["model"]["artifacts"]
        }
        actual_artifacts = {
            str(item.get("path")): item for item in manifest.get("artifacts", [])
        }
        if set(actual_artifacts) != set(expected_artifacts):
            raise ValueError("multilingual-e5-small manifest artifact set is incomplete")
        for artifact in expected_artifacts.values():
            path = model_dir / str(artifact["path"])
            if (
                not path.is_file()
                or path.stat().st_size != int(artifact["size"])
                or sha256_file(path) != str(artifact["sha256"])
            ):
                raise ValueError(f"multilingual-e5-small artifact failed verification: {path.name}")
        return model_dir, runtime_python

    @staticmethod
    def e5_environment() -> Dict[str, str]:
        environment = os.environ.copy()
        environment.update({
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        })
        return environment

    def e5_embed(
        self,
        texts: List[str],
        prefix: str,
        output: Path,
        batch_size: int = 8,
    ) -> None:
        model_dir, runtime_python = self.validate_e5_install()
        payload = output.with_suffix(".input.json")
        payload.write_text(
            json.dumps({"texts": texts}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            subprocess.run(
                [
                    str(runtime_python),
                    str(Path(__file__).with_name("semantic_e5_worker.py")),
                    "--contract", str(CONTRACT_PATH),
                    "--model-dir", str(model_dir),
                    "--input", str(payload),
                    "--output", str(output),
                    "--prefix", prefix,
                    "--batch-size", str(batch_size),
                ],
                check=True,
                env=self.e5_environment(),
                timeout=SEMANTIC_BUILD_WORKER_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            output.unlink(missing_ok=True)
            raise ValueError("semantic index build worker failed") from exc
        finally:
            payload.unlink(missing_ok=True)

    def e5_scores(self, query: str, matrix: Path) -> List[float]:
        model_dir, runtime_python = self.validate_e5_install()
        with tempfile.TemporaryDirectory(prefix="memory-wuxian-e5-query-") as temp:
            temporary = Path(temp)
            payload = temporary / "query.json"
            output = temporary / "scores.json"
            payload.write_text(
                json.dumps({"texts": [query]}, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                subprocess.run(
                    [
                        str(runtime_python),
                        str(Path(__file__).with_name("semantic_e5_worker.py")),
                        "--contract", str(CONTRACT_PATH),
                        "--model-dir", str(model_dir),
                        "--input", str(payload),
                        "--output", str(output),
                        "--prefix", "query",
                        "--batch-size", "1",
                        "--matrix", str(matrix),
                    ],
                    check=True,
                    env=self.e5_environment(),
                    timeout=SEMANTIC_QUERY_WORKER_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("semantic query worker failed") from exc
            if not output.is_file() or output.stat().st_size > MAX_E5_SCORE_BYTES:
                raise ValueError("semantic score output exceeds the query bound")
            result = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or set(result) != {"scores"} or not isinstance(result["scores"], list):
                raise ValueError("semantic score output is malformed")
            scores = [float(value) for value in result["scores"]]
            if any(not math.isfinite(value) for value in scores):
                raise ValueError("semantic score output contains a non-finite value")
            return scores

    def semantic_build(self, provider: str) -> Dict[str, Any]:
        if provider not in {"local-hash-v1", E5_PROVIDER}:
            raise ValueError("Semantic provider must be local-hash-v1 or multilingual-e5-small")
        directory = self.store.index_dir / "semantic"
        records = []
        raw_records = self.store.read_all_raw()
        pointer_index = self.raw_pointer_index(raw_records)
        for record in raw_records:
            pointer = pointer_index.get(
                (str(record.get("_path", "")), str(record.get("message_id", ""))),
                {"raw_line_start": None, "raw_line_end": None},
            )
            item = {
                "message_id": record["message_id"],
                "conversation_id": record["conversation_id"],
                "raw_path": record["_path"],
                "record_sha256": record["content_sha256"],
                **pointer,
                "provider": provider,
            }
            if provider == "local-hash-v1":
                item["vector"] = self._hash_embedding(str(record.get("text", "")))
            records.append(item)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "vectors.jsonl"
        vector_path = None
        if provider == E5_PROVIDER:
            vector_path = directory / "vectors.npy"
            temporary_vector = directory / ".vectors.npy"
            self.e5_embed(
                [str(record.get("text", "")) for record in raw_records],
                "passage",
                temporary_vector,
                batch_size=16,
            )
            os.replace(temporary_vector, vector_path)
        text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        contract = load_contract() if provider == E5_PROVIDER else None
        atomic_json(directory / "manifest.json", {
            "format": "memory-wuxian-semantic-index-v2",
            "provider": provider,
            "model_id": contract["model"]["id"] if contract else None,
            "model_revision": contract["model"]["revision"] if contract else None,
            "interface_version": contract["interface_version"] if contract else None,
            "vector_file": vector_path.name if vector_path else None,
            "record_count": len(records),
            "raw_source": raw_source_snapshot(raw_records),
            "disposable": True,
            "raw_archive_required_for_verification": True,
        })
        return {
            "status": "built",
            "provider": provider,
            "record_count": len(records),
            "path": str(path),
            "vector_path": str(vector_path) if vector_path else None,
        }

    def semantic_clear(self) -> Dict[str, Any]:
        directory = self.store.index_dir / "semantic"
        if directory.exists():
            shutil.rmtree(directory)
        return {
            "status": "cleared",
            "raw_archive_modified": False,
            "keyword_retrieval_available": True,
        }

    def semantic_retrieve(
        self,
        query: str,
        top_k: int,
        *,
        raw_records: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 1_000:
            raise ValueError("Semantic result limit is invalid")
        path = self.store.index_dir / "semantic" / "vectors.jsonl"
        if not path.exists():
            raise ValueError("Semantic index is not built")
        raw = {
            str(item["message_id"]): item
            for item in (
                raw_records if raw_records is not None else self.store.read_all_raw()
            )
        }
        manifest_path = self.store.index_dir / "semantic" / "manifest.json"
        if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_SEMANTIC_MANIFEST_BYTES:
            raise ValueError("Semantic index manifest exceeds the query bound")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_fields = {
            "format", "provider", "model_id", "model_revision", "interface_version",
            "vector_file", "record_count", "raw_source", "disposable",
            "raw_archive_required_for_verification",
        }
        raw_source_fields = {"format", "record_count", "high_watermark", "identity_sha256"}
        watermark_fields = {"sequence", "message_id", "record_sha256"}
        indexed_source = manifest.get("raw_source") if isinstance(manifest, dict) else None
        indexed_watermark = (
            indexed_source.get("high_watermark")
            if isinstance(indexed_source, dict)
            else None
        )
        if (
            not isinstance(manifest, dict)
            or set(manifest) != manifest_fields
            or manifest.get("format") != "memory-wuxian-semantic-index-v2"
            or manifest.get("provider") not in {"local-hash-v1", E5_PROVIDER}
            or isinstance(manifest.get("record_count"), bool)
            or not isinstance(manifest.get("record_count"), int)
            or not 0 <= manifest["record_count"] <= MAX_SEMANTIC_RECORDS
            or not isinstance(indexed_source, dict)
            or set(indexed_source) != raw_source_fields
            or indexed_source.get("format") != "memory-wuxian-raw-source-snapshot-v1"
            or indexed_source.get("record_count") != manifest.get("record_count")
            or not isinstance(indexed_source.get("identity_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", indexed_source["identity_sha256"])
            or (
                manifest.get("record_count") == 0
                and indexed_watermark is not None
            )
            or (
                manifest.get("record_count", 0) > 0
                and (
                    not isinstance(indexed_watermark, dict)
                    or set(indexed_watermark) != watermark_fields
                    or isinstance(indexed_watermark.get("sequence"), bool)
                    or not isinstance(indexed_watermark.get("sequence"), int)
                    or indexed_watermark["sequence"] < 1
                    or not isinstance(indexed_watermark.get("message_id"), str)
                    or not indexed_watermark["message_id"]
                    or not isinstance(indexed_watermark.get("record_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", indexed_watermark["record_sha256"])
                )
            )
            or manifest.get("disposable") is not True
            or manifest.get("raw_archive_required_for_verification") is not True
        ):
            raise ValueError("Semantic index manifest is malformed")
        current_source = raw_source_snapshot(raw.values())
        if current_source != indexed_source:
            raise SemanticIndexStaleError(
                "Semantic index does not cover the current raw archive"
            )
        provider = manifest["provider"]
        query_vector = self._hash_embedding(query)
        e5_scores = None
        if provider == E5_PROVIDER:
            if not isinstance(manifest.get("vector_file"), str) or Path(manifest["vector_file"]).name != manifest["vector_file"]:
                raise ValueError("Semantic vector filename is malformed")
            e5_scores = self.e5_scores(
                query,
                self.store.index_dir / "semantic" / str(manifest["vector_file"]),
            )
            if len(e5_scores) != manifest["record_count"]:
                raise ValueError("Semantic score count does not match the manifest")
        elif manifest.get("vector_file") is not None:
            raise ValueError("Local semantic index must not declare a matrix")
        matches = []
        scanned_bytes = 0
        record_count = 0
        allowed_fields = {
            "message_id", "conversation_id", "raw_path", "record_sha256", "raw_line_start",
            "raw_line_end", "verified_against_raw", "provider", "vector",
        }
        with path.open("rb") as handle:
            while True:
                line = handle.readline(MAX_SEMANTIC_LINE_BYTES + 1)
                if not line:
                    break
                scanned_bytes += len(line)
                if len(line) > MAX_SEMANTIC_LINE_BYTES or scanned_bytes > MAX_SEMANTIC_INDEX_BYTES:
                    raise ValueError("Semantic index exceeds the query bound")
                record_count += 1
                if record_count > MAX_SEMANTIC_RECORDS:
                    raise ValueError("Semantic index record count exceeds the query bound")
                item = json.loads(line.decode("utf-8"))
                required = allowed_fields - {"vector"}
                if (
                    not isinstance(item, dict)
                    or set(item) - allowed_fields
                    or not required.issubset(item)
                    or item.get("provider") != provider
                    or not all(isinstance(item.get(key), str) and item[key] for key in ("message_id", "conversation_id", "raw_path", "record_sha256"))
                ):
                    raise ValueError("Semantic index record is malformed")
                vector = item.get("vector")
                if provider == "local-hash-v1" and (
                    not isinstance(vector, list)
                    or len(vector) != len(query_vector)
                    or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector)
                ):
                    raise ValueError("Semantic index vector is malformed")
                if provider == E5_PROVIDER and vector is not None:
                    raise ValueError("E5 semantic metadata must not contain vectors")
                source = raw.get(item["message_id"])
                if not source or source.get("content_sha256") != item["record_sha256"]:
                    continue
                score = e5_scores[record_count - 1] if e5_scores is not None else float(sum(a * float(b) for a, b in zip(query_vector, vector)))
                match = {
                    "message_id": item["message_id"], "conversation_id": item["conversation_id"],
                    "score": round(score, 8), "raw_path": item["raw_path"],
                    "raw_line_start": item.get("raw_line_start"), "raw_line_end": item.get("raw_line_end"),
                    "record_sha256": item["record_sha256"],
                }
                heapq.heappush(matches, (match["score"], record_count, match))
                if len(matches) > top_k:
                    heapq.heappop(matches)
        if record_count != manifest["record_count"]:
            raise ValueError("Semantic index record count does not match the manifest")
        ranked = [entry[2] for entry in sorted(matches, key=lambda entry: (entry[0], entry[1]), reverse=True)]
        return {
            "query": query,
            "provider": provider,
            "matches": ranked,
            "verified_against_raw": True,
        }
