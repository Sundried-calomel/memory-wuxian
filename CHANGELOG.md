# Changelog

## Unreleased

## 2.12.5 - 2026-08-02

- Recover unresolved rounds by conversation identity across the complete raw
  history, even when a legacy race caused two conversations to share one
  global round number.
- Keep the Python auditor and native collector recovery algorithms equivalent;
  repair only derived state and preserve an automatic rollback backup.
- Add a regression for one completed and one still-open conversation sharing
  the same historical round number.

## 2.12.4 - 2026-08-02

- Preserve sparse fields in lossless raw-record and child-summary tabular
  payloads by encoding field presence separately from JSON `null` values.
  Parent summaries with mixed legacy metadata can now enter the existing
  bounded semantic map/reduce path instead of being quarantined before AI is
  invoked.
- Serialize heartbeat audit and repair with native collection through the
  shared archive lock. A collector batch can no longer be observed between
  its raw append and derived transcript/index/state writes and cached as false
  projection drift.
- Add focused regressions for sparse-versus-null round trips, malformed
  presence maps, heartbeat lock ownership, and the real quarantined parent-job
  payload. Raw history and persisted summaries remain unchanged.

## 2.12.3 - 2026-08-02

- Fixed continuous semantic catch-up being blocked indefinitely by rebuildable
  transcript, index, or state drift while the native collector remained active.
- Existing source-hash-bound summary jobs now continue draining through
  repairable projection drift; raw-integrity failures still fail closed.
- Reuses a recent deep-recovery result for up to one hour unless explicit
  recovery debt exists, avoiding a full derived-view rebuild before every
  five-minute summary batch.
- Added a targeted regression and a live scheduled-task effect gate requiring
  pending semantic debt to decrease without a manual dispatcher invocation.

## 2.12.2 - 2026-08-02

- Restored the daily chart's bold date labels below the baseline, matched the local and all-device bar widths, and refreshed the blue and green palette.
- Fixed native token-ledger backfill so a reset scan cursor retains an independent archived-message high-water mark and cannot replay already archived visible events with changed round state.
- Reserved child summaries used by pending parent jobs and added a receipt-backed repair that quarantines only later overlapping derived jobs without modifying raw history or persisted summaries.

## 2.12.1 - 2026-08-01

- Fixed native collector upgrades from format-v1 token ledgers: retained rollout telemetry now rebuilds the derived format-v2 daily ledger instead of aborting collection.
- Made recovery-marker cleanup idempotent when the concurrent maintenance supervisor has already verified and removed the marker.
- Added a real native v1-to-v2 migration regression and a targeted patch release gate.

## 2.12.0 - 2026-08-01

- Added nested local/all-trusted-device daily message and Codex-reported token bars with three-locale labels, per-device drilldown, coverage warnings, and `Asia/Tokyo` day boundaries.
- Added reset-aware daily token ledgers and bounded format-v2 backfill in both Python and the native collector.
- Extended federation protocol v2 with immutable path-sanitized token-usage revisions while preserving v1 bundle reads and read-only peer replicas.
- Added hash-bound capability admission, full release rehearsal, architecture ownership, and documentation contracts for federated daily metrics.

## 2.11.6 - 2026-08-01

- Fixed Windows upgrade activation under Codex sandbox launchers by preferring
  a validated explicit Skill root over the process SID profile.
- Made desktop-shortcut replacement self-verifying and rollback-capable, and
  added exact post-install checks for target, working directory, icon,
  arguments, launcher configuration, and target existence.
- Extended the runtime-effect and release rehearsal gates so a present but
  misdirected `.lnk` is a release failure rather than false activation health.

## 2.11.5 - 2026-08-01

- Removed the collector's legacy synchronous AI execution path so capture only
  persists semantic work and the independent maintenance scheduler owns it;
  sustained history recovery now yields the shared lock between batches so
  maintenance and backup work cannot be starved.
- Connected automatic Level-2+ summary creation, normalized legacy Windows job
  paths, repaired derived-index holes, and exposed permanent debt as attention.
  Operators can explicitly requeue one quarantined job only after its previous
  hash, attempts, and redacted failure are preserved in an immutable receipt.
  Level-1 prompts now carry an explicit citation allow-list so models copy exact
  message IDs while the existing out-of-scope rejection remains fail closed.
- Bound semantic indexes to the current raw-source snapshot and made stale
  semantic search fail closed or explicitly fall back to keyword retrieval.
  Full local E5 builds now receive a bounded one-hour worker window and remove
  partial vectors on timeout instead of failing at the query-sized limit.
- Cleaned interrupted backup directories, lengthened the Windows maintenance
  execution budget, corrected cloud waiting/partial-failure accounting, and
  added rollback-safe configuration-default migration. Legacy committed
  Environment receipts now upgrade only from matching state, ledger,
  transaction, and output-hash evidence.
- Added a shared runtime-effect gate and fixed rehearsal cases that reject
  hidden fallback, stale waterlines, orphan backups, false success, and missing
  parent-summary work even when processes exit successfully.

## 2.11.4 - 2026-08-01

- Fixed the Windows semantic-runtime availability probe so a configured
  `~/.codex/...` executable is launched through its expanded absolute path.
- Preserved the redacted runtime failure in maintenance state and surfaced
  blocked semantic debt as dashboard attention instead of silent backlog.
- Added a live-effect release gate for background executors: a synthetic task
  must move pending `1 -> 0` and summary registry `0 -> 1` under the installed
  scheduler identity; process existence and mocked success are insufficient.

## 2.11.3 - 2026-08-01

- Included excluded and pre-v2.11 cursors in one-time metadata convergence when
  completion, byte-offset, observed-size, or mtime fields are absent, instead
  of permanently skipping them as already synchronized.

## 2.11.2 - 2026-08-01

- Made every successfully verified source converge legacy cursor identity and
  completion metadata even when no new line is appended, allowing zero-byte
  coverage debt to reach a stable covered state.

## 2.11.1 - 2026-08-01

- Kept global coverage projection ownership with startup and event loops that
  hold the complete activation scope, preventing one changed rollout from
  replacing the 213-source catch-up view with a false one-source healthy view.

## 2.11.0 - 2026-08-01

- Added upgrade-safe collector activation state so reinstalls never move the
  retained-source coverage boundary later or abandon a rollout without a
  completed cursor.
- Replaced whole-file native recovery with bounded streaming batches, durable
  byte-offset cursors, partial-line handling, and explicit coverage debt.
- Added a fail-closed pre-resume repair gate so a process interruption between
  raw append and derived-state commit cannot change round linkage on replay.
- Added a hidden five-minute maintenance supervisor for persistent mechanical,
  semantic, and backup-debt convergence while preserving model-free capture.
- Added hash-bound resumable map-reduce summary plans with strict 900,000
  character and UTF-8 byte prompt budgets and a 16-call ceiling.
- Split dashboard health into coverage, mechanical, semantic, and backup debt;
  recoverable backlog reports catching-up instead of a false integrity alarm.
- Fixed macOS Skill ZIP verification so Apple's fixed `/var`, `/tmp`, and
  `/etc` aliases are accepted only when they resolve to their exact `/private`
  targets; arbitrary package-path links remain rejected.

## 2.10.0 - 2026-08-01

- Added deterministic, path-free personal Environment profiles for explicit
  global Rule files and user-managed, system-bundled, or plugin-managed Skills.
- Added immutable predecessor-linked profile generations, preview-first capture,
  atomic current pointers, pointer reconstruction, and duplicate suppression.
- Added trusted `environment-v1` transport into read-only per-peer profile
  replicas with replay, target, integrity, and conflict checks.
- Added all six cross-platform comparison outcomes and bounded convergence plans
  that preserve provider ownership and never authorize automatic activation.
- Added CLI and localized dashboard profile views plus fixed v2.10 determinism,
  redaction, failure-injection, exchange, comparison, and no-installer cases.

## 2.9.0 - 2026-08-01

- Added one bounded provenance-aware read service shared by local CLI,
  loopback HTTP, and an allow-listed MCP stdio adapter.
- Added strict malformed, over-broad, unavailable-source, stale-index, and raw
  verification behavior with explicit confidence and provenance fields.
- Added stable, beta, and development update selection plus verified delta to
  full-package fallback; release metadata uses a pinned Ed25519 SSH signer and
  downloaded artifacts remain inert until a second version-and-hash-bound
  approval authorizes installation.
- Added a deterministic summary-budget scheduler that queues only completed
  dialogue rounds and never invokes AI during eligibility checks.
- Added fixed v2.9 interface parity, provenance, update fallback, corruption,
  approval, scheduler threshold, and no-AI rehearsal cases.

## 2.8.0 - 2026-08-01

- Added a removable exact-byte shadow content store addressed only by SHA-256,
  with closed ordered manifests recording stable source identity, path, byte
  length, and whole-file hash.
- Added preview-first exact reconstruction that refuses conflicting targets and
  verifies every reconstructed file without changing authoritative sources.
- Added independent archive/environment transfer streams with durable ordered
  checkpoints, bounded ranges, exact resume, idempotent replay, corruption
  rejection, and explicit gap/overlap/conflict errors.
- Added preview-first CLI adapters for shadow build, status, verification,
  reconstruction, disable, and local resumable transfer.
- Added fixed v2.8 exact-byte, interruption, duplicate, corrupt, isolation,
  conflict, tamper, and rollback rehearsal cases.

## 2.7.0 - 2026-08-01

- Added a persistent model-free maintenance queue with idempotent delivery,
  explicit leases, bounded retries, restart recovery, and quarantine.
- Added desired-versus-actual collector, queue, and semantic-worker state plus
  redacted diagnostic bundles that exclude raw dialogue and local user paths.
- Routed due semantic work through a durable completed-round eligibility gate
  before the existing one-shot worker, while keeping native capture independent.
- Preserved coalesced backup debt handling and restored the v2.6 rebuild-index
  desktop-backup contract after detecting a displaced code block.
- Added fixed v2.7 crash, stale-lease, permission, capture-independence,
  round-boundary, redaction, and no-unrequested-AI rehearsal cases.

- Restored the authoritative ordered v2.6-v3.0 execution roadmap and reserved
  review-first personal Environment convergence for v2.10.
- Required predecessor evidence, bounded work contracts, exact-candidate
  cross-platform gates, and rollback proof before each future release.
- Kept v3.0 conditional on a separately accepted incompatible public-contract
  decision rather than roadmap completion.

## 2.6.0 - 2026-08-01

- Added immutable shadow index generations with deterministic identities,
  exact source manifests, closed schemas, and payload hash verification.
- Added preview-first atomic activation and pointer-only rollback while
  retaining prior generations and leaving raw history byte-identical.
- Added a fixed versioned retrieval benchmark for policy lineage, exact
  disambiguation, corpus hashing, and unexplained-delta rejection.
- Extracted deterministic index reconstruction into the Memory Plane without
  changing the existing rebuild CLI or persisted index bytes.
- Added dedicated Windows/macOS release-rehearsal contracts for generation
  integrity, retrieval evaluation, CLI preview behavior, and rollback.

## 2.5.2 - 2026-07-31

- Removed duplicate feature-branch push and pull-request CI matrices; only
  `main` is validated on push, while feature branches use cancellable PR runs.
- Replaced twelve serial Windows shard jobs with one bounded candidate job.
- Run each platform's full unittest suite at most once and let rehearsal
  scenarios retain SHA-256-bound references to that successful evidence.
- Kept the full same-SHA `main` gate before installer publication while making
  the installer workflow consume, rather than repeat, candidate validation.
- Bound the optimized release lifecycle into Agent rules, Skill instructions,
  product architecture, decisions, rehearsal documentation, and all localized
  READMEs so it follows the repository across devices.

## 2.5.1 - 2026-07-31

- Rebuilt the checked-in Windows collector from the current Rust sources so
  its reported version matches the product version.
- Restored a non-optional checked-in native-version regression test on
  supported desktop platforms.
- Made both installer jobs execute the freshly built collector and envelope
  before packaging, and made the Windows job prove that all three native
  executables are included in the installer payload.
- Wait for newly published cloud envelopes to become visible through their
  returned display path before reporting a successful sync.
- Reserve Windows extended-path prefixes for paths near the legacy path-length
  boundary, avoiding short-path file-provider rename anomalies while retaining
  deep OneDrive folder support.
- Return an operable native `path` for long Windows cloud envelopes while
  retaining a prefix-free `display_path` for dashboards and logs.
- Run the macOS symlink contract on macOS CI instead of requiring Windows
  developer-mode symlink privileges during the Windows rehearsal.
- Retained the stable-line dashboard SSE startup-race, Windows native cloud
  path, and agent-rule newline fixes without merging the obsolete beta branch.

## 2.5.0 - 2026-07-30

- Restricted the supported main runtime to Python 3.14.x and removed the
  redundant Python 3.9-3.14 full-suite compatibility matrix. macOS, Windows,
  and Ubuntu release gates now validate the same Python 3.14 contract.
- Kept the separately pinned Python 3.12 semantic-model environment unchanged;
  it remains an isolated provider runtime rather than a supported main runtime.
- Added a closed configuration-v1 compiler with deterministic hashes,
  per-value source provenance, preserved archive-root precedence, and
  fail-closed duplicate, unknown-key, type, range, and relationship checks.
- Added stateless read-only CLI commands for effective configuration
  compilation, source explanation, and device capability diagnostics without
  archive initialization or locking.
- Added closed path-free capability offers and deterministic compatibility
  reason codes. Legacy peers continue through existing synchronization, and
  negotiation grants no installation, trust, permission, or sync authority.
- Added a localized read-only System dashboard view for configuration and
  local capability diagnostics.
- Recorded memory sharing scopes as a future decision trigger only; no runtime
  fields, migration, filtering, enforcement, or dashboard controls were added.

## 2.4.7 - 2026-07-30

- Changed routine macOS updates to extract the verified Skill payload from the
  published PKG and run the existing user-space transaction instead of waiting
  for the privileged platform installer.
- Kept full PKG installation for first install and recovery while preserving
  active archives, device configuration, stable executable paths, isolated
  candidate probing, live post-switch checks, and automatic rollback for
  routine updates.
- Made the auto-update LaunchAgent pass the stable Python entry path through to
  the transaction, avoiding version-specific Homebrew Cellar identities.

## 2.4.6 - 2026-07-30

- Added a content-addressed `global-runtime-contract` Environment artifact for
  the optional multilingual E5 capability. It synchronizes the pinned model,
  artifact hashes, runtime packages, embedding interface, and installer entry
  across macOS, Windows, and Linux without copying platform virtual
  environments, credentials, models, or semantic indexes.
- Added preview-first CLI flows to register, inspect, and explicitly realize
  the shared semantic runtime contract. Incoming contracts remain
  `pending-review`; registration and cloud transport never install or download
  the model automatically.
- Made the E5 installer, ONNX worker, installation validator, and semantic index
  manifest consume one canonical contract, while preserving compatibility with
  already verified local installations.
- Reduced first-time semantic index construction from repeated whole-file raw
  scans to one precomputed raw-pointer pass, with regression coverage proving
  each raw file is read once.

## 2.4.5 - 2026-07-30

- Added recoverable Environment import transactions so incomplete bundles stay
  invisible and interrupted cursor/receipt commits can be retried safely.
- Serialized manual and scheduled cloud synchronization while keeping
  Environment import/export locks scoped to their write transactions, avoiding
  nested-lock deadlocks, and reporting both stream outcomes independently.
- Made dashboard cross-origin rejection consume bounded request bodies before
  returning JSON 403, preventing intermittent Windows connection-reset results.
- Prevented no-change Environment items from starving newer incoming work and
  preserved deterministic evidence for partial batch processing.
- Unified product and native binary version reporting, tightened the Windows
  PyYAML 6.x check, and repaired Windows legacy cloud-path rehearsal coverage.
- Extended path-escape protection from symbolic links to Windows directory
  junctions so ordinary-user reparse points fail closed across Registry,
  binding, and Rule installation paths.
- Expanded cross-platform release gates for version consistency, failure
  injection, Python compatibility, bounded cloud-sync tests, and portable
  version-bound rehearsal evidence whose hashes are revalidated before a pass
  is reported.
- Preserved stable macOS Python entry paths in every generated background
  definition instead of resolving Homebrew symlinks to version-specific Cellar
  executables that can repeatedly trigger Desktop and Documents permission
  prompts after upgrades.
- Added a user-space macOS update transaction that stages a candidate, proves
  end-to-end capture in an isolated archive, switches only after the probe
  passes, verifies the new collector and dashboard, and restores the previous
  Skill, plist, and collector on any post-switch failure.
- Prevented a due AI summary from blocking collector startup and transactional
  cutover. Startup persists the source-locked summary job but leaves execution
  to the existing semantic-backfill worker after the collector becomes ready.
- Replaced synchronous full-archive backup copies in the native capture path
  with an atomic, coalescing backup-debt record. The low-frequency maintenance
  worker creates one complete verified snapshot for all pending mutations and
  clears the debt only after success; the dashboard exposes pending debt.
- Extended native collector telemetry with explicit `starting` and `ready`
  phases, an idle heartbeat, and independent source and archive watermarks. The
  dashboard now distinguishes live startup from stale telemetry, a stopped
  collector, and retained source data ahead of the archive.
- Added deterministic report-cutoff waterline verification and bounded native
  backfill so reports can refuse stale archives before reading summaries.
- Added an accessible localized hover and keyboard-focus bubble to each daily
  archive bar showing its full date, exact archived-message count, and exact
  visible-character count.

## 2.4.4 - 2026-07-30

- Fixed macOS OneDrive Files On-Demand handling for encrypted archive and
  Environment envelopes. Visible but locally unavailable placeholders now
  trigger bounded hydration and remain transient instead of being
  misclassified as corrupt.
- Added verified Environment overlap recovery for a sender that republishes a
  wider range from an earlier cursor. The receiver advances only when every
  persisted prefix event matches exactly; conflicting overlap still fails
  closed.
- Preferred the widest newest valid Environment envelope when duplicate
  candidates share the expected start, while preserving exact replay for lost
  acknowledgement recovery.
- Isolated `environment-v1` status history from `archive-v1` logs so the
  dashboard and CLI no longer report archive imports as Environment activity.

## 2.4.3 - 2026-07-30

- Added a machine-readable module ownership registry and fail-closed
  architecture gate. Every production file must have exactly one canonical
  owner, and declared prohibited dependencies now fail tests and release
  rehearsal.
- Required both Windows and macOS release packages to contain `SKILL.md`,
  `AGENTS.md`, the canonical product architecture, the module ownership
  registry, and the architecture checker.

## 2.4.2 - 2026-07-30

- Fixed Windows dashboard cross-application port capture. The native Memory
  Wuxian launcher now asks Windows for an unused loopback port and opens the
  actual assigned port instead of assuming `127.0.0.1:8765`.
- Kept the storage-organizer audit console on its independent fixed API port;
  launching either desktop application can no longer display the other
  application's interface.
- Added a launcher regression test that forbids restoring the fixed 8765 port.

## 2.4.1 - 2026-07-30

- Fixed batch Environment export so every artifact has a stable per-item
  source identity instead of collapsing on a shared transaction event ID.
  Existing 2.4 ledgers migrate without rewriting Registry history.
- Added read-only transport for project registrations. Peer project metadata
  never creates, binds, or activates a local project automatically.
- Replaced scalar-only Skill metadata parsing with PyYAML's safe loader.
  Nested mappings, lists, and block scalars are accepted; unsafe tags and
  duplicate keys fail closed. `default_prompt` is optional.
- Added PyYAML 6.x dependency checks and regression coverage for batch export,
  old-ledger migration, project replication, and safe nested YAML.

## 2.4.0 - 2026-07-29

- Added the optional offline `intfloat/multilingual-e5-small` semantic provider
  with 384-dimensional multilingual embeddings and retained `local-hash-v1` as
  the no-download default.
- Added a dedicated isolated ONNX runtime and installer pinned to an immutable
  model revision, exact artifact sizes, and SHA-256 values. Inference is forced
  offline and remote model code is disabled.
- Bound the Windows E5 runtime to Python 3.12 so worker, model, and semantic
  matrix paths remain Unicode-safe when the archive or Skill path is Chinese.
- Added a three-language dashboard Memory search view with keyword, semantic,
  and hybrid modes backed by the same verified retrieval engine as the CLI.
  Results retain readable source text, titles, timestamps, raw line ranges,
  and SHA-256 evidence; tool activity remains searchable but ranks below
  matching user and assistant dialogue.
- Kept semantic vectors disposable and outside human-readable metadata while
  preserving message IDs, raw line ranges, and raw-record SHA-256 backlinks.
- Built and verified the current local archive index without rewriting existing
  raw conversation or summary bytes.

## 2.3.0 - 2026-07-29

- Added deterministic governance-AI discovery, micro-batching, and a one-shot
  ephemeral Codex worker for product retrospectives, reusable-lesson
  extraction, governance classification, and supersession review.
- Added count/age/size limits, urgent bypass, daily run limits, evidence
  hashes, source-device ownership, and an explicit cross-device coordinator.
- Kept all AI outputs as schema-validated drafts requiring human review; the
  worker cannot accept rules, install Skills, remediate products, or rewrite
  archives.
- Added CLI, separate scheduler, dashboard controls, retry isolation, tests,
  and release-gate coverage. The feature remains disabled by default.

## 2.2.0 - 2026-07-29

- Added immutable product evolution records for bounded development history,
  verified current state, corrected future flow, and reusable lesson candidates.
- Added signed, encrypted Environment transport into read-only peer replicas;
  receipt never implies remediation or governance acceptance.
- Defined the deterministic scheduler versus bounded AI semantic-review
  boundary for recurring product retrospectives.

## 2.1.0 - 2026-07-29

- Added immutable governance-insight proposal envelopes to the independent
  `environment-v1` stream so verified local product lessons can be shared
  across paired devices without being accepted or installed automatically.
- Kept imported proposals in read-only peer replicas, separate from incoming
  Rule and Skill installation staging.
- Added explicit proposal preview, persistence, listing, origin, content-hash,
  idempotency, conflict, and tamper checks.
- Documented the boundary between Memory無限 transport and
  `work-system-governor` classification, validation, and acceptance.

## 2.0.5 - 2026-07-29

- Correct the macOS release gate to expand the built package into a
  not-yet-created destination before checking its relocation metadata.

## 2.0.4 - 2026-07-29

- Mark the packaged macOS dashboard bundle as non-relocatable so Installer
  leaves it inside the Skill payload until the post-install transaction copies
  the verified application to the user's Desktop.
- Add release and regression gates that reject a macOS package when bundle
  relocation is still enabled.

## 2.0.3 - 2026-07-29

- Passed URL-safe signing public keys to the native envelope helper with an
  unambiguous `--option=value` argument so keys beginning with `-` cannot be
  parsed as command-line options.
- Retained failed release-rehearsal evidence on every platform for faster,
  source-grounded diagnosis.

## 2.0.2 - 2026-07-29

- Made the cloud-root normalization regression tests compare Windows native
  long-path representations correctly without changing runtime behavior.

## 2.0.1 - 2026-07-29

- Added a canonical product-architecture contract for module ownership,
  dependency direction, application contracts, refactoring gates, and the
  ordered modular-monolith roadmap.
- Normalized cloud-folder configuration when a user selects either the cloud
  provider root or its existing `MemoryWuxianExchange` child, preventing paired
  devices from silently scanning different nested queue roots.

## 2.0.0 - 2026-07-29

- Added an independent Environment Registry for global and project Rules and
  Skills without changing the authority or write ownership of conversation
  archives.
- Added node-local bindings, immutable revisions, verified Skill packages,
  append-only receipts, rollback objects, and crash-safe Rule and Skill
  installation.
- Added a separate signed and target-encrypted `environment-v1` cloud stream
  with its own event sequence, predecessor chain, cursors, acknowledgements,
  staging area, and package cache.
- Added deterministic, model-free incoming processing. Compatible global Rule
  fast-forwards may be registered only under an explicit policy; project
  artifacts, Skills, divergence, identity changes, and permission expansion
  always require review.
- Added three-way conflict assessment, explicit conflict resolution, and
  evidence-gated project-to-global capability promotion.
- Added Environment inventory, incoming-update status, conflict and promotion
  state, and a manual update check to the desktop dashboard.
- Kept each device's local archive independently writable. Remote conversation
  history remains a verified read-only replica and environment exchange never
  rewrites raw history.

## 1.13.0 - 2026-07-28

- Added persistent per-conversation ledgers for top-level Codex-reported model
  usage, with incremental native collection and preview-first historical
  backfill from retained rollout files.
- Added reset-safe cumulative accounting, duplicate-snapshot request
  deduplication, subagent exclusion, and explicit prevention of cached-input or
  reasoning-output double counting.
- Added global and per-conversation reported usage to the dashboard, together
  with reported-Token achievements and clear separation from archive-text
  estimates and billing usage.
- Added Python/Rust parity, idempotency, archive-invariant, documentation, and
  desktop release-gate coverage for the new telemetry layer.
- Made the macOS dashboard build verify its version directly from `Info.plist`,
  avoiding false failures for localized application paths.

## 1.12.0 - 2026-07-28

- Added a versioned native macOS `Memory無限操作台.app` backed by WebKit and a
  configuration file containing the current Python, Skill, and active archive
  paths instead of machine-specific paths compiled into the application.
- Made every macOS PKG install or upgrade preserve the active archive and
  atomically replace and verify the desktop dashboard application.
- Added dashboard version, signature, executable-hash, path, and self-check
  validation to the release contract so dashboard-affecting updates cannot
  complete without refreshing the desktop launcher.
- Suppressed harmless localhost broken-pipe tracebacks when a dashboard client
  disconnects before a response finishes.

## 1.11.0 - 2026-07-28

- Added human-readable JSONL retrieval evaluation with recall-at-k, wrong
  citation counts, and per-case latency.
- Added an optional offline `local-hash-v1` semantic index with no model
  download, network call, or external service.
- Added verified backlinks from every semantic hit to conversation/message ID,
  raw path, exact raw line range, and raw-record SHA-256.
- Added a semantic-index clear operation that leaves raw history and keyword
  retrieval intact.

## 1.10.0 - 2026-07-28

- Added timezone-strict, read-only `as-of` historical views.
- Added a derived decision and rule graph from explicit policy events, including
  source message IDs, raw paths, record hashes, and supersession edges.

## 1.9.0 - 2026-07-28

- Added a preview-first archive migration wizard. Migration copies into a
  temporary destination, compares source-before, source-after, and destination
  SHA-256 manifests, never deletes the source, and switches the active pointer
  only with an explicit request after verification.
- Added human-readable project memory package export and verified read-only
  package import outside local raw authority.
- Added executable archive red-line tests and version-bound rehearsal reports.

## 1.8.0 - 2026-07-28

- Remove the legacy encoded PowerShell collector loop and register the native
  collector directly through Task Scheduler or the current-user Run key.
- Apply a shared no-console policy to Windows child processes.
- Add an append-only dashboard event journal and localhost SSE status stream,
  with low-frequency polling only as a disconnect fallback.
- Add project, source, and origin-device conversation filters.
- Add an auditable release-rehearsal gate with one hashed evidence log per
  required scenario.

## 1.7.11 - 2026-07-27

- Prevent dashboard startup, refresh, and Settings status reads from creating
  visible Windows console processes or stealing foreground focus.
- Return the last persisted dashboard snapshot immediately, rebuild stale
  archive statistics in the background, and keep manual refresh authoritative.
- Animate changed metrics, timeline bars, and summary progress while preserving
  a reduced-motion-compatible, readable dashboard.
- Open Settings before refreshing remote state, normalize command-button text,
  and vertically center switch thumbs.
- Run Windows cloud synchronization and automatic update checks without visible
  consoles, while keeping the desktop shortcut bound to the native launcher.
- Use Windows extended paths only for long cloud-envelope file operations,
  without passing extended paths to Python or native process launchers.
- Reaffirm that Windows installation and upgrade recreate the native dashboard
  shortcut by default.

## 1.7.10 - 2026-07-27

- Preserve validated ordinary Windows paths in the native dashboard launcher
  instead of converting them to `\\?\` extended paths before invoking Python.
- Prevent `pythonw.exe` from immediately exiting when the installed Skill or
  archive path contains non-ASCII characters.

## 1.7.9 - 2026-07-27

- Resolve the real Windows profile from the current user's SID and
  `ProfileList` before running post-install activation.
- Prevent installer processes launched from Codex isolation from recreating
  the native dashboard shortcut with a `CodexSandboxOffline` target.

## 1.7.8 - 2026-07-27

- Replace the Windows dashboard shortcut's direct `pythonw.exe` script target
  and long argument string with a dedicated no-console native launcher.
- Keep the desktop shortcut argument-free and store the validated Python and
  active archive paths in a local launcher configuration under `.codex`.
- Validate launcher paths before starting the localhost dashboard, reducing
  the shortcut pattern that triggered heuristic antivirus detection.

## 1.7.7 - 2026-07-27

- Resolve the real Windows user profile from the installed Skill path instead
  of trusting a launcher or sandbox process `USERPROFILE`.
- Preserve the real active archive pointer and Codex sessions directory when
  an installer is launched through an isolated desktop client environment.

## 1.7.6 - 2026-07-27

- Rebuild `Memory无限状态台.lnk` during every Windows install or automatic
  upgrade so stale Codex runtime paths cannot break native dashboard startup.
- Bind the shortcut to the Python runtime that passed the current Windows
  bootstrap and install the required `pywebview` dependency before creating it.
- Preserve the existing active archive root during Windows upgrades instead of
  silently reverting the collector and dashboard to the default archive.
- Remove the dashboard shortcut during uninstall without deleting the archive.

## 1.7.5 - 2026-07-26

- Make redirected Windows CLI output explicitly UTF-8 even when Python inherits
  a legacy code page through `PYTHONIOENCODING`.
- Keep interactive legacy Windows consoles non-fatal by escaping only
  characters their active code page cannot represent.
- Add a Windows regression test that emits `¥`, Japanese, and Chinese text
  while the child process is forced to inherit GBK.

## 1.7.4 - 2026-07-25

- Add a machine-readable documentation contract covering every public CLI
  command and major non-CLI feature across the English, Simplified Chinese, and
  Japanese READMEs.
- Block pull requests with functional changes unless all three READMEs,
  `CHANGELOG.md`, and the feature contract change together.
- Block installer releases when the documentation contract is incomplete or
  the current package version is absent from the changelog.
- Document the previously omitted conversation recovery, title registration,
  semantic-summary maintenance, rebuild, and federation-status commands.

## 1.7.3 - 2026-07-24

- Stop treating normal out-of-order completion across concurrent conversations as a dashboard health warning.
- Keep dashboard attention status reserved for actionable integrity, warning, or failed-job fields.
- Invalidate the previous dashboard snapshot so the corrected health status appears immediately without changing archive or summary data.

## 1.7.2 - 2026-07-24

- Audit Level-1 summaries by raw-message range and higher summaries by direct child-summary identity, eliminating false overlap reports from interleaved historical backfill.
- Sort future parent-summary candidates by source chronology instead of completion-assigned summary ID.
- Upgrade `age` to 0.12.1, removing the transitive `proc-macro-error2` future-incompatibility warning while preserving encrypted-envelope tests.
- Rebuild stale derived round state from immutable raw records before resuming semantic backfill.

## 1.7.1 - 2026-07-24

- Restore the documented bounded semantic-backfill batch entrypoint.
- Process queued higher-level summaries before Level-1 debt, stop on the first failed job, and create one verified snapshot after a successful batch.

## 1.7.0 - 2026-07-24

- Add strict source-cited policy events to Level-1 semantic summaries.
- Derive append-only policy lineage with active, superseded, withdrawn, proposed, conflict, and unresolved states.
- Add `retrieve --mode current-policy` to restore policy lineage, cited raw messages, and newer matching evidence.
- Add current-policy guidance to runtime context capsules without rewriting historical summaries.
- Rebuild and audit global and per-conversation policy indexes alongside existing derived indexes.
- Keep legacy summary jobs and summary files readable by defaulting missing policy events to an empty array.

## 1.6.3 - 2026-07-24

- Add complete Simplified Chinese and Japanese README translations.
- Add language navigation between English, Simplified Chinese, and Japanese documentation.
- Require all three README files to remain synchronized whenever documented behavior changes.

## 1.6.2 - 2026-07-24

- Add an experimental ChatGPT official-export importer to Dashboard Settings.
- Stream selected ZIP or JSON exports only through the localhost server and remove the temporary upload after import.
- Reuse the existing visible-branch, stable-ID, duplicate-safe importer and verified backup flow.
- Document that synthetic fixtures are tested but no real user-provided ChatGPT export has been validated yet.

## 1.6.1 - 2026-07-24

- Add explicit cloud-sync controls to the dashboard Settings panel.
- Let the dashboard enable or disable both encrypted exchange and its five-minute scheduler.
- Add a one-click forced encrypted synchronization action and display the configured provider directory and scheduler state.
- Restrict dashboard mutation requests to local same-origin JSON calls.

## 1.6.0 - 2026-07-23

- Add encrypted cloud-folder federation alongside direct SSH synchronization, using user-selected iCloud Drive, OneDrive, or compatible synchronized directories without handling provider credentials.
- Preserve `.mwxb` as the inner delta protocol while signing each cloud payload with the origin device's Ed25519 identity and encrypting it to the receiving device with age/X25519.
- Keep private identities on their owning devices and store only public encryption keys, public signing keys, and fingerprints in trusted-peer records.
- Add single-writer per-node outboxes, signed encrypted acknowledgements, stop-and-wait delivery, idempotent imports, transient placeholder handling, and sender-owned retention cleanup.
- Add short-lived five-minute cloud synchronization tasks for macOS and Windows, with a fifteen-minute merge window, one-megabyte early flush, sixty-minute maximum pending interval, and manual immediate synchronization.
- Bound macOS `kqueue` rollout watches to the 64 most recently modified files while retaining directory events and adaptive metadata fallback, preventing long histories from exhausting file descriptors.
- Skip unchanged rollout files during collector startup by comparing their persisted source-size and modification-time cursors, while still importing new or changed files.
- Log compact startup-stage counts without conversation content so collector catch-up delays can be diagnosed from the LaunchAgent log.
- Keep Windows scheduler output ASCII-safe for paths containing non-Latin characters and tolerate platform-specific limits when tests inject pre-epoch local timestamps.
- Keep macOS LaunchAgent installation testable from Windows by resolving the launchd user domain without assuming `os.getuid()` exists.
- Use short same-directory temporary envelope names before atomic publication so long Windows exchange paths remain supported.
- Keep native local collection, immutable archives, read-only peer replicas, SSH host authentication, global retrieval, and summary behavior unchanged.

## 1.5.0 - 2026-07-23

- Add stable Memory無限 node identities and explicit trusted-peer registration without reusing OpenAI account sessions or Codex credentials.
- Add artifact-ledger-based `export-delta`, `inspect-bundle`, and `import-delta` workflows for idempotent `.mwxb` exchange.
- Store imported history as read-only peer replicas in the default sibling `<archive>-federation-cache`, leaving each local archive under exclusive local write authority.
- Validate artifact SHA-256, event-sequence gaps and overlaps, target and origin nodes, and predecessor bundle SHA-256 continuity before import.
- Bound large exports into contiguous pages and reconstruct the export-state cache from the append-only artifact ledger after interrupted writes.
- Contain untrusted summary identifiers with hashed replica filenames, reject nested replica roots, preflight peer trust, and bound SSH execution time.
- Add reconstructible global indexes, `retrieve-global`, federation status, and peer revocation.
- Add SSH peer pull with strict host-key checking and `posix` or `powershell` remote command construction.
- Document that SSH encrypts and authenticates transport, while offline `.mwxb` bundles are not encrypted or signed and must use a trusted channel.
- Exclude reconstructible peer replicas from desktop primary-archive backups.

## 1.4.2 - 2026-07-23

- Add archive-file-size achievements using the actual persisted raw, conversation, summary, index, and state files.
- Add separate archive-context and user/assistant-message token-estimate achievements so visible tool activity does not inflate the message-only track.
- Add objective dialogue-depth, per-project growth, raw-verified retrieval, and cross-file retrieval achievements without importance scoring.

## 1.4.1 - 2026-07-23

- Add progressive higher-level summary achievements for L2 through L8, with quantity milestones scaled to the increasing cost of each hierarchy level.

## 1.4.0 - 2026-07-23

- Add deterministic title-targeted conversation tails: resolve all known Codex title aliases to one archived conversation before selecting its latest visible messages, and fail on missing or ambiguous titles instead of falling back to the newest conversation.
- Persist user-confirmed conversation-title aliases and allow historical title lookup to exclude the active task ID, preventing a newly auto-titled task from capturing its own history request.
- Separate active and archived Codex conversations in the status console, keep archived history fully retrievable, and group both views by the Codex project name with project-root fallback.
- Hide summary levels that do not yet exist, instead of displaying empty higher-level rows.
- Add a local achievement system with archive, message, summary, time-span, and project milestones; existing milestones are silently initialized and only newly crossed milestones trigger optional animations and bottom-right notifications.
- Add local dashboard settings for achievements, milestone effects, notifications, compact mode, default conversation view, and automatic refresh interval.
- Persist a derived dashboard status snapshot guarded by archive and Codex metadata fingerprints, so unchanged archives open without rereading all historical source text.
- Render the last successful dashboard response from browser-local storage immediately, then refresh it from the verified local API in the background.
- Recover automatically from missing, stale, or malformed status snapshots by rebuilding them from authoritative archive records.

## 1.3.0 - 2026-07-21

- Add a daily updater for stable GitHub Releases with strict semantic-version checks and SHA-256 verification.
- Stage verified Windows updates for silent installation at the next login and retain macOS packages until system installation authorization is available.
- Register the updater through Task Scheduler with a per-user login fallback on Windows and a daily LaunchAgent on macOS.

## 1.2.1 - 2026-07-21

- Run read-only context refresh status and capsule generation without opening the archive write lock, so Codex tasks with read-only archive access can refresh context normally.

## 1.2.0 - 2026-07-21

- Add single-file macOS PKG and Windows EXE installers that install the Skill, initialize an external archive, and activate continuous Codex capture.
- Preserve an existing archive and local configuration during reinstall or upgrade, and leave conversation history intact during uninstall.
- Add a tag-driven GitHub release workflow that builds platform-native collectors, installers, and SHA-256 checksum files.
- Allow the macOS LaunchAgent installer to persist the Python and Codex CLI paths detected by the package installer.

- Encode Level-1 source messages and higher-level child summaries as locally verified, reversible tabular model payloads to reduce repeated prompt structure without changing source text, order, provenance, or hashes.
- Allocate higher-level summary IDs across both persisted summaries and pending jobs to prevent parent-job collisions during backlog processing.
- Resolve current Codex task titles through the bundled macOS CLI and native thread title field before falling back to the first user message.

## 1.1.0 - 2026-07-19

- Import ChatGPT official data-export ZIPs, extracted directories, or `conversations.json` files into the same immutable archive.
- Follow each conversation's current visible branch, preserve exported titles and stable source IDs, and keep repeat/update imports idempotent.
- Exclude system messages and abandoned regenerated-answer branches while retaining user and assistant text plus source metadata.
- Show exported ChatGPT titles in the status console and include imports in normal indexing, backup, summary, and retrieval flows.

## 1.0.3 - 2026-07-19

- Add atomic collector runtime telemetry for mode, fallback interval, recent file/archive activity, wakeups, and process identity.
- Show collector activity plus CPU and memory in the Chinese, English, and Japanese status console.
- Add `psutil` to the optional native-dashboard dependencies for cross-platform process metrics.

## 1.0.2 - 2026-07-19

- Adapt macOS and Windows metadata safety checks from 5 seconds while active to 30 seconds after 2 idle minutes and 5 minutes after 15 idle minutes.
- Wake immediately on native filesystem events and return to active mode as soon as Codex writes a session file.

## 1.0.1 - 2026-07-19

- Archive successful structured file changes with per-file operation types, move targets, exact unified diffs, hunk line ranges, and addition/deletion totals.
- Backfill historical patch events once on upgrade without duplicating existing conversation messages.
- Keep general tool output and hidden reasoning excluded while making applied edits independently verifiable.
- Record the active Windows archive root during collector installation and resolve it automatically for CLI retrieval and maintenance commands.
- Preserve explicit `--root` and `MEMORY_WUXIAN_ROOT` overrides while preventing silent queries against the Skill template archive.
- Make retrieval genuinely read-only: it no longer takes the archive write lock and tolerates unavailable query-log permissions.
- Add a dedicated Memory Wuxian application icon combining an infinity loop with an archive drawer, including PNG and multi-size Windows ICO assets.
- Apply the bundled icon to the native dashboard window so Windows no longer shows the default Python icon.
- Replace the visible language select with a compact `文/A` icon button and a three-language dropdown menu.
- Add a persistent Chinese, English, and Japanese language menu covering all native dashboard labels, states, tooltips, charts, and footer text.
- Reduce dashboard text density by removing the scope paragraph, keeping archive totals in the footer, splitting per-task archive/tool counts, and showing request/window telemetry as a percentage.
- Prevent the Windows title-refresh subprocess from opening a console window or stealing focus; slow passive dashboard refreshes to 30 seconds.
- Render archived activity as `Ran <command>` or `Called tool: <name>` to match the visible Codex task timeline more closely.
- Archive lightweight tool activity visible in Codex task timelines, including tool names and command text, while continuing to exclude tool outputs and hidden reasoning.
- Separate visible-archive token estimates from Codex model-request telemetry, and stop labeling the latter as precise context utilization.

- Show Codex task titles from the read-only local thread database, falling back to the first user message only when title metadata is unavailable.
- Animate and temporarily disable the manual refresh control while a refresh is in progress.
- Clarify that character totals cover archived visible source dialogue rather than summaries, and replace the ambiguous token count with a CJK-aware context-size estimate that is explicitly not billing or summary-generation usage.
- Add a native Windows dashboard window backed by Microsoft Edge WebView2, preserving the complete existing local UI without browser chrome.
- Detect the optional open-source `pywebview` dependency during Windows bootstrap and install it only when `-InstallMissing` is explicitly selected.
- Add a read-only local status dashboard with per-conversation context utilization, message and round totals, summary levels, daily archive volume, pending work, archived days, visible characters, and estimated tokens.
- Read current context utilization from each rollout's latest `last_token_usage` event and cache file-tail telemetry for sub-second refreshes on large archives.
- Keep all dashboard data local on `127.0.0.1`, refresh every five seconds, and expose the same statistics as JSON at `/api/status`.

## 1.0.0 - 2026-07-19

- Add bounded runtime context refresh that detects completed-round intervals, context utilization stages, and Codex compaction events.
- Build a temporary context capsule from the highest useful semantic-summary levels plus recent dialogue, capped by a configurable context fraction and an absolute 10,000-token ceiling.
- Add `context-refresh-status`, `context-capsule`, and `ack-context-refresh` commands with per-conversation acknowledgement state.
- Ship reusable `AGENTS.md` rules so each installation checks for due refreshes without archiving generated capsules as source dialogue.
- Run the native collector on an explicit 16 MiB stack, fixing Windows stack overflow during full-history imports.
- Validate a fresh Windows import of 15 rollout files, 1,197 visible messages, and 14 deterministic Level-1 indexes.
- Promote the cross-platform append-only archive, hierarchical summaries, verified retrieval, automatic capture, environment bootstrap, integrity checks, and external recovery snapshots to the stable 1.0 contract.

## 0.8.1

- Add a Windows environment bootstrap that reports the exact Python version and discovers Codex-bundled Python and CLI paths before activation.
- Install official Python only when no compatible 3.9+ runtime is available and the user explicitly enables missing-runtime installation.
- Ship the Windows collector binary with the Skill so Rust and MSVC remain development-only dependencies.

## 0.8.0

- Add a Windows-native collector build with Task Scheduler and hidden per-user Run-key fallback while preserving the macOS LaunchAgent.
- Replace Python's Unix-only `fcntl` dependency with equivalent advisory locks on Unix and Windows.
- Keep LF archive serialization and normalized source paths identical across Python, macOS Rust, and Windows Rust implementations.
- Add the five-second metadata fallback to the Windows native watcher and pass explicit Python/Codex executable paths to one-shot semantic jobs.
- Add Windows installer and cross-process lock coverage to the storage-contract test suite.

## 0.7.1 - 2026-07-17

- Replace whole-query substring retrieval with deterministic normalized multi-term ranking across concepts, summaries, routing indexes, and authoritative raw text.
- Exclude every conversation's currently incomplete round from historical matching so the active request cannot satisfy its own lookup.
- Restore neighboring context only from the matched conversation instead of using globally interleaved message positions.
- Return `verified` only when ranked raw records actually matched; index routes alone no longer promote arbitrary source ranges to verified context.
- Add a regression case for the mixed Chinese/English `L +/-51 bp`, `90% identity`, and reciprocal-capture discussion.

## 0.7.0 - 2026-07-17

- Add script-detected summary boundaries triggered by 5 completed rounds or 20,000 visible characters, whichever occurs first.
- Group every 10 deterministic child indexes into the next level without model calls.
- Store exact source ranges, SHA-256, counts, and normalized user/assistant excerpts in global and per-conversation indexes.
- Search deterministic excerpts before returning to raw-text verification.
- Run semantic summarization through an ephemeral Codex CLI worker only after a due round is complete; no AI conversation remains active between summaries.
- Add a five-second macOS metadata fallback so missed deep-directory events are recovered without reading unchanged rollout contents or invoking a model.

## 0.6.2 - 2026-07-17

- Reduce the default Level-1 assignment threshold from 20 completed rounds to 10.
- Preserve existing pending-job source ranges when the threshold changes; the new value applies only to future jobs.

## 0.6.1 - 2026-07-17

- Retain only the newest derived-file recovery backup under `memory/archive/` by default.
- Separate workspace recovery retention from desktop snapshot retention through `backup.workspace_retention_count`.
- Require one replaceable development code backup instead of accumulating timestamped project copies.

## 0.6.0 - 2026-07-17

- Retain only the newest complete external recovery snapshot by default, while keeping the append-only backup operation log.
- Exclude Codex sessions whose native session metadata identifies them as subagent sessions.
- Generate Level-1 and higher-level summary assignments within one conversation only.
- Persist deterministic message, timeline, summary, and concept indexes under a separate directory for every conversation.
- Rebuild global and per-conversation derived indexes together from authoritative raw records and persisted summaries.
- Added an explicit `backup` command that creates a verified snapshot and applies configured retention.
- Recognize both legacy minute-stamped snapshots and current microsecond-stamped snapshots during retention cleanup.
- Cache source-derived message IDs inside each native collector process so full-history imports do not rescan all raw files for every message.
- Select the next 20 eligible completed rounds within each conversation even when global round numbers are interleaved.

## 0.5.1 - 2026-07-17

- Isolated pending user rounds and `reply_to` relationships by conversation ID.
- Added globally unique round allocation with deferred high-watermark advancement for out-of-order conversation completion.
- Marked new round metadata with `round_scope: conversation`; assistant messages without a pending user remain visible but do not complete or allocate a dialogue round.
- Added migration-aware state reconstruction and audit detection for any new cross-conversation reply link while preserving legacy raw records unchanged.
- Added concurrent Python/Rust contract tests covering interleaved conversations and reverse completion order.

## 0.5.0 - 2026-07-16

- Replaced the 15-second Python polling process with a persistent Rust filesystem watcher, using native `kqueue` vnode events on macOS.
- Moved Codex JSONL parsing, raw append, per-conversation transcripts, cursors, deterministic indexes, Level-1 job creation, and desktop snapshots into the native collector.
- Kept the Python CLI for low-frequency summary ingestion, retrieval, reconstruction, and maintenance.
- Added a Python/Rust storage-contract parity test and a native KeepAlive LaunchAgent test.
- Added a shared archive transaction lock so maintenance commands cannot observe a partially committed native event batch.
- Preserved the existing archive schema, source-derived message IDs, round semantics, and backup ordering.

## 0.4.0 - 2026-07-16

- Added one complete Markdown transcript per conversation ID under `memory/conversations/`.
- Added automatic transcript updates during append and Codex synchronization.
- Made idempotent retries restore missing transcript records and create a recovery snapshot.
- Added preview-first `rebuild-conversations` recovery with archived replacement and desktop backup.
- Added heartbeat detection and repair for missing, altered, extra, or cross-conversation transcript content.
- Preserved existing raw records and summary hashes as immutable authority during historical transcript backfill.

## 0.3.2 - 2026-07-16

- Preserve the configured stable Python entry path in the LaunchAgent instead of resolving it to a versioned Homebrew Cellar path.
- Added a symlink-path regression test so Homebrew Python upgrades do not require plist rewrites.

## 0.3.1 - 2026-07-16

- Added explicit LaunchAgent Python executable selection.
- Removed the hard-coded `/usr/bin/python3` runtime, which may resolve to an ungranted Xcode interpreter on macOS.
- Added a plist-generation regression test for the selected interpreter path.

## 0.3.0 - 2026-07-16

- Added incremental parsing of native Codex rollout JSONL files.
- Added stable source IDs and persisted per-session cursors for idempotent synchronization.
- Preserved visible commentary while counting only final answers as completed dialogue rounds.
- Excluded system instructions, internal reasoning, tool calls, and tool outputs from imported dialogue records.
- Added timestamped desktop snapshots and an append-only backup log after successful memory writes.
- Added a macOS LaunchAgent installer for automatic current-and-future Codex session synchronization.

## 0.2.0 - 2026-07-16

- Added SHA-256 integrity fields for raw records, summary sources, and summary files.
- Added source-drift rejection during summary ingestion.
- Added preview-first `rebuild-state` and `rebuild-indexes` commands with archived backups.
- Added heartbeat check-only, maintenance, and repair modes.
- Added overlap, failed-job, index-consistency, state-consistency, and hash checks.
- Added project invariants, decision records, Git data exclusions, and recovery tests.

## 0.1.0 - 2026-07-16

- Added append-only raw conversation storage.
- Added fixed-round Level-1 and fixed-count parent summary jobs.
- Added persistent concept and timeline indexes with raw-backed retrieval.
- Added deterministic CLI, heartbeat validation, secret redaction, and functional tests.
