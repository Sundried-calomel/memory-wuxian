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
12. ChatGPT export adapter
13. External backup snapshots
14. Runtime context refresh
15. Dashboard status cache
16. Federated memory

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

Construct the model payload with the reversible `memory-wuxian-lossless-tabular-v1`
representation. Deduplicate repeated structural fields without changing source text,
record order, message state, or provenance. Before every model call, decode the
representation locally and require canonical SHA-256 equality with the assigned
records. Keep the complete pending job and raw source unchanged on disk.

For Level-2 and higher jobs, apply the corresponding reversible
`memory-wuxian-lossless-summary-tabular-v1` representation to child summaries.
Deduplicate repeated child-summary metadata without changing complete summary text,
source hashes, order, or hierarchy. Reconstruct every child-summary object locally
and require canonical SHA-256 equality before invoking the model.

Evaluate automatic job creation only when the current synchronization batch increases the number of completed rounds. User messages, commentary, maintenance writes, process restarts, and cursor catch-up without a new `final_answer` must not drain historical summary backlog or start AI work.

Drain an explicitly requested historical backlog with `scripts/semantic_backfill.py`.
The backfill is bounded and resumable, prefers an already-due higher-level parent
before creating another Level-1 job, and creates one external recovery snapshot at
the end of each successful batch. This keeps routine event-driven capture unchanged
while allowing old imported conversations to catch up without a complete archive
copy after every generated summary.

Create Level-N jobs after one conversation accumulates `higher_level_trigger_count` ungrouped Level-(N-1) summaries. A parent and all children must share one conversation ID. Routine parent generation reads only assigned child summaries and their metadata. Consult raw history only to resolve a contradiction. Preserve child summaries and persist parent-child relationships.

## 4. State and indexes

`state.json` records message and round counters, the latest completed and summarized ranges, next IDs, and the last successful update. Write state atomically after associated file writes succeed. Treat files as authoritative and rebuild state when counters disagree with persisted records.

Maintain both Markdown and JSONL indexes:

- Timeline: summary time ranges, source files, and message ranges.
- Concepts: exact phrases, optional canonical labels, first and later appearances, summary references, and raw ranges.
- Conversations: raw routing metadata without replacing raw payloads.
- Summaries: level, path, source range, child relationships, and concepts.
- Deterministic hierarchy: hybrid chunk boundaries, source hashes, exact excerpts, and parent-child index IDs.
- Conversation titles: append-only user-confirmed and source-observed aliases keyed by conversation ID. Titles route to IDs but never replace IDs as identity.

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

For mutable operational rules, run retrieval in `current-policy` mode. Search
the append-only policy-event index, derive validity from exact statement and
scope links, expand both earlier and later events in the matched lineage, and
restore every cited raw message. Also include newer raw matches so archives
created before policy events existed do not default silently to the earliest
matching rule. Unresolved, conflicting, uncertain, and proposed events are
review signals, not current policy.

Exclude currently incomplete rounds from historical matching so the user's active request and visible assistant commentary cannot satisfy their own lookup. Once a raw message matches, restore neighboring records only from the same conversation ID. Never label a routed summary range as verified unless at least one raw record actually matched the query.

Narrow to the smallest plausible source range. Use a Level-1 summary to route to original messages. Read matching messages plus the configured number of neighboring messages. Insert retrieved history into working context with date, time range, file, message range, and retrieval reason.

Answer factual historical questions from verified text. Distinguish quoted recollection from interpretation. Log the query, matched concepts, summaries, raw files, message range, and verification level.

For a title-targeted historical continuation, exclude the active conversation ID before title matching. A missing or ambiguous result is a hard stop. Persist a user-confirmed title alias in the local title index rather than editing Codex client databases.

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

Both collectors also persist top-level rollout `token_count` telemetry into a
separate per-session ledger. Process only lines beyond the ledger cursor during
normal synchronization. On an explicit historical backfill, scan retained
rollouts, exclude subagent and exec sessions, and create no ledger when no
telemetry exists. Sum cumulative counter segments when a reset is observed,
deduplicate equal snapshots for request counting, and keep the operation
idempotent. Never infer missing usage from archived text. Telemetry writes may
invalidate the dashboard cache and trigger the normal verified backup, but may
not alter raw messages, completed rounds, summaries, or semantic indexes.

Import only top-level Codex sessions. Preserve `event_msg` records representing `user_message` or visible `agent_message` phases `commentary` and `final_answer`, plus lightweight `response_item` tool-call descriptions already visible in the task timeline. The tool record contains its name, nested tool names, and command text when available. Also preserve successful `patch_apply_end` events as `file_change` records containing file paths, change types, moves, exact unified diffs, hunk line ranges, and computed addition/deletion counts. This structured file-change event is the only tool-output exception. Reject a complete native session when `session_meta.payload.source` identifies it as a subagent session. Do not import session/system instructions, internal reasoning, general tool output, token counters, maintenance events, or approval-review context embedded in subagent traffic. Commentary, tool activity, and file changes do not close the pending dialogue round; `final_answer` closes it. A cursor without `file_change_format_version: 1` receives a one-time patch-event-only historical backfill before the marker is written.

Pending-round state is keyed by conversation ID. User messages in different conversations receive different global round numbers even when both are awaiting answers. If a later round finishes first, record it in `completed_rounds_out_of_order`; advance `completed_rounds` only when all preceding round numbers are complete. This preserves fixed-round summary ranges while preventing cross-conversation `reply_to` links.

Codex Desktop does not expose an in-process post-turn hook to a plain Skill. On macOS, the supplied LaunchAgent keeps one optimized Rust process alive and uses kqueue. On Windows, the installer prefers a user-level scheduled task and falls back to the current user's `Run` registry key when local policy denies task creation. Both platforms combine immediate native events with an adaptive metadata fallback: 5 seconds while active, 30 seconds after 2 idle minutes, and 5 minutes after 15 idle minutes. Any native event immediately restores active mode. They debounce adjacent writes and process only rollout files whose size or modification time changed. The fallback does not reread unchanged completed source and does not invoke a model.

The first activation boundary is persisted independently from the generated launch command. An upgrade may preserve it or move it earlier, but never later. A retained top-level rollout without a completed cursor is still admitted even when it predates that boundary. The native collector reads complete JSONL lines through a bounded buffer and commits at most 512 lines or 4 MiB in one archive transaction. A trailing half-line remains uncommitted. Backward-compatible cursors retain the durable byte offset, observed source size, and completion state; source truncation or same-size identity drift fails closed. Derived indexes settle after all available batches are caught up.

The native collector owns high-frequency parsing, raw append, per-conversation transcript append, deterministic conversation-index append, cursor updates, due Level-1 job creation, and post-mutation backup debt. Trigger detection uses only completed rounds: commentary may increase the pending character count, but the range is not assigned until `final_answer` closes that round. For a newly created job, the collector invokes the one-shot semantic dispatcher after releasing the archive lock. The dispatcher first persists and mechanically promotes a completed-boundary eligibility job, then leases the semantic attempt, invokes Codex CLI only for summary JSON, verifies the source hash during ingestion, records completion or bounded failure, and exits. Repeated failure is quarantined and never blocks native capture.

The hidden five-minute maintenance supervisor reconciles all pending summary jobs and backup debt in linear time and writes a bounded dashboard projection. It continues model-free mechanical and backup work while Codex is closed. Semantic unavailability defers a job without consuming a retry and does not block later eligible jobs. An oversized summary uses a hash-bound resumable map-reduce plan: complete rounds are preferred, an oversized round falls back to contiguous record groups, and an oversized record falls back to bounded field spans. Every actual prompt satisfies both the configured character and UTF-8 byte limit, completed pieces are reused only after hash verification, and the original parent job is the only object ingested as the final Level-1 summary.

Python remains authoritative for summary ingestion, higher-level job maintenance, retrieval, heartbeat checks, diagnostics, and preview-first reconstruction. Both implementations hold `memory/.locks/archive.lock` only for a bounded durable transaction; rollout parsing, model invocation, and complete backup copying occur outside it. Contract tests must compare parsed raw records, hashes, round state, cursor positions, and shared-lock behavior across both implementations.

## 12. ChatGPT export adapter

Ordinary ChatGPT chats are outside the Codex rollout stream. Accept official export ZIPs, extracted export directories, and direct `conversations.json` files as explicit import sources. Follow the `current_node` parent chain to preserve only the current visible branch; use chronological mapping order only for older exports without that field. Import user and assistant text, skip system roles and empty content, preserve the exported conversation title and stable IDs in `source`, and derive deterministic IDs only when an export omits a message ID. Prefix conversation IDs with `chatgpt:` and message IDs with `chatgpt-`. Repeated or newer exports must be idempotent. Treat this as batch import, never as evidence of real-time ChatGPT access.

## 13. External backup snapshots

When backup is enabled, complete the primary raw/index/state mutation first. Then copy the archive to a new timestamped directory outside the primary root. Exclude transient lock files. Write a manifest containing the archive state and SHA-256/size of copied files, then atomically expose the completed snapshot and append `backup-log.jsonl` in the backup root.

Create one snapshot per successful synchronization batch or other logical mutation. After the new manifest-backed snapshot is complete, prune older snapshot directories beyond `backup.retention_count`; the default retention is one. Keep `backup-log.jsonl` as operation history. A no-op synchronization creates no snapshot. Desktop backups are recovery copies; the workspace archive remains the writable authority.

## 14. Runtime context refresh

Keep context refresh derived and rebuildable under `retrieval/context-refresh-state.json`; it is not authoritative conversation history and does not require an archive snapshot. At the start of an Agent turn, inspect the newest top-level rollout for its latest `token_count` event and compare completed rounds with the last acknowledgement. Mark refresh due after the configured round interval, when utilization first crosses 65% or 80%, or when usage drops by at least 20 percentage points after reaching the low threshold, which indicates client compaction.

Render a capsule from the highest available semantic summary levels, omit child summaries already covered by a selected parent, append uncovered newer summaries, and finish with a bounded recent-task tail. Cap the estimated budget at the smallest of 1% of the rollout-reported effective context window, 3,000 tokens, and 10,000 tokens. Load the capsule as tool context and acknowledge only after it was read. Never append the capsule to raw history or submit it as semantic-summary source.

## 15. Dashboard status cache

Store expensive dashboard statistics in `dashboard/status-snapshot.json`. The
snapshot is derived and disposable: it must contain a format version, a
fingerprint of archive and Codex metadata sources, and the complete last
verified dashboard payload. Write it atomically and never include it in source
authority, summary provenance, or retrieval results.

At startup, compare source file size and modification metadata with the
persisted fingerprint. Reuse the snapshot when they match. Rebuild it from
authoritative archive records when it is missing, stale, malformed, or from an
unsupported format version. Refresh lightweight collector telemetry separately
so it does not force a historical archive scan.

The browser may render its last successful API response from local storage
before the server finishes verification. Replace that display with the current
local API response when available. Browser and server caches must remain
optional performance layers; deleting either cache must never remove or alter
conversation history.

## 16. Federated memory

### 16.1 Authority and layout

Each node owns one writable local archive. Federation never imports remote
messages through the local append path and never changes local `state.json`,
round numbers, message sequences, or summary counters.

Node metadata and the export ledger live under the local archive:

```text
<archive>/federation/
├── node.json
├── export-state.json
├── export-ledger.jsonl
├── peers/<node-id>.json
└── sync-log.jsonl
```

Imported data uses the default sibling cache:

```text
<archive>-federation-cache/
├── peers/<origin-node-id>/
│   ├── raw-records.jsonl
│   ├── conversation-titles.jsonl
│   ├── summaries/
│   ├── receipts/
│   └── replica-state.json
└── global-index/
```

The cache is read-only from the receiving node's perspective and can be
recreated from source nodes. It is not included in the desktop backup of the
primary archive. A configured replica directory may replace the default sibling
path without changing these authority rules.

### 16.2 Node and artifact identity

`init-node` creates a stable Memory無限 node identity. Federation does not use
OpenAI session state, Codex credentials, or an OpenAI account's active-device
list. Peer trust is explicit and local.

Global indexes qualify message, conversation, and summary identifiers with the
origin node. Original payloads keep their existing identifiers and hashes.
Only locally originated artifacts enter a node's export ledger; imported
replicas are never re-exported.

The append-only artifact ledger assigns an event sequence whenever a local raw
record, semantic summary, or confirmed conversation title first appears or its
locally authoritative content changes. This captures summaries or titles
created after the original raw message range was archived.

### 16.3 Delta bundle

`export-delta` writes one ZIP-compressed `.mwxb` containing `manifest.json` and
`payload/artifacts.jsonl`. The manifest identifies the origin and optional
target node, event-sequence range, artifact count, payload size, payload
SHA-256, and predecessor bundle SHA-256.

Initial bundles start after event sequence zero and declare no predecessor.
Every noninitial export requires the receiving peer's last imported event
sequence and last bundle SHA-256. Import rejects event-sequence gaps, partial
overlaps, origin mismatch, target mismatch, an invalid predecessor chain,
artifact hash mismatch, path-unsafe content, and conflicting duplicate
artifacts. Reimporting an already accepted bundle returns no change.

Exports are bounded by both artifact count and uncompressed payload bytes. A
result with `has_more: true` is the next contiguous page, not a completed
backlog. The append-only export ledger is authoritative; `export-state.json` is
a reconstructible cache.

`inspect-bundle` performs structural and integrity validation without importing.
`import-delta` validates every artifact before writing, uses atomic per-file
replacement, and leaves a durable transaction marker until replica state and
the accepted-bundle receipt are complete. Retrying the same bundle resumes or
finishes an interrupted transaction. Accepted bundle receipts and replica state
provide the idempotency and continuation cursor.

### 16.4 Global retrieval

`rebuild-global-index` derives cross-device node, conversation, message,
summary, and concept routes from peer replicas. `retrieve-global` combines
those routes with the current local authority and verifies a matching peer
result against its imported payload SHA-256. The existing `retrieve` command
remains local-only.

`revoke-peer` marks a peer untrusted and rejects later imports or SSH pulls.
Revocation does not silently delete already imported historical replicas.

### 16.5 SSH pull transport

`sync-peer` is a pull operation. It invokes the remote node's `export-delta`
through SSH, receives either the next `.mwxb` bytes or a no-change response, and
passes the bundle through the same local import validation. Peer configuration
supports `posix` and `powershell` remote command construction.

SSH must use strict host-key checking and the user's existing SSH
authentication. SSH encrypts the connection and authenticates the transport
endpoints. The `.mwxb` format itself is not encrypted and is not
cryptographically signed. SHA-256 detects content changes but does not
authenticate the sender. Offline bundles must therefore travel only through a
trusted channel.

Version 1.6.0 does not implement public-internet automatic discovery, NAT
traversal, relay service, or a mobile client.

### 16.6 Command examples

```bash
python3 scripts/memory_cli.py --root /path/to/archive init-node --display-name "Lab Mac"
python3 scripts/memory_cli.py --root /path/to/archive add-peer --node-id <peer-node-id>
python3 scripts/memory_cli.py --root /path/to/archive export-delta \
  --output /trusted/path/update.mwxb \
  --target-node-id <peer-node-id>
python3 scripts/memory_cli.py --root /path/to/archive inspect-bundle \
  --bundle /trusted/path/update.mwxb
python3 scripts/memory_cli.py --root /path/to/archive import-delta \
  --bundle /trusted/path/update.mwxb \
  --expected-node-id <peer-node-id>
python3 scripts/memory_cli.py --root /path/to/archive rebuild-global-index
python3 scripts/memory_cli.py --root /path/to/archive retrieve-global --query "topic"
python3 scripts/memory_cli.py --root /path/to/archive federation-status
python3 scripts/memory_cli.py --root /path/to/archive revoke-peer --node-id <peer-node-id>
```

For SSH pull:

```bash
python3 scripts/memory_cli.py --root /path/to/local add-peer \
  --node-id <peer-node-id> \
  --host user@example-host \
  --port 22 \
  --remote-root /path/to/remote/archive \
  --remote-config /path/to/remote/config.yaml \
  --remote-cli /path/to/remote/scripts/memory_cli.py \
  --remote-python python3 \
  --remote-shell posix
python3 scripts/memory_cli.py --root /path/to/local sync-peer --node-id <peer-node-id>
```

Use `--remote-shell powershell` and Windows paths for a Windows peer.

### 16.7 Encrypted cloud-folder transport

Keep `.mwxb` as the sole inner federation delta. The cloud transport wraps it
in `memory-wuxian-envelope-v1`: the origin node signs the framed payload with
Ed25519 and encrypts it to both the origin and target age/X25519 recipients.
Encrypting to the origin allows durable outbox recovery without retaining a
second plaintext bundle. The target verifies the trusted peer signing key,
origin, target, envelope kind, payload length, and payload SHA-256 before the
normal `.mwxb` importer runs.

Store private identities under the user's local Codex key directory by default.
Reject an identity path inside the primary archive, replica cache, or
synchronized directory. Peer records contain public keys and fingerprints only.

Use this single-writer cloud layout:

```text
<selected-sync-directory>/MemoryWuxianExchange/v1/
└── nodes/<writer-node-id>/
    ├── outbox/<target-node-id>/*.mwxe
    └── acks/<origin-node-id>/*.mwxa
```

A node may create, atomically replace, or remove only files under its own
writer namespace. It never moves, deletes, or quarantines a peer's synchronized
file. Invalid peer files produce local quarantine metadata under
`<archive>/federation/cloud-quarantine/`.

Cloud delivery is stop-and-wait per peer. The sender publishes no later delta
until it imports a signed encrypted acknowledgement for the current outstanding
bundle. Retries are safe because the normal replica receipt and event chain
remain authoritative. Preserve the newest acknowledgement per peer and remove
older acknowledgements or acknowledged outbox envelopes only after the
configured retention interval.

Run a short process every 300 seconds. Mark local material pending only after a
round closes or a summary/title artifact for closed history changes. Coalesce
for 900 seconds, permit an estimated 1,048,576-byte early flush, and attempt
delivery after 3,600 seconds. A forced pass bypasses the merge window. Provider
upload completion is outside this contract.

## 17. Environment synchronization

### 17.1 Authority and layout

Environment synchronization is independent from conversation memory authority.
Each device writes only its local formal rules, installed Skills, environment
registry, and installation state.

```text
<archive>/environment/
├── registry.jsonl
├── state.json
├── projects/
├── manifests/
├── revisions/
├── objects/sha256/
├── staging/
├── transactions/
├── conflicts/
├── receipts/
└── promotions/
```

The content-addressed object store keeps one copy of identical bytes.
Conversation raw files, semantic summaries, token ledgers, and federation
memory replicas remain outside this domain. Authoritative revisions and
promotion decisions are immutable; manifests, state, and dashboard snapshots
are reconstructible.

### 17.2 Stable identities and project bindings

An environment artifact has a stable ASCII `artifact_id`. Every content change
creates a new immutable revision with its own `revision_id`, version, base
SHA-256, and content SHA-256. The revision records one of four object classes,
origin node, scope, optional project ID, supported platforms, runtime
requirements, and provenance.

Paths are local bindings rather than global identity. A project registration
maps one stable `project_id` to zero or one root on the current device. A
missing local binding leaves received project artifacts in staging. Similar
directory names do not establish identity.

### 17.3 Read-only inventory

The first implementation phase scans only explicitly configured roots and
registered paths. It emits a preview inventory and proposed classifications
without modifying formal rules, Skills, projects, cloud files, or installation
state. Ambiguous objects remain unclassified and require a persisted user
decision.

### 17.4 Managed rule installation

Mixed-ownership documents use one named managed block. The installer requires a
recorded base hash, writes a temporary candidate, validates marker uniqueness
and document structure, preserves mode bits, and atomically replaces the
target. It writes a receipt only after the final file hash matches the
candidate. Every byte outside the managed block must remain unchanged.

Whole-file installation is permitted only for explicitly registered canonical
files whose owner is Memory無限. Project-local, generated, and excluded files
are never installed from a peer.

### 17.5 Skill package installation

A Skill package contains only manifest-declared relative paths. Reject absolute
paths, parent traversal, escaping symlinks, undeclared files, hash mismatches,
unsupported platforms, missing runtimes, undeclared network or persistence
expansion, and invalid Skill metadata.

Extract to staging, run declared no-side-effect checks, preserve one verified
rollback version, atomically switch the installation directory, validate Codex
discovery, and write an installation receipt. A project Skill additionally
requires a matching active project binding.

### 17.6 Capability promotion

Candidate discovery records evidence without changing ownership. Classify each
candidate as `duplicate`, `extension`, `new-global-capability`, `mixed`,
`project-only`, or `conflict`. A promotion contract names the global owner,
source capability, proposed interface, retained project adapter, removed
duplication, affected projects, and validations.

Accepted promotion requires the original project regression suite, at least one
unrelated project or generic fixture, macOS and Windows compatibility, missing
adapter and invalid-input failures, and a scan for project-specific paths,
identifiers, credentials, scientific policy, or private data. Replace the
project core only after those checks pass.

### 17.7 Conflicts and installation scheduling

Use base/local/remote hashes for three-way decisions. Only verified,
nonconflicting, compatible ordinary updates may auto-install. Core rules,
permission expansion, network expansion, project identity ambiguity, and
promotion acceptance require explicit review.

The short-lived scheduler performs:

```text
receive -> authenticate -> decrypt -> verify -> stage -> compatibility check
-> conflict check -> rehearsal -> atomic install -> self-check -> acknowledge
```

No-change checks create no object, bundle, receipt, backup, or AI request.

### 17.9 Personal Environment profiles

`memory_environment_profiles.py` inventories only explicit source descriptors.
Skill walks are bounded by file count, byte count, and depth; ignored caches,
virtual environments, models, archives, and indexes do not affect identity.
Links, junctions, unreadable files, malformed metadata, duplicate portable
paths or identities, non-UTF-8 Rules, unknown fields, and oversize input fail
before a generation or pointer is written.

Capture is preview-first. Applied generations are immutable and linked to their
predecessor, while `current.json` is a reconstructible atomic pointer. The
Environment exchange ledger transports a validated generation as one
`personal-environment-profile` event. Import writes only a per-peer read-only
replica with `automatic_activation=false`; it never enters Registry incoming
staging. Comparison and convergence planning are deterministic and model-free.
Only an exact, already registered Environment artifact may be linked for later
review, and existing Rule and Skill installers still require their own preview,
binding, explicit approval, self-check, and rollback contracts.

### 17.8 Independent exchange streams

Reuse the federation and encrypted envelope framework but keep `archive-v1` and
`environment-v1` as separate streams. Each stream owns its event ledger,
sequence, predecessor bundle chain, replica state, ACK, and outstanding
envelope. Legacy peers exchange only `archive-v1`; both peers must advertise
the environment capability before an `environment-v1` envelope is published.
