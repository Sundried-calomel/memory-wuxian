# Release rehearsal gate

A release may be described as fully rehearsed only when
`scripts/run_release_rehearsal.py` produces a report whose `status` is `passed`
and every required scenario has its own evidence log and SHA-256.

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

Every version uses a dedicated output directory such as
`outputs/rehearsal/v1.9.0`. A report generated for another version is not valid
release evidence.

An unrun, skipped, interrupted, or evidence-free scenario is not a pass.
Platform-specific live installation checks must be recorded separately. A
desktop-affecting release is not complete until the installed dashboard has
been replaced, its version and launcher configuration have been verified, and
the dashboard has successfully opened against the preserved active archive.
