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

`source` is required for client-imported records and omitted for manually appended records. Imported `commentary` is preserved with `completes_round: false`; a visible `final_answer` completes the pending user round.

## Per-conversation transcript

Each file under `memory/conversations/` represents exactly one `conversation_id`. It contains the same complete stored JSON records as the authoritative raw archive, ordered by global sequence, followed by a readable rendering of each stored message. A transcript may contain user messages and visible assistant commentary/final answers from its own conversation only.

Transcripts are derived files. `rebuild-conversations` compares them with authoritative raw records, previews differences by default, and archives existing transcript files before an applied rebuild.

## Codex import cursor

Each imported session has one cursor under `memory/imports/codex/<session-id>.json`. The native collector and Python recovery adapter use the same cursor schema. It records the source path, last consumed complete JSONL line, source size and modification time. Cursor writes occur only after all selected source lines are handled. Stable source-derived message IDs provide a second idempotency boundary if cursor recovery repeats a line.

## Desktop backup manifest

Every enabled external snapshot contains `backup-manifest.json` with the source archive, reason, archive state, and SHA-256/size of every copied file. The backup root contains append-only `backup-log.jsonl` entries. The primary archive remains authoritative.

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

Jobs remain pending until successfully ingested. Re-running job creation for an already assigned source range returns the existing job.

New summary files persist `source_sha256` in frontmatter. Summary indexes and registries also record `summary_sha256`. `ingest-summary` recalculates the source hash and stops when it differs from the pending job.

## Confidence values

- `verified`: raw text was read.
- `summary-supported`: a Level-1 summary was found without raw verification.
- `index-only`: only a higher-level index was found.
- `unverified`: no persisted supporting source was found.
