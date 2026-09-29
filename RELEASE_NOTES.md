# v2.20.3

Closes the read-only Codex SQLite connection explicitly on all platforms. The
v2.20.2 tag did not produce a formal release after Windows detected an open handle.

- Restore dashboard summary-level counts, actual completed-round counts, storage and incremental text estimates.
- Read local Codex titles, projects and archived status without changing the Codex database.
- Show persisted Token ledger observation dates separately from estimated text tokens; missing usage is not zero.
- Publish scheduled-worker phase, process identity, capture backlog, summary progress/errors, backup and sync status during a tick.
- Preserve historical warnings and receiving-device ACK requirements instead of displaying false health.
- Include the existing Mac migration fixes and desktop launcher adapter; do not reimport raw history during this update.
- Add matching English, Chinese and Japanese README documentation.

Existing installations keep their local configuration and archive. Set the local Codex
executable to its actual installed location and match `interval_seconds` to the OS
schedule. The independent installer remains excluded. This release does not repair
historical capture gaps or add a new billing collector.

## Previous: v2.20.1

- Direct Codex capture replaces the legacy collector and raw-archive bridge in normal maintenance.
- Existing archive storage, summaries, dashboard, search, backup and core-v1 sync remain one runtime.
- Every platform package includes the independently built minimal encryption/signing helper.
- Added a portable configuration entry point for local identity creation and explicit peer pairing.
- Windows x64, Linux x64 and macOS ARM64 packages are built and exercised in CI.

Requires Python and an authenticated Codex CLI. Historical migration preserves original
archives. The independent installer remains excluded. No real Mac peer ACK is claimed.
