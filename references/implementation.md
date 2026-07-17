# Memory無限 Implementation Specification

## Contents

1. Storage layout
2. Source authority and immutability
3. Turn counting and summary hierarchy
4. State and indexes
5. Retrieval
6. Heartbeat, idempotency, and recovery
7. Concurrency and privacy
8. Agent operating procedure
9. Version-1 boundary
10. Integrity and reconstruction
11. Codex client integration
12. External backup snapshots

## 1. Storage layout

```text
memory/
├── README.md
├── state.json
├── raw/YYYY/MM/YYYY-MM-DD.md
├── conversations/
│   ├── README.md
│   └── codex-<session-id>.md
├── summaries/
│   ├── level-1/L1-000001.md
│   ├── level-2/L2-000001.md
│   └── registry.jsonl
├── indexes/
│   ├── timeline.md
│   ├── concepts.md
│   ├── conversations.jsonl
│   ├── summaries.jsonl
│   ├── concepts.jsonl
│   └── by-conversation/<conversation>/
│       ├── messages.jsonl
│       ├── timeline.md
│       ├── summaries.jsonl
│       ├── summary-timeline.md
│       ├── concepts.jsonl
│       └── concepts.md
├── retrieval/
│   ├── last-query.md
│   └── retrieval-log.jsonl
├── pending/
│   ├── unsummarized.json
│   └── failed-jobs.jsonl
├── archive/
└── .locks/
```

Raw files contain complete stored messages and remain authoritative. `conversations/` contains one complete deterministic transcript per conversation ID so different tasks are never mixed in the same human-readable archive. Summary files contain every persistent summary level. Indexes provide human-readable and machine-readable navigation. Retrieval records how history was recovered. Pending files preserve unfinished work. State stores reconstructable counters and checkpoints.

## 2. Source authority and immutability

Use this authority order:

```text
raw source > Level-1 > Level-2 > higher levels > model recollection
```

Append raw messages before compression. Flush each append. Never replace original wording with a correction or summary. Add a linked correction record when needed. Do not silently rewrite a summary when a discrepancy appears; log the discrepancy and use the raw record for current reasoning.

Store raw records as daily Markdown files containing parseable one-line JSON payloads. Include timestamp with timezone, unique message ID, speaker, exact stored text, sequence, round number, conversation ID, and reply relationship when available.

After each authoritative raw append, append the same stored record to exactly one file under `conversations/`. Codex transcripts use the native session ID in the filename. Non-Codex conversation IDs use a deterministic SHA-256-derived filename. Each transcript includes the exact JSON record plus readable message text. Treat transcripts as derived archives: rebuild them from raw records when missing or inconsistent, and never use a transcript rebuild to rewrite raw history.

When a source-derived message ID is encountered again, verify that the corresponding transcript record exists before treating the retry as a complete no-op. Restore a missing transcript record from authoritative raw history and create the configured backup snapshot for that repair.

## 3. Turn counting and summary hierarchy

Define one dialogue round as one user message plus its corresponding assistant response. System instructions, internal reasoning, tool output, and maintenance operations do not count unless explicitly represented as dialogue messages.

Keep incomplete user messages. Mark their round complete only after the corresponding assistant response is persisted.

Create Level-1 jobs after one conversation accumulates `level_1_trigger_rounds` completed, unassigned rounds. Never combine records from different conversation IDs in one job. Lock the exact source message IDs, create a persistent job, and leave new raw writes available. Ingest the summary only after its schema and source references validate.

Create Level-N jobs after one conversation accumulates `higher_level_trigger_count` ungrouped Level-(N-1) summaries. A parent and all children must share one conversation ID. Routine parent generation reads only assigned child summaries and their metadata. Consult raw history only to resolve a contradiction. Preserve child summaries and persist parent-child relationships.

## 4. State and indexes

`state.json` records message and round counters, the latest completed and summarized ranges, next IDs, and the last successful update. Write state atomically after associated file writes succeed. Treat files as authoritative and rebuild state when counters disagree with persisted records.

Maintain both Markdown and JSONL indexes:

- Timeline: summary time ranges, source files, and message ranges.
- Concepts: exact phrases, optional canonical labels, first and later appearances, summary references, and raw ranges.
- Conversations: raw routing metadata without replacing raw payloads.
- Summaries: level, path, source range, child relationships, and concepts.

Maintain the same navigational categories separately for each conversation under `indexes/by-conversation/`. Global indexes route across conversations; conversation indexes never contain another conversation ID.

JSONL indexes are append-only. Represent corrections with later records that reference superseded entries.

Extract explicit concepts only: named topics, technical terms, project names, file names, repeated phrases, and user-defined labels. Preserve exact wording. Record objective counts without converting them into psychological claims.

## 5. Retrieval

Parse available clues: concept, phrase, approximate date or time, project, entity, and whether exact wording is requested.

Search in this order:

1. Concept index
2. Timeline index
3. Highest useful summary level
4. Lower summary levels
5. Raw files when indexes fail or verification is required

Narrow to the smallest plausible source range. Use a Level-1 summary to route to original messages. Read matching messages plus the configured number of neighboring messages. Insert retrieved history into working context with date, time range, file, message range, and retrieval reason.

Answer factual historical questions from verified text. Distinguish quoted recollection from interpretation. Log the query, matched concepts, summaries, raw files, message range, and verification level.

## 6. Heartbeat, idempotency, and recovery

Use completed rounds and summary counts as primary triggers. Use heartbeat to:

- Check pending and unsummarized ranges
- Create due jobs when configured
- Validate source references
- Detect duplicate or overlapping source assignments
- Retry recoverable work
- Report state inconsistencies

Make every operation safe to retry. Before creating a summary job, check whether its source range already has a pending or completed assignment. A retry produces no change or an explicitly versioned correction.

If raw writing fails, leave the round unpersisted and do not summarize it. If summary generation fails, retain the raw range and job. If index updating fails after summary creation, retain the summary and rebuild indexes. If state conflicts with persisted files, rebuild counters and never delete source files merely to match stale state.

## 7. Concurrency and privacy

Use file locks for raw appends, state transitions, and source-range assignment. Use atomic temporary-file replacement for state. Keep lock duration short and reject duplicate ownership of the same source range.

Redact obvious credentials before persistence when configured. This means the redacted text becomes the exact stored source. Do not imply that an unredacted copy was retained. User-authorized deletion must cover raw records, summaries, indexes, retrieval logs, and controlled backups, followed by regeneration of affected derived artifacts.

## 8. Agent operating procedure

At startup, read `SKILL.md`, state, pending work, and only the indexes needed for the task. Do not load the entire archive.

After each user and assistant message, invoke append, flush the raw record, update round state, and check the Level-1 trigger. After summary ingestion, check parent thresholds.

For historical questions, search indexes, locate the smallest summary, read its raw source, answer from recovered evidence, and log retrieval.

Before stopping, flush pending writes, save state atomically, and leave unfinished summary jobs in `pending/`. Do not force a summary solely because the process stops.

## 9. Version-1 boundary

Version 1 requires exact append, fixed-count Level-1 jobs, fixed-count parent jobs, persisted indexes, raw-backed retrieval, heartbeat checks, and recoverable state.

It does not require embeddings, an external database, autonomous importance scoring, memory decay, hidden preference extraction, or a client event hook. Those capabilities may be added later without changing the invariant that generated representations never replace original records.

## 10. Integrity and reconstruction

New raw records contain a canonical content SHA-256. Each summary job hashes its exact raw range or ordered child-summary set. Summary ingestion recalculates this value and refuses to continue when the source changed. The summary index records the resulting summary-file SHA-256.

Use `rebuild-state`, `rebuild-conversations`, and `rebuild-indexes` without `--apply` to preview reconstruction. Applying a command archives the previous derived files under `memory/archive/` before replacement. Reconstruction never edits raw messages or summary files.

Heartbeat modes are distinct:

- Default maintenance validates the archive and may create a due count-triggered job.
- `--check-only` performs no job creation and no repair.
- `--repair` rebuilds only deterministic derived inconsistencies after backup.

Hash mismatches, missing historical boundaries, overlapping source ranges, and corrupted summary files remain integrity failures. Repair mode reports them and does not legitimize altered history by rebuilding over them.

## 11. Codex client integration

Use the Rust collector as the continuous input adapter for native rollout JSONL. Keep Python `sync-codex` as a manual compatibility and recovery path. Both implementations persist one cursor per Codex session under `memory/imports/codex/`, derive stable message IDs from session ID, source line, and speaker, and write the same archive schema. Cursor loss may cause a source line to be considered again, but stable IDs must turn that retry into a no-op or an explicit content-conflict error.

Import only top-level Codex sessions and only their `event_msg` records representing `user_message` or visible `agent_message` phases `commentary` and `final_answer`. Reject a complete native session when `session_meta.payload.source` identifies it as a subagent session. Do not import session/system instructions, internal reasoning, tool calls, tool output, token counters, maintenance events, or approval-review context embedded in subagent traffic. Commentary remains part of the exact visible transcript but does not close the pending dialogue round. `final_answer` closes it.

Pending-round state is keyed by conversation ID. User messages in different conversations receive different global round numbers even when both are awaiting answers. If a later round finishes first, record it in `completed_rounds_out_of_order`; advance `completed_rounds` only when all preceding round numbers are complete. This preserves fixed-round summary ranges while preventing cross-conversation `reply_to` links.

Codex Desktop does not expose an in-process post-turn hook to a plain Skill. On macOS, the supplied LaunchAgent keeps one optimized Rust process alive. Its recursive filesystem watcher receives changes through the operating-system notification backend, debounces adjacent writes, and processes only changed rollout files. It does not wake on a fixed polling interval. The activation timestamp prevents an installation from silently importing all older sessions; explicitly selected current sessions may be backfilled once before activation.

The native collector owns high-frequency parsing, raw append, per-conversation transcript append, deterministic conversation-index append, cursor updates, due Level-1 job creation, and post-mutation backup snapshots. Python remains authoritative for summary ingestion, higher-level job maintenance, retrieval, heartbeat checks, and preview-first reconstruction. Both implementations hold `memory/.locks/archive.lock` for a complete event batch or maintenance command so readers cannot observe a partial archive transaction. Contract tests must compare parsed raw records, hashes, round state, cursor positions, and shared-lock behavior across both implementations.

## 12. External backup snapshots

When backup is enabled, complete the primary raw/index/state mutation first. Then copy the archive to a new timestamped directory outside the primary root. Exclude transient lock files. Write a manifest containing the archive state and SHA-256/size of copied files, then atomically expose the completed snapshot and append `backup-log.jsonl` in the backup root.

Create one snapshot per successful synchronization batch or other logical mutation. After the new manifest-backed snapshot is complete, prune older snapshot directories beyond `backup.retention_count`; the default retention is one. Keep `backup-log.jsonl` as operation history. A no-op synchronization creates no snapshot. Desktop backups are recovery copies; the workspace archive remains the writable authority.
