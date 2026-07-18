# Memory無限 Project Invariants

1. Original conversation records are historical authority.
2. Persist raw records before runtime compression.
3. Keep raw records append-only; represent corrections as linked additions.
4. Use summaries as retrieval indexes and preserve every summary level.
5. Build deterministic Level-1 indexes when either the completed-round or visible-character threshold is reached.
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
17. Serialize complete collector batches and maintenance commands through the shared archive transaction lock.
18. Keep continuous capture and trigger detection script-only. Invoke AI only as an ephemeral worker for a due semantic summary, after the current dialogue round has completed.
19. Keep runtime context capsules derived, bounded, acknowledgement-driven, and separate from authoritative archive records.
20. Cap every runtime context capsule at 10,000 estimated tokens even when the active model exposes a larger context window.

Architectural changes must preserve these invariants or document an explicit replacement decision in `references/decisions.md` before implementation.
