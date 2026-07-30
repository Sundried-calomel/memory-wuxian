"""Deterministic micro-batch queue for ephemeral governance AI reviews."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from memory_environment import EnvironmentRegistry, atomic_write_json, canonical_bytes
from memory_federation import FederationManager, safe_node_id
from platform_lock import exclusive_lock


TASK_KINDS = {
    "evolution-synthesis",
    "lesson-extraction",
    "governance-classification",
    "supersession-review",
}
SOURCE_TASKS = {"evolution-synthesis", "lesson-extraction"}
GLOBAL_TASKS = {"governance-classification", "supersession-review"}
ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,191}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_POLICY = {
    "enabled": False,
    "mode": "automatic-drafts",
    "coordinator_node_id": "",
    "product_min_items": 3,
    "product_max_items": 5,
    "product_max_wait_seconds": 6 * 60 * 60,
    "classification_min_items": 5,
    "classification_max_items": 10,
    "classification_max_wait_seconds": 24 * 60 * 60,
    "maximum_evidence_characters": 80000,
    "maximum_ai_runs_per_day": 6,
    "maximum_failed_retries": 2,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GovernanceAIQueue:
    """Own model-free queueing, batching, limits, receipts, and draft storage."""

    def __init__(self, store: Any):
        if not hasattr(store, "config"):
            store = type(
                "_GovernanceAIMemoryStore",
                (),
                {"root": Path(store), "config": {}},
            )()
        self.store = store
        self.registry = EnvironmentRegistry(store.root)
        self.federation = FederationManager(store)
        self.root = self.registry.root / "governance-ai"
        self.pending = self.root / "pending"
        self.completed = self.root / "completed"
        self.failed = self.root / "failed"
        self.results = self.root / "results"
        self.failures = self.root / "failure-events"
        self.policy_path = self.root / "policy.json"
        self.lock_path = self.registry.locks_dir / "environment-governance-ai.lock"

    def init(self) -> dict[str, Any]:
        self.registry.init()
        for path in (
            self.pending,
            self.completed,
            self.failed,
            self.results,
            self.failures,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return {"status": "initialized", "root": str(self.root)}

    def local_node_id(self) -> str:
        return safe_node_id(self.federation.node()["node_id"])

    def optional_local_node_id(self) -> str | None:
        try:
            return self.local_node_id()
        except (OSError, ValueError, KeyError):
            return None

    def policy(self) -> dict[str, Any]:
        policy = dict(DEFAULT_POLICY)
        configured = self.store.config.get("governance_ai", {})
        if isinstance(configured, dict):
            policy.update({key: value for key, value in configured.items() if key in policy})
        if self.policy_path.is_file():
            runtime = json.loads(self.policy_path.read_text(encoding="utf-8"))
            if not isinstance(runtime, dict):
                raise ValueError("governance AI policy must be an object")
            policy.update({key: value for key, value in runtime.items() if key in policy})
        self._validate_policy(policy)
        return policy

    @staticmethod
    def _validate_policy(policy: dict[str, Any]) -> None:
        if policy["mode"] not in {"manual", "automatic-drafts"}:
            raise ValueError("governance AI mode is unsupported")
        if not isinstance(policy["enabled"], bool):
            raise ValueError("governance AI enabled must be boolean")
        coordinator = str(policy["coordinator_node_id"])
        if coordinator:
            safe_node_id(coordinator)
        positive = (
            "product_min_items",
            "product_max_items",
            "product_max_wait_seconds",
            "classification_min_items",
            "classification_max_items",
            "classification_max_wait_seconds",
            "maximum_evidence_characters",
            "maximum_ai_runs_per_day",
            "maximum_failed_retries",
        )
        for key in positive:
            if not isinstance(policy[key], int) or policy[key] < 1:
                raise ValueError(f"governance AI {key} must be a positive integer")
        if policy["product_min_items"] > policy["product_max_items"]:
            raise ValueError("product minimum exceeds maximum")
        if policy["classification_min_items"] > policy["classification_max_items"]:
            raise ValueError("classification minimum exceeds maximum")

    def configure(self, changes: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
        current = self.policy()
        unknown = sorted(set(changes) - set(DEFAULT_POLICY))
        if unknown:
            raise ValueError("unknown governance AI policy fields: " + ", ".join(unknown))
        proposed = {**current, **changes}
        self._validate_policy(proposed)
        result = {
            "status": "preview",
            "policy": proposed,
            "automatic_acceptance": False,
            "automatic_installation": False,
        }
        if not apply:
            return result
        self.init()
        with exclusive_lock(self.lock_path):
            atomic_write_json(
                self.policy_path,
                {key: proposed[key] for key in DEFAULT_POLICY},
            )
        return {**result, "status": "configured"}

    @staticmethod
    def validate_item(value: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "item_id",
            "task_kind",
            "origin_node_id",
            "owner_id",
            "product_ids",
            "source_revision",
            "priority",
            "evidence",
            "created_at",
        }
        if not isinstance(value, dict) or not required.issubset(value):
            raise ValueError("governance AI item fields are incomplete")
        if value["schema_version"] != 1:
            raise ValueError("governance AI schema_version is unsupported")
        if not ITEM_ID_RE.fullmatch(str(value["item_id"])):
            raise ValueError("governance AI item_id is invalid")
        if value["task_kind"] not in TASK_KINDS:
            raise ValueError("governance AI task_kind is unsupported")
        safe_node_id(str(value["origin_node_id"]))
        if not isinstance(value["owner_id"], str) or len(value["owner_id"]) < 2:
            raise ValueError("governance AI owner_id is invalid")
        if not isinstance(value["product_ids"], list) or not value["product_ids"]:
            raise ValueError("governance AI product_ids must be non-empty")
        if value["priority"] not in {"normal", "urgent"}:
            raise ValueError("governance AI priority is unsupported")
        if value["priority"] == "urgent" and not value.get("urgent_reason"):
            raise ValueError("urgent governance AI items require urgent_reason")
        parse_time(str(value["created_at"]))
        if not isinstance(value["evidence"], list) or not value["evidence"]:
            raise ValueError("governance AI evidence must be non-empty")
        evidence_ids: set[str] = set()
        for evidence in value["evidence"]:
            evidence_id = str(evidence.get("evidence_id", ""))
            if not evidence_id or evidence_id in evidence_ids:
                raise ValueError("governance AI evidence IDs must be unique")
            evidence_ids.add(evidence_id)
            content = evidence.get("content")
            digest = evidence.get("sha256")
            if not isinstance(content, str) or not SHA256_RE.fullmatch(str(digest)):
                raise ValueError("governance AI evidence content or hash is invalid")
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
                raise ValueError("governance AI evidence content hash mismatch")

    def enqueue(self, value: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
        self.validate_item(value)
        if safe_node_id(str(value["origin_node_id"])) != self.local_node_id():
            raise ValueError("governance AI queue accepts only locally originated items")
        payload = canonical_bytes(value)
        digest = hashlib.sha256(payload).hexdigest()
        item_id = str(value["item_id"])
        result = {
            "status": "preview",
            "item_id": item_id,
            "content_sha256": digest,
            "ai_invoked": False,
        }
        matches = list(self.root.glob(f"*/{item_id}-*.json")) if self.root.exists() else []
        if matches:
            existing = json.loads(matches[0].read_text(encoding="utf-8"))
            if existing == value:
                return {**result, "status": "no-change", "path": str(matches[0])}
            raise ValueError("governance AI item ID already has different content")
        if not apply:
            return result
        self.init()
        path = self.pending / f"{item_id}-{digest}.json"
        with exclusive_lock(self.lock_path):
            if list(self.root.glob(f"*/{item_id}-*.json")):
                raise ValueError("governance AI item appeared before apply")
            atomic_write_json(path, value)
        return {**result, "status": "queued", "path": str(path)}

    def pending_items(self) -> list[dict[str, Any]]:
        if not self.pending.is_dir():
            return []
        items = []
        for path in sorted(self.pending.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.validate_item(value)
            items.append({**value, "_path": str(path)})
        return items

    @staticmethod
    def _evidence(
        evidence_id: str,
        kind: str,
        reference: str,
        value: Any,
    ) -> dict[str, Any]:
        content = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "reference": reference,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
        }

    @staticmethod
    def _decode_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = base64.b64decode(
                str(envelope["content_base64"]), validate=True
            )
        except Exception as error:
            raise ValueError("governance source envelope encoding is invalid") from error
        if hashlib.sha256(payload).hexdigest() != envelope.get("content_sha256"):
            raise ValueError("governance source envelope hash mismatch")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("governance source envelope content must be an object")
        return value

    def _item_for(
        self,
        *,
        task_kind: str,
        owner_id: str,
        product_ids: list[str],
        source_revision: str,
        evidence: list[dict[str, Any]],
        created_at: str,
        priority: str = "normal",
        urgent_reason: str | None = None,
    ) -> dict[str, Any]:
        identity = {
            "contract": 1,
            "task_kind": task_kind,
            "owner_id": owner_id,
            "product_ids": sorted(product_ids),
            "source_revision": source_revision,
            "evidence_sha256": [item["sha256"] for item in evidence],
        }
        item_id = "gai-" + hashlib.sha256(canonical_bytes(identity)).hexdigest()
        return {
            "schema_version": 1,
            "item_id": item_id,
            "task_kind": task_kind,
            "origin_node_id": self.local_node_id(),
            "owner_id": owner_id,
            "product_ids": product_ids,
            "source_revision": source_revision,
            "priority": priority,
            "urgent_reason": urgent_reason,
            "evidence": evidence,
            "created_at": created_at,
        }

    def _owner_evidence(self, owner_id: str) -> dict[str, Any]:
        candidates = (
            f"global-skill:{owner_id}",
            f"global-rule:{owner_id}",
        )
        for artifact_id in candidates:
            try:
                shown = self.registry.show(artifact_id)
            except (OSError, ValueError, KeyError):
                continue
            revision = shown["revision"]
            object_path = self.registry.root / revision["object_path"]
            raw = object_path.read_bytes()
            try:
                parsed = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = {
                    "binary_sha256": hashlib.sha256(raw).hexdigest(),
                    "binary_size": len(raw),
                    "content_omitted": True,
                }
            if isinstance(parsed, dict):
                parsed = {
                    key: value
                    for key, value in parsed.items()
                    if key not in {"content_base64", "package_attachment"}
                }
            return self._evidence(
                f"current-owner-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}",
                "current-owner",
                artifact_id,
                {
                    "artifact": shown["artifact"],
                    "revision": revision,
                    "content": parsed,
                },
            )
        inventory = {
            "observation": "No exact registered global Owner artifact matched.",
            "proposed_owner_id": owner_id,
            "registered_artifact_ids": [
                item["artifact_id"] for item in self.registry.list()
            ],
        }
        return self._evidence(
            f"current-owner-inventory-{hashlib.sha256(owner_id.encode()).hexdigest()[:16]}",
            "current-owner",
            "environment-registry-inventory",
            inventory,
        )

    def discover(self, *, apply: bool = False) -> dict[str, Any]:
        """Create deterministic queue items from registered, already bounded evidence."""
        if apply:
            self.init()
        elif not self.registry.registry_path.is_file():
            return {
                "status": "preview",
                "candidates": 0,
                "result_counts": {},
                "ai_invoked": False,
            }
        registry = json.loads(self.registry.registry_path.read_text(encoding="utf-8"))
        candidates: list[dict[str, Any]] = []
        for event in registry.get("events", []):
            if event.get("operation") != "artifact-revision":
                continue
            artifact_path = self.registry.root / event["artifact_path"]
            revision_path = self.registry.root / event["revision_path"]
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            revision = json.loads(revision_path.read_text(encoding="utf-8"))
            evidence = [
                self._evidence(
                    f"environment-revision-{revision['revision_id'].split(':')[-1][:16]}",
                    "source",
                    event["revision_path"],
                    {"artifact": artifact, "revision": revision},
                )
            ]
            candidates.append(
                self._item_for(
                    task_kind="evolution-synthesis",
                    owner_id=artifact["artifact_id"],
                    product_ids=[artifact["artifact_id"]],
                    source_revision=revision["revision_id"],
                    evidence=evidence,
                    created_at=event["recorded_at"],
                )
            )

        evolution_roots = [self.registry.root / "product-evolution" / "local"]
        for directory in evolution_roots:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                envelope = json.loads(path.read_text(encoding="utf-8"))
                record = self._decode_envelope(envelope)
                candidates.append(
                    self._item_for(
                        task_kind="lesson-extraction",
                        owner_id=str(record["owner_id"]),
                        product_ids=[str(record["product_id"])],
                        source_revision=str(record["source_revision"]),
                        evidence=[
                            self._evidence(
                                f"product-evolution-{envelope['content_sha256'][:16]}",
                                "product-evolution",
                                str(path.relative_to(self.registry.root)),
                                record,
                            )
                        ],
                        created_at=str(record["created_at"]),
                    )
                )

        proposal_paths = []
        local_proposals = self.registry.root / "governance-proposals" / "local"
        if local_proposals.is_dir():
            proposal_paths.extend(local_proposals.glob("*.json"))
        replicas = self.registry.root / "replicas" / "peers"
        if replicas.is_dir():
            proposal_paths.extend(replicas.glob("*/governance-proposals/*.json"))
        for path in sorted(proposal_paths, key=str):
            envelope = json.loads(path.read_text(encoding="utf-8"))
            proposal = self._decode_envelope(envelope)
            owner_id = str(proposal["proposed_global_owner"])
            evidence = [
                self._evidence(
                    f"governance-proposal-{envelope['content_sha256'][:16]}",
                    "governance-proposal",
                    str(path.relative_to(self.registry.root)),
                    proposal,
                ),
                self._owner_evidence(owner_id),
            ]
            candidates.append(
                self._item_for(
                    task_kind="governance-classification",
                    owner_id=owner_id,
                    product_ids=[str(proposal["source_product_id"])],
                    source_revision=str(proposal["source_revision"]),
                    evidence=evidence,
                    created_at=str(proposal["created_at"]),
                    priority=(
                        "urgent"
                        if proposal.get("classification") == "conflict"
                        else "normal"
                    ),
                    urgent_reason=(
                        "source proposal declares an active governance conflict"
                        if proposal.get("classification") == "conflict"
                        else None
                    ),
                )
            )

        results = []
        for candidate in candidates:
            results.append(self.enqueue(candidate, apply=apply))
        counts: dict[str, int] = {}
        for result in results:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        return {
            "status": "discovered" if apply else "preview",
            "candidates": len(candidates),
            "result_counts": counts,
            "ai_invoked": False,
        }

    @staticmethod
    def group_key(item: dict[str, Any]) -> tuple[str, ...]:
        if item["task_kind"] in SOURCE_TASKS:
            if len(item["product_ids"]) != 1:
                raise ValueError("source-device governance tasks require one product")
            return (item["task_kind"], item["owner_id"], item["product_ids"][0])
        return (item["task_kind"], item["owner_id"])

    def eligible(self, item: dict[str, Any], policy: dict[str, Any]) -> bool:
        local = self.optional_local_node_id()
        if local is None:
            return False
        if item["task_kind"] in SOURCE_TASKS:
            return item["origin_node_id"] == local
        coordinator = str(policy["coordinator_node_id"])
        return bool(coordinator) and safe_node_id(coordinator) == local

    def _failure_count(self, item_id: str) -> int:
        if not self.failures.is_dir():
            return 0
        return len(list(self.failures.glob(f"{item_id}-*.json")))

    def _runs_today(self, at: datetime) -> int:
        if not self.results.is_dir():
            return 0
        prefix = at.astimezone(timezone.utc).date().isoformat()
        count = 0
        for path in self.results.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if str(value.get("completed_at", "")).startswith(prefix):
                count += 1
        return count

    def due_batches(self, *, at: datetime | None = None) -> list[dict[str, Any]]:
        at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        policy = self.policy()
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for item in self.pending_items():
            if self.eligible(item, policy):
                groups.setdefault(self.group_key(item), []).append(item)
        batches = []
        for key, items in sorted(groups.items()):
            items.sort(key=lambda value: (parse_time(value["created_at"]), value["item_id"]))
            urgent = any(item["priority"] == "urgent" for item in items)
            source = items[0]["task_kind"] in SOURCE_TASKS
            minimum = policy["product_min_items"] if source else policy["classification_min_items"]
            maximum = policy["product_max_items"] if source else policy["classification_max_items"]
            wait = policy["product_max_wait_seconds"] if source else policy["classification_max_wait_seconds"]
            age = max(0.0, (at - parse_time(items[0]["created_at"])).total_seconds())
            if not urgent and len(items) < minimum and age < wait:
                continue
            selected = []
            characters = 0
            for item in items:
                item_characters = sum(len(entry["content"]) for entry in item["evidence"])
                if selected and (
                    len(selected) >= maximum
                    or characters + item_characters > policy["maximum_evidence_characters"]
                ):
                    break
                if not selected and item_characters > policy["maximum_evidence_characters"]:
                    continue
                selected.append(item)
                characters += item_characters
            if not selected:
                continue
            item_ids = sorted(item["item_id"] for item in selected)
            batch_id = "gai-" + hashlib.sha256(
                canonical_bytes(
                    {
                        "contract": 1,
                        "group": key,
                        "item_ids": item_ids,
                    }
                )
            ).hexdigest()
            batches.append(
                {
                    "batch_id": batch_id,
                    "task_kind": selected[0]["task_kind"],
                    "owner_id": selected[0]["owner_id"],
                    "item_ids": item_ids,
                    "items": selected,
                    "evidence_characters": characters,
                    "due_reason": "urgent" if urgent else ("count" if len(items) >= minimum else "age"),
                }
            )
        return batches

    @staticmethod
    def validate_result(result: dict[str, Any], batch: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "batch_id",
            "task_kind",
            "source_item_ids",
            "facts",
            "interpretations",
            "recommendations",
            "classifications",
            "product_evolution_records",
            "governance_proposals",
            "no_change",
            "human_review_required",
        }
        if not isinstance(result, dict) or set(result) != required:
            raise ValueError("governance AI result fields are invalid")
        if result["schema_version"] != 1:
            raise ValueError("governance AI result schema_version is unsupported")
        if result["batch_id"] != batch["batch_id"]:
            raise ValueError("governance AI result batch identity mismatch")
        if result["task_kind"] != batch["task_kind"]:
            raise ValueError("governance AI result task identity mismatch")
        if sorted(result["source_item_ids"]) != sorted(batch["item_ids"]):
            raise ValueError("governance AI result source item mismatch")
        if result["human_review_required"] is not True:
            raise ValueError("governance AI result must require human review")
        evidence_ids = {
            entry["evidence_id"]
            for item in batch["items"]
            for entry in item["evidence"]
        }
        for section in ("facts", "interpretations", "recommendations", "classifications"):
            if not isinstance(result[section], list):
                raise ValueError(f"governance AI result {section} must be an array")
            for entry in result[section]:
                refs = entry.get("evidence_refs", [])
                if not refs or not set(refs).issubset(evidence_ids):
                    raise ValueError(f"governance AI result {section} has invalid evidence refs")
        for section in ("product_evolution_records", "governance_proposals"):
            if not isinstance(result[section], list):
                raise ValueError(f"governance AI result {section} must be an array")

    def tick(
        self,
        *,
        run_ai: bool,
        worker: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        at: datetime | None = None,
        maximum_batches: int = 1,
    ) -> dict[str, Any]:
        if maximum_batches < 1:
            raise ValueError("maximum_batches must be positive")
        discovery = self.discover(apply=True)
        policy = self.policy()
        due = self.due_batches(at=at)
        preview = {
            "status": "disabled" if not policy["enabled"] else "idle",
            "ai_invoked": False,
            "due_batches": [
                {key: value for key, value in batch.items() if key != "items"}
                for batch in due
            ],
            "policy": policy,
            "discovery": discovery,
        }
        if not policy["enabled"] or policy["mode"] == "manual" or not run_ai or not due:
            if due and policy["enabled"]:
                preview["status"] = "due"
            return preview
        at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        remaining = policy["maximum_ai_runs_per_day"] - self._runs_today(at)
        if remaining <= 0:
            return {**preview, "status": "daily-limit"}
        if worker is None:
            from governance_ai_worker import run_batch

            worker = lambda batch: run_batch(self.store, batch)
        completed = []
        failed = []
        self.init()
        with exclusive_lock(self.lock_path):
            for batch in due[: min(maximum_batches, remaining)]:
                try:
                    result = worker(batch)
                    self.validate_result(result, batch)
                    completed_at = at.isoformat()
                    result_record = {**result, "completed_at": completed_at}
                    result_path = self.results / f"{batch['batch_id']}.json"
                    if result_path.exists():
                        existing = json.loads(result_path.read_text(encoding="utf-8"))
                        if existing != result_record:
                            raise ValueError("governance AI result batch already differs")
                    else:
                        atomic_write_json(result_path, result_record)
                    for item in batch["items"]:
                        source = Path(item["_path"])
                        destination = self.completed / source.name
                        os.replace(source, destination)
                    completed.append(batch["batch_id"])
                except Exception as error:
                    error_digest = hashlib.sha256(
                        f"{type(error).__name__}:{error}".encode("utf-8")
                    ).hexdigest()
                    for item in batch["items"]:
                        item_id = item["item_id"]
                        count = self._failure_count(item_id) + 1
                        failure = {
                            "item_id": item_id,
                            "batch_id": batch["batch_id"],
                            "attempt": count,
                            "error_type": type(error).__name__,
                            "error_digest": error_digest,
                            "failed_at": at.isoformat(),
                        }
                        atomic_write_json(
                            self.failures / f"{item_id}-{count:03d}-{error_digest}.json",
                            failure,
                        )
                        if count >= policy["maximum_failed_retries"]:
                            source = Path(item["_path"])
                            if source.exists():
                                os.replace(source, self.failed / source.name)
                    failed.append(
                        {
                            "batch_id": batch["batch_id"],
                            "error_type": type(error).__name__,
                            "error_digest": error_digest,
                        }
                    )
        return {
            **preview,
            "status": "completed" if completed and not failed else ("failed" if failed else "idle"),
            "ai_invoked": bool(completed or failed),
            "completed_batches": completed,
            "failed_batches": failed,
        }

    def status(self) -> dict[str, Any]:
        if not self.registry.registry_path.is_file():
            return {
                "status": "not-initialized",
                "local_node_id": None,
                "coordinator_node_id": None,
                "is_coordinator": False,
                "policy": dict(DEFAULT_POLICY),
                "counts": {
                    "pending": 0,
                    "due_batches": 0,
                    "completed_items": 0,
                    "failed_items": 0,
                    "draft_results": 0,
                },
                "automatic_acceptance": False,
                "automatic_installation": False,
                "automatic_remediation": False,
            }
        policy = self.policy()
        local = self.optional_local_node_id()
        if local is None:
            return {
                "status": "node-not-initialized",
                "local_node_id": None,
                "coordinator_node_id": policy["coordinator_node_id"] or None,
                "is_coordinator": False,
                "policy": policy,
                "counts": {
                    "pending": len(list(self.pending.glob("*.json")))
                    if self.pending.is_dir()
                    else 0,
                    "due_batches": 0,
                    "completed_items": 0,
                    "failed_items": 0,
                    "draft_results": 0,
                },
                "automatic_acceptance": False,
                "automatic_installation": False,
                "automatic_remediation": False,
            }
        due = self.due_batches()
        return {
            "status": "ok",
            "local_node_id": local,
            "coordinator_node_id": policy["coordinator_node_id"] or None,
            "is_coordinator": bool(policy["coordinator_node_id"])
            and safe_node_id(str(policy["coordinator_node_id"])) == local,
            "policy": policy,
            "counts": {
                "pending": len(list(self.pending.glob("*.json"))),
                "due_batches": len(due),
                "completed_items": len(list(self.completed.glob("*.json"))),
                "failed_items": len(list(self.failed.glob("*.json"))),
                "draft_results": len(list(self.results.glob("*.json"))),
            },
            "automatic_acceptance": False,
            "automatic_installation": False,
            "automatic_remediation": False,
        }
