# v2.20.1

- Direct Codex capture replaces the legacy collector and raw-archive bridge in normal maintenance.
- Existing archive storage, summaries, dashboard, search, backup and core-v1 sync remain one runtime.
- Every platform package includes the independently built minimal encryption/signing helper.
- Added a portable configuration entry point for local identity creation and explicit peer pairing.
- Windows x64, Linux x64 and macOS ARM64 packages are built and exercised in CI.

Requires Python and an authenticated Codex CLI. Historical migration preserves original
archives. The independent installer remains excluded. No real Mac peer ACK is claimed.
