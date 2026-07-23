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
22. Use the persistent native collector for continuous Codex capture through LaunchAgent on macOS or Task Scheduler on Windows. Use Python for low-frequency maintenance and Agent-facing memory operations, not interval polling.
23. Hold `memory/.locks/archive.lock` for every complete native event batch and Python maintenance command so readers never observe a partial archive transaction.
24. Keep one replaceable Memory無限 code backup in the workspace when editing the Skill. Do not accumulate timestamped full-project copies or copy the live conversation archive into development outputs.
25. Keep only the native collector continuously active. After a completed round reaches either summary threshold, run one ephemeral AI worker to generate and ingest that summary, then exit.
26. Check automatic semantic backlog only when a synchronization batch completes a new dialogue round. Commentary, restart catch-up, and other nonfinal writes must not trigger AI work.
27. Preserve lightweight tool activity already visible in the Codex task timeline: tool name, nested tool names, and command text when available. Preserve successful structured file-change events with exact unified diffs as the sole tool-output exception. Keep both in the corresponding conversation and round, but exclude general tool output and hidden reasoning.
28. Treat the current node's local archive as its exclusive writable authority. Import peer data only into the read-only federation cache, never into local raw history or local counters.
29. Identify every federated object by its origin node and preserve the original artifact bytes and SHA-256.
30. Before importing a delta, require a trusted peer, validate bundle structure and artifact hashes, reject sequence gaps or overlaps, and validate the predecessor bundle SHA-256 chain.
31. Export only artifacts originated by the current node. Never re-export a replica received from another peer.
32. Use SSH with strict host-key checking for automated peer pulls. Select the declared `posix` or `powershell` remote shell and do not weaken host authentication.
33. Treat an offline `.mwxb` as unencrypted and unsigned. Do not send it through an untrusted channel or describe SHA-256 as sender authentication.
34. Do not use OpenAI sessions, Codex credentials, or account login state as Memory無限 device identity.
35. Keep reconstructible peer replicas outside the primary archive and outside its desktop backup. Use `retrieve-global` when cross-device history is requested.
36. Keep SSH and encrypted cloud-folder exchange as separate transports over the same federation import contract.
37. Before placing a delta in iCloud Drive, OneDrive, or another synchronized folder, sign it with the origin device identity and encrypt it to the target device. Never upload readable `.mwxb` files or private keys.
38. Let each node write only its own cloud outbox and acknowledgements. Imported cloud history remains a read-only peer replica.
39. Run cloud exchange as a short-lived low-frequency task. Keep the native collector's local event capture and adaptive fallback unchanged, and do not use AI for cloud transfer.

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
