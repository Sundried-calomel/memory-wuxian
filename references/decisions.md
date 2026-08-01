# Memory無限 Architecture Decisions

## D-001: Fixed-round Level-1 summaries

Status: Accepted.

Generate a Level-1 summary job after a configurable number of completed user-assistant dialogue rounds. Keep incomplete rounds persisted and outside the completed count.

The default is 10 completed rounds. Previously assigned jobs remain unchanged when the configuration changes.

## D-002: Fixed-count summary hierarchy

Status: Accepted.

Generate a Level-N parent after a configurable number of ungrouped Level-(N-1) summaries. Preserve every child summary and record parent-child links.

## D-003: Summaries are indexes

Status: Accepted.

Use summaries to locate history. Verify factual historical claims against raw records when those records are available.

## D-004: Content integrity is explicit

Status: Accepted.

Store SHA-256 for new raw records, summary source sets, and summary files. Recalculate the source hash before summary ingestion. Report drift without automatically rewriting source or summary history.

## D-005: Recovery is preview-first

Status: Accepted.

Reconstruct derived state and indexes from persisted raw and summary files. Preview differences by default. Require `--apply` or heartbeat `--repair` for writes, and archive the previous derived files first.

## D-006: Codex integration is an idempotent source adapter

Status: Accepted.

Read native Codex rollout JSONL incrementally and persist per-session cursors. Import user messages, visible assistant commentary/final answers, and lightweight tool activity displayed in the task timeline. Tool activity stores tool and nested-tool names plus command text when available. Successful structured file-change events are the sole tool-output exception: preserve their exact applied unified diffs and per-file metadata so historical edits are verifiable. Never import hidden reasoning or general tool output. Use source-derived message IDs so retries cannot duplicate history. Count only a final assistant answer as completing a dialogue round.

## D-007: Desktop backup follows the primary archive

Status: Accepted.

Write and index the primary archive first. After a successful mutation, create a timestamped external snapshot with a file-hash manifest and append an entry to the backup log. A backup never becomes the writable source of truth.

## D-008: Conversation transcripts are isolated derived archives

Status: Accepted.

Maintain one complete Markdown transcript for each conversation ID. Never combine records from different conversations in one transcript. Keep daily raw files as immutable authority and rebuild per-conversation transcripts only as deterministic derived files, with preview, archived replacement, and integrity checks.

## D-009: High-frequency capture uses a native event-driven process

Status: Accepted.

Run one persistent Rust collector that watches native Codex session files through the operating system filesystem notification backend. The collector owns deterministic high-frequency capture and backup operations. Keep the Python CLI for low-frequency Agent-facing maintenance, summary ingestion, retrieval, and reconstruction. Preserve one storage contract across both implementations and test their persisted records for parity.

## D-010: Pending rounds are conversation-scoped

Status: Accepted.

Maintain one pending round per conversation ID and allocate each new dialogue round a globally unique number. A final assistant answer may close only its own conversation's pending round. Preserve summary ranges by advancing the global completed-round high-watermark only across contiguous completed round numbers; retain later completions in an explicit out-of-order set until preceding rounds finish. Store assistant text without a pending user as visible non-round content.

## D-011: One current external recovery snapshot

Status: Accepted.

Create a complete manifest-backed snapshot after each successful primary mutation, then remove older snapshot directories beyond configured retention. Retain one latest snapshot by default and keep the append-only backup log as operation history.

## D-012: Summaries and detailed indexes are conversation-scoped

Status: Accepted.

Assign Level-1 sources and higher-level child summaries within one conversation only. Persist message, timeline, summary, and concept indexes separately for each conversation; retain global indexes only for cross-conversation routing.

## D-013: Native subagent sessions are excluded

Status: Accepted.

Use native session metadata to reject complete Codex subagent sessions before importing any message. Archive only top-level user-visible conversation sessions.

## D-014: One current workspace recovery backup

Status: Accepted.

Before replacing deterministic derived files, preserve their previous versions under `memory/archive/`, then remove older recovery directories beyond `backup.workspace_retention_count`. Retain one latest workspace recovery backup by default. Do not copy the live conversation archive into development output folders.

## D-015: Hybrid deterministic indexes are the source-routing layer

Status: Accepted.

Build Level-1 routing records after 5 completed rounds or 20,000 visible characters, whichever occurs first. Store only deterministic source metadata, hashes, counts, and normalized excerpts. Group every 10 child routing records into the next level. These records make trigger decisions and source recovery auditable; they do not replace semantic summaries.

## D-016: Semantic AI runs only for a closed due range

Status: Accepted.

Let scripts count rounds and visible characters. If a character threshold is crossed while an answer is still being written, wait for that answer's `final_answer` before freezing the source range. Then invoke one ephemeral Codex CLI process to generate the constrained semantic summary, ingest it after source-hash verification, and exit. Never keep an AI conversation active merely to watch for trigger conditions.

## D-017: Historical retrieval is multi-term and conversation-local

Status: Accepted.

Normalize mixed natural-language queries and rank explicit terms deterministically across generated routes and raw records. Do not require the full query to occur verbatim. Exclude currently incomplete rounds to prevent self-matching, restore neighboring context only within the matched conversation, and report `verified` only after a raw-text match.

## D-018: Runtime memory refresh is bounded and hierarchical

Status: Accepted.

Keep persistent memory separate from the client-managed active context. At each Agent turn, inspect the active top-level rollout's latest token telemetry and completed-round count. Refresh after 10 completed rounds, at 65% and 80% effective context utilization, or after a detected compaction drop. Inject a derived capsule through tool context rather than archiving it as a source message. Prefer the highest available summary level, add only uncovered lower summaries and recent task state, and cap the capsule at 1% of the effective model context with a 3,000-token soft limit and 10,000-token absolute limit.

## D-019: Federation preserves single-writer local authority

Status: Accepted.

Each device exclusively writes its own local archive. Imported artifacts live
in a separate read-only sibling federation cache and never modify local raw
records, state, rounds, sequences, or summary counters. Only locally originated
artifacts may be exported, preventing replica circulation.

## D-020: Global identity is origin-qualified

Status: Accepted.

Keep original artifact payloads and hashes unchanged. Build reconstructible
global indexes by qualifying message, conversation, and summary identifiers
with the origin node.

## D-021: Delta continuity is explicit

Status: Accepted.

Use an artifact event ledger so late summaries and title updates are exported.
Verify artifact SHA-256, reject event-sequence gaps and overlaps, and require
each noninitial bundle to name the SHA-256 of the previously accepted bundle.
Treat repeated accepted bundles as no change and conflicting artifacts as
integrity failures.

## D-022: SSH protects transport, not the bundle format

Status: Accepted.

Use SSH pull with strict host-key checking and existing SSH user
authentication. Support `posix` and `powershell` remote shells. SSH encrypts and
authenticates the transport connection. The offline `.mwxb` container is
compressed but neither encrypted nor cryptographically signed, so it may travel
only through a trusted channel.

## D-023: Federation identity is independent of OpenAI

Status: Accepted.

Use Memory無限 node IDs and explicit local peer trust. Do not reuse OpenAI
sessions, Codex credentials, or an OpenAI active-device list as federation
identity or authorization.

## D-024: Replicas are reconstructible and excluded from primary backups

Status: Accepted.

Keep peer replicas under the default sibling `<archive>-federation-cache` or an
explicitly configured equivalent. Do not include replicas in the desktop backup
of the writable primary archive. Rebuild global indexes from local authority
and imported replicas.

## D-025: Cloud folders transport encrypted, signed envelopes

Status: Accepted.

Use iCloud Drive, OneDrive, or another user-selected synchronized directory only
as an asynchronous federation transport. Preserve `.mwxb` as the inner delta
contract, sign each inner payload with the origin device's Ed25519 identity, and
encrypt the signed envelope to the target device with age/X25519 before writing
it to the synchronized directory. Verify decryption, signature, origin, target,
payload size, payload SHA-256, and the existing `.mwxb` chain before import.
Never upload a readable `.mwxb`, a private key, or a cloud-account credential.

## D-026: Cloud exchange is single-writer and acknowledgement-driven

Status: Accepted.

Give each node its own cloud outbox and acknowledgement namespace. A receiving
node reads but never edits or removes another node's files. Send at most one
unacknowledged delta per peer, make retries idempotent, and let the sender remove
only its own acknowledged envelopes after the configured retention period.
Treat missing, partial, placeholder, and temporarily unavailable cloud files as
transient delivery state rather than archive corruption.

## D-027: Cloud scheduling is low-frequency and model-free

Status: Accepted.

Keep the event-driven native collector unchanged. Wake one short-lived cloud
sync process every five minutes, coalesce ordinary local changes for fifteen
minutes, allow an approximately one-megabyte pending delta to flush early, and
attempt a flush after at most sixty minutes. A manual force operation may run
immediately. Empty checks create no cloud files and invoke no AI process.

## D-028: Policy supersession is explicit and append-only

Status: Accepted.

Preserve raw conversations and existing summaries unchanged. Let Level-1
summaries emit source-cited policy events, then derive a current-policy view
from those events. A revision, withdrawal, or reaffirmation changes validity
only when it identifies exactly one active prior statement in the same scope.
Do not use a newest-record-wins rule.

## D-029: Historical and current-policy retrieval are separate modes

Status: Accepted.

Keep ordinary historical retrieval chronological and evidence-oriented. Use a
separate current-policy mode for operational questions, returning the full
matched lineage and cited raw records. Treat unresolved, conflicting,
uncertain, and proposed events as requiring review. When old summaries contain
no policy events, search newer matching raw records and disclose that no
explicit lineage was available.

## D-030: Model usage is persisted as source-reported derived telemetry

Status: Accepted.

Persist top-level Codex rollout `token_count` events in one per-conversation
ledger. Report the result as Codex-reported model usage, not billing usage or a
text-derived estimate. Preserve cumulative counter segments across resets and
do not double-count cached-input or reasoning-output breakdowns. Retained
rollouts may be backfilled; missing telemetry, ChatGPT web conversations, and
ChatGPT export files remain unmeasured. Keep these ledgers outside raw dialogue
and semantic summaries.

## D-031: Environment synchronization is a separate authority domain

Status: Accepted.

Keep synchronized rules, Skills, project bindings, installation receipts, and
promotion records under `environment/`. Do not write them into conversation
raw history, semantic summaries, token-usage ledgers, or peer memory replicas.
Each device remains the sole writer of its local formal files and installed
Skills. A received environment artifact is staged until local validation and
installation complete.

## D-032: Four object classes use stable logical identities

Status: Accepted.

Synchronize global rules, project rules, global Skills, and project Skills.
Identify them by stable artifact and project IDs rather than absolute paths.
Store device-specific project paths as bindings. Do not infer project identity
from a similar directory name, and do not create an absent project
automatically. Keep stable logical identity separate from each immutable
revision identity.

## D-033: Rules use managed ownership boundaries

Status: Accepted.

For mixed-ownership files such as a global `AGENTS.md`, synchronize only an
explicit Memory無限 managed block and preserve every byte outside that block.
Classify registered project documents as `canonical`, `project-local`,
`generated`, or `excluded`. Back up, preview, install atomically, restore
permissions, and record before/after hashes. Never silently replace a locally
diverged rule.

## D-034: Skill versions are immutable verified packages

Status: Accepted.

Package one complete Skill version with its manifest, `SKILL.md`, optional
agent metadata, scripts, references, templates, tests, file hashes, platform
requirements, runtime requirements, network and persistence declarations, and
source revision. Validate in staging, preserve the prior installed version,
switch atomically, verify Codex discovery, and roll back on failure. Project
Skills require an explicit local project binding before activation.

## D-035: Project capability promotion is reviewed evolution

Status: Accepted.

Treat project-to-global promotion as a workflow between the four object
classes, not as a fifth synchronized class. Scripts may detect candidates, but
promotion always requires an explicit owner classification and review. Prefer
extending an existing global Skill. For mixed capabilities, move only the
project-independent core to the global owner and retain paths, scientific or
statistical policy, permissions, and output contracts in a project adapter.
Require source-project regression evidence and an unrelated project or generic
fixture before promotion is accepted.

## D-036: Environment conflicts use a common base

Status: Accepted.

Compare the common base, local version, and remote version. Automatically
converge only one-sided changes, identical changes, or structurally disjoint
managed-block edits. Same-block edits, delete/modify races, divergent Skill
code, unregistered local changes, uncertain project identity, incompatible
platform contracts, and expanded permissions or network access enter a
conflict queue. Modification time alone never grants authority.

## D-037: Environment exchange uses an independent trusted stream

Status: Accepted.

Reuse the existing federation and signed encrypted cloud transport, but keep
`archive-v1` and `environment-v1` as independent event streams with separate
ledgers, sequence chains, acknowledgements, and outstanding bundles. A fault or
conflict in one stream must not advance or block the other stream. Preserve
target-specific encryption, origin signatures, explicit peer trust, and the
prohibition on replica recirculation. Core-rule overwrite and capability
promotion are never automatic by default.

## D-038: Effective configuration is compiled and explainable

Status: Accepted.

Compile one canonical effective configuration from a closed versioned default
contract and explicitly declared input layers. Record the source of every
effective value and a canonical SHA-256. Read-only compilation and explanation
must not create archive, Environment, scheduler, or dashboard state. Preserve
the existing root precedence and operational defaults until a separately
versioned decision changes them. Unknown keys, duplicate keys, invalid types,
and invalid ranges fail closed.

## D-039: Device capability negotiation is diagnostic, not authority

Status: Accepted.

Publish a minimal path-free and credential-free technical capability offer and
derive compatibility from two validated offers. A successful result never
authorizes installation, trust, permission expansion, or synchronization.
Missing legacy offers yield `unknown-legacy` and do not block the existing
`archive-v1` or `environment-v1` behavior. Capability exchange uses an
independent target-encrypted sidecar so older clients can ignore it without
rejecting an existing event stream.

## D-040: Memory sharing scopes remain a deferred design

Status: Accepted as design only.

The current product remains a single-user system in which all retained memory
may be shared among the user's trusted devices. Do not add runtime scope
fields, filtering, policy enforcement, migration, or dashboard controls.
Reopen this decision before multi-user access, third-party AI write access,
partial project sharing, hosted memory service, explicitly non-shareable data,
or shared memory across identity domains is introduced.

## D-041: Content addressing is exact-byte shadow storage

Status: Accepted and implemented in v2.8.

Content addressing may reuse an object only when its persisted bytes and
cryptographic digest are identical. Do not normalize text, reserialize JSON,
merge similar records, or use AI or vectors for deduplication. Initial
operation is shadow-only, outside the authoritative archive, with no source
deletion and no garbage collection. Every file manifest preserves ordered
objects, byte length, and whole-file SHA-256, and reconstruction must reproduce
the source bytes exactly.

## D-042: External memory access is read-only by default

Status: Accepted and implemented in v2.9.

Expose stable application services through thin CLI, loopback HTTP, and MCP
stdio adapters. The default service surface is read-only, bounded, path-safe,
and unable to create query logs or initialize missing state. The dashboard's
mutable HTTP server is not the external read-only API. Any future write surface
requires a separate contract and explicit authority.

## D-043: Major version follows compatibility, not roadmap completion

Status: Accepted.

Completing the planned integration does not by itself justify version 3.0. If
the public CLI, persisted formats, protocols, extension API, and supported
compatibility contract remain backward compatible, publish the integrated
result as the next 2.x version. Use 3.0 only after an explicit incompatible
contract decision, migration matrix, legacy-reader path, and rollback evidence
have passed the release gate.

## D-044: Release evidence is deduplicated, not weakened

Status: Accepted.

Feature branches run one PR matrix and do not also run a branch-push matrix.
Only `main` pushes run the complete same-SHA candidate gate. Each platform job
runs its full unittest suite at most once; rehearsal contracts covered by that
suite retain individual SHA-256-bound reference logs instead of rerunning the
same modules. Windows uses bounded whole jobs rather than serial test shards,
and superseded PR runs are cancelled. Installer publication consumes the
successful same-SHA candidate proof and does not repeat unit or rehearsal
suites. No optimization may delete a safety contract, reuse evidence across
commits, versions, jobs, or platforms, or report an unproved scenario as passed.

## D-045: Personal Environment convergence is v2.10 and review-first

Status: Accepted and implemented in v2.10.

Place cross-device personal Environment inventory and convergence after the
v2.9 read-only interface and update-governance work. Represent installed Skills
and global Rules as a deterministic, content-addressed, path-free profile and
transport it only as immutable evidence through the existing independent
`environment-v1` stream. Imported profiles remain read-only. A profile may
identify differences and link to an existing approved Environment artifact,
but it never authorizes installation, whole-file Rule overwrite, credential or
runtime copying, project activation, permission expansion, or archive changes.
System-bundled and plugin-managed Skills retain provider ownership. Release
v2.10 only after the v2.9 gate and exact-candidate macOS/Windows evidence pass;
v3.0 remains conditional on a separately accepted incompatible contract.

The authentication boundary is the signed and target-encrypted envelope opened
by the native helper. Arbitrary code already executing as the same local user in
the Memory Wuxian Python process is inside the trusted computing base: it can
write the archive directly and is not treated as a sandboxed adversary. The
one-shot Python open-result object is therefore a misuse guard that binds the
native helper result to one import, not a security boundary against hostile
in-process code. A wider overlapping environment bundle is a recovery rebase,
not ordinary history replacement: it is accepted only from the authenticated
peer when every retained prefix event matches exactly, then commits a receipt
bound to the resulting state, complete replica ledger, and every output hash.

## D-046: Index generations are immutable and pointer-activated

Status: Accepted and implemented in v2.6.

Build each deterministic index reconstruction beside the active indexes from
an exact verified raw-and-summary source manifest. Persist a closed immutable
generation manifest and payload before exposing it, activate only by an atomic
pointer replacement after integrity and fixed-benchmark checks pass, and retain
the previous generation for pointer-only rollback. Creation timestamps and the
previous-pointer metadata do not participate in generation identity. Source
drift, payload tampering, incomplete generations, and unexplained retrieval
deltas fail closed without modifying raw history or semantic indexes.

## D-047: Background maintenance is persistent, bounded, and model-free

Status: Accepted and implemented in v2.7.

Persist mechanical maintenance as closed-schema jobs with stable idempotency
keys, explicit leases, bounded attempts, restart recovery, and quarantine.
Summary eligibility may advance only after an existing immutable summary job
identifies a complete dialogue boundary. AI is never part of the queue tick; an
explicit one-shot dispatcher leases only a `semantic-ready` job and records its
completion or bounded failure. Queue, diagnostic, or semantic failure must not
stop native capture or mutate authoritative raw history.

## D-048: Resumable transfer is contiguous and domain-isolated

Status: Accepted and implemented in v2.8.

Track each archive or environment shadow stream with an independent durable
checkpoint bound to source identity, target identity, manifest identity, and
the exact ordered hash prefix already accepted. Duplicate ranges are
idempotent only when their hashes match; gaps, crossing overlaps, corruption,
and checkpoint tampering fail closed with explicit source and target context.
Completion requires exact object verification and never grants authority over
the target device's raw archive or changes the existing transport codecs.

## D-049: Update and summary eligibility are mechanical approval gates

Status: Accepted and implemented in v2.9.

Select stable, beta, and development updates from closed metadata, verify every
downloaded artifact, and stage it as inert evidence. A failed verified delta may
fall back to a verified full package, but neither path executes or installs
without a separate explicit user approval. Evaluate summary budgets from closed
numeric metrics only at a completed dialogue boundary; the scheduler may enqueue
one idempotent semantic job but cannot invoke AI itself.

## D-050: Retained source coverage and recoverable debt must converge

Status: Accepted for v2.11.

Persist the earliest collector activation boundary across installation and
upgrade. Any retained top-level rollout without a completed cursor remains
coverage debt regardless of the latest installer timestamp. Parse it by
complete JSONL lines in bounded native transactions and advance its cursor only
after durable append; existing partial archive records remain append-only.

Reconcile historical mechanical, semantic, and backup debt with idempotent
linear-time maintenance. Model unavailability defers semantic work without
consuming retry attempts. Oversized semantic jobs use a source-hash-bound,
resumable map-reduce plan whose actual prompts satisfy both character and UTF-8
byte budgets and whose completed pieces are reused only after hash validation.
The dashboard reports recoverable backlog as catching-up and reserves attention
or error for quarantine, permanent failure, corruption, or integrity drift.
