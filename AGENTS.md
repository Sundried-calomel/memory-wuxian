# Memory無限 Agent Rules

## Objective

Use Memory無限 to preserve conversation history outside the active context window and restore only the historical material needed for the current task.

## Required behavior

1. Save every source message before any runtime context compression.
2. Preserve timestamp, timezone, speaker, message ID, conversation ID, turn order, and exact stored text.
3. Keep raw records append-only. Store corrections as new linked records.
4. Count one user message and its corresponding assistant response as one completed dialogue round.
5. Generate deterministic Level-1 indexes after 5 completed rounds or 20,000 visible characters, whichever occurs first.
6. Generate a parent summary after the configured number of ungrouped child summaries, normally 10.
7. Persist every summary and index as a file. Keep all child summaries after grouping.
8. Include precise source ranges in every summary.
9. Record explicit topics, conclusions, unresolved questions, and concepts only.
10. Do not infer long-term preferences, hidden motivations, or subjective importance.
11. Search concept and time indexes first, route through summaries, and verify against raw text before making historical claims.
12. Clearly identify retrieval confidence as `verified`, `summary-supported`, `index-only`, or `unverified`.
13. Keep runtime compression temporary and separate from persistent memory.
14. Use heartbeat for validation and recovery. Keep count-based events as primary triggers.
15. Verify summary source SHA-256 before ingestion and report source drift without rewriting history.
16. Preview state or index reconstruction before applying it; archive the previous derived files before replacement and retain only the configured newest workspace recovery backup, normally one.
17. Treat raw or summary hash mismatches as integrity failures that require review, not automatic repair.
18. When Codex synchronization is configured, import native rollout files incrementally and preserve source session, line, and phase metadata.
19. Preserve visible assistant commentary, but complete a dialogue round only when the corresponding final answer is persisted.
20. After each successful primary-archive mutation, create and log the configured desktop snapshot before reporting the write as fully backed up.
21. Maintain one complete derived transcript per conversation under `memory/conversations/`; a transcript must never contain records from another conversation ID.
22. Use the persistent native collector for continuous Codex capture. Use Python for low-frequency maintenance and Agent-facing memory operations, not interval polling.
23. Hold `memory/.locks/archive.lock` for every complete native event batch and Python maintenance command so readers never observe a partial archive transaction.
24. Keep one replaceable Memory無限 code backup in the workspace when editing the Skill. Do not accumulate timestamped full-project copies or copy the live conversation archive into development outputs.
25. Keep only the native collector continuously active. After a completed round reaches either summary threshold, run one ephemeral AI worker to generate and ingest that summary, then exit.
26. Check automatic semantic backlog only when a synchronization batch completes a new dialogue round. Commentary, restart catch-up, and other nonfinal writes must not trigger AI work.

## Authority order

```text
Raw conversation segment
  > Level-1 summary
  > Level-2 summary
  > higher-level summary
  > unverified recollection
```

When sources conflict, retrieve the raw segment, use it as authoritative, log the discrepancy, and preserve the earlier summary unchanged.

## Core principle

Original conversations are the source of truth. Summaries are indexes. Indexes locate history. Retrieved history supports reasoning.
