# Memory Wuxian Versioned Execution Roadmap: v2.5 to v3.0

## Authority and intended use

This document is the implementation companion for the ordered roadmap in
`PRODUCT_ARCHITECTURE.md`. It records the agreed execution rules for the
post-v2.5 product line so that another Codex client can resume the work without
inventing a new scope.

The v2.6--v3.0 plan was recovered from repository commit
`fad4a642dc1c9f49d53105f259103db89db0fcec`. This file restores that omitted
authority on the current product line. It also records the separately approved
v2.10 personal Environment convergence work and the D-051 v2.16-v2.18 P0
Capture Core sequence. Do not reconstruct the roadmap from release numbers
mentioned in conversation history.

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
3. `archive-v1`, `environment-v1`, and `project-evidence-v1` remain separate
   payload and failure domains even when they share lower-level transport
   machinery.
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
| v2.6.0 | Released | v2.5.0 | Index safety and retrieval evaluation. |
| v2.7.0 | Released | v2.6.0 | Background autonomy and diagnostics. |
| v2.8.0 | Released | v2.7.0 | Lossless storage and sync protocol hardening. |
| v2.9.0 | Released | v2.8.0 | Unified read-only interfaces and update governance. |
| v2.10.0 | Candidate | v2.9.0 | Personal Environment inventory, comparison, and review-first convergence. |
| v2.11.0 | Candidate | v2.10.0 | Continuous source catch-up and bounded debt convergence. |
| v2.12.7 | Released | v2.11.0 | Stable live capture, catch-up, repair, and cross-platform activation baseline. |
| v2.13.0 | Candidate | v2.12.7 | Explicit immutable project evidence packages and independent encrypted exchange. |
| v2.14.0 | Candidate | v2.13.0 | Device-local Project Evidence owners. |
| v2.15.0 | Current baseline | v2.14.0 | Baseline for the approved Capture Core isolation sequence. |
| v2.16.0 | Approved | v2.15.0 | Transactional collector lifecycle, readiness, activation, and rollback. |
| v2.17.0 | Approved | v2.16.0 | Extract the P0 native Capture Core with watcher-first byte parity. |
| v2.18.0 | Approved | v2.17.0 | Durable transaction recovery, failure isolation, telemetry, and release evidence. |
| v3.0.0 | Conditional | v2.18.0 plus an accepted incompatibility decision | Cross-platform integration only when a real breaking public contract requires a major version. |

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

## v2.10.0: personal Environment convergence

### Objective

Let explicitly trusted devices describe and compare the user's Codex
environment as a device-independent, content-addressed profile. The profile
must make global Rules, installed Skills, provider-managed capabilities, and
platform differences visible without copying credentials, silently installing
software, or treating one device's absolute paths as portable truth.

### Canonical owners and payload boundary

- Environment Plane owns inventory, profile validation, comparison, and the
  review state.
- Exchange Plane transports the immutable profile envelope through the existing
  independent `environment-v1` stream.
- Product Shell exposes preview-first CLI and dashboard views; it does not own
  profile semantics or installation policy.
- A personal Environment profile is evidence. Existing Environment Registry
  artifacts, immutable Skill packages, managed Rule blocks, binding contracts,
  and installers remain the only activation paths.

### Included work

- Deterministically inventory user-managed, system-bundled, and plugin-managed
  Skills using stable installation identities, versions when declared, exact
  tree hashes, file counts, byte counts, and explicit incomplete-inventory
  reasons.
- Record global `AGENTS.md` and `AGENTS.override.md` as exact UTF-8 evidence with
  SHA-256 identities while separately identifying Memory Wuxian managed blocks.
- Exclude credentials, tokens, environment-variable values, hostnames,
  usernames, absolute local paths, virtual environments, caches, models,
  semantic indexes, archives, and conversation content from the profile.
- Persist local profile generations immutably and keep a current pointer that
  can be rebuilt from the generations.
- Exchange profiles only with already trusted peers through `environment-v1`;
  retain imported profiles in read-only per-peer replicas.
- Compare local and peer profiles with explicit `same`, `missing-local`,
  `missing-peer`, `content-differs`, `platform-inapplicable`, and
  `inventory-incomplete` outcomes where applicable.
- Provide a human-readable convergence plan that links a difference to an
  existing immutable Environment artifact or marks it `evidence-only` when no
  approved distribution object exists.
- Let a later explicit review select individual managed Rule blocks or verified
  Skill packages for the existing preview, registration, binding, install,
  self-check, and rollback workflow.
- Preserve provider provenance. System-bundled and plugin-managed capabilities
  are references to their provider and version, not packages to be copied from
  another device.

### Non-goals and prohibitions

- Do not automatically install, update, enable, disable, remove, or downgrade a
  Skill, plugin, Rule, runtime, model, hook, scheduler, or application.
- Do not overwrite a complete `AGENTS.md` or `AGENTS.override.md`. Only an
  explicitly approved managed block may enter the existing Rule installer.
- Do not synchronize project Rules or project Skills without an explicit
  project binding and the existing project-scoped authorization contract.
- Do not copy `.env`, credentials, keychains, cloud-account data, SSH material,
  device identity private keys, local paths, executable locations, or process
  state.
- Do not copy platform runtime directories. Synchronize a reviewed runtime
  contract and realize it independently on each platform.
- Do not infer that a Skill with the same display name is equivalent. Compare
  stable installation identity, provider, immutable manifest, and tree hash.
- Do not modify, normalize, compact, merge, or delete Memory Wuxian raw
  conversation archives, summaries, indexes, or replicas.
- Do not create a v2.10 tag or release before v2.9 has passed its release gate.
  Contract work and an untagged implementation branch may be prepared earlier,
  but it is not an installable release candidate.

### Required execution and evidence

1. Freeze a closed profile schema, size and count limits, provider taxonomy,
   ignored-file policy, source identities, and redaction contract.
2. Capture the same unchanged fixture twice and prove byte-identical profile
   hashes, one immutable generation, and no duplicate export event.
3. Change one Skill file, one managed Rule block, and one ignored cache file in
   separate cases; prove only the first two create intended differences.
4. Exercise unreadable files, links, junctions, malformed Skill metadata,
   duplicate identities, oversized trees, non-UTF-8 Rules, unknown fields, and
   content/hash tampering; fail closed without partial activation.
5. Export one profile in `environment-v1`, interrupt and resume delivery, replay
   the same range, and import it on a trusted macOS/Windows peer as read-only
   evidence. Reject untrusted, wrong-target, conflicting, and corrupt bundles.
6. Compare Windows and macOS fixtures containing user-managed, bundled, and
   plugin-managed Skills. Explain missing, different, provider-owned, and
   platform-inapplicable entries without proposing unsafe file copying.
7. Generate a bounded convergence plan. Prove that items without an approved
   immutable artifact stay `evidence-only` and that every activation still
   requires the existing explicit preview and approval path.
8. Rehearse managed-block Rule integration while proving all bytes outside the
   selected block remain unchanged and rollback restores the prior file.
9. Rehearse a verified Skill-package install and rollback through existing
   Environment Registry contracts; prove a received profile alone cannot call
   either installer.
10. Run architecture, documentation, focused Environment/exchange, full suite,
    fault-injection, Windows, macOS, real incremental-upgrade, same-SHA CI, and
    rollback gates on one frozen candidate after v2.9 is released.

Release gate: deterministic profile identity, complete redaction checks,
read-only peer replicas, replay-safe exchange, explainable differences, zero
automatic activation, managed-block byte preservation, verified installer
handoff, and same-SHA macOS/Windows evidence all pass on the exact candidate.

## v2.11.0: continuous catch-up and debt convergence

### Objective

Make retained source coverage and every recoverable debt class converge after
idle periods, process restarts, upgrades, and temporary model unavailability,
without rewriting authoritative history or weakening bounded execution.

### Implementation contract

1. Persist the earliest collector activation boundary; upgrades may move it
   earlier but never later.
2. Treat every retained top-level rollout without a completed cursor as
   coverage debt. Stream complete JSONL lines in bounded native batches and
   commit a cursor only after the corresponding archive append is durable.
3. Reconcile historical mechanical, semantic, and backup debt in linear time.
   A quarantined or deferred item must not block later eligible work.
4. Run low-frequency maintenance independently of the foreground Codex task.
   Mechanical work remains model-free; unavailable Codex defers semantic work
   without consuming a retry.
5. Split oversized summary jobs through a source-hash-bound resumable plan.
   Enforce both actual prompt character and UTF-8 byte budgets and a fixed
   model-call ceiling while retaining the original parent job identity.
6. Expose coverage, mechanical, semantic, and backup debt independently. Normal
   backlog is catching-up; corruption, quarantine, or permanent failure is an
   attention or error state.

Release gate: exact source-contract parity, interruption and restart recovery,
old no-cursor discovery, partial-import recovery, prompt-budget proof, hidden
background scheduling, full cross-platform rehearsal, same-SHA CI, and
installer rollback all pass on one frozen candidate.

## v2.14.0: device-local Project Evidence Owners

### Objective

Extend the explicit v2.13 project evidence package contract with persistent,
device-local owners that maintain a closed project evidence selection without
scanning a workspace or exporting local source paths.

### Required behavior

1. Registration is preview-first, explicit, and idempotent.
2. The five-minute model-free supervisor processes at most 20 owners per pass.
3. No-change creates no generation, event, receipt, backup, or model call.
4. Changed files must remain byte-stable through capture and create exactly one
   immutable successor linked to the current local head.
5. Missing, linked, oversized, secret-bearing, or conflicting inputs fail per
   owner without blocking archives, summaries, cloud sync, or other owners.
6. Imported packages remain read-only and never create a local owner.
7. CLI and the macOS/Windows dashboard expose equivalent bounded status and
   manual refresh behavior without displaying source-root paths.

Release gate: focused domain and scheduler tests, authenticated exchange
regressions, architecture and documentation contracts, browser rehearsal,
same-candidate macOS and Windows package rehearsal, transactional update, and
rollback to v2.13.0 all pass.

## v2.16.0: transactional collector lifecycle

### Objective

Make every collector lifecycle change one bounded, observable transaction
before moving implementation ownership. Preserve the v2.15 persisted bytes and
public behavior.

### Implementation contract

1. `scripts/collector_lifecycle.py` owns structured lifecycle orchestration:
   inspect pre-state, stage, activate, prove runtime readiness, commit, and
   restore the exact pre-state on failure.
2. Lifecycle subprocesses use explicit UTF-8 and argument arrays. Tests cover
   Chinese, Japanese, currency symbols, emoji, spaces, leading hyphens, and
   long paths without shell reconstruction.
3. Readiness proves the intended executable, configuration, archive identity,
   single-writer lock, and live watcher state. Process existence alone is not
   acceptance evidence.
4. Capture remains available when optional control, memory, exchange,
   environment, project, dashboard, or AI services fail.

Release gate: lifecycle fault injection at every transition, exact rollback,
installed Windows and macOS readiness evidence, and the schema-v2 architecture
contract pass on one frozen candidate.

## v2.17.0: P0 Capture Core extraction

### Objective

Move mechanical capture behind the D-051 boundary without changing accepted
source events or persisted archive bytes.

### Implementation contract

1. Register and extract `native-collector/src/lib.rs`, `runtime.rs`,
   `locking.rs`, `telemetry.rs`, `source/**`, `store/**`, and
   `bin/memory-wuxian-core-launcher.rs` under the single `capture-core` owner.
2. Establish filesystem watchers before bounded startup enumeration and catch-up
   so a write during startup is observed or recovered exactly once.
3. Preserve event selection, normalization, ordering, archive append, cursor,
   and backup-debt bytes against frozen golden fixtures.
4. Capture Core may depend only on Platform Foundation. No Control, Memory,
   Exchange, Environment, Project Evidence, Product Shell/UI, product-quality,
   or AI dependency may enter the crate or lifecycle adapter.

Release gate: old/new byte parity, startup-race and restart fixtures,
single-writer contention, architecture allowlist checks, and installed
cross-platform launcher evidence pass on one frozen candidate.

## v2.18.0: durable capture recovery and fault isolation

### Objective

Make interrupted Capture Core transactions recoverable and diagnosable without
coupling P0 availability to any peripheral subsystem.

### Implementation contract

1. Persist bounded write-ahead transaction evidence before mutation and recover
   it idempotently after interruption; advance a source cursor only after its
   raw append is durable.
2. Isolate source, store, lock, telemetry, and lifecycle failures with explicit
   machine-readable states. Optional telemetry failure cannot stop capture.
3. Emit privacy-safe bounded diagnostics for readiness, last durable append,
   cursor position, replay, lock contention, and backup-debt handoff without
   reading or exporting dialogue content.
4. Prove crash recovery, disk-full behavior, partial-write rejection, lock loss,
   stale readiness, and peripheral service failure on the frozen candidate.

Release gate: fault-injection and restart matrices, byte and cursor invariants,
bounded telemetry, no-peripheral-dependency checks, exact update rollback, and
same-candidate Windows/macOS rehearsal all pass.

### v2.16-v2.18 non-goals and prohibitions

- Do not change Summary V1, Summary V2, summary eligibility, atomic summaries,
  parent summaries, map-reduce summaries, or any AI worker behavior.
- Do not add or change historical semantic backfill, historical mechanical
  catch-up policy, repair policy, or retained-source coverage semantics.
- Do not change cloud or federation protocols, including `archive-v1`,
  `environment-v1`, `project-evidence-v1`, envelopes, acknowledgements,
  peer-state, sequence, replica, or live-archive payload behavior.
- Do not change public CLI behavior, persisted record schemas, raw bytes,
  source selection, summary state, protocol state, or release version metadata
  as a side effect of module extraction.

## v3.0.0: conditional major-version integration

### Decision rule

Do not create v3.0 simply because v2.6--v2.18 are complete. Continue with a 2.x
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
