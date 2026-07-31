#!/usr/bin/env python3
"""Persistent model-free maintenance queue for Memory Wuxian."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from platform_lock import exclusive_lock
from platform_transaction import atomic_write_canonical_json, read_canonical_json


FORMAT = "memory-wuxian-maintenance-job-v1"
STATES = {"queued", "running", "retry", "semantic-ready", "completed", "quarantined"}
KINDS = {"backup-debt", "semantic-summary-eligibility", "archive-health"}
TERMINAL_STATES = {"semantic-ready", "completed", "quarantined"}


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
                if existing["payload"] != (payload or {}) or existing["max_attempts"] != max_attempts:
                    raise ValueError("Idempotency key already exists with different parameters")
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

    def claim(self, owner: str, *, lease_seconds: int = 60) -> Optional[Dict[str, Any]]:
        if not owner or not 5 <= lease_seconds <= 3600:
            raise ValueError("Invalid maintenance lease request")
        self.recover_expired()
        with exclusive_lock(self.lock):
            now = self.clock()
            candidates = [
                job for job in self.jobs()
                if job["state"] in {"queued", "retry"} and parse_iso(job["available_at"]) <= now
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
    for _ in range(limit):
        job = queue.claim(owner)
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
