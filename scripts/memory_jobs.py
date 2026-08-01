#!/usr/bin/env python3
"""Persistent model-free maintenance queue for Memory Wuxian."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json, read_canonical_json


FORMAT = "memory-wuxian-maintenance-job-v1"
STATES = {"queued", "running", "retry", "semantic-ready", "completed", "quarantined"}
KINDS = {"backup-debt", "semantic-summary-eligibility", "archive-health"}
TERMINAL_STATES = {"semantic-ready", "completed", "quarantined"}
PROJECTION_FORMAT = "memory-wuxian-maintenance-projection-v1"


def _strip_windows_extended_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _is_windows_absolute(value: str) -> bool:
    drive, _tail = ntpath.splitdrive(value)
    return bool(drive) and ntpath.isabs(value)


def canonical_path_text(value: os.PathLike[str] | str) -> str:
    """Return one usable path spelling across normal and Windows extended paths."""
    text = _strip_windows_extended_prefix(os.fspath(value))
    if _is_windows_absolute(text):
        return ntpath.normpath(text)
    return str(Path(text).resolve())


def stable_path_identity(value: os.PathLike[str] | str) -> str:
    """Return a comparison identity that folds Windows path spelling and case."""
    canonical = canonical_path_text(value)
    if _is_windows_absolute(canonical):
        return ntpath.normcase(canonical)
    return os.path.normcase(os.path.normpath(canonical))


def _semantic_payload_equivalent(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_copy = dict(left)
    right_copy = dict(right)
    left_path = left_copy.pop("summary_job", None)
    right_path = right_copy.pop("summary_job", None)
    return (
        left_copy == right_copy
        and left_path is not None
        and right_path is not None
        and stable_path_identity(left_path) == stable_path_identity(right_path)
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def redact_error(value: BaseException | str) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    for marker in ("token=", "password=", "secret=", "authorization:"):
        start = text.casefold().find(marker)
        if start >= 0:
            end = text.find(" ", start)
            text = text[:start] + marker + "[REDACTED]" + (text[end:] if end >= 0 else "")
    return text[:500]


class MaintenanceQueue:
    def __init__(self, archive_root: Path, *, clock: Callable[[], datetime] = now_utc):
        self.archive_root = Path(archive_root)
        self.root = self.archive_root / "maintenance" / "jobs"
        self.lock = self.archive_root / ".locks" / "maintenance-jobs.lock"
        self.clock = clock

    def _path(self, job_id: str) -> Path:
        if not job_id.startswith("job-") or len(job_id) != 68:
            raise ValueError("Invalid maintenance job identity")
        return self.root / f"{job_id}.json"

    @staticmethod
    def _validate(job: Any) -> Dict[str, Any]:
        fields = {
            "format", "job_id", "kind", "idempotency_key", "payload", "state",
            "attempts", "max_attempts", "available_at", "created_at", "updated_at",
            "lease_owner", "lease_expires_at", "last_error", "result_sha256",
        }
        if not isinstance(job, dict) or set(job) != fields:
            raise ValueError("Maintenance job has an invalid closed field set")
        if job["format"] != FORMAT or job["kind"] not in KINDS or job["state"] not in STATES:
            raise ValueError("Maintenance job format, kind, or state is unsupported")
        expected = "job-" + canonical_hash({
            "kind": job["kind"],
            "idempotency_key": job["idempotency_key"],
        })
        if job["job_id"] != expected:
            raise ValueError("Maintenance job identity mismatch")
        if not isinstance(job["payload"], dict):
            raise ValueError("Maintenance job payload must be an object")
        if not isinstance(job["attempts"], int) or not isinstance(job["max_attempts"], int):
            raise ValueError("Maintenance retry counters must be integers")
        if not 0 <= job["attempts"] <= job["max_attempts"] or job["max_attempts"] < 1:
            raise ValueError("Maintenance retry counters are invalid")
        for field in ("available_at", "created_at", "updated_at"):
            parse_iso(job[field])
        if (job["lease_owner"] is None) != (job["lease_expires_at"] is None):
            raise ValueError("Maintenance lease fields must be set together")
        if job["lease_expires_at"] is not None:
            parse_iso(job["lease_expires_at"])
        return job

    def _read(self, path: Path) -> Dict[str, Any]:
        return self._validate(read_canonical_json(path))

    def _write(self, job: Dict[str, Any]) -> None:
        atomic_write_canonical_json(self._path(job["job_id"]), self._validate(job))

    def enqueue(
        self,
        kind: str,
        idempotency_key: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        if kind not in KINDS or not idempotency_key or max_attempts < 1 or max_attempts > 20:
            raise ValueError("Invalid maintenance job request")
        if kind == "semantic-summary-eligibility" and (
            not isinstance(payload, dict)
            or payload.get("round_complete") is not True
            or not isinstance(payload.get("completed_round"), int)
            or payload["completed_round"] < 1
        ):
            raise ValueError("Semantic eligibility requires an explicit completed round boundary")
        job_id = "job-" + canonical_hash({"kind": kind, "idempotency_key": idempotency_key})
        with exclusive_lock(self.lock):
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(job_id)
            if path.exists():
                existing = self._read(path)
                requested_payload = payload or {}
                equivalent_semantic_payload = (
                    kind == "semantic-summary-eligibility"
                    and _semantic_payload_equivalent(existing["payload"], requested_payload)
                )
                if (
                    existing["payload"] != requested_payload
                    and not equivalent_semantic_payload
                ) or existing["max_attempts"] != max_attempts:
                    raise ValueError("Idempotency key already exists with different parameters")
                if equivalent_semantic_payload and existing["payload"] != requested_payload:
                    existing["payload"] = {
                        **requested_payload,
                        "summary_job": canonical_path_text(requested_payload["summary_job"]),
                    }
                    existing["updated_at"] = iso(self.clock())
                    self._write(existing)
                return {**existing, "created": False}
            timestamp = iso(self.clock())
            job = {
                "format": FORMAT,
                "job_id": job_id,
                "kind": kind,
                "idempotency_key": idempotency_key,
                "payload": payload or {},
                "state": "queued",
                "attempts": 0,
                "max_attempts": max_attempts,
                "available_at": timestamp,
                "created_at": timestamp,
                "updated_at": timestamp,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error": None,
                "result_sha256": None,
            }
            self._write(job)
            return {**job, "created": True}

    def jobs(self) -> list[Dict[str, Any]]:
        if not self.root.exists():
            return []
        return [self._read(path) for path in sorted(self.root.glob("job-*.json"))]

    def recover_expired(self) -> list[str]:
        recovered: list[str] = []
        with exclusive_lock(self.lock):
            for path in sorted(self.root.glob("job-*.json")) if self.root.exists() else []:
                job = self._read(path)
                if job["state"] != "running" or parse_iso(job["lease_expires_at"]) > self.clock():
                    continue
                job["state"] = "quarantined" if job["attempts"] >= job["max_attempts"] else "retry"
                job["available_at"] = iso(self.clock())
                job["updated_at"] = iso(self.clock())
                job["lease_owner"] = None
                job["lease_expires_at"] = None
                job["last_error"] = "expired lease recovered after restart"
                self._write(job)
                recovered.append(job["job_id"])
        return recovered

    def claim(
        self,
        owner: str,
        *,
        lease_seconds: int = 60,
        kinds: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not owner or not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid maintenance lease request")
        allowed_kinds = set(kinds) if kinds is not None else set(KINDS)
        if not allowed_kinds or not allowed_kinds <= KINDS:
            raise ValueError("Invalid maintenance job kind filter")
        self.recover_expired()
        with exclusive_lock(self.lock):
            now = self.clock()
            candidates = [
                job for job in self.jobs()
                if job["kind"] in allowed_kinds
                and job["state"] in {"queued", "retry"}
                and parse_iso(job["available_at"]) <= now
            ]
            if not candidates:
                return None
            job = sorted(candidates, key=lambda item: (item["available_at"], item["created_at"], item["job_id"]))[0]
            job["state"] = "running"
            job["attempts"] += 1
            job["updated_at"] = iso(now)
            job["lease_owner"] = owner
            job["lease_expires_at"] = iso(now + timedelta(seconds=lease_seconds))
            self._write(job)
            return job

    def mark_semantic_ready_bulk(self, *, maximum_jobs: int = 100) -> list[str]:
        """Promote due semantic eligibility jobs in one O(N) locked scan."""
        if maximum_jobs < 0 or maximum_jobs > 10000:
            raise ValueError("maximum_jobs must be between 0 and 10000")
        promoted: list[str] = []
        limit = maximum_jobs or 10000
        with exclusive_lock(self.lock):
            now = self.clock()
            paths = sorted(self.root.glob("job-*.json")) if self.root.exists() else []
            for path in paths:
                if len(promoted) >= limit:
                    break
                job = self._read(path)
                if (
                    job["kind"] != "semantic-summary-eligibility"
                    or job["state"] not in {"queued", "retry"}
                    or parse_iso(job["available_at"]) > now
                ):
                    continue
                job["state"] = "semantic-ready"
                job["result_sha256"] = canonical_hash({"eligible": True})
                job["updated_at"] = iso(now)
                job["lease_owner"] = None
                job["lease_expires_at"] = None
                job["last_error"] = None
                self._write(job)
                promoted.append(job["job_id"])
        return promoted

    def claim_semantic(
        self,
        job_id: str,
        owner: str,
        *,
        lease_seconds: int = 900,
    ) -> Optional[Dict[str, Any]]:
        """Lease one explicitly eligible semantic job without scanning other work."""
        if not owner or not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid semantic lease request")
        self.recover_expired()
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["state"] in {"completed", "quarantined"}:
                return None
            if job["state"] != "semantic-ready" or parse_iso(job["available_at"]) > self.clock():
                return None
            now = self.clock()
            job["state"] = "running"
            job["attempts"] += 1
            job["updated_at"] = iso(now)
            job["lease_owner"] = owner
            job["lease_expires_at"] = iso(now + timedelta(seconds=lease_seconds))
            self._write(job)
            return job

    def complete(self, job_id: str, owner: str, result: Dict[str, Any], *, semantic_ready: bool = False) -> Dict[str, Any]:
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["state"] == "completed" or job["state"] == "semantic-ready":
                return job
            if job["state"] != "running" or job["lease_owner"] != owner:
                raise ValueError("Maintenance job is not owned by this worker")
            job["state"] = "semantic-ready" if semantic_ready else "completed"
            job["result_sha256"] = canonical_hash(result)
            job["updated_at"] = iso(self.clock())
            job["lease_owner"] = None
            job["lease_expires_at"] = None
            job["last_error"] = None
            self._write(job)
            return job

    def fail(self, job_id: str, owner: str, error: BaseException | str, *, retry_delay_seconds: int = 0) -> Dict[str, Any]:
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["state"] != "running" or job["lease_owner"] != owner:
                raise ValueError("Maintenance job is not owned by this worker")
            job["state"] = "quarantined" if job["attempts"] >= job["max_attempts"] else "retry"
            job["available_at"] = iso(self.clock() + timedelta(seconds=max(0, retry_delay_seconds)))
            job["updated_at"] = iso(self.clock())
            job["lease_owner"] = None
            job["lease_expires_at"] = None
            job["last_error"] = redact_error(error)
            self._write(job)
            return job

    def fail_semantic(
        self,
        job_id: str,
        owner: str,
        error: BaseException | str,
        *,
        retry_delay_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Return a failed semantic attempt to its explicit ready state or quarantine it."""
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["kind"] != "semantic-summary-eligibility":
                raise ValueError("Maintenance job is not semantic work")
            if job["state"] != "running" or job["lease_owner"] != owner:
                raise ValueError("Maintenance job is not owned by this worker")
            job["state"] = "quarantined" if job["attempts"] >= job["max_attempts"] else "semantic-ready"
            job["available_at"] = iso(self.clock() + timedelta(seconds=max(0, retry_delay_seconds)))
            job["updated_at"] = iso(self.clock())
            job["lease_owner"] = None
            job["lease_expires_at"] = None
            job["last_error"] = redact_error(error)
            self._write(job)
            return job

    def defer_semantic(
        self,
        job_id: str,
        owner: str,
        reason: BaseException | str,
        *,
        retry_delay_seconds: int = 300,
    ) -> Dict[str, Any]:
        """Release unavailable semantic work without consuming an attempt."""
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["kind"] != "semantic-summary-eligibility":
                raise ValueError("Maintenance job is not semantic work")
            if job["state"] != "running" or job["lease_owner"] != owner:
                raise ValueError("Maintenance job is not owned by this worker")
            job["state"] = "semantic-ready"
            job["attempts"] = max(0, job["attempts"] - 1)
            job["available_at"] = iso(self.clock() + timedelta(seconds=max(0, retry_delay_seconds)))
            job["updated_at"] = iso(self.clock())
            job["lease_owner"] = None
            job["lease_expires_at"] = None
            job["last_error"] = redact_error(reason)
            self._write(job)
            return job

    def requeue_quarantined(self, job_id: str, reason: str) -> Dict[str, Any]:
        reason = redact_error(reason).strip()
        if not reason:
            raise ValueError("A quarantine requeue reason is required")
        with exclusive_lock(self.lock):
            job = self._read(self._path(job_id))
            if job["state"] != "quarantined":
                raise ValueError("Only a quarantined maintenance job can be requeued")
            now = self.clock()
            previous_sha256 = canonical_hash(job)
            receipt = {
                "format": "memory-wuxian-maintenance-requeue-receipt-v1",
                "job_id": job_id,
                "kind": job["kind"],
                "requeued_at": iso(now),
                "reason": reason,
                "previous_job_sha256": previous_sha256,
                "previous_attempts": job["attempts"],
                "previous_last_error": job["last_error"],
            }
            receipt_root = self.archive_root / "maintenance" / "requeue-receipts"
            receipt_name = (
                f"{job_id}-{now.strftime('%Y%m%dT%H%M%SZ')}-{previous_sha256[:12]}.json"
            )
            receipt_path = receipt_root / receipt_name
            if receipt_path.exists():
                raise ValueError("Maintenance requeue receipt already exists")
            atomic_write_canonical_json(receipt_path, receipt)
            job["state"] = (
                "semantic-ready"
                if job["kind"] == "semantic-summary-eligibility"
                else "retry"
            )
            job["attempts"] = 0
            job["available_at"] = iso(now)
            job["updated_at"] = iso(now)
            job["lease_owner"] = None
            job["lease_expires_at"] = None
            job["last_error"] = None
            job["result_sha256"] = (
                canonical_hash({"eligible": True})
                if job["kind"] == "semantic-summary-eligibility"
                else None
            )
            self._write(job)
            return {**job, "requeue_receipt": str(receipt_path)}

    def status(self) -> Dict[str, Any]:
        counts = {state: 0 for state in sorted(STATES)}
        for job in self.jobs():
            counts[job["state"]] += 1
        return {
            "format": "memory-wuxian-maintenance-status-v1",
            "counts": counts,
            "total": sum(counts.values()),
            "actionable": counts["queued"] + counts["retry"],
            "quarantined": counts["quarantined"],
        }


def run_model_free_tick(
    queue: MaintenanceQueue,
    handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]],
    *,
    owner: str,
    maximum_jobs: int = 20,
) -> Dict[str, Any]:
    if maximum_jobs < 0 or maximum_jobs > 100:
        raise ValueError("maximum_jobs must be between 0 and 100")
    processed = []
    limit = maximum_jobs or 100
    allowed_kinds = {"semantic-summary-eligibility", *handlers.keys()}
    for _ in range(limit):
        job = queue.claim(owner, kinds=allowed_kinds)
        if job is None:
            break
        try:
            if job["kind"] == "semantic-summary-eligibility":
                completed = queue.complete(job["job_id"], owner, {"eligible": True}, semantic_ready=True)
            else:
                handler = handlers.get(job["kind"])
                if handler is None:
                    raise RuntimeError("No model-free handler is registered")
                completed = queue.complete(job["job_id"], owner, handler(job["payload"]))
            processed.append({"job_id": job["job_id"], "state": completed["state"]})
        except Exception as exc:
            failed = queue.fail(job["job_id"], owner, exc)
            processed.append({"job_id": job["job_id"], "state": failed["state"]})
    return {"processed": processed, "status": queue.status(), "ai_invocations": 0}


def semantic_eligibility_payload(job_path: Path) -> Dict[str, Any]:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    completed_round = int(job.get("source_round_end") or 0)
    if int(job.get("summary_level", 0)) > 1:
        completed_round = int(job.get("source_end_sequence") or 0)
    if completed_round < 1:
        raise ValueError("Semantic summary job has no completed source boundary")
    return {
        "summary_job": canonical_path_text(job_path),
        "summary_job_id": str(job["job_id"]),
        "source_signature": str(job["source_signature"]),
        "conversation_id": str(job.get("conversation_id") or ""),
        "completed_round": completed_round,
        "round_complete": True,
    }


def read_backup_debt_generation(archive_root: Path) -> Optional[Dict[str, Any]]:
    path = Path(archive_root) / "pending" / "backup-debt.json"
    if not path.exists():
        return None
    debt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(debt, dict):
        raise ValueError("Backup debt must be a JSON object")
    return {
        "path": str(path.resolve()),
        "debt": debt,
        "debt_sha256": canonical_hash(debt),
        "mutation_count": int(debt.get("mutation_count", 0)),
    }


def commit_backup_debt_generation(archive_root: Path, expected_sha256: str) -> bool:
    """Clear only the exact snapshotted debt generation under a short lock."""
    root = Path(archive_root)
    path = root / "pending" / "backup-debt.json"
    with exclusive_lock(root / ".locks" / "archive.lock"):
        current = read_backup_debt_generation(root)
        if current is None or current["debt_sha256"] != expected_sha256:
            return False
        path.unlink()
        return True


def reconcile_pending_debt(archive_root: Path, queue: Optional[MaintenanceQueue] = None) -> Dict[str, Any]:
    """Reconcile every pending summary and backup generation in one source scan."""
    root = Path(archive_root).resolve()
    queue = queue or MaintenanceQueue(root)
    pending = root / "pending"
    summary_paths = sorted(pending.glob("job-*.json")) if pending.exists() else []
    created = 0
    existing = 0
    invalid: list[Dict[str, str]] = []
    for path in summary_paths:
        try:
            payload = semantic_eligibility_payload(path)
            result = queue.enqueue(
                "semantic-summary-eligibility",
                f"summary:{payload['source_signature']}",
                payload,
                max_attempts=4,
            )
            created += int(result["created"])
            existing += int(not result["created"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            invalid.append({"path": str(path), "error": redact_error(exc)})

    backup = read_backup_debt_generation(root)
    if backup is not None:
        result = queue.enqueue(
            "backup-debt",
            f"backup:{backup['debt_sha256']}",
            {
                "debt_sha256": backup["debt_sha256"],
                "mutation_count": backup["mutation_count"],
            },
            max_attempts=4,
        )
        created += int(result["created"])
        existing += int(not result["created"])
    return {
        "summary_jobs_scanned": len(summary_paths),
        "backup_debt_present": backup is not None,
        "created": created,
        "existing": existing,
        "invalid": invalid,
    }


def maintenance_projection(archive_root: Path, queue: Optional[MaintenanceQueue] = None) -> Dict[str, Any]:
    root = Path(archive_root).resolve()
    queue = queue or MaintenanceQueue(root)
    jobs = queue.jobs()
    pending_dir = root / "pending"
    pending_summaries = len(list(pending_dir.glob("job-*.json"))) if pending_dir.exists() else 0

    def category(kind: str) -> Dict[str, Any]:
        selected = [job for job in jobs if job["kind"] == kind]
        counts = {state: 0 for state in sorted(STATES)}
        for job in selected:
            counts[job["state"]] += 1
        oldest = min((job["created_at"] for job in selected if job["state"] not in {"completed"}), default=None)
        terminal = [
            {
                "maintenance_job_id": job["job_id"],
                "summary_job_id": job["payload"].get("summary_job_id"),
                "last_error": job["last_error"],
            }
            for job in selected
            if job["state"] == "quarantined"
        ]
        return {
            "total": len(selected),
            "counts": counts,
            "oldest_pending_at": oldest,
            "permanent_failures": len(terminal),
            "permanent_failure_details": terminal[:50],
            "permanent_failure_details_truncated": len(terminal) > 50,
        }

    backup = read_backup_debt_generation(root)
    return {
        "format": PROJECTION_FORMAT,
        "updated_at": iso(now_utc()),
        "process_id": os.getpid(),
        "coverage_debt": {"status": "reported-by-native-collector"},
        "mechanical_debt": {
            "backup": category("backup-debt"),
            "archive_health": category("archive-health"),
        },
        "semantic_debt": {
            "pending_summary_jobs": pending_summaries,
            "maintenance": category("semantic-summary-eligibility"),
        },
        "backup_debt": {
            "present": backup is not None,
            "generation_sha256": backup["debt_sha256"] if backup else None,
            "mutation_count": backup["mutation_count"] if backup else 0,
        },
    }


def write_maintenance_projection(archive_root: Path, queue: Optional[MaintenanceQueue] = None) -> Path:
    root = Path(archive_root).resolve()
    path = root / "maintenance" / "status-projection.json"
    atomic_write_canonical_json(path, maintenance_projection(root, queue))
    return path
