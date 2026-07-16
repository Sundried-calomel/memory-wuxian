# Memory無限 Architecture Decisions

## D-001: Fixed-round Level-1 summaries

Status: Accepted.

Generate a Level-1 summary job after a configurable number of completed user-assistant dialogue rounds. Keep incomplete rounds persisted and outside the completed count.

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

Read native Codex rollout JSONL incrementally and persist per-session cursors. Import user messages and visible assistant commentary/final answers. Exclude system instructions, internal reasoning, tool calls, and tool outputs. Use source-derived message IDs so retries cannot duplicate history. Count only a final assistant answer as completing a dialogue round.

## D-007: Desktop backup follows the primary archive

Status: Accepted.

Write and index the primary archive first. After a successful mutation, create a timestamped external snapshot with a file-hash manifest and append an entry to the backup log. A backup never becomes the writable source of truth.

## D-008: Conversation transcripts are isolated derived archives

Status: Accepted.

Maintain one complete Markdown transcript for each conversation ID. Never combine records from different conversations in one transcript. Keep daily raw files as immutable authority and rebuild per-conversation transcripts only as deterministic derived files, with preview, archived replacement, and integrity checks.
