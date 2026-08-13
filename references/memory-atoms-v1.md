# memory-atoms-v1 Sidecar Contract

Status: experimental, disabled by default, not connected to live collection,
summary generation, retrieval, federation, cloud sync, or maintenance.

## Purpose

`memory-atoms-v1` adds source-bound work facts, tasks, methods, artifacts,
explicit relations, and deterministic scene projections beside the existing
`summary-v1` output. It does not replace or modify `summary-v1`.

The design borrows the useful typed-memory and scene concepts observed during
the pinned TencentDB Agent Memory v2.0.0 audit, while deliberately excluding
model-authored storage actions, inferred priority/persona fields, destructive
deduplication, and model-controlled edits or deletes.

## Immutable Boundaries

- Raw messages remain the highest authority and remain append-only.
- Existing summary files and summary schemas are not read-modify-written.
- The projector accepts only a closed Level-1 job whose embedded source records
  match its existing Memory Wuxian `source_sha256`.
- Candidate `job_id`, `source_sha256`, and every source message ID are checked
  against that job.
- The model cannot choose final IDs, paths, timestamps, priority, or importance.
- Corrections are represented by explicit relations. No atom is updated or
  deleted in place.
- A sidecar file is created only outside the declared archive root. Existing
  identical bytes are idempotent; different bytes at the same path fail closed.

## Deterministic Projection

`scripts/memory_atoms.py` computes atom and relation IDs from canonical UTF-8
JSON, preserves a compact source identity manifest (sequence, message ID, and
record hash), groups scenes by the explicit `scope` field, orders them by source
message sequence, and binds the complete projection to `projection_sha256`.
Readers recompute the source, atom, relation, and scene identities rather than
trusting the outer hash alone. No clock, random value, network service, or model
call participates in projection.

The CLI has three explicit operations:

```text
memory_atoms.py validate --job JOB.json --candidate CANDIDATE.json
memory_atoms.py project --job JOB.json --candidate CANDIDATE.json \
  --output-dir SIDECAR_ROOT --archive-root ARCHIVE_ROOT
memory_atoms.py compare --summary SUMMARY.json --atoms SIDECAR.json
```

`compare` reports byte and traceability counts only. It explicitly does not
claim semantic quality. Human review is required before any future retrieval or
production activation.

## Promotion Gate

Production activation requires a separate governed change with frozen human
review cases, false-positive and false-negative evidence, retrieval evaluation,
bounded queue/waterline behavior, rollback evidence, cross-platform rehearsal,
and an explicit decision about whether these sidecars participate in cloud
exchange. This experimental implementation grants none of those permissions.
