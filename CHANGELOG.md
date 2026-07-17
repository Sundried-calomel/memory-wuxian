# Changelog

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
