# Memory無限 Product Architecture

## Status And Authority

This is the canonical owner for Memory無限 product module boundaries,
dependency direction, application contracts, refactoring gates, and the
ordered productization roadmap. `PROJECT.md` owns historical invariants and
`references/decisions.md` owns accepted architecture decisions.

Before work crosses module boundaries, changes a public contract, or starts a
roadmap phase, read this document and the directly affected decisions. Do not
replace this architecture from conversation recollection.

## Product Form

Memory無限 is a local-first modular monolith:

- one installable product per platform;
- one independently writable local archive per device;
- a small number of local processes with explicit ownership;
- no required Memory無限 cloud service;
- internal modules connected through versioned contracts;
- platform adapters isolated from domain behavior.

Do not split the product into network microservices merely to obtain module
boundaries. Do not let a dashboard, CLI, scheduler, or installer become a
second implementation of domain rules.

## Top-Level Architecture

```text
Memory無限
├── Product Shell
│   ├── Desktop Dashboard
│   ├── CLI
│   └── Installer / Updater
├── Control Plane
│   ├── Scheduler
│   ├── Job Queue
│   ├── Health / Diagnostics
│   └── Configuration
├── Memory Plane
│   ├── Source Adapters
│   ├── Archive Store
│   ├── Summary Engine
│   ├── Index Engine
│   ├── Retrieval Engine
│   └── Token Telemetry
├── Environment Plane
│   ├── Environment Registry
│   ├── Rule Manager
│   ├── Skill Package Manager
│   ├── Conflict Manager
│   ├── Promotion Manager
│   └── Governance Proposal Replica
├── Project Evidence Plane
│   ├── Explicit Package Builder
│   ├── Immutable Generation Store
│   ├── Bounded Query / Reconstruction
│   └── Read-Only Peer Replica
├── Exchange Plane
│   ├── Federation Protocol
│   ├── Cloud-Folder Transport
│   ├── SSH Transport
│   ├── Identity / Cryptography
│   └── Acknowledgement / Replica Manager
└── Platform Foundation
    ├── Atomic Storage
    ├── Locking
    ├── Transactions / Rollback
    ├── Schema Validation
    ├── macOS Adapter
    └── Windows Adapter
```

## Authority Domains

The Memory Plane owns source events, raw history, transcripts, summary jobs,
summaries, indexes, retrieval, context capsules, and token telemetry.

The Environment Plane owns registered Rules and Skills, immutable revisions,
local bindings, installation transactions, conflicts, promotion review,
receipts, and rollback evidence.

The Project Evidence Plane owns explicitly selected bounded project records,
their exact-byte immutable generations, predecessor references, bounded query,
conflict-safe reconstruction, and read-only peer replicas. It never scans a
workspace, installs a capability, activates a Rule or Skill, or treats a report
as proof of current external state.

The Exchange Plane owns transport identities, envelopes, event sequences,
predecessor chains, acknowledgements, replica delivery, and diagnostics. It
does not own the meaning of a conversation, summary, Rule, or Skill.

Governance-insight proposals are inert domain records. The Environment Plane
stores local immutable proposals and read-only peer replicas; the Exchange
Plane transports them. `work-system-governor` owns semantic abstraction,
classification, validation, and acceptance. A transported or imported proposal
does not authorize a Rule or Skill revision.

`archive-v1`, `environment-v1`, and `project-evidence-v1` remain independent
streams. A fault, conflict, or acknowledgement delay in one stream must not
advance or block another.

## Dependency Direction

```text
Product Shell -> Application Services -> Domain Modules -> Platform Foundation
Control Plane -> Application Services
Exchange Adapters -> Exchange Contracts -> Domain Importers
```

Prohibited dependencies:

- domain modules importing dashboard or CLI code;
- the dashboard importing command-parser internals;
- installers implementing archive, conflict, or retrieval policy;
- transports directly mutating local authoritative domain files;
- source adapters bypassing the archive transaction contract;
- tests treating a UI response as proof of domain persistence.

## Versioned Application Contracts

### 1. Source Adapter Contract

Every source adapter emits normalized, source-referenced visible events. It
preserves stable source identity and does not write archive internals directly.
Hidden reasoning, system prompts, and general tool output remain excluded.

### 2. Archive Transaction Contract

One mutation owns raw append, transcript and index updates, cursor updates,
trigger evaluation, and an atomic coalescing backup-debt handoff in the
prescribed order under the archive lock. Failure must not expose a partially
committed state. Complete external snapshot creation is an independent
maintenance transaction; it clears debt only after the snapshot succeeds.
Large source files are parsed outside the lock and admitted through bounded
durable batches. A source cursor advances only after its corresponding batch is
committed, and an upgrade cannot move the persisted coverage boundary later.

### 3. Summary Job Contract

Deterministic code freezes a closed source range and SHA-256. AI receives only
the bounded job and returns schema-constrained data. Ingest rechecks the source
hash. Summaries remain routing indexes.
If a job exceeds the actual prompt budget, a hash-bound resumable map-reduce
plan preserves its parent identity, validates reusable partial results, and
caps both characters and UTF-8 bytes for every model invocation.
The semantic maintenance owner may overlap no more than three independent model
calls within one bounded batch. It serializes source verification, archive
ingestion, parent-job creation, and state/index mutation through existing locks.
Concurrency does not change trigger thresholds, prompt budgets, source identity,
retry/quarantine semantics, or the immutable archive.

### 4. Retrieval Contract

Indexes locate candidates; raw records verify historical claims. Results
identify origin, conversation, source range, and verification level.
Current-policy and chronological retrieval remain distinct modes.

### 5. Environment Artifact Contract

Each global Rule, project Rule, global Skill, project Skill, and global runtime
contract has a stable artifact identity and immutable revision. Revisions
declare content, base, provenance, platform and runtime requirements, network
access, persistence, and local binding requirements.

A runtime contract synchronizes a portable capability interface rather than a
platform binary environment. It may pin model identity, artifact hashes,
runtime packages, input/output semantics, and installer entry points. Model
files, virtual environments, credentials, and derived indexes remain local.
Registration and transport do not authorize realization; realization is an
explicit device-local installer transaction.

### 6. Installer Transaction Contract

```text
preview -> stage -> validate -> persist rollback -> atomic switch
-> post-install self-check -> append receipt
```

Failure at or after replacement restores verified rollback state.
Mixed-ownership files preserve every byte outside their managed block.

### 7. Transport Contract

Transport handles identity, signing, target encryption, framing, sequence
continuity, predecessor hashes, idempotency, acknowledgements, and delivery
diagnostics. It passes verified payloads to the owning domain importer and
never interprets domain policy.

### 8. Scheduler Job Contract

Every background action is a bounded, model-free job unless a closed semantic
summary is due. A job declares trigger, input cursor, idempotency key, lock,
retry policy, terminal states, and diagnostic evidence. Empty checks create no
artifact or AI request.
The five-minute semantic maintenance run selects at most eight jobs, limits
model-call concurrency to three, isolates sibling failures, and emits aggregate
phase timing without prompts or source text. A clean full-recovery audit may be
reused for 24 hours; explicit recovery debt bypasses that cache. All writes and
parent-summary scheduling remain serialized.

### 9. Dashboard API Contract

The dashboard calls stable application services through versioned models.
Routine status is read-only. Mutations use the same services as the CLI. The
dashboard does not depend on CLI parser internals or reimplement decisions.

### 10. Release Evidence Contract

Every release binds a clean commit and tag to schema versions, migration
behavior, documentation, platform tests, installer hashes, dashboard
replacement, active-archive preservation, live post-install self-check, and
rollback evidence. Evidence from another version does not satisfy the release.

Release-candidate development and formal publication are separate lifecycle
states:

1. Accumulate related fixes on the candidate branch without creating a formal
   tag, GitHub Release, or published installer.
2. During iteration, run the smallest tests that cover the affected contract.
   Before publication, run one complete candidate CI matrix against the exact
   commit to be released. A local full rehearsal is optional when the same
   platform and commit are covered by that matrix; local focused tests remain
   required for changed behavior.
3. Fix candidate-test, CI, packaging, and unpublished-artifact defects in the
   candidate series. These defects do not create a new product version.
4. Freeze the version and documentation only after the candidate matrix,
   installer build, package-content checks, and required live installation
   rehearsal pass.
5. Create the immutable formal tag once, then run one formal build and upload.
   A defect in an already published artifact requires a new patch version.

The candidate matrix has three bounded platform jobs. Feature branches trigger
it only through `pull_request`; only `main` triggers it through `push`, and a
new commit cancels an older run for the same PR. A full unittest suite runs at
most once per platform job. Rehearsal scenarios already covered by that suite
retain individual hashed reference logs to the full-suite evidence instead of
rerunning the same modules. Windows tests are not divided into serial shards.
The installer workflow verifies and consumes one successful same-SHA `main`
candidate run, then builds packages without repeating unit or rehearsal suites.

Routine updates for an installed device use the verified user-space update
transaction. They stage and validate changed product files, preserve the active
archive and device configuration, atomically replace the installed version,
refresh the dashboard, run post-update checks, and retain rollback state.
They do not display the platform installer or request administrator credentials.
The full installer is reserved for first installation, explicit recovery from
a damaged or mixed installation, and an upgrade whose declared system-level
permission or privileged component change cannot be completed in user space.
Such an upgrade must explain the privileged change before requesting approval.
Formal releases may continue to publish complete installers for new devices;
their existence does not require an already installed device to run them.

On macOS, the transaction must preserve a stable executable entry path rather
than a version-specific Homebrew Cellar target. Before cutover it must prove
exact user and assistant capture with the candidate collector in an isolated
archive. After cutover it must prove that the previous collector PID was
replaced, telemetry is fresh, and the current dashboard passes its live
self-check. Any failure after cutover restores the previous Skill tree,
LaunchAgent bytes, and collector process before reporting failure.
Initial synchronization may create a source-locked semantic-summary job, but it
must not execute or await the AI worker before collector readiness. The job
remains durable for the independent semantic-backfill scheduler. Raw capture,
cursor advancement, deterministic indexes, and atomic backup-debt registration
complete before the startup watermark becomes ready. Full snapshot copying must
not hold collector readiness or transactional cutover open.

Archive freshness is a two-watermark contract. The source watermark records the
newest retained rollout state observed by the collector; the archive watermark
advances only after the corresponding archive transaction succeeds. Reporting
for a historical cutoff must verify persisted source cursors through that
cutoff, and may backfill only the exact retained files found to be lagging.

### 11. Governance Insight Contract

A local product may emit one source-bound governance proposal containing its
origin node, source product and Owner, source revision, local problem and
change, generalized principle, applicability, exclusions, invariants, negative
cases, proposed global Owner, and evidence references. Proposal identity and
content are immutable.

Cross-device transport verifies the envelope and stores a read-only replica.
Semantic classification remains `unresolved` until the global governance Owner
reviews it as `duplicate`, `extension`, `conflict`, `new_domain`,
`project-only`, or `rejected`. Only an explicitly accepted `extension` or
`new_domain` may start a separate Rule or Skill revision lifecycle.

### 12. Configuration Resolution Contract

Configuration resolution compiles a closed default contract and explicit input
layers into one canonical effective configuration. Every effective value names
its source layer, and the canonical value set has a stable SHA-256. Compilation
and explanation are read-only and never mutate a source configuration,
archive, Environment Registry, scheduler, or dashboard setting. Unknown keys,
duplicate keys, invalid types, and invalid ranges fail closed.

### 13. Device Capability Negotiation Contract

A device capability offer declares only product, platform, runtime, protocol,
and interface support needed for compatibility decisions. It contains no local
paths, usernames, hostnames, credentials, complete configuration, or memory
content. Negotiation produces diagnostic reason codes and never grants trust,
installation authority, permission expansion, or synchronization authority.
Legacy devices without an offer remain `unknown-legacy` and continue through
the existing archive and Environment contracts.

When capability offers are later exchanged between devices, they must use a
target-encrypted sidecar independent from the ordered `archive-v1` and
`environment-v1` streams. The v2.5 contract provides local offer generation and
explicit peer-file diagnostics only; it does not publish or transport offers.
Transport remains opaque and does not interpret compatibility policy.

### 14. Deferred Memory-Scope Design

Memory sharing scopes are intentionally not a runtime product capability. The
current single-user product treats retained memory as shareable among the
user's explicitly trusted devices. Reconsider scopes before multi-user access,
third-party AI write access, partial project sharing, hosted memory service,
explicitly non-shareable data, or cross-identity shared memory is introduced.
Until then, do not add scope fields, filters, migration, enforcement, or
dashboard controls.

## Intended Package Boundaries

`docs/module-architecture.json` is the machine-readable ownership registry for
the current source tree. Every production file under its declared source roots
must have exactly one module owner. `scripts/check_architecture_contract.py`
rejects unowned files, overlapping ownership, and declared prohibited
dependencies. A feature change must register its target owner before adding a
new production file, and the architecture check must pass before focused tests
or release rehearsal can count as evidence.

```text
memory_wuxian/
├── application/
├── archive/
├── summaries/
├── retrieval/
├── environment/
├── exchange/
├── scheduler/
├── platform/
├── dashboard_api/
└── contracts/
```

`scripts/` converges toward thin executable adapters and build/install helpers.
The Rust collector converges toward parser, cursor, event normalization,
archive writer, trigger detector, and worker-launcher modules implementing the
same persisted contracts as Python.

Atomic I/O, canonical hashing, locking, schema validation, transaction,
rollback, and receipt behavior belong to Platform Foundation. Rule and Skill
installers may specialize validation and activation but must not maintain
divergent transaction engines.

## Refactoring Hard Gate

Before each modularization change, record:

1. current owner and target owner;
2. public and persisted contracts involved;
3. affected files and entry points;
4. invariants that must remain unchanged;
5. golden fixtures or before/after evidence;
6. platform coverage;
7. rollback path;
8. explicit non-goals.

Preserve event order, source identity, unchanged-format storage bytes, hashes,
trigger boundaries, CLI behavior, dashboard semantics, installer behavior,
transport sequences, and failure gates.

Do not combine module extraction with a storage migration, protocol revision,
new feature, changed default, or changed security policy. If a contract must
change, first amend an architecture decision and version the contract.

## Ordered Productization Roadmap

The detailed version-by-version execution authority is
`references/version-roadmap-v2.5-to-v3.0.md`. Read it before assigning or
implementing any v2.6-or-later scope. The phases below describe architecture
order; they do not override the version dependencies, non-goals, evidence
requirements, or release gates in that file.

### Phase 1: 2.0.x Operational Stabilization

Complete real macOS and Windows installation, bidirectional cloud exchange,
acknowledgement, replica import, global retrieval, and environment-stream
verification. Fix observed defects without broad module movement.

### Phase 2: Boundary And Contract Freeze

Adopt this document, inventory dependencies, define request and response
models, and add checks that reject prohibited dependencies.

### Phase 3: Shared Platform Foundation

Extract atomic I/O, locking, hashing, schema validation, transaction, rollback,
receipt, and platform-process behavior. Prove parity with golden fixtures and
failure injection.

### Phase 4: Application Services And Thin Shells

Create stable application services. Make CLI, dashboard, scheduler, and
installers thin adapters while preserving commands and dashboard behavior.

### Phase 5: Memory Plane Extraction

Move archive, summary, index, retrieval, context, and telemetry behavior one
module at a time. Compare persisted artifacts and retrieval results after
every move.

### Phase 6: Exchange Foundation Unification

Share identity, envelope, sequence, acknowledgement, and delivery machinery
while keeping `archive-v1` and `environment-v1` payload codecs, ledgers,
cursors, and failure domains independent.

### Phase 7: Product Quality Layer

Add migration matrices, fault injection, performance baselines, diagnostic
bundles, compatibility policy, supported-version policy, and upgrade/rollback
rehearsals.

### Phase 8: Major-Version Decision

Remain on 2.x for behavior-preserving internal modularization. Use 3.0 only
when a public CLI, persisted format, protocol, extension API, or compatibility
contract changes incompatibly.

Personal Environment convergence is a backward-compatible v2.10 Environment
Plane capability. `memory_environment_profiles.py` owns closed profile and
generation validation, bounded explicit-source inventory, immutable local
generations, pointer reconstruction, comparison, and inert convergence plans.
The Exchange Plane transports generation events through `environment-v1` and
stores them only as read-only per-peer replicas. Product Shell CLI and dashboard
adapters expose preview and comparison state without owning activation policy.
Profiles do not enter Environment Registry registration or incoming installer
staging, and they contain no local paths, credentials, runtime state, archive
content, conversations, models, or indexes. This compatible feature does not
justify v3.0.

## Completion Rule

A phase is complete only when its contracts are implemented, required
platforms have evidence, existing behavior remains verified, and no temporary
duplicate owner remains. File movement, narrow unit tests, or documentation
alone do not complete a phase.
