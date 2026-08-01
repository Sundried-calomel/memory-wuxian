# Release rehearsal gate

## Lifecycle order

Use a release-candidate branch before assigning an immutable formal version.
Run targeted tests while the candidate changes, then run one three-platform
candidate CI matrix after the changes stabilize. A bounded patch may declare
`validation_profile: targeted-patch` and an explicit affected scenario list in
its version work contract; `--contract-profile` and CI then run that impact
matrix instead of unrelated historical suites. Missing, ambiguous, structural,
cross-owner, or platform-expanding scope defaults to the complete gate. A
separate complete local rehearsal is optional when the same commit and platform
are already represented by that matrix. Repair failures without creating
formal tags or installers.

Only a candidate that has passed the required matrix, installer builds,
package-content checks, and applicable live installation rehearsal may be
version-frozen and tagged. The tag triggers one formal build and upload.
Published-artifact defects use a new patch version; pre-publication test,
workflow, packaging, or candidate-artifact defects do not.

An installed device normally consumes the verified user-space update
transaction and does not rerun the full installer. Use the full installer for
first installation, explicit damaged-installation recovery, or a declared
privileged-component change that cannot be applied in user space.

## Evidence gate

A release may be described as fully rehearsed only when
`scripts/run_release_rehearsal.py` produces a report whose `status` is `passed`
and every required scenario has its own report-relative evidence log and
SHA-256. Relative evidence paths keep the report portable across devices and
avoid locale-dependent corruption of absolute Windows paths.

Required scenarios:

1. Python production scripts compile.
2. The native collector is formatted, compiles, and its tests pass.
3. The focused Python regression suites pass.
4. Windows startup definitions contain no PowerShell collector loop and use
   direct no-window process launches.
5. Dashboard status, SSE, filters, cache, and manual-refresh contracts exist.
6. Documentation and package versions agree.
7. The repository diff has no whitespace errors.
8. Guarded feature tests prove that migration preserves the source, project
   imports do not enter local raw history, historical views are read-only, and
   semantic-index deletion leaves authoritative files byte-identical.
9. Desktop-dashboard contract tests prove that both platform installers
   recreate the current dashboard launcher, preserve the active archive, and
   bind the launcher version to the package version.
10. Token-ledger tests prove reset-safe aggregation, subagent exclusion,
    preview-first backfill, idempotency, Python/Rust parity, and dashboard
    exposure without rewriting archive authority.
11. Environment Registry tests prove closed schemas, independent locks,
    immutable revisions, node-local bindings, and unchanged 1.x archive
    authority.
12. Environment exchange tests prove target encryption, origin signatures,
    predecessor-chain continuity, independent cursors, bounded ZIP handling,
    and staging without automatic installation.
13. Incoming-processing, conflict, promotion, and installer tests prove that
    the five-minute task is model-free, no-change is write-free, divergent or
    permission-expanding updates fail closed, and accepted installs remain
    rollback-safe.
14. Dashboard tests and live viewport checks cover Environment inventory,
    incoming status, manual update checks, effective conflict and promotion
    states, desktop layout, and narrow mobile layout.
15. Governance-AI tests prove disabled and no-due checks invoke no model;
    compatible batching, age triggers, urgent bypass, coordinator ownership,
    character and daily limits are deterministic; evidence tampering fails
    before invocation; malformed results retry twice then isolate; valid
    results remain unreviewed drafts; and macOS/Windows schedulers remain
    independent from cloud synchronization.
16. The machine-readable architecture contract proves every production file
    has exactly one module owner and no declared prohibited dependency.
17. macOS runtime-path tests prove generated background definitions retain a
    stable Python entry path and contain no version-specific Homebrew Cellar
    executable.
18. macOS update-transaction tests prove candidate capture before cutover,
    no cutover on probe failure, live collector replacement after success, and
    restoration of the prior Skill, plist, and process after a post-switch
    failure.
19. Collector-health tests prove idle heartbeat renewal, stale-telemetry
    detection, independent source/archive watermarks, and archive-lag warning.
20. Report-preflight tests prove both a covered cutoff and a lagging cutoff,
    with bounded backfill restricted to the exact retained source files.
21. Dashboard interaction tests prove every daily archive bar exposes the
    localized full date, exact message count, and exact character count by
    mouse hover and keyboard focus without changing the bar metric.
22. Collector-startup tests prove initial catch-up persists summary work without
    invoking or awaiting the semantic worker before ready telemetry.
23. Coalesced-backup tests prove native capture records durable debt without
    copying the full archive, maintenance creates one complete snapshot even
    when no summary job is due, debt clears only after success, and the
    dashboard exposes pending debt.
24. Configuration-v1 tests prove closed defaults, duplicate and unknown-key
    rejection, stable canonical hashes, unchanged root precedence, source
    provenance for every effective leaf, and read-only CLI diagnostics on every
    supported platform.
25. Device-capability tests prove closed path-free offers, deterministic
    compatibility reason codes, legacy-peer continuity, and zero installation,
    trust, permission, or synchronization authority.
26. Dashboard System tests prove the supplied configuration path is used,
    `/api/system` is read-only, no archive state is initialized, and localized
    desktop and mobile views expose configuration and capability diagnostics.
27. Bundled-native tests prove both final `bin/` executables exist and report
    the exact current product version, preventing newly versioned source from
    shipping stale collector or envelope binaries.
28. On macOS, bundled-dashboard signature tests run deep strict verification
    against the exact `.app` candidate so version metadata changes cannot reach
    the update transaction with a stale signature.
29. v2.6 index-generation tests prove deterministic identity, exact verified
    source manifests, closed schema validation, immutable payloads, and source
    hash rejection without changing raw history.
30. v2.6 switch and rollback tests inject an interrupted atomic replacement,
    preserve the prior pointer, retain both complete generations, and restore
    the previous pointer without source reprocessing.
31. v2.6 retrieval tests use the fixed corpus to prove policy-lineage and exact
    disambiguation cases, bind the corpus SHA-256, and reject unexplained
    active-versus-shadow deltas.
32. v2.6 CLI tests prove shadow build and status are distinct from activation,
    while activation and rollback remain preview-only until explicit apply.
33. v2.7 queue tests prove durable closed states, duplicate idempotency, bounded
    retries, restart recovery, stale-lease recovery, permission-failure safety,
    and explicit quarantine.
34. v2.7 capture-independence tests prove collector telemetry and raw capture
    continue while semantic or maintenance work is failed or quarantined.
35. v2.7 diagnostic tests prove bundles omit raw dialogue, redact secrets and
    local user paths, and leave authoritative raw bytes unchanged.
36. v2.7 semantic-dispatch tests prove mechanical eligibility invokes no AI,
    incomplete rounds fail before queueing, and a complete boundary permits at
    most one explicitly leased worker attempt.
37. v2.8 content-store tests prove stable ordered manifest identity, exact-byte
    objects and reconstruction, source-drift and corruption rejection,
    destination conflict explanation, path safety, and removable rollback.
38. v2.8 transfer tests prove fixed-checkpoint interruption and restart,
    idempotent replay, gap and overlap rejection, corrupt-segment failure,
    checkpoint tamper rejection, and archive/environment failure isolation.
39. v2.8 CLI tests prove build, reconstruction, disable, and transfer remain
    preview-only until explicit apply and never overwrite conflicting targets.
40. v2.9 interface-parity tests prove CLI, loopback HTTP, and MCP share bounded
    request validation, result payloads, confidence, provenance, raw-source
    verification, stale-index fallback, and unchanged raw bytes.
41. v2.9 update tests prove explicit channel selection, artifact hashing,
    failed-delta fallback to a verified full package, corruption rollback, and
    no execution or installation before separate user approval.
42. v2.9 summary-budget tests prove before/at/after thresholds, completed-round
    gating, idempotent queue creation, and zero AI calls during eligibility.
43. v2.10 personal Environment tests prove deterministic path-free profile and
    generation identities, preview-first capture, pointer reconstruction,
    bounded inventory, metadata and redaction failures, all six comparison
    outcomes, trusted read-only exchange, replay and corruption rejection,
    provider-owned references, zero automatic activation, and unchanged raw
    history. Existing Rule and Skill installer scenarios remain the required
    handoff and rollback evidence; a received profile alone cannot invoke them.
44. v2.11 catch-up tests prove activation boundaries never move later, retained
    no-cursor sources remain eligible, native batches resume after interruption,
    maintenance uses hidden bounded schedulers, deferred semantic work does not
    consume retries, oversized prompts satisfy character and UTF-8 byte limits,
    and the dashboard distinguishes four independent debt classes.
45. Any release that changes a background executor or runtime resolver must run
    a synthetic live semantic canary under the installed scheduler identity.
    The receipt must prove the pending count decreases from 1 to 0, the summary
    registry increases from 0 to 1, the generated summary is indexable, no real
    archive content entered the canary, and the temporary task was removed.
    Process existence, task registration, mocked completion, or a zero exit code
    alone is not effect evidence. Backend optimizations must likewise expose a
    route counter or before/after metric proving the intended production path
    ran; silent fallback to the legacy path fails the release gate.
46. The v2.11.5 runtime-effect matrix must exercise the production Owner paths,
    not substitute mocks for the effect being claimed. Fixed cases must prove:
    the collector persists work without launching or waiting for AI; ingesting
    the threshold number of Level-1 summaries creates a Level-2 job, while a
    bounded serial parent backlog reports catching-up only when at least one
    parent job is really pending; an index
    built before a new raw record is rejected as stale and hybrid retrieval
    emits an explicit keyword-fallback warning; an interrupted backup removes
    its own temporary directory and reports failure; internal conversation-index
    holes are repaired while source-integrity failures remain untouched;
    quarantined, invalid, transient, waiting-ack, and remaining debt cannot be
    reported as healthy or completed; waiting for a cloud acknowledgement does
    not advance the publication observation; duplicate no-change imports do not
    count as new imports; and an upgrade adds missing configuration defaults
    without replacing user values. A legacy Environment receipt may be upgraded
    only when its committed transaction, replica state, complete event ledger,
    and every materialized output hash agree; changed or missing evidence must
    remain rejected. The exact candidate must also pass
    `runtime_effect_gate.py` against an isolated installed archive. Any hidden
    fallback, stale waterline, permanent debt, orphan backup, or stale supervisor
    state is a release failure even when every process exits with code zero.
    Sustained native history recovery must also release the archive lock and
    yield between bounded batches so maintenance, repair, and backup workers
    cannot remain alive but make no observable progress.
47. The Windows post-install gate must resolve the installed `.lnk` through the
    Windows Shell and compare its target, working directory, icon, arguments,
    launcher configuration, and target existence with the exact candidate
    Skill root and active archive. A shortcut that merely exists is not effect
    evidence. Rehearsal must include a Codex sandbox process whose SID resolves
    to a service profile and prove that a validated package-provided Skill root
    remains authoritative; `CodexSandboxOffline` or another process profile
    must never replace the interactive user's installed path.

Every version uses a dedicated output directory such as
`outputs/rehearsal/v1.9.0`. A report generated for another version is not valid
release evidence.

An unrun, skipped, interrupted, or evidence-free scenario is not a pass.
When a full unittest suite has passed in the same CI job, focused scenarios may
reuse its retained log through `--reuse-unittest-evidence`. Each scenario still
receives its own hashed reference log containing the source evidence SHA-256.
This is evidence deduplication, not a skipped scenario. Evidence from another
commit, version, job, or platform is invalid.
Platform-specific live installation checks must be recorded separately. A
desktop-affecting release is not complete until the installed dashboard has
been replaced, its version and launcher configuration have been verified, and
the dashboard has successfully opened against the preserved active archive.
# v2.12.3 targeted semantic-drain hotfix

The Windows production task was observed without a manual dispatcher call.
It started at 03:01:10 JST, invoked ephemeral Codex at 03:01:11, and reduced
pending semantic debt from 158 to 157 at 03:04:10. Focused regressions preserve
raw-integrity blocking, force fresh recovery when explicit debt exists, and
reuse recent deep-recovery evidence for no more than one hour.

# v2.12.4 targeted lossless-parent and audit-lock hotfix

The focused candidate must round-trip sparse and explicit-null fields for both
raw records and child summaries, reject malformed presence maps, and prove that
heartbeat performs audit and repair while holding the same archive lock as the
native collector. Installed-runtime evidence must use the saved ten-child
parent payload, requeue only its quarantined maintenance job, and observe the
Level-2 summary ingest without rewriting raw history or existing summaries.

The Windows installed effect check completed on 2026-08-02 JST. Nineteen raw
archive files retained identical before/after hashes, heartbeat repaired three
derived categories with no remaining integrity or repairable issue, and the
audited requeue moved the real parent job from quarantined to completed in one
attempt. `L2-000005` was created with SHA-256
`84bbd0e9ad73a642fc2319d3ec4118087bc984389b6fc7931b20d4e1bc46e7e3`.

# v2.12.5 targeted duplicate pending-round recovery hotfix

- Reproduce one completed and one unresolved conversation sharing a legacy
  global round number.
- Require both Python and Rust recovery to scan every positive-numbered raw
  record and pair completion within one conversation.
- Apply repair only to derived `state.json` with a rollback backup.
- During a bounded collector pause, require heartbeat `ok`, zero integrity and
  repairable issues, zero quarantine, and unchanged SHA-256 for every existing
  raw file.
- Run only the six scenarios bound by `docs/work-contracts/v2.12.5.json`.

# v2.12.6 targeted shared-round completion hotfix

- Keep a shared conversation-scoped round incomplete while any user-bearing
  conversation lacks a final answer.
- Add the missing final and require recovered completion with no pending entry.
- Repair only derived state with backup, then require heartbeat `ok`, zero
  integrity issues, and zero repairable issues.
