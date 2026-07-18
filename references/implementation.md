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

Keep incomplete user messages. Mark their round complete only after the corresponding assistant `final_answer` is persisted. A character threshold crossed by user text or assistant commentary makes the pending round due, but does not close or summarize it early.

Build deterministic Level-1 indexes after one conversation accumulates `level_1_trigger_rounds` completed rounds or `level_1_trigger_characters` visible characters, whichever occurs first. Defaults are 5 rounds and 20,000 characters. Store exact source IDs, ranges, hashes, counts, and normalized source excerpts. Group every `higher_level_trigger_count` child indexes into the next deterministic level without a model call.

Automatic semantic-job creation is enabled in the installed configuration. After a due job is frozen at a completed-round boundary, invoke one ephemeral Codex CLI process for constrained summary JSON, ingest it after source-hash verification, and exit. Existing pending jobs retain their original source ranges and hashes.

Evaluate automatic job creation only when the current synchronization batch increases the number of completed rounds. User messages, commentary, maintenance writes, process restarts, and cursor catch-up without a new `final_answer` must not drain historical summary backlog or start AI work.

Create Level-N jobs after one conversation accumulates `higher_level_trigger_count` ungrouped Level-(N-1) summaries. A parent and all children must share one conversation ID. Routine parent generation reads only assigned child summaries and their metadata. Consult raw history only to resolve a contradiction. Preserve child summaries and persist parent-child relationships.

## 4. State and indexes

`state.json` records message and round counters, the latest completed and summarized ranges, next IDs, and the last successful update. Write state atomically after associated file writes succeed. Treat files as authoritative and rebuild state when counters disagree with persisted records.

Maintain both Markdown and JSONL indexes:

- Timeline: summary time ranges, source files, and message ranges.
- Concepts: exact phrases, optional canonical labels, first and later appearances, summary references, and raw ranges.
- Conversations: raw routing metadata without replacing raw payloads.
- Summaries: level, path, source range, child relationships, and concepts.
- Deterministic hierarchy: hybrid chunk boundaries, source hashes, exact excerpts, and parent-child index IDs.

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

Normalize natural-language queries and rank explicit terms across each layer rather than requiring the entire query to appear as one unchanged substring. Use deterministic term frequency and document rarity only; do not invoke a model for retrieval routing. Search authoritative raw text even when generated indexes are incomplete.

Exclude currently incomplete rounds from historical matching so the user's active request and visible assistant commentary cannot satisfy their own lookup. Once a raw message matches, restore neighboring records only from the same conversation ID. Never label a routed summary range as verified unless at least one raw record actually matched the query.

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

Use `rebuild-state`, `rebuild-conversations`, and `rebuild-indexes` without `--apply` to preview reconstruction. Applying a command archives the previous derived files under `memory/archive/` before replacement, then removes older workspace recovery directories beyond `backup.workspace_retention_count`. The default retention is one. Reconstruction never edits raw messages or summary files.

Heartbeat modes are distinct:

- Default maintenance validates the archive and may create a due count-triggered job.
- `--check-only` performs no job creation and no repair.
- `--repair` rebuilds only deterministic derived inconsistencies after backup.

Hash mismatches, missing historical boundaries, overlapping source ranges, and corrupted summary files remain integrity failures. Repair mode reports them and does not legitimize altered history by rebuilding over them.

## 11. Codex client integration

Use the Rust collector as the continuous input adapter for native rollout JSONL on macOS and Windows. Keep Python `sync-codex` as a manual compatibility and recovery path. Both implementations persist one cursor per Codex session under `memory/imports/codex/`, derive stable message IDs from session ID, source line, and speaker, and write the same archive schema. Normalize Windows verbatim path prefixes before persistence so Python and Rust produce identical source records and hashes. Cursor loss may cause a source line to be considered again, but stable IDs must turn that retry into a no-op or an explicit content-conflict error.

Import only top-level Codex sessions. Preserve `event_msg` records representing `user_message` or visible `agent_message` phases `commentary` and `final_answer`, plus lightweight `response_item` tool-call descriptions already visible in the task timeline. The tool record contains its name, nested tool names, and command text when available, belongs to the active conversation round, and excludes tool output. Reject a complete native session when `session_meta.payload.source` identifies it as a subagent session. Do not import session/system instructions, internal reasoning, tool output, token counters, maintenance events, or approval-review context embedded in subagent traffic. Commentary and tool activity do not close the pending dialogue round; `final_answer` closes it.

Pending-round state is keyed by conversation ID. User messages in different conversations receive different global round numbers even when both are awaiting answers. If a later round finishes first, record it in `completed_rounds_out_of_order`; advance `completed_rounds` only when all preceding round numbers are complete. This preserves fixed-round summary ranges while preventing cross-conversation `reply_to` links.

Codex Desktop does not expose an in-process post-turn hook to a plain Skill. On macOS, the supplied LaunchAgent keeps one optimized Rust process alive and uses kqueue. On Windows, the installer prefers a user-level scheduled task and falls back to the current user's `Run` registry key when local policy denies task creation; both use a hidden restart-on-exit wrapper around the native Windows backend exposed by `notify`. Both platforms combine native events with a five-second metadata fallback, debounce adjacent writes, and process only rollout files whose size or modification time changed. The fallback does not reread unchanged rollout contents and does not invoke a model. The activation timestamp prevents an installation from silently importing all older sessions; explicitly selected current sessions may be backfilled once before activation.

The native collector owns high-frequency parsing, raw append, per-conversation transcript append, deterministic conversation-index append, cursor updates, due Level-1 job creation, and post-mutation backup snapshots. Trigger detection uses only completed rounds: commentary may increase the pending character count, but the range is not assigned until `final_answer` closes that round. For a newly created job, the collector runs one synchronous ephemeral semantic worker after releasing the archive lock; the worker invokes Codex CLI only for summary JSON, verifies the source hash during ingestion, writes the summary and backup, and exits. Python remains authoritative for summary ingestion, higher-level job maintenance, retrieval, heartbeat checks, and preview-first reconstruction. Both implementations hold `memory/.locks/archive.lock` for a complete event batch or maintenance command so readers cannot observe a partial archive transaction. Contract tests must compare parsed raw records, hashes, round state, cursor positions, and shared-lock behavior across both implementations.

## 12. External backup snapshots

When backup is enabled, complete the primary raw/index/state mutation first. Then copy the archive to a new timestamped directory outside the primary root. Exclude transient lock files. Write a manifest containing the archive state and SHA-256/size of copied files, then atomically expose the completed snapshot and append `backup-log.jsonl` in the backup root.

Create one snapshot per successful synchronization batch or other logical mutation. After the new manifest-backed snapshot is complete, prune older snapshot directories beyond `backup.retention_count`; the default retention is one. Keep `backup-log.jsonl` as operation history. A no-op synchronization creates no snapshot. Desktop backups are recovery copies; the workspace archive remains the writable authority.

## 13. Runtime context refresh

Keep context refresh derived and rebuildable under `retrieval/context-refresh-state.json`; it is not authoritative conversation history and does not require an archive snapshot. At the start of an Agent turn, inspect the newest top-level rollout for its latest `token_count` event and compare completed rounds with the last acknowledgement. Mark refresh due after the configured round interval, when utilization first crosses 65% or 80%, or when usage drops by at least 20 percentage points after reaching the low threshold, which indicates client compaction.

Render a capsule from the highest available semantic summary levels, omit child summaries already covered by a selected parent, append uncovered newer summaries, and finish with a bounded recent-task tail. Cap the estimated budget at the smallest of 1% of the rollout-reported effective context window, 3,000 tokens, and 10,000 tokens. Load the capsule as tool context and acknowledge only after it was read. Never append the capsule to raw history or submit it as semantic-summary source.
