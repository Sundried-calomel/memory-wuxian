# Memory無限 Architecture Decisions

## D-001: Fixed-round Level-1 summaries

Status: Accepted.

Generate a Level-1 summary job after a configurable number of completed user-assistant dialogue rounds. Keep incomplete rounds persisted and outside the completed count.

The default is 10 completed rounds. Previously assigned jobs remain unchanged when the configuration changes.

## D-002: Fixed-count summary hierarchy

Status: Accepted.

Generate a Level-N parent after a configurable number of ungrouped Level-(N-1) summaries. Preserve every child summary and record parent-child links.

## D-003: Summaries are indexes

Status: Accepted.

Use summaries to locate history. Verify factual historical claims against raw records when those records are available.

## D-004: Content integrity is explicit

Status: Accepted.

Store SHA-256 for new raw records, summary source sets, and summary files. Recalculate the source hash before summary ingestion. Report drift without automatically rewriting source or summary history.

## D-005: Recovery is preview-first

Status: Accepted.

Reconstruct derived state and indexes from persisted raw and summary files. Preview differences by default. Require `--apply` or heartbeat `--repair` for writes, and archive the previous derived files first.

## D-006: Codex integration is an idempotent source adapter

Status: Accepted.

Read native Codex rollout JSONL incrementally and persist per-session cursors. Import user messages, visible assistant commentary/final answers, and lightweight tool activity displayed in the task timeline. Tool activity stores tool and nested-tool names plus command text when available, but never tool output or hidden reasoning. Use source-derived message IDs so retries cannot duplicate history. Count only a final assistant answer as completing a dialogue round.

## D-007: Desktop backup follows the primary archive

Status: Accepted.

Write and index the primary archive first. After a successful mutation, create a timestamped external snapshot with a file-hash manifest and append an entry to the backup log. A backup never becomes the writable source of truth.

## D-008: Conversation transcripts are isolated derived archives

Status: Accepted.

Maintain one complete Markdown transcript for each conversation ID. Never combine records from different conversations in one transcript. Keep daily raw files as immutable authority and rebuild per-conversation transcripts only as deterministic derived files, with preview, archived replacement, and integrity checks.

## D-009: High-frequency capture uses a native event-driven process

Status: Accepted.

Run one persistent Rust collector that watches native Codex session files through the operating system filesystem notification backend. The collector owns deterministic high-frequency capture and backup operations. Keep the Python CLI for low-frequency Agent-facing maintenance, summary ingestion, retrieval, and reconstruction. Preserve one storage contract across both implementations and test their persisted records for parity.

## D-010: Pending rounds are conversation-scoped

Status: Accepted.

Maintain one pending round per conversation ID and allocate each new dialogue round a globally unique number. A final assistant answer may close only its own conversation's pending round. Preserve summary ranges by advancing the global completed-round high-watermark only across contiguous completed round numbers; retain later completions in an explicit out-of-order set until preceding rounds finish. Store assistant text without a pending user as visible non-round content.

## D-011: One current external recovery snapshot

Status: Accepted.

Create a complete manifest-backed snapshot after each successful primary mutation, then remove older snapshot directories beyond configured retention. Retain one latest snapshot by default and keep the append-only backup log as operation history.

## D-012: Summaries and detailed indexes are conversation-scoped

Status: Accepted.

Assign Level-1 sources and higher-level child summaries within one conversation only. Persist message, timeline, summary, and concept indexes separately for each conversation; retain global indexes only for cross-conversation routing.

## D-013: Native subagent sessions are excluded

Status: Accepted.

Use native session metadata to reject complete Codex subagent sessions before importing any message. Archive only top-level user-visible conversation sessions.

## D-014: One current workspace recovery backup

Status: Accepted.

Before replacing deterministic derived files, preserve their previous versions under `memory/archive/`, then remove older recovery directories beyond `backup.workspace_retention_count`. Retain one latest workspace recovery backup by default. Do not copy the live conversation archive into development output folders.

## D-015: Hybrid deterministic indexes are the source-routing layer

Status: Accepted.

Build Level-1 routing records after 5 completed rounds or 20,000 visible characters, whichever occurs first. Store only deterministic source metadata, hashes, counts, and normalized excerpts. Group every 10 child routing records into the next level. These records make trigger decisions and source recovery auditable; they do not replace semantic summaries.

## D-016: Semantic AI runs only for a closed due range

Status: Accepted.

Let scripts count rounds and visible characters. If a character threshold is crossed while an answer is still being written, wait for that answer's `final_answer` before freezing the source range. Then invoke one ephemeral Codex CLI process to generate the constrained semantic summary, ingest it after source-hash verification, and exit. Never keep an AI conversation active merely to watch for trigger conditions.

## D-017: Historical retrieval is multi-term and conversation-local

Status: Accepted.

Normalize mixed natural-language queries and rank explicit terms deterministically across generated routes and raw records. Do not require the full query to occur verbatim. Exclude currently incomplete rounds to prevent self-matching, restore neighboring context only within the matched conversation, and report `verified` only after a raw-text match.

## D-018: Runtime memory refresh is bounded and hierarchical

Status: Accepted.

Keep persistent memory separate from the client-managed active context. At each Agent turn, inspect the active top-level rollout's latest token telemetry and completed-round count. Refresh after 10 completed rounds, at 65% and 80% effective context utilization, or after a detected compaction drop. Inject a derived capsule through tool context rather than archiving it as a source message. Prefer the highest available summary level, add only uncovered lower summaries and recent task state, and cap the capsule at 1% of the effective model context with a 3,000-token soft limit and 10,000-token absolute limit.
