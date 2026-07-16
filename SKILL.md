---
name: memory-wuxian
description: Persist, summarize, index, retrieve, restore, and verify conversation history beyond the active context window. Use when Codex needs to preserve exact dialogue records, generate count-triggered hierarchical summaries, recover an earlier discussion by time or concept, verify recollection against raw text, run memory maintenance, or diagnose the file-based Memory無限 archive.
---

# Memory無限

Build effectively unbounded, retrievable conversation memory from immutable source records and persistent indexes.

## Core invariants

1. Persist the exact source message before allowing runtime compression.
2. Treat raw records as append-only historical authority.
3. Use summaries only as routing indexes; verify historical claims against raw text.
4. Generate Level-1 summaries after a configured number of completed dialogue rounds.
5. Generate each higher level after a configured number of ungrouped child summaries.
6. Persist every summary level and every parent-child relationship.
7. Record only explicit source information. Do not infer hidden preferences, motives, or importance.
8. Keep runtime compression separate from permanent memory.
9. Verify SHA-256 source integrity before summary ingestion.
10. Rebuild only derived state and indexes; never repair integrity failures by rewriting history.

## Operating workflow

1. Run `python3 scripts/memory_cli.py init` for a new memory root.
2. Append each user and assistant message with `append`; one user message plus its assistant response forms a completed round.
3. Run `make-summary-job` when the configured count threshold is reached.
4. Read the generated job and [summary prompt](prompts/summarize.md), produce the required JSON without adding unstated claims, then run `ingest-summary`.
5. Use `retrieve` for earlier topics. Let it search indexes first and raw records second.
6. Base answers on the recovered raw segment and report the returned verification level.
7. Run `heartbeat` for validation, pending-job recovery, and count-trigger checks. Do not use heartbeat as the primary trigger.
8. Preview `rebuild-state` or `rebuild-indexes` before applying a recovery operation.

## Commands

```bash
python3 scripts/memory_cli.py init
python3 scripts/memory_cli.py append --speaker user --text "..."
python3 scripts/memory_cli.py append --speaker assistant --text "..."
python3 scripts/memory_cli.py status
python3 scripts/memory_cli.py make-summary-job
python3 scripts/memory_cli.py ingest-summary --job memory/pending/<job>.json --summary-json <summary>.json
python3 scripts/memory_cli.py retrieve --query "..."
python3 scripts/memory_cli.py rebuild-state
python3 scripts/memory_cli.py rebuild-state --apply
python3 scripts/memory_cli.py rebuild-indexes
python3 scripts/memory_cli.py rebuild-indexes --apply
python3 scripts/memory_cli.py heartbeat --check-only
python3 scripts/memory_cli.py heartbeat
python3 scripts/memory_cli.py heartbeat --repair
```

Pass `--root <memory-directory>` before the subcommand to use a memory archive outside this skill folder.

## Load supporting material selectively

- Read [implementation.md](references/implementation.md) before changing storage formats, counters, summary hierarchy, retrieval behavior, state recovery, locking, privacy behavior, or client integration.
- Read [schemas.md](references/schemas.md) when constructing or validating raw records, summary JSON, indexes, state, or retrieval output.
- Read [decisions.md](references/decisions.md) before changing architectural behavior.
- Read [AGENTS.md](AGENTS.md) when integrating this skill into an Agent's persistent operating rules.
- Use files in `templates/` as output contracts and files in `prompts/` as Agent prompts.

## Client integration boundary

Do not claim that the skill automatically intercepts Codex client events unless the client provides and configures such a hook. The CLI reliably stores messages explicitly passed to it. An Agent integration must invoke `append` around each dialogue turn to provide automatic capture behavior.
