# Memory無限 Project Invariants

1. Original conversation records are historical authority.
2. Persist raw records before runtime compression.
3. Keep raw records append-only; represent corrections as linked additions.
4. Use summaries as retrieval indexes and preserve every summary level.
5. Trigger Level-1 summaries by completed dialogue-round count.
6. Trigger parent summaries by ungrouped child-summary count.
7. Persist raw records, summaries, indexes, jobs, and retrieval logs as files.
8. Verify historical claims against raw records whenever available.
9. Verify source SHA-256 before ingesting a generated summary.
10. Rebuild derived state and indexes from persisted records without modifying raw history.
11. Do not infer long-term preferences, psychological traits, or subjective importance in Version 1.
12. Keep runtime compression temporary and separate from permanent memory.
13. Treat Codex rollout files as import sources; store only user-visible dialogue and retain source line references.
14. Create a recorded external snapshot after each successful archive synchronization or derived-memory update when backup is enabled.
15. Maintain one complete derived transcript for each conversation ID without rewriting or replacing authoritative raw history.
16. Keep high-frequency capture in the native event-driven collector and verify its storage output against the Python maintenance implementation.

Architectural changes must preserve these invariants or document an explicit replacement decision in `references/decisions.md` before implementation.
