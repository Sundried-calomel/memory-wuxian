# Memory無限

Memory無限 is a file-based Codex Skill for persistent, hierarchical, and verifiable conversation memory beyond the active context window.

The installable Skill identifier is `memory-wuxian`; `Memory無限` is its project and display name. The design keeps exact source records as historical authority, uses summaries as navigation, and returns to raw text before treating a historical claim as verified.

## What it provides

- Append-only Markdown conversation records with timestamps and SHA-256 integrity fields
- One complete, automatically updated Markdown transcript per conversation
- Conversation-scoped pending rounds and reply relationships during concurrent tasks
- Conversation-scoped Level-1 summaries and conversation-scoped higher-level summaries
- Separate message, timeline, concept, and summary indexes for every conversation, plus global routing indexes
- Script-detected summary boundaries after 5 completed rounds or 20,000 visible characters
- Ephemeral AI summary generation only when a completed round makes a summary due
- Index-first retrieval with raw-text verification
- Preview-first state and index recovery
- Heartbeat validation, maintenance, and repair modes
- Incremental Codex rollout parsing with stable source IDs and per-session cursors
- Event-driven macOS synchronization through a persistent native LaunchAgent
- One latest verified desktop snapshot with a SHA-256 manifest and an append-only backup log
- One latest workspace recovery backup for derived-file reconstruction
- A transparent file layout with no database dependency

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

Continuous capture does not call a model. Scripts create a source-locked summary job only after a complete dialogue round reaches a configured threshold. The one-shot semantic worker then invokes the authenticated Codex CLI in ephemeral mode, ingests the constrained JSON summary, and exits.

## Automatic Codex capture on macOS

Installing a Skill does not by itself subscribe to Codex client events. Build the Rust collector once, then install its persistent LaunchAgent:

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

The LaunchAgent keeps one optimized Rust process alive and receives filesystem change notifications from the operating system, with a five-second size/mtime fallback for missed deep-directory events. It stores user messages and visible assistant commentary/final answers from top-level Codex sessions. It excludes subagent sessions, system instructions, internal reasoning, tool calls, and tool output. A per-session cursor and stable source-derived IDs make retries idempotent.

The native collector directly owns high-frequency JSONL parsing, raw append, per-conversation transcript updates, deterministic routing indexes, cursor writes, due Level-1 job creation, and desktop snapshots. When a job becomes due, it runs one Python wrapper that invokes one ephemeral Codex CLI summary process and exits after ingestion. The Python CLI remains the low-frequency interface for summary ingestion, retrieval, heartbeat, and preview-first reconstruction.

Every imported conversation is also written to its own file under `memory/conversations/`. A transcript contains only one conversation ID and includes both exact machine-readable records and readable message text. Its isolated indexes are stored under `memory/indexes/by-conversation/<conversation>/`. The immutable files under `raw/` remain authoritative; per-conversation transcripts and indexes are deterministic views that can be rebuilt without changing raw history.

On macOS, grant Full Disk Access to `bin/memory-wuxian-collector` when the archive or backup is stored under protected `Documents` or `Desktop` locations. Verify the exact executable in the generated plist before claiming automatic capture is active.

With the default configuration, every successful memory mutation creates a new complete snapshot under `~/Desktop/Memory無限-记忆归档备份/` after the primary archive write finishes, verifies its manifest, and removes older snapshot directories. The backup root therefore contains one latest recovery copy plus the append-only `backup-log.jsonl` operation history.

Applied reconstruction commands may first preserve the previous derived files under `memory/archive/`. These internal recovery copies use `backup.workspace_retention_count` and also retain only the newest one by default. Development edits use one replaceable code backup; they do not create additional copies of the live conversation archive.

## Memory hierarchy

```text
Raw conversation records
  -> Complete per-conversation transcripts
  -> Separate indexes for every conversation
    -> Conversation-scoped AI Level-1 summaries after a completed-round or character threshold
      -> Conversation-scoped higher-level summaries after a fixed number of child summaries
        -> Global routing indexes
          -> Retrieved raw-text evidence
```

The default thresholds are configurable. The initial implementation deliberately avoids subjective importance scoring and automatic inference of long-term user preferences.

The default Level-1 boundary is 5 completed dialogue rounds or 20,000 visible characters per conversation, whichever occurs first. Crossing 20,000 characters during an answer marks the summary as due, but the source range is not closed until that answer's `final_answer` completes the round. Scripts store exact source ranges, hashes, counts, and normalized routing excerpts; the ephemeral AI worker alone produces topics, conclusions, open questions, and concepts.

Automatic semantic-summary jobs and the one-shot worker are enabled in the installed configuration. No AI process remains active between due summaries. Existing pending jobs keep their immutable source ranges and are not silently rewritten when thresholds change.

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
