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

The Exchange Plane owns transport identities, envelopes, event sequences,
predecessor chains, acknowledgements, replica delivery, and diagnostics. It
does not own the meaning of a conversation, summary, Rule, or Skill.

Governance-insight proposals are inert domain records. The Environment Plane
stores local immutable proposals and read-only peer replicas; the Exchange
Plane transports them. `work-system-governor` owns semantic abstraction,
classification, validation, and acceptance. A transported or imported proposal
does not authorize a Rule or Skill revision.

`archive-v1` and `environment-v1` remain independent streams. A fault,
conflict, or acknowledgement delay in one stream must not advance or block the
other.

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
trigger evaluation, and backup handoff in the prescribed order under the
archive lock. Failure must not expose a partially committed state.

### 3. Summary Job Contract

Deterministic code freezes a closed source range and SHA-256. AI receives only
the bounded job and returns schema-constrained data. Ingest rechecks the source
hash. Summaries remain routing indexes.

### 4. Retrieval Contract

Indexes locate candidates; raw records verify historical claims. Results
identify origin, conversation, source range, and verification level.
Current-policy and chronological retrieval remain distinct modes.

### 5. Environment Artifact Contract

Each global Rule, project Rule, global Skill, and project Skill has a stable
artifact identity and immutable revision. Revisions declare content, base,
provenance, platform and runtime requirements, network access, persistence,
and local binding requirements.

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
   Before publication, run one complete local rehearsal and one candidate CI
   matrix against the same candidate series.
3. Fix candidate-test, CI, packaging, and unpublished-artifact defects in the
   candidate series. These defects do not create a new product version.
4. Freeze the version and documentation only after the candidate matrix,
   installer build, package-content checks, and required live installation
   rehearsal pass.
5. Create the immutable formal tag once, then run one formal build and upload.
   A defect in an already published artifact requires a new patch version.

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

## Intended Package Boundaries

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

## Completion Rule

A phase is complete only when its contracts are implemented, required
platforms have evidence, existing behavior remains verified, and no temporary
duplicate owner remains. File movement, narrow unit tests, or documentation
alone do not complete a phase.
