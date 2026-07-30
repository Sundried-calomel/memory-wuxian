# Memory Wuxian Versioned Execution Roadmap: v2.5 to v3.0

## Authority and intended use

This document is the implementation companion for the ordered roadmap in
`PRODUCT_ARCHITECTURE.md`. It records the agreed execution rules for the
post-v2.5 product line so that another Codex client can resume the work without
inventing a new scope.

`PRODUCT_ARCHITECTURE.md` remains the canonical owner of module boundaries,
dependency direction, and phase order. `references/decisions.md` remains the
authority for individual accepted decisions. This document does not authorize a
release, an installation, an incoming proposal, an external connection, or a
change outside the selected version work item.

Read this document before planning or implementing any v2.6--v3.0 product
change. Then read the directly affected architecture contracts and decisions.
A later version may not start merely because it is listed here: its predecessor
must have passed its stated release and recovery gates.

## Product-wide invariants

1. The raw archive is authoritative, append-only, and immutable. A summary,
   index, derived representation, or runtime compression never replaces it.
2. Devices write only their local archive. A remote archive replica is
   read-only unless a separately approved transport contract says otherwise.
3. `archive-v1` and `environment-v1` remain separate payload and failure
   domains even when they share lower-level transport machinery.
4. Content addressing is exact-byte and hash based only. No AI, semantic,
   normalization, near-duplicate, or approximate deduplication may rewrite,
   merge, delete, or garbage-collect raw memory.
5. Future privacy scopes are design-only. Do not enable runtime memory scopes
   until a separately approved product decision explicitly authorizes them.
6. Incoming Skills, environment items, and proposals are evidence only. They
   are never installed or activated automatically.
7. The main product runtime supports Python `>=3.14,<3.15` only. Do not restore
   a historical multi-version Python test matrix. The optional semantic-model
   runtime stays isolated and is tested only when that isolated runtime changes.
8. macOS and Windows behavior remains a release concern. Narrow runtime
   support does not relax relevant platform rehearsal.

## Common delivery procedure

For every versioned work item:

1. Create one bounded work contract: objective, exact files and contracts in
   scope, preserved invariants, acceptance evidence, rollback point, and
   explicit non-goals.
2. Check the predecessor's release, recovery evidence, and open warnings. Do
   not use a roadmap heading as proof that prerequisite work is complete.
3. Make the smallest coherent change under the existing canonical module owner.
   Register a newly created production file in `docs/module-architecture.json`
   before implementation.
4. Use targeted local tests while iterating. When a defect is found, repair the
   affected contract and rerun the affected test; do not restart a full release
   rehearsal for every small correction.
5. Freeze one exact candidate. Run the complete candidate gate once: applicable
   unit and integration tests, architecture checks, macOS and Windows evidence,
   failure/rollback rehearsal, and dashboard visual rehearsal when the dashboard
   changes.
6. Publish only the exact passing candidate. Keep iteration untagged; create one
   immutable tag and release only after the final gate passes.
7. Use the user-space update transaction for an installed product. Use a full
   installer only for first installation, explicit recovery, or an approved
   privileged-component migration.

## Version status and dependency order

| Version | Status | Dependency | Purpose |
| --- | --- | --- | --- |
| v2.5.0 | Published | none | Configuration and capability foundation; Python 3.14 main-runtime contract. |
| v2.6.0 | Planned | v2.5.0 | Index safety and retrieval evaluation. |
| v2.7.0 | Planned | v2.6.0 | Background autonomy and diagnostics. |
| v2.8.0 | Planned | v2.7.0 | Lossless storage and sync protocol hardening. |
| v2.9.0 | Planned | v2.8.0 | Unified read-only interfaces and update governance. |
| v3.0.0 | Conditional | v2.9.0 plus an accepted incompatibility decision | Cross-platform integration only when a real breaking public contract requires a major version. |

## v2.5.0: completed foundation

v2.5.0 is the published baseline. It establishes configuration provenance and
capability foundations and narrows the main runtime to Python 3.14.x. Do not
reopen v2.5 merely to add a future feature. A corrective release must be a
separate, narrowly scoped patch with its own evidence.

## v2.6.0: index safety and retrieval evaluation

### Objective

Make index rebuilding and retrieval-policy evolution auditable without altering
raw history or silently changing what a historical query returns.

### Included work

- Create a generation identity and source manifest for each shadow index build.
- Rebuild indexes beside the active index from verified raw-source hashes.
- Evaluate retrieval against a fixed, versioned benchmark corpus.
- Record temporal and supersession lineage so an older statement can be located
  without being presented as the current policy when a later record supersedes
  it.
- Switch the active index pointer atomically only after all required checks pass.

### Non-goals and prohibitions

- Do not modify, compact, normalize, merge, or delete raw conversation text.
- Do not use model judgment to deduplicate source records or silently choose a
  later policy without preserving the earlier record and its lineage.
- Do not make a retrieval result authoritative when the required raw source is
  available but was not verified.

### Required execution and evidence

1. Snapshot the active generation and record its manifest and rollback pointer.
2. Build a shadow generation from an exact source range and hashes.
3. Run fixed retrieval cases, including a corrected/superseded policy case and
   an exact-title or exact-ID disambiguation case.
4. Compare active and shadow results, record intended deltas, and reject
   unexplained deltas.
5. Atomically switch only the generation pointer; preserve the prior generation.
6. Prove rollback by restoring the prior pointer without reprocessing raw data.

Release gate: all benchmark cases pass, each indexed source exists and hashes
match, lineage is queryable, pointer switch and rollback are rehearsed on every
affected platform.

## v2.7.0: background autonomy and diagnostics

### Objective

Make background maintenance observable, bounded, and recoverable without
requiring an always-active AI conversation or creating hidden autonomous work.

### Included work

- Maintain explicit desired-versus-actual service state.
- Use a persistent, model-free job queue for mechanical collection, trigger,
  retry, and repair eligibility.
- Bound retries, quarantine repeated failures, and emit redacted diagnostic
  bundles with actionable cause and recovery state.
- Keep AI involvement limited to explicitly requested semantic tasks, such as
  generating a summary after the mechanical trigger has selected a completed
  dialogue boundary.

### Non-goals and prohibitions

- Do not invoke an AI continuously, infer a user's intent, or split a dialogue
  halfway through a user/assistant round merely because a threshold was crossed.
- Do not let the queue activate incoming rules, Skills, or environment items.
- Do not implement a new transport protocol in this version.

### Required execution and evidence

1. Define durable job states, idempotency keys, retry limits, and quarantine.
2. Test crash, restart, duplicate-delivery, missing-permission, and stale-lock
   paths using fixed case IDs.
3. Prove that mechanical raw capture continues independently of summary work.
4. Prove that a triggered summary waits for the next completed dialogue round.
5. Rehearse diagnostic-bundle generation with secret redaction and no raw-data
   mutation.

Release gate: every queue state is recoverable or explicitly quarantined,
telemetry advances after restart, retries are bounded, and no test causes an
unrequested AI or external side effect.

## v2.8.0: lossless storage and sync protocol hardening

### Objective

Add verifiable exact-byte storage and resumable synchronization without treating
derived data as a replacement for source memory.

### Included work

- Maintain a shadow content store addressed by exact-byte SHA-256 hashes.
- Use ordered manifests containing source identity, byte length, and file hash.
- Add resumable per-stream checkpoints and explicit conflict explanations.
- Verify imported/reconstructed bytes against the recorded hash before marking a
  transfer complete.

### Non-goals and prohibitions

- No semantic or approximate deduplication, AI-assisted merging, source rewrite,
  raw deletion, or garbage collection.
- No cross-device write authority over another device's local raw archive.
- No replacement of existing `archive-v1` or `environment-v1` payload codecs,
  cursors, ledgers, or failure semantics merely because transport helpers are
  shared.

### Required execution and evidence

1. Build a shadow store from an immutable source snapshot and compare every
   byte/hash/length entry.
2. Interrupt transfers at fixed checkpoints and resume each stream exactly.
3. Exercise duplicate delivery, overlap, missing segment, corrupt segment, and
   acknowledgement replay cases.
4. Produce a human-readable conflict explanation that references source IDs and
   does not guess a resolution.
5. Demonstrate that removing the shadow store leaves the original archive
   readable and intact.

Release gate: exact reconstruction hash equality, manifest ordering validation,
resumption evidence, per-domain failure isolation, and rollback by disabling the
shadow path without source-data changes.

## v2.9.0: unified read-only interfaces and update governance

### Objective

Expose consistent, bounded, read-only access to local memory and make update
selection and summary scheduling explicit, deterministic, and reviewable.

### Included work

- Define aligned read-only local CLI, loopback HTTP, and MCP access contracts.
- Require bounded results, clear provenance, and raw-source verification routes.
- Define stable, beta, and development update metadata; stage a verified delta
  update path with a full-package fallback.
- Add a deterministic summary-budget scheduler that decides only when a summary
  job is due. AI remains a one-shot worker for the queued summary itself.

### Non-goals and prohibitions

- No write, deletion, pairing, installation, or remote-control API.
- No automatic install of a downloaded update or incoming Skill/environment
  item.
- No claim that an index-only or summary-only result is an exact historical
  quotation.

### Required execution and evidence

1. Specify equivalent request/response/error/provenance behavior for CLI, HTTP,
   and MCP read operations.
2. Test unavailable source, malformed request, over-broad query, stale index,
   and raw-verification fallback cases.
3. Test update-channel selection, signature/hash verification, failed delta
   fallback, and user-space rollback.
4. Test scheduler thresholds and completed-round boundary behavior without
   invoking an AI during mechanical eligibility checks.

Release gate: interface parity cases pass, every externally visible memory claim
has a confidence/provenance route, update fallback works, and the product still
requires explicit user approval for installation.

## v3.0.0: conditional major-version integration

### Decision rule

Do not create v3.0 simply because v2.6--v2.9 are complete. Continue with a 2.x
release when all public contracts remain compatible. Start v3.0 only after an
explicit accepted decision identifies an incompatible public CLI, persisted
format, protocol, extension API, or compatibility-policy change.

### Required prerequisites when v3.0 is proposed

1. Publish the incompatibility decision and a supported-version/migration matrix.
2. Provide a legacy reader or other documented path for preserved historical
   records.
3. Rehearse migration, downgrade/rollback, interrupted migration, and mixed
   macOS/Windows device states.
4. Freeze public compatibility and recovery contracts before implementation.
5. Publish the major release only after full cross-platform integration evidence
   proves the new contract and the rollback path.

Without all five prerequisites, v3.0 remains unassigned and future work stays
on a compatible 2.x line.

## Handoff checklist for another Codex client

Before acting on this roadmap, the next client must report:

1. the exact target version and why the predecessor gate is satisfied;
2. the canonical architecture owner and directly affected decisions;
3. the exact files/contracts to change and those deliberately out of scope;
4. the raw-archive, device-authority, and no-auto-install invariants it will
   preserve;
5. the targeted iteration checks, final candidate gate, rollback rehearsal, and
   whether a release is actually in scope.

It must stop for clarification if any answer is missing. It must not convert a
planned roadmap item into a silent implementation, tag, release, or installation.
