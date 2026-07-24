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

`source` is required for client-imported records and omitted for manually appended records. ChatGPT export records use `source.kind: chatgpt-data-export` and include the export path, native conversation/message IDs, exported conversation title, and content type; their archive conversation ID is prefixed with `chatgpt:`. Imported `commentary` is preserved with `completes_round: false`; a visible `final_answer` completes only the pending user round in the same conversation. New records use `round_scope: conversation`. A visible assistant message without a pending user is stored with `round_number: 0`, `reply_to: null`, and `completes_round: false`.

## Round state

`state.json` keeps `pending_rounds` keyed by conversation ID. `next_round_number` allocates globally unique positive dialogue-round numbers. `completed_rounds` is the highest contiguous completed round; `completed_rounds_out_of_order` temporarily records later rounds that completed before an earlier pending round. Completing the missing earlier round advances the high-watermark through every now-contiguous entry.

The legacy scalar `pending_round` remains `null` for compatibility. Historical records without `round_scope` retain their original semantics and are never rewritten during migration.

## Per-conversation transcript

Each file under `memory/conversations/` represents exactly one `conversation_id`. It contains the same complete stored JSON records as the authoritative raw archive, ordered by global sequence, followed by a readable rendering of each stored message. A transcript may contain user messages, visible assistant commentary/final answers, lightweight visible tool activity, and successful structured file changes from its own conversation only. Ordinary tool records use `speaker: "tool"` and `source.phase: "tool_activity"`. File-change records use `speaker: "tool"` and `source.phase: "file_change"`; their text contains a file/addition/deletion summary followed by each file's change type, optional move target, and exact unified diff. General tool output remains excluded.

Transcripts are derived files. `rebuild-conversations` compares them with authoritative raw records, previews differences by default, and archives existing transcript files before an applied rebuild.

## Codex import cursor

Each imported session has one cursor under `memory/imports/codex/<session-id>.json`. The native collector and Python recovery adapter use the same cursor schema. It records the source path, last consumed complete JSONL line, source size and modification time. `file_change_format_version: 1` confirms that historical successful patch events were backfilled. Cursor writes occur only after all selected source lines are handled. Stable source-derived message IDs provide a second idempotency boundary if cursor recovery repeats a line.

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
  "concepts": ["Explicit concept or label"],
  "policy_events": [{
    "topic": "Explicit policy topic",
    "statement": "Current statement in this event",
    "scope": "Where this rule applies",
    "event_type": "adopted",
    "prior_statement": "",
    "source_message_ids": ["msg-000001-u"]
  }]
}
```

The first four values are arrays of strings. `policy_events` is an array of
strict objects and may be empty. Level-1 events must cite messages inside the
assigned source range. Higher-level summaries always return an empty
`policy_events` array.

`event_type` is one of `adopted`, `revised`, `withdrawn`, `reaffirmed`,
`proposed`, or `uncertain`. A `revised`, `withdrawn`, or `reaffirmed` event
changes current validity only when `prior_statement` exactly identifies one
active statement in the same scope.

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

Before a Level-1 job is sent to the semantic model, its `source_records` may be
converted to `memory-wuxian-lossless-tabular-v1`. The representation moves fields
that are identical across all records into `constants`, lists remaining field names
once in `columns`, and stores exact values in ordered `rows`. Nested `source` keys use
the `source.` prefix. `content_sha256` may be omitted only because it is
deterministically recomputed from the restored canonical record. The worker must
decode the payload locally and verify canonical SHA-256 equality with the original
records before invoking the model. This prompt representation never replaces the
pending job or authoritative raw archive.

Before a Level-2 or higher job is sent to the semantic model, its
`source_summary_payload` is converted to
`memory-wuxian-lossless-summary-tabular-v1`. Repeated top-level and nested
`metadata` fields are stored once as constants or columns. Complete child-summary
Markdown and recorded SHA-256 values remain exact. The worker reconstructs the
complete child-summary objects locally and requires canonical SHA-256 equality
before invoking the model.

## Deterministic hybrid index

`indexes/deterministic/level-N.jsonl` is generated entirely from authoritative raw records. A Level-1 record includes conversation ID, source message and round boundaries, timestamps, source SHA-256, round count, visible-character count, and normalized user/assistant excerpts. It is emitted after 5 completed rounds or 20,000 visible characters by default. Higher levels contain ordered child index IDs and mechanically aggregated boundaries and counts. Per-conversation copies live under `indexes/by-conversation/<conversation>/deterministic-level-N.jsonl`.

New summary files persist `source_sha256` in frontmatter. Summary indexes and registries also record `summary_sha256`. `ingest-summary` recalculates the source hash and stops when it differs from the pending job.

## Confidence values

- `verified`: raw text was read.
- `summary-supported`: a Level-1 summary was found without raw verification.
- `index-only`: only a higher-level index was found.
- `unverified`: no persisted supporting source was found.

## Context refresh state

`retrieval/context-refresh-state.json` is derived runtime state keyed by conversation ID. Each acknowledgement records timestamp, completed-round count, utilization stage, detected compaction count, last used tokens, and effective model context window. Deleting this file causes a safe initial refresh; it never removes raw history or summaries.

`context-refresh-status` reports the selected top-level session, due reasons, latest token usage, utilization, compaction count, and capsule budget. `context-capsule` emits derived Markdown plus machine-readable metadata. It prefers higher-level summaries over their covered children and is not a raw-message record.

## Federation node

`federation/node.json` conforms to `schemas/device-node.schema.json` and records
the format and federation protocol versions, stable ASCII `node_id`,
human-readable `display_name`, creation time, and resolved replica root.

The node ID is a Memory無限 namespace identifier. It is not an OpenAI account,
session, access token, or cryptographic public-key identity.

## Federation peer

Each `federation/peers/<node-id>.json` contains the peer node ID, display name,
`trusted` state, and transport settings. An offline peer uses transport type
`offline`. An SSH peer records its host, port, remote archive, optional remote
config and CLI paths, remote Python command, and `posix` or `powershell` shell.

Peer files contain no OpenAI credentials. Setting `trusted: false` rejects
future imports and SSH pulls.

## Artifact export ledger

`federation/export-ledger.jsonl` is append-only derived routing state. Each
entry assigns a monotonically increasing local `event_sequence` to one locally
originated artifact and records its artifact ID, kind, and content SHA-256.
`export-state.json` stores the next sequence and latest known
artifact hashes. Imported replicas never enter this ledger.

## Delta manifest

`manifest.json` inside a `.mwxb` conforms to
`schemas/delta-manifest.schema.json`. It records:

- bundle format and protocol compatibility
- deterministic bundle ID
- origin node and optional target node
- base, first, and last event sequence
- optional predecessor bundle SHA-256
- artifact count
- payload path, byte length, and SHA-256

The payload is newline-delimited canonical JSON under
`payload/artifacts.jsonl`. Each event includes its original payload and
artifact SHA-256. An initial bundle has base sequence zero and no predecessor.
A noninitial bundle must continue the receiving replica's sequence and name the
SHA-256 of its immediately preceding accepted bundle.

The `.mwxb` ZIP container is compressed, not encrypted, and not
cryptographically signed. Its SHA-256 fields provide integrity checks only.

## Federated index entry

Entries under `<archive>-federation-cache/global-index/` conform to
`schemas/federated-index-entry.schema.json`. They identify the origin node,
artifact kind, original ID, qualified cross-node ID, conversation and timing
metadata, searchable text, source replica path, and verified content SHA-256.

Qualified identifiers use the origin node as a namespace. The original remote
payload is not rewritten to add that namespace.

## Replica state and receipts

Each peer replica stores `replica-state.json` with its last contiguous imported
event sequence, last accepted bundle ID, and last accepted bundle SHA-256.
Receipts make repeated import idempotent. A later bundle is rejected when it
creates a gap or overlap or names a predecessor different from the recorded
bundle SHA-256.
