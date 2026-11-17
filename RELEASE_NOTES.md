# v2.20.9 - lean release checks

- Stop generating/publishing standalone checksum sidecars and SHA256SUMS files.
- Preserve installation checks using GitHub asset digests, package manifests, safe paths, compatibility and transactional recovery.
- Select regression tests from changes since the previous release. Documentation-only changes do not run behavioral suites; small patches run mapped tests.
- Run native exchange and complete-install smoke tests only when dependencies change. Shared-core changes retain wider coverage; full regression can be explicitly requested.
- Disable duplicate automatic installer push/PR workflows. One release-tag pipeline owns automatic validation across supported platforms.
- No archive migration or runtime behavior change. Installer engine version stays unchanged.

## Previous v2.20.8 changes

- Missing rule-source mappings now wait without failing unrelated synchronization. No partial-rule ACK is fabricated.
- Portable AGENTS path templates, automatic one-sided updates, verified content baselines and Git three-way merging; overlapping conflicts retain originals.
- Accept MCP _meta without relaxing argument validation. Reconnect existing clients after updating.
- Include macOS cloud-placeholder handling and independent ACK-channel error isolation.
- Add targeted regression tests to all platform builds. Installer remains 1.1.0.
- Windows live verification: waiting-source, completed maintenance cycles, fresh MCP stdio status/query passed. Existing chat reconnection and Mac activation are not asserted.

## Previous v2.20.7 changes

- Restore a native WebView dashboard entry (`core/window.py`; requires pywebview).
- Show one activity mode; hide zero error and pending counts; move usage timestamp to footer.
- Adaptive worker (`core/live.py --loop`): 5 seconds active, 60 seconds idle after one quiet minute, 300 seconds deep idle after ten quiet minutes. Work is serialized; intervals start after a tick completes.
- Restore peer daily totals, deduplicating old and current replicas by device/message identity.
- Persist incremental model-reported usage in the new token-usage directory; unavailable historical usage remains unknown.
- Resolve rotated Codex executable paths and constrain summary source references. Summaries use GPT 6 Luna with medium reasoning in the configured installation.
- A receive gap no longer blocks independent sending. Keep the gap visible until the missing authenticated package arrives.
- Accept already-identical environment files and process the newest full selected revision.

Installer stays at 1.1.0. Complete ZIPs include it; existing installations use the regular update payload.
Known live boundary: the Windows device still awaits a missing Mac package and peer ACKs; this release does not assert remote-device recovery.
