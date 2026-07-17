# Memory無限 Schemas

## Raw message record

Each daily Markdown file contains append-only fenced JSON records. Required keys are:

```json
{
  "record_type": "raw_message",
  "sequence": 1,
  "message_id": "msg-000001-u",
  "conversation_id": "default",
  "timestamp": "2026-07-16T12:00:00+09:00",
  "speaker": "user",
  "round_number": 1,
  "round_scope": "conversation",
  "reply_to": null,
  "text": "Exact stored message text",
  "completes_round": false,
  "source": {
    "kind": "codex-rollout-jsonl",
    "session_id": "019f...",
    "path": "/Users/example/.codex/sessions/...jsonl",
    "line": 42,
    "phase": "commentary"
  },
  "content_sha256": "SHA-256 of the canonical record without this field"
}
```

The JSON payload inside the raw Markdown file is authoritative. Machine-readable indexes contain routing metadata and do not replace it.

`source` is required for client-imported records and omitted for manually appended records. Imported `commentary` is preserved with `completes_round: false`; a visible `final_answer` completes only the pending user round in the same conversation. New records use `round_scope: conversation`. A visible assistant message without a pending user is stored with `round_number: 0`, `reply_to: null`, and `completes_round: false`.

## Round state

`state.json` keeps `pending_rounds` keyed by conversation ID. `next_round_number` allocates globally unique positive dialogue-round numbers. `completed_rounds` is the highest contiguous completed round; `completed_rounds_out_of_order` temporarily records later rounds that completed before an earlier pending round. Completing the missing earlier round advances the high-watermark through every now-contiguous entry.

The legacy scalar `pending_round` remains `null` for compatibility. Historical records without `round_scope` retain their original semantics and are never rewritten during migration.

## Per-conversation transcript

Each file under `memory/conversations/` represents exactly one `conversation_id`. It contains the same complete stored JSON records as the authoritative raw archive, ordered by global sequence, followed by a readable rendering of each stored message. A transcript may contain user messages and visible assistant commentary/final answers from its own conversation only.

Transcripts are derived files. `rebuild-conversations` compares them with authoritative raw records, previews differences by default, and archives existing transcript files before an applied rebuild.

## Codex import cursor

Each imported session has one cursor under `memory/imports/codex/<session-id>.json`. The native collector and Python recovery adapter use the same cursor schema. It records the source path, last consumed complete JSONL line, source size and modification time. Cursor writes occur only after all selected source lines are handled. Stable source-derived message IDs provide a second idempotency boundary if cursor recovery repeats a line.

An excluded native subagent session receives a terminal cursor with `excluded_reason: subagent-session`. No raw record, transcript, summary job, or conversation index is created for that session.

## Per-conversation indexes

Each deterministic directory under `memory/indexes/by-conversation/` belongs to exactly one conversation ID and contains message routing data, a message timeline, summary routing data, a summary timeline, and concept indexes. Every indexed record in that directory must have the same conversation ID. Global indexes may reference multiple conversations and act only as cross-conversation routers.

## Desktop backup manifest

Every enabled external snapshot contains `backup-manifest.json` with the source archive, reason, archive state, and SHA-256/size of every copied file. The backup root contains append-only `backup-log.jsonl` entries. Snapshot directories beyond `backup.retention_count` are removed only after the new snapshot is complete; the default retention is one. The primary archive remains authoritative.

## Workspace recovery backup

Applied derived-file reconstruction stores the replaced state, transcript, or index files in a timestamped directory under `memory/archive/`. Directories matching these reconstruction backup names are pruned after a new backup is created. `backup.workspace_retention_count` defaults to one. Pending summary jobs and ingested-job records are not workspace backup snapshots and are not pruned by this rule.

## Summary result JSON

The Agent returns this contract to `ingest-summary`:

```json
{
  "topics": ["Explicit topic"],
  "established_conclusions": ["Conclusion explicitly accepted in the source"],
  "open_questions": ["Question left unresolved in the source"],
  "concepts": ["Explicit concept or label"]
}
```

All four values must be arrays of strings. Empty arrays are valid. A higher-level summary uses the same schema and only summarizes its assigned child summaries.

## Summary job

A pending job records:

- Job ID and target summary ID
- Summary level and creation time
- Exact source message range or child summary IDs
- Source files and time range
- Source payload needed by the Agent
- Required summary-result schema
- SHA-256 of the exact assigned source set
- One conversation ID shared by every assigned source

Jobs remain pending until successfully ingested. Re-running job creation for an already assigned source range returns the existing job.

## Deterministic hybrid index

`indexes/deterministic/level-N.jsonl` is generated entirely from authoritative raw records. A Level-1 record includes conversation ID, source message and round boundaries, timestamps, source SHA-256, round count, visible-character count, and normalized user/assistant excerpts. It is emitted after 5 completed rounds or 20,000 visible characters by default. Higher levels contain ordered child index IDs and mechanically aggregated boundaries and counts. Per-conversation copies live under `indexes/by-conversation/<conversation>/deterministic-level-N.jsonl`.

New summary files persist `source_sha256` in frontmatter. Summary indexes and registries also record `summary_sha256`. `ingest-summary` recalculates the source hash and stops when it differs from the pending job.

## Confidence values

- `verified`: raw text was read.
- `summary-supported`: a Level-1 summary was found without raw verification.
- `index-only`: only a higher-level index was found.
- `unverified`: no persisted supporting source was found.
