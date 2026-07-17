# Memory無限

Memory無限 is a file-based Codex Skill for persistent, hierarchical, and verifiable conversation memory beyond the active context window.

The installable Skill identifier is `memory-wuxian`; `Memory無限` is its project and display name. The design keeps exact source records as historical authority, uses summaries as navigation, and returns to raw text before treating a historical claim as verified.

## What it provides

- Append-only Markdown conversation records with timestamps and SHA-256 integrity fields
- One complete, automatically updated Markdown transcript per conversation
- Conversation-scoped pending rounds and reply relationships during concurrent tasks
- Conversation-scoped Level-1 summaries and conversation-scoped higher-level summaries
- Separate message, timeline, concept, and summary indexes for every conversation, plus global routing indexes
- Index-first retrieval with raw-text verification
- Preview-first state and index recovery
- Heartbeat validation, maintenance, and repair modes
- Incremental Codex rollout parsing with stable source IDs and per-session cursors
- Event-driven macOS synchronization through a persistent native LaunchAgent
- One latest verified desktop snapshot with a SHA-256 manifest and an append-only backup log
- A transparent file layout with no database or external model API dependency

## Install

Install the Skill from its GitHub directory, then restart Codex so it can discover the new Skill:

```text
$skill-installer install https://github.com/Sundried-calomel/memory-wuxian
```

For a manual local installation, place the repository at:

```text
~/.codex/skills/memory-wuxian
```

## Quick start

Start with [`SKILL.md`](SKILL.md). Use an external archive root for real conversation history so a source checkout or Skill update cannot mix with private memory data.

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
python3 scripts/memory_cli.py --root "$ARCHIVE" status
python3 scripts/memory_cli.py --root "$ARCHIVE" backup
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
```

The CLI does not call a language-model API. It creates deterministic summary jobs; the invoking Agent generates constrained summary JSON and gives it back to `ingest-summary`.

## Automatic Codex capture on macOS

Installing a Skill does not by itself subscribe to Codex client events. Build the Rust collector once, then install its persistent LaunchAgent:

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

The LaunchAgent keeps one optimized Rust process alive and receives recursive filesystem change notifications from the operating system. It performs no interval polling and starts work only when Codex rollout files change. It stores user messages and visible assistant commentary/final answers from top-level Codex sessions. It excludes subagent sessions, system instructions, internal reasoning, tool calls, and tool output. A per-session cursor and stable source-derived IDs make retries idempotent.

The native collector directly owns high-frequency JSONL parsing, raw append, per-conversation transcript updates, deterministic conversation indexes, cursor writes, due Level-1 job creation, and desktop snapshots. The Python CLI remains the low-frequency interface for summary ingestion, retrieval, heartbeat, and preview-first reconstruction.

Every imported conversation is also written to its own file under `memory/conversations/`. A transcript contains only one conversation ID and includes both exact machine-readable records and readable message text. Its isolated indexes are stored under `memory/indexes/by-conversation/<conversation>/`. The immutable files under `raw/` remain authoritative; per-conversation transcripts and indexes are deterministic views that can be rebuilt without changing raw history.

On macOS, grant Full Disk Access to `bin/memory-wuxian-collector` when the archive or backup is stored under protected `Documents` or `Desktop` locations. Verify the exact executable in the generated plist before claiming automatic capture is active.

With the default configuration, every successful memory mutation creates a new complete snapshot under `~/Desktop/Memory無限-记忆归档备份/` after the primary archive write finishes, verifies its manifest, and removes older snapshot directories. The backup root therefore contains one latest recovery copy plus the append-only `backup-log.jsonl` operation history.

## Memory hierarchy

```text
Raw conversation records
  -> Complete per-conversation transcripts
  -> Separate indexes for every conversation
    -> Conversation-scoped Level-1 summaries after a fixed number of completed dialogue rounds
      -> Conversation-scoped higher-level summaries after a fixed number of child summaries
        -> Global routing indexes
          -> Retrieved raw-text evidence
```

The default thresholds are configurable. The initial implementation deliberately avoids subjective importance scoring and automatic inference of long-term user preferences.

## Privacy and integration boundary

- Use `--root` outside the repository for private archives.
- Mutable files under the bundled `memory/` directory are excluded by `.gitignore`.
- The CLI can redact obvious secrets when explicitly configured, but users remain responsible for deciding what may be persisted.
- Automatic capture requires the supplied native LaunchAgent or another explicitly configured client hook.

## Development

Run the functional test suite without creating bytecode files:

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Architecture decisions and implementation contracts are documented in [`PROJECT.md`](PROJECT.md) and [`references/`](references/). Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## License

Memory無限 is released under the [MIT License](LICENSE.txt).
