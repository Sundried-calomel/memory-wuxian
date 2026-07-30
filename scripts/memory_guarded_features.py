#!/usr/bin/env python3
"""Guarded portability, history, graph, evaluation, and semantic features."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


AUTHORITATIVE_PREFIXES = ("raw/", "summaries/")
AUTHORITATIVE_FILES = {"state.json"}
IGNORED_MIGRATION_PARTS = {".locks", ".DS_Store"}
E5_PROVIDER = "multilingual-e5-small"
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


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
        requested: Dict[str, set[str]] = {}
        for record in records:
            relative = str(record.get("_path", ""))
            message_id = str(record.get("message_id", ""))
            if relative and message_id:
                requested.setdefault(relative, set()).add(message_id)
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
                    pointers[(relative, message_id)] = {
                        "raw_line_start": index + 1,
                        "raw_line_end": index + 4,
                    }
        return pointers

    def retrieval_evaluate(self, dataset: Path, top_k: int) -> Dict[str, Any]:
        cases = [
            json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results = []
        started = time.perf_counter()
        for case in cases:
            case_started = time.perf_counter()
            _, metadata = self.store.retrieve(str(case["query"]))
            actual = [item["message_id"] for item in metadata["raw_matches"][:top_k]]
            expected = set(map(str, case.get("expected_message_ids", [])))
            hits = len(expected.intersection(actual))
            results.append({
                "id": case.get("id"),
                "query": case["query"],
                "expected_message_ids": sorted(expected),
                "actual_message_ids": actual,
                "recall_at_k": hits / len(expected) if expected else 1.0,
                "wrong_citation_count": len([item for item in actual if item not in expected]),
                "latency_ms": round((time.perf_counter() - case_started) * 1000, 3),
            })
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
        model_dir = (
            Path.home()
            / ".codex/models/memory-wuxian/multilingual-e5-small"
            / E5_REVISION
        )
        runtime_python = Path.home() / ".codex/runtimes/memory-wuxian-e5-py312"
        runtime_python /= "Scripts/python.exe" if os.name == "nt" else "bin/python"
        return model_dir, runtime_python

    def validate_e5_install(self) -> tuple[Path, Path]:
        model_dir, runtime_python = self.e5_paths()
        manifest_path = model_dir / "model-manifest.json"
        if not runtime_python.exists() or not manifest_path.exists():
            raise ValueError(
                "multilingual-e5-small is not installed; run scripts/install_multilingual_e5.py"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("format") != "memory-wuxian-e5-model-v1"
            or manifest.get("model_id") != "intfloat/multilingual-e5-small"
            or manifest.get("model_revision") != E5_REVISION
            or manifest.get("offline_only") is not True
        ):
            raise ValueError("multilingual-e5-small manifest does not match the pinned provider")
        for artifact in manifest.get("artifacts", []):
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
                    "--model-dir", str(model_dir),
                    "--input", str(payload),
                    "--output", str(output),
                    "--prefix", prefix,
                    "--batch-size", str(batch_size),
                ],
                check=True,
                env=self.e5_environment(),
            )
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
            subprocess.run(
                [
                    str(runtime_python),
                    str(Path(__file__).with_name("semantic_e5_worker.py")),
                    "--model-dir", str(model_dir),
                    "--input", str(payload),
                    "--output", str(output),
                    "--prefix", "query",
                    "--batch-size", "1",
                    "--matrix", str(matrix),
                ],
                check=True,
                env=self.e5_environment(),
            )
            return [
                float(value)
                for value in json.loads(output.read_text(encoding="utf-8"))["scores"]
            ]

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
        atomic_json(directory / "manifest.json", {
            "format": "memory-wuxian-semantic-index-v1",
            "provider": provider,
            "model_id": "intfloat/multilingual-e5-small" if provider == E5_PROVIDER else None,
            "model_revision": E5_REVISION if provider == E5_PROVIDER else None,
            "vector_file": vector_path.name if vector_path else None,
            "record_count": len(records),
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

    def semantic_retrieve(self, query: str, top_k: int) -> Dict[str, Any]:
        path = self.store.index_dir / "semantic" / "vectors.jsonl"
        if not path.exists():
            raise ValueError("Semantic index is not built")
        raw = {str(item["message_id"]): item for item in self.store.read_all_raw()}
        manifest = json.loads(
            (self.store.index_dir / "semantic" / "manifest.json").read_text(encoding="utf-8")
        )
        provider = str(manifest.get("provider") or "local-hash-v1")
        query_vector = self._hash_embedding(query)
        e5_scores = None
        if provider == E5_PROVIDER:
            e5_scores = self.e5_scores(
                query,
                self.store.index_dir / "semantic" / str(manifest["vector_file"]),
            )
        matches = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            item = json.loads(line)
            source = raw.get(str(item["message_id"]))
            if not source or source.get("content_sha256") != item["record_sha256"]:
                continue
            score = (
                e5_scores[index]
                if e5_scores is not None
                else float(sum(a * b for a, b in zip(query_vector, item["vector"])))
            )
            matches.append({
                "message_id": item["message_id"],
                "conversation_id": item["conversation_id"],
                "score": round(score, 8),
                "raw_path": item["raw_path"],
                "raw_line_start": item.get("raw_line_start"),
                "raw_line_end": item.get("raw_line_end"),
                "record_sha256": item["record_sha256"],
            })
        matches.sort(key=lambda item: item["score"], reverse=True)
        return {
            "query": query,
            "provider": provider,
            "matches": matches[:top_k],
            "verified_against_raw": True,
        }
