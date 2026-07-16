# Memory無限

Memory無限 is a file-based Codex Skill for persistent, hierarchical, and verifiable conversation memory beyond the active context window.

The installable Skill identifier is `memory-wuxian`; `Memory無限` is its project and display name. The design keeps exact source records as historical authority, uses summaries as navigation, and returns to raw text before treating a historical claim as verified.

## What it provides

- Append-only Markdown conversation records with timestamps and SHA-256 integrity fields
- Count-triggered Level-1 summaries and count-triggered higher-level summaries
- Persistent timeline, concept, summary, and conversation indexes
- Index-first retrieval with raw-text verification
- Preview-first state and index recovery
- Heartbeat validation, maintenance, and repair modes
- Incremental Codex rollout parsing with stable source IDs and per-session cursors
- Automatic macOS synchronization through a LaunchAgent
- Timestamped desktop snapshots with SHA-256 manifests and an append-only backup log
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
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
```

The CLI does not call a language-model API. It creates deterministic summary jobs; the invoking Agent generates constrained summary JSON and gives it back to `ingest-summary`.

## Automatic Codex capture on macOS

Installing a Skill does not by itself subscribe to Codex client events. Memory無限 supplies an incremental parser plus a LaunchAgent installer:

```bash
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

The LaunchAgent checks native Codex rollout files every 15 seconds. It stores user messages and visible assistant commentary/final answers, while excluding system instructions, internal reasoning, tool calls, and tool output. A per-session cursor and stable source-derived IDs make retries idempotent.

With the default configuration, every successful memory mutation creates a new snapshot under `~/Desktop/Memory無限-记忆归档备份/` after the primary archive write finishes. Each snapshot contains `backup-manifest.json`; the backup root contains `backup-log.jsonl`.

## Memory hierarchy

```text
Raw conversation records
  -> Level-1 summaries after a fixed number of completed dialogue rounds
    -> Higher-level summaries after a fixed number of child summaries
      -> Timeline and concept indexes
        -> Retrieved raw-text evidence
```

The default thresholds are configurable. The initial implementation deliberately avoids subjective importance scoring and automatic inference of long-term user preferences.

## Privacy and integration boundary

- Use `--root` outside the repository for private archives.
- Mutable files under the bundled `memory/` directory are excluded by `.gitignore`.
- The CLI can redact obvious secrets when explicitly configured, but users remain responsible for deciding what may be persisted.
- Automatic capture requires the supplied LaunchAgent or another explicitly configured client hook.

## Development

Run the functional test suite without creating bytecode files:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Architecture decisions and implementation contracts are documented in [`PROJECT.md`](PROJECT.md) and [`references/`](references/). Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## License

Memory無限 is released under the [MIT License](LICENSE.txt).
