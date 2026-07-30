# Release rehearsal gate

## Lifecycle order

Use a release-candidate branch before assigning an immutable formal version.
Run targeted tests while the candidate changes, then run one complete local
rehearsal and one three-platform candidate CI matrix after the changes
stabilize. Repair failures in that candidate series without creating formal
tags or published installers.

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

Every version uses a dedicated output directory such as
`outputs/rehearsal/v1.9.0`. A report generated for another version is not valid
release evidence.

An unrun, skipped, interrupted, or evidence-free scenario is not a pass.
Platform-specific live installation checks must be recorded separately. A
desktop-affecting release is not complete until the installed dashboard has
been replaced, its version and launcher configuration have been verified, and
the dashboard has successfully opened against the preserved active archive.
