# Changelog

## Unreleased

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
