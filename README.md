# Memory無限

> **2.16.0:** Makes collector installation and activation transactional on
> Windows and macOS. A candidate must prove its exact startup owner, command,
> archive root, live identity, and bounded watermark progress before commit;
> failures restore the previous runnable generation and preserve evidence in
> `collector-lifecycle.json`.

> **2.15.0:** Adds the independent encrypted `project-attachment-v1` stream for
> explicit large project deliverables. Exact 4 MiB chunks, SHA-256 manifests,
> resumable delivery, and verified atomic reconstruction keep ordinary source
> files readable while leaving every older stream unchanged.

> **2.14.5:** Completes the Project Evidence cloud contract with stream-bound native encryption kinds and a transport-compatible bundle hash. Cross-stream rejection remains mandatory.

> **2.14.4:** Makes runtime context-capsule loading fully read-only and acknowledgement-free, derives refreshes from the latest deterministic telemetry transition, and keeps the legacy ACK command as a no-write compatibility no-op. **2.14.3** repairs L2 semantic-job replay, raises the bounded five-minute batch to eight, allows at most three concurrent model calls with serialized ingestion, keeps full recovery audit off the five-minute hot path, reports stage timing in the dashboard, and retires the obsolete macOS semantic-backfill launcher after a successful transaction. **2.14.2** verifies same-size rollout rewrites by byte hash and lets the macOS user transaction finish its full readiness window. **2.14.1** fixes macOS installation with an offline isolated PyYAML fallback. **2.14.0** adds device-local Project Evidence Owners that maintain
> explicit closed file selections through bounded model-free refresh. **2.13.0** adds explicit immutable Project Evidence Packages and
> an independent encrypted `project-evidence-v1` stream. It retains the
> **2.12.7** live-capture correction, where Python and native capture wait for the last pending
> conversation sharing a legacy round before advancing global completion.

> **2.12.6:** A shared legacy round is complete only after every conversation
> with a user message has its own final answer, keeping recovered completion
> and pending state consistent.

> **2.12.5:** Legacy duplicate-round recovery now tracks unresolved rounds per
> conversation across all immutable raw records. Repair rebuilds only derived
> state and preserves source dialogue unchanged.

> **2.12.4:** Lossless semantic payloads now distinguish absent fields from
> explicit JSON `null`, allowing mixed legacy Level-1 metadata to form parent
> summaries. Heartbeat audits now share the collector archive lock so an
> in-flight native batch cannot create a cached false projection-drift alert.

> **2.12.3:** Automatic semantic catch-up now continues through transient,
> rebuildable transcript/index/state drift while Codex capture remains active.
> Recent deep-recovery evidence is reused for up to 24 hours unless recovery
> debt is present, while each frozen source SHA-256 is still checked at ingest.
> Immutable raw-history integrity failures still stop semantic execution.

> **2.12.2:** Daily chart dates are bold and visible below the baseline, nested
> local/all-device bars share one width, and the palette has clearer blue and
> green contrast. Native recovery now preserves an independent archived-message
> high-water mark during token-ledger backfill. Pending parent summaries reserve
> their children; a legacy overlap is quarantined with a SHA-256 receipt without
> changing raw history or persisted summaries.

> **2.12.1:** The native collector now rebuilds a retained format-v1 token
> ledger into the format-v2 daily ledger instead of aborting collection during
> an upgrade. Raw rollout files and append-only memory records remain unchanged.

> **2.12.0:** `daily_metrics.py` adds a nested daily chart for this device and
> all trusted synchronized devices, with message and Codex-reported token
> modes, per-device drilldown, stale-sync warnings, and an `Asia/Tokyo` day
> boundary. Federation protocol v2 exchanges immutable path-sanitized token
> ledger revisions while retaining protocol v1 read compatibility. Missing
> peer telemetry remains visibly incomplete and is never replaced by a
> character estimate or described as an account-global total.
>
> **2.11.6:** Windows upgrades now trust a validated package-provided Skill
> root before consulting the process SID, preventing Codex sandbox profiles
> from contaminating the desktop shortcut. Shortcut installation atomically
> resolves and verifies the final target, working directory, icon, arguments,
> and launcher configuration. The post-install runtime-effect gate rejects a
> shortcut that merely exists but targets the wrong user or a missing binary.
>
> **2.11.5:** Background health now means verified effects, not merely running
> processes. The collector never launches or waits for AI; the independent
> scheduler creates Level-2+ summary work, repairs safe derived-index holes,
> and reports permanent debt explicitly. Semantic indexes are bound to the
> current raw-source snapshot and stale indexes fail closed or visibly fall
> back to keyword retrieval with `semantic-index-stale-keyword-fallback`.
> Interrupted backups clean their own temporary
> directories, cloud waiting and partial failures no longer report success,
> upgrades merge missing configuration defaults without replacing user values,
> and `runtime_effect_gate.py` rejects hidden fallback or stale waterlines.
>
> **2.11.4:** Continuous catch-up now survives installs and upgrades. The native
> collector preserves the earliest boundary in `collector-activation.json`,
> streams retained rollout files in bounded batches, resumes from durable
> cursors, and publishes `coverage-status.json`. If capture is interrupted
> between an append and its derived-state commit, the next native pass must
> complete deterministic `heartbeat --repair` before resuming. A hidden five-minute task
> installed by `install_maintenance_supervisor.py` runs
> `maintenance_supervisor.py` so mechanical and backup debt continue while
> Codex is closed; semantic debt resumes when Codex is available. Oversized
> jobs use the hash-bound `semantic_plan.py` map-reduce path, with every actual
> prompt capped below `900,000` characters and UTF-8 bytes. The dashboard
> distinguishes coverage, mechanical, semantic, and backup debt and reports
> recoverable backlog as `catching-up`.
> Global coverage is refreshed only from the complete activation scope; an
> incremental one-rollout event cannot replace the all-source status view.
> Successful no-op verification also converges legacy cursor identity and
> completion metadata, so zero-byte metadata debt does not remain permanent.
> This one-time convergence also covers legacy excluded subagent/exec cursors
> without importing their content into top-level memory.
> On Windows, the semantic-runtime probe now executes the expanded absolute
> Codex path instead of passing a literal `~` path to the operating system.
> Runtime-blocked semantic debt exposes its redacted cause in the dashboard.
> Background-executor releases now require a synthetic live scheduler canary
> that proves pending `1 -> 0` and summary registry `0 -> 1`.
>
> **2.4.6:** This stable release adds a cross-device semantic runtime contract,
> explicit local E5 realization, and linear-time raw-pointer construction for
> first-time semantic indexing. It synchronizes the interface and pinned
> dependencies without copying platform runtimes, model caches, or indexes.
>
> Windows v1.7.8 security note: the desktop dashboard shortcut now targets a
> dedicated no-console native launcher with no command-line arguments. The
> installer stores the validated Python runtime and active archive path in a
> local `.codex` configuration file; it no longer creates a shortcut that
> directly launches `pythonw.exe` with a script and long argument string.
> Packaged entry: `memory-wuxian-dashboard-launcher.exe`; shortcut policy:
> `no command-line arguments`.
> Windows v1.7.9 additionally resolves the real user profile from the current
> Windows SID, so isolated installer environments cannot write a
> `CodexSandboxOffline` launcher target.
> Windows v1.7.10 keeps validated ordinary Windows paths when invoking Python,
> avoiding extended-path startup failures with non-ASCII installations.
> Windows v1.7.11 removes visible console subprocesses from dashboard startup
> and Settings reads, serves the persisted snapshot before background refresh,
> and animates changed metrics. Windows installation and upgrade recreate the
> native desktop dashboard shortcut by default. A bare Codex Skill copy has no
> traditional installer wizard; first activation runs the supplied bootstrap
> and shortcut installer.
> Windows v1.8.0 also removes the legacy PowerShell collector loop, uses direct
> no-console process launches, and adds event-driven dashboard updates with
> project, source, and origin-device filters.

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Memory無限 is a file-based Codex Skill for persistent, hierarchical, and verifiable conversation memory beyond the active context window.

The installable Skill identifier is `memory-wuxian`; `Memory無限` is its project and display name. The design keeps exact source records as historical authority, uses summaries as navigation, and returns to raw text before treating a historical claim as verified.

## What it provides

- Append-only Markdown conversation records with timestamps and SHA-256 integrity fields
- One complete, automatically updated Markdown transcript per conversation
- Conversation-scoped pending rounds and reply relationships during concurrent tasks
- Conversation-scoped Level-1 summaries and conversation-scoped higher-level summaries
- Source-aware hierarchy audits: raw ranges for Level-1 and direct child identities for higher levels
- Separate message, timeline, concept, and summary indexes for every conversation, plus global routing indexes
- Script-detected summary boundaries after 5 completed rounds or 20,000 visible characters
- Ephemeral AI summary generation only when a completed round makes a summary due
- Bounded runtime context refresh after configured round, utilization, or compaction thresholds
- Index-first retrieval with raw-text verification
- Append-only policy events with explicit revision, withdrawal, and reaffirmation lineage
- A `current-policy` retrieval mode that prevents explicitly superseded rules from being presented as current
- Preview-first state and index recovery
- Heartbeat validation, maintenance, and repair modes
- Incremental Codex rollout parsing with stable source IDs and per-session cursors
- Persistent per-conversation Codex-reported Token usage ledgers with reset-safe historical backfill
- Event-driven synchronization through a native macOS LaunchAgent or Windows scheduled task
- One latest verified desktop snapshot with a SHA-256 manifest and an append-only backup log
- One latest workspace recovery backup for derived-file reconstruction
- Federated read-only replicas with delta bundles, artifact-ledger cursors, and cross-device retrieval
- Parallel SSH and encrypted cloud-folder federation transports
- An independent Environment Registry for verified cross-device Rule and Skill convergence
- An experimental local adapter for official ChatGPT export ZIP files and `conversations.json`
- A transparent file layout with no database dependency

## Install

### One-file installers

Download the installer for the operating system from the latest GitHub Release:

- macOS: `MemoryWuxian-<version>-macOS-universal.pkg`
- Windows: `MemoryWuxian-<version>-Windows-x64-Setup.exe`

The status console opens from its last successful browser-local response and a
persisted, source-validated statistics snapshot. Unchanged archives do not need
to reread the complete raw history; stale or malformed snapshots rebuild
automatically from authoritative archive records. Its optional local achievement
system tracks archive size, archive-context and message-only token estimates,
Codex-reported cumulative usage, dialogue depth, project growth, summary
hierarchy, and raw-verified retrieval.

Opening that one file installs the Skill under the current user's Codex directory,
initializes `Documents/MemoryWuxianArchive`, and activates continuous Codex capture.
Reinstalling or upgrading preserves the existing configuration and archive. Uninstalling
removes the program and background integration but deliberately leaves conversation
history in place. Public builds are unsigned unless the release workflow is supplied
with platform code-signing credentials, so the operating system may request an explicit
security confirmation.

Skill ZIP verification accepts fixed macOS system path aliases only when
`/var`, `/tmp`, or `/etc` resolves to its exact `/private` target. Arbitrary
package-path links and junctions remain rejected.

### Codex Skill installer

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

Official installers register a daily stable-release check. The updater ignores branches,
drafts, and prereleases, downloads both the platform installer and its published SHA-256
file, and refuses an update unless the checksum and filename match. Windows
installs a verified update silently at the next login. On an existing macOS
installation, the updater extracts only the verified Skill payload and runs the
rollback-capable user-space transaction without opening Installer or requesting
an administrator password. The full PKG remains the first-install and recovery
path. Disable the check with
`python scripts/install_auto_update.py --uninstall`.

Every Windows install or automatic upgrade preserves the archive named by
`~/.codex/memory-wuxian-active-root.txt`, verifies or installs the native-window
dependencies, and atomically rebuilds the desktop
`Memory无限状态台.lnk` shortcut with the current validated Python runtime. This
prevents a Codex runtime upgrade from leaving the dashboard bound to a stale
absolute `pythonw.exe` path. The installer resolves the real user profile from
the installed Skill path, so an isolated desktop-client `USERPROFILE` cannot
redirect the collector or shortcut to a sandbox archive.

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill --apply
python3 scripts/memory_cli.py --root "$ARCHIVE" status
python3 scripts/memory_cli.py --root "$ARCHIVE" backup
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
python3 scripts/memory_cli.py --root "$ARCHIVE" retrieve --query "summary trigger" --mode current-policy
```

Continuous capture does not call a model. Scripts create a source-locked summary job only after a complete dialogue round reaches a configured threshold. The one-shot semantic worker then invokes the authenticated Codex CLI in ephemeral mode, ingests the constrained JSON summary, and exits.

## Runtime context refresh

Memory無限 can periodically restore compressed history into a continuing Codex task without opening a replacement task. `context-refresh-status` detects the latest completed-round milestone, context-utilization threshold crossing, or compaction event. When refresh is due, `context-capsule` selects the highest useful semantic-summary levels, suppresses covered child summaries, adds a small recent-dialogue tail, and emits temporary derived context with a stable `refresh_id`. The active reasoning context skips an ID it already contains; after compaction removes it, the same capsule may be loaded again. Reading is fully read-only and requires no acknowledgement or permission-bearing write. The deprecated `ack-context-refresh` command exists only as a no-write compatibility no-op and must not be used in normal operation.

The capsule budget is derived from the model context window. The default is one percent, with a 3,000-token soft cap and an absolute 10,000-token ceiling. A capsule is navigation context rather than historical authority: claims still return to append-only raw records for verification, and the generated capsule must never be archived as a new source message. Reusable rules for workspace `AGENTS.md` files are shipped under `agents/` and `templates/`.

## Policy evolution

Level-1 summaries may record explicit policy events as `adopted`, `revised`,
`withdrawn`, `reaffirmed`, `proposed`, or `uncertain`. A revision or withdrawal
supersedes an active rule only when it cites the exact prior statement in the
same scope. Recency alone never changes validity. Derived policy indexes remain
rebuildable, while raw conversations and existing summaries stay immutable.

Use `retrieve --mode current-policy` for operational rules, defaults, and
strategies that may have changed. It returns the matched lineage, restores its
source messages, and also searches newer matching raw text. Existing summaries
created before this feature contain no policy events unless they are separately
reanalyzed; in that case the command reports that no explicit lineage matched
instead of silently treating an early statement as current.

## Local status dashboard

On macOS, every PKG install or upgrade rebuilds
`~/Desktop/Memory無限操作台.app`. The native WebKit launcher reads
`memory-wuxian-dashboard-launcher.json`, so it follows the current Python,
Skill, and preserved active archive paths instead of embedding machine-specific
paths. `install_dashboard_app_macos.py` verifies the application version,
signature, executable hash, configured paths, and launcher self-check. A release
that changes dashboard behavior is incomplete until this desktop application
has been replaced and opened successfully.

On Windows, start the local dashboard as a native application window. It uses the installed Microsoft Edge WebView2 runtime, the bundled Memory Wuxian archive/infinity application icon, and preserves the complete dashboard UI without browser chrome:

```powershell
python scripts/memory_dashboard.py `
  --root "C:\path\to\memory-wuxian-archive" `
  --config "C:\path\to\memory-wuxian\config.yaml" `
  --window
```

Run `scripts/bootstrap_windows.ps1 -InstallMissing` once if the environment check reports that the open-source `pywebview` package is missing. The window offers persistent Chinese, English, and Japanese UI modes, refreshes quietly in the background every 30 seconds, and shows the Codex task title for each conversation, messages, completed rounds, summary levels, daily archive volume, pending summaries, archived visible source characters, an explicitly labeled archive-token estimate, and Codex-reported cumulative model usage. Character totals include stored user and visible assistant dialogue but exclude generated summaries. Estimated archive tokens use a CJK-aware size heuristic; they are neither billing usage nor the tokens consumed by summary generation. The cumulative value comes from retained top-level Codex rollout `token_count` telemetry. Counter resets form separate additive segments; duplicate snapshots do not create requests; cached input and reasoning output remain included subfields and are never added to `total_tokens` again. Retained rollout files can be backfilled exactly, while deleted telemetry, ChatGPT web conversations, and official ChatGPT exports cannot be reconstructed as actual model usage. Separately, the latest request count is compared with the advertised context window; that ratio may exceed 100 percent and is not a precise occupancy or remaining-context gauge.

The Windows installer runs
`scripts/install_dashboard_shortcut_windows.ps1` after every install or upgrade.
It recreates `Memory无限状态台.lnk` with the current Skill path, active archive,
bundled icon, and validated `pythonw.exe`. Uninstalling removes only the shortcut,
not the archive.

The dashboard binds only to localhost and sends no archive data to an external service. Its routine status views are read-only. The Memory search view uses the same verified retrieval engine as the CLI and offers keyword, multilingual semantic, and hybrid modes. Every result remains human-readable and includes its title, timestamp, speaker, raw line range, and SHA-256 backlink. Explicit Settings actions may enable or disable encrypted cloud-folder exchange, run one immediate exchange pass, or import a user-selected ChatGPT export into the local archive. Without `--window`, the cross-platform browser mode remains available; use `--no-browser` to start only the local server, or `--port` to choose another local port.

The local read-only endpoint is `/api/memory-search`; its mode values are
`keyword`, `semantic`, and `hybrid`.

## Automatic Codex capture on macOS

Installing a Skill does not by itself subscribe to Codex client events. Build the Rust collector once, then install its persistent LaunchAgent:

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

The LaunchAgent keeps one optimized Rust process alive and receives filesystem change notifications from the operating system, with an adaptive size/mtime fallback for missed deep-directory events. The fallback runs every 5 seconds while active, slows to 30 seconds after 2 idle minutes, and to 5 minutes after 15 idle minutes; a native event wakes it immediately. It stores user messages, visible assistant commentary/final answers, and the lightweight tool activity already visible in top-level Codex task timelines. Tool activity retains the tool name, nested tool names, and command text when available; tool outputs, system instructions, hidden reasoning, and subagent sessions remain excluded. A per-session cursor and stable source-derived IDs make retries idempotent.

The native collector directly owns event-driven JSONL parsing, raw append, per-conversation transcript updates, deterministic routing indexes, cursor writes, due Level-1 job creation, and atomic backup-debt registration. It records successful Codex file edits with their file paths, change types, move targets, addition/deletion counts, hunk line ranges, and exact unified diffs. General tool output and hidden reasoning remain excluded. Existing installations perform a one-time patch-event-only history backfill. When a job becomes due, it runs one Python wrapper that invokes one ephemeral Codex CLI summary process and exits after ingestion. The Python CLI remains the low-frequency interface for summary ingestion, retrieval, heartbeat, backup maintenance, and preview-first reconstruction.

Every imported conversation is also written to its own file under `memory/conversations/`. A transcript contains only one conversation ID and includes both exact machine-readable records and readable message text. Its isolated indexes are stored under `memory/indexes/by-conversation/<conversation>/`. The immutable files under `raw/` remain authoritative; per-conversation transcripts and indexes are deterministic views that can be rebuilt without changing raw history.

On macOS, grant Full Disk Access to `bin/memory-wuxian-collector` when the archive or backup is stored under protected `Documents` or `Desktop` locations. Verify the exact executable in the generated plist before claiming automatic capture is active. Background definitions preserve a stable Python entry path such as `/opt/homebrew/bin/python3`; they do not resolve it to a version-specific Homebrew Cellar path, so a routine Python upgrade does not create a new privacy identity and repeat Desktop or Documents permission prompts.

The collector publishes lightweight runtime telemetry under `imports/codex/collector-telemetry.json`. The status console shows its active, idle, or deep-idle mode, current safety interval, latest filesystem event, latest archive write, wakeups during the last hour, and CPU/memory use. A new process first reports `phase=starting` and `ready=false`; it becomes `phase=ready` only after initial synchronization succeeds. Telemetry renews on every monitoring interval, including idle intervals, and carries independent source and archive watermarks. The dashboard warns when startup is still pending, telemetry is stale, the collector is stopped, or the source watermark is ahead of the archive watermark.

Existing macOS installations update through `scripts/install_macos_transaction.py`. It stages a candidate, runs an isolated candidate probe that must capture exact synthetic user and assistant messages, and cuts over only after that proof passes. It then verifies a replacement collector PID, fresh telemetry, and the current dashboard. Any post-switch failure restores the previous Skill, LaunchAgent, and collector. Routine updates use this user-space transaction and do not require the full installer or an administrator password.

Before switching files, the transaction waits for the shared archive lock, verifies that no native recovery debt remains, and stops the old collector while still holding that lock. It releases the lock before starting the replacement collector and loads scheduled maintenance only after the replacement reports ready. This idle-boundary handoff prevents an interrupted write or maintenance race from turning a routine update into a full-history recovery audit. If the handoff or first directory switch fails, the previous collector is restored immediately.

Initial collector synchronization never waits for an AI summary. If startup
crosses a summary threshold, it persists the immutable summary job and lets the
existing semantic-backfill worker process that queue after collector readiness.
This keeps exact raw capture and summary debt durable without allowing a long
Codex CLI call to block transactional cutover.

Before a time-bounded report relies on Memory无限, run `scripts/archive_waterline.py --cutoff <ISO-8601>`. The preflight verifies persisted source cursors through the report cutoff. `--backfill` is explicit and bounded to the retained source files reported as lagging; the report may proceed only after the result is `covered`.

The daily archive chart uses the same character-count bars as before. Hovering a bar, or focusing it from the keyboard, opens a localized bubble with the full date, exact archived-message count, and exact visible-character count.

## Import ChatGPT conversations

ChatGPT ordinary chats are not exposed through the Codex rollout stream. Import an official ChatGPT data export without extracting it first, or pass its extracted directory or `conversations.json` directly:

```bash
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
```

Use repeated `--conversation-id <native-id>` options to select specific conversations. The importer follows the export's current visible branch, skips system messages and abandoned regenerated-answer branches, preserves titles and stable IDs, and safely imports the same or a newer export again without duplication. Imported chats use `chatgpt:<conversation-id>` and enter the normal backup, indexing, summary, retrieval, and dashboard flows. This is an export adapter, not real-time ChatGPT capture.

The same adapter is available under Dashboard > Settings > Import ChatGPT conversations. The selected ZIP or JSON file is streamed only to the localhost dashboard server, parsed through the existing importer, and removed from temporary storage after the operation. Memory無限 does not log in to ChatGPT, request account credentials, or upload the export to another service.

This feature is **experimental**. Automated tests cover synthetic ZIP and JSON fixtures, visible-branch selection, duplicate-safe repeated imports, stable IDs, and local dashboard upload. It has **not yet been tested with a real user-provided ChatGPT official export**, because no such export has been supplied to this project. Export schemas may change, so the first real import should be treated as a validation run and its counts and recovered conversations should be reviewed before relying on it.

## Automatic Codex capture on Windows

Run the environment bootstrap first. It reports the detected Python version and paths for Python, Codex CLI, the bundled collector, and Codex sessions. The only supported main runtime is Python 3.14.x. With `-InstallMissing`, it installs Python 3.14 only when no supported runtime or compatible Codex-bundled Python exists.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows.ps1
```

The release includes `bin/memory-wuxian-collector.exe`, so Rust and Visual C++ Build Tools are development-only dependencies. Rebuild the collector only when changing native source, then install its user-level startup integration:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_native_collector.ps1
python scripts/install_codex_autosync_windows.py `
  --archive-root "$PWD\memory" `
  --python-executable "C:\path\to\python.exe" `
  --codex-cli "C:\path\to\codex.exe" `
  --load
```

The task starts at user logon and is also started immediately by `--load`. If local policy denies Task Scheduler registration, the installer falls back to a direct native-collector command in the current user's `Run` registry key. It does not create a PowerShell loop, CMD wrapper, or VBS launcher. Archive data remains in the selected workspace root. Remove either backend with `python scripts/install_codex_autosync_windows.py --archive-root "$PWD\memory" --uninstall`.

Successful collector transactions append a lightweight local event. The
localhost dashboard receives changes over SSE and keeps a two-minute-or-slower
polling path only as a disconnect fallback.

The implementation contracts are `/api/events`, `project-filter`,
`source-filter`, and `device-filter`. Before a release claim, run
`scripts/run_release_rehearsal.py` under the rules in
`references/release-rehearsal.md`.

The installer also records the selected archive in `~/.codex/memory-wuxian-active-root.txt`. CLI retrieval and maintenance commands use that active archive when `--root` is omitted, preventing an installed Skill's empty template archive from being mistaken for the live archive. `--root` and `MEMORY_WUXIAN_ROOT` remain explicit overrides.

Retrieval itself does not take the archive's exclusive write lock. If the current Codex workspace can read the active archive but cannot write there, retrieval still succeeds and simply skips `last-query.md` and query-log updates.

The collector uses an explicit 16 MiB worker stack so a fresh full-history import can safely parse and index large Codex rollout sets on Windows, where the default console main-thread stack is comparatively small.

With the default configuration, every successful native memory mutation atomically updates `pending/backup-debt.json` after the primary archive write. The low-frequency maintenance worker coalesces all pending mutations into one complete verified snapshot under `~/Desktop/Memory無限-记忆归档备份/`, then clears the debt only after success and removes older snapshot directories. The collector never blocks startup or capture by copying the complete archive. The backup root therefore contains one latest recovery copy plus the append-only `backup-log.jsonl` operation history, while the dashboard warns whenever a newer snapshot is pending.

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

## Federated memory

Version 1.6.0 keeps every device's local archive as that device's exclusive
writable authority. A device exports its own new raw records, summaries, and
confirmed conversation titles as `.mwxb` delta bundles. A trusted peer imports
them into a read-only replica under the default sibling directory:

```text
<archive>-federation-cache/
├── peers/<origin-node-id>/
└── global-index/
```

Peer records never enter the receiving device's local `raw/`, `state.json`,
round counters, or summary counters. Reconstructible peer indexes qualify
identifiers by origin node; `retrieve-global` combines those routes with the
current local authority at query time. `retrieve` remains local-only.

Initialize two nodes and exchange an offline delta:

```bash
python3 scripts/memory_cli.py --root /path/to/node-a init-node --display-name "Node A"
python3 scripts/memory_cli.py --root /path/to/node-b init-node --display-name "Node B"
python3 scripts/memory_cli.py --root /path/to/node-b add-peer --node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-a export-delta \
  --output /trusted/path/node-a-0001.mwxb \
  --target-node-id <node-b-id>
python3 scripts/memory_cli.py --root /path/to/node-b inspect-bundle \
  --bundle /trusted/path/node-a-0001.mwxb
python3 scripts/memory_cli.py --root /path/to/node-b import-delta \
  --bundle /trusted/path/node-a-0001.mwxb \
  --expected-node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-b retrieve-global \
  --query "earlier topic"
```

The artifact ledger detects locally authoritative artifacts even when a summary
or title is created after its original message range. Import verifies artifact
SHA-256, rejects event-sequence gaps and overlaps, and requires each noninitial
bundle to name the SHA-256 of its imported predecessor. Reimporting an accepted
bundle is idempotent. `revoke-peer` blocks future imports and SSH pulls without
silently deleting already imported history.

Large backlogs are exported as bounded, contiguous pages. When `has_more` is
true, use the returned `to_event_sequence` and bundle SHA-256 as the next
export cursor and predecessor. Export state is reconstructible from the
append-only artifact ledger after an interrupted state-cache write.

For authenticated transport, register an SSH peer and pull its next delta:

```bash
python3 scripts/memory_cli.py --root /path/to/local add-peer \
  --node-id <remote-node-id> \
  --host user@example-host \
  --remote-root /path/to/remote/archive \
  --remote-config /path/to/remote/config.yaml \
  --remote-cli /path/to/remote/scripts/memory_cli.py \
  --remote-shell posix
python3 scripts/memory_cli.py --root /path/to/local sync-peer \
  --node-id <remote-node-id>
```

Use `--remote-shell powershell` for a Windows peer. SSH encrypts the connection
and authenticates the transport through strict host-key checking and the
configured SSH user credentials. SSH synchronization also uses bounded
connection and command timeouts. The `.mwxb` format itself is compressed but is
not encrypted and is not cryptographically signed. Offline bundles must
therefore travel only through a trusted channel.

Federation uses Memory無限 node identities and explicit peer records. It does
not reuse OpenAI account sessions, Codex credentials, or OpenAI device identity.
The reconstructible federation cache is excluded from desktop primary-archive
backups. Version 1.6.0 does not provide public-internet automatic discovery,
NAT traversal, or a mobile client.

## Encrypted cloud-folder exchange

Version 1.6.0 adds an asynchronous transport for a user-selected iCloud Drive,
OneDrive, or compatible synchronized directory. Memory無限 does not receive or
store provider credentials. It writes only target-specific `.mwxe` envelopes
after signing the inner `.mwxb` with the origin device's Ed25519 key and
encrypting it to the target device with age/X25519.

Each device keeps its private identity outside the archive, replica cache, and
synchronized directory. Pairing files contain public keys and a fingerprint
only. Compare the fingerprint through a trusted channel before import:

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"
SHARED="$HOME/Library/CloudStorage/OneDrive-Personal"

python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-configure \
  --directory "$SHARED"
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-export \
  --output /trusted/path/this-device-pairing.json
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-import \
  --pairing-file /trusted/path/other-device-pairing.json \
  --expected-fingerprint <fingerprint-shown-on-the-other-device>
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-sync --force
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-status
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-disable
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-enable
```

The selected directory must already exist so a mistyped path cannot silently
become an unsynchronized local folder. On Windows, select the local OneDrive or
iCloud Drive directory shown in File Explorer. Selecting either the provider
root or its existing `MemoryWuxianExchange` child is normalized to the same
canonical queue at `<provider-root>/MemoryWuxianExchange/v1`; this prevents two
paired devices from silently scanning different nested queue roots.

Register the short-lived five-minute scheduler after configuration:

```bash
python3 scripts/install_cloud_sync.py \
  --archive-root "$ARCHIVE" \
  --skill-root "$HOME/.codex/skills/memory-wuxian" \
  --python-executable "$(command -v python3)" \
  --load
```

The task imports available peer envelopes on every wake. Ordinary local changes
are coalesced for 15 minutes, approximately 1 MiB of pending material may flush
early, and the oldest pending change is attempted after 60 minutes. These
timings describe writes into the local synchronized directory; the provider
client controls when network upload completes. Empty checks create no files and
invoke no AI process.

The cloud folder is a transport queue rather than a shared writable archive.
Every node writes only its own outbox and acknowledgements. Imported history
continues to live in read-only peer replicas, and `retrieve-global` follows the
same verified source path for SSH and cloud deliveries. Use `cloud-disable` to
stop exchange without deleting archives, keys, or encrypted cloud files.

On macOS, OneDrive Files On-Demand envelopes may initially appear as directory
entries without readable local bytes. Memory Wuxian probes them to trigger
bounded hydration and treats temporary File Provider availability failures as
retryable, not corrupt. For `environment-v1`, a wider retry that overlaps an
already verified prefix is accepted only when every persisted prefix event
matches exactly; conflicting overlap remains quarantined.

Version 1.6.1 also exposes these operations in the dashboard Settings panel.
The Cloud sync switch enables or disables encrypted exchange together with its
five-minute scheduler. Sync now performs one immediate encrypted exchange pass.
The panel displays the configured provider directory and the observed scheduler
state, so ordinary operation does not require an AI conversation or terminal
commands.

## Project evidence packages

Memory Wuxian can preserve and exchange an explicit, bounded set of project
rules, status and next-plan records, decisions, QA, reports, templates, and
compact supporting artifacts. It does not scan or upload a whole workspace.
Every package preserves exact bytes and SHA-256 hashes, names its predecessor
when applicable, and is stored as an immutable generation. Source-root paths
are not persisted, probable text secrets are rejected, and peer copies remain
read-only.

Project evidence uses its own signed and target-encrypted
`project-evidence-v1` stream. This leaves `archive-v1` and `environment-v1`
unchanged, and older clients can ignore the new stream safely. The dashboard
shows package and stream cursors. Use `project-evidence-query` for bounded
location results and `project-evidence-reconstruct` for exact complete bytes.
See [the project evidence contract](references/project-evidence.md).

```text
project-evidence-build
project-evidence-list
project-evidence-query
project-evidence-reconstruct
project-evidence-status
project-evidence-owner-register
project-evidence-owner-refresh
project-evidence-owner-status
```

A device-local Project Evidence Owner can maintain one explicit closed file
selection. The five-minute model-free supervisor refreshes at most 20 owners
per pass. Unchanged content creates no record; changed stable content creates a
predecessor-linked immutable generation. Source paths remain local, failures
are isolated, and imported packages never create owners.

## Project attachments

Large final deliverables belong to the independent `project-attachment-v1`
stream instead of raising the bounded Project Evidence limit. A closed JSON
specification may select PDF, PPTX, DOCX, XLS/XLSX, TIF/TIFF, PNG, JPEG, or
WebP files. Memory Wuxian preserves the original files untouched and records
exact SHA-256 manifests over deduplicated 4 MiB chunks. One logical file is
limited to 256 MiB and one generation to 1 GiB.

Encrypted delivery is resumable and stream-bound. A receiver materializes no
file until every chunk and the complete-file hash pass. Successful application
writes a reconstruction receipt; missing, corrupt, reordered, wrong-target, or
cross-stream data fails closed. Use the dedicated sync command when only these
attachments are authorized for transfer. See
[the project attachment contract](references/project-attachments.md).

```text
project-attachment-build
project-attachment-owner-register
project-attachment-owner-refresh
project-attachment-owner-status
project-attachment-status
project-attachment-sync
project-attachment-reconstruct
```

## Memory無限 2.0 environment convergence

Version 2.0 adds a second, independent synchronization plane for global Rules,
project Rules, global Skills, and project Skills. It does not turn multiple
devices into one shared writable archive: each device keeps its own local
conversation authority, while peer conversations remain verified read-only
replicas.

Environment artifacts use immutable content-addressed revisions and explicit
node-local bindings. The selected cloud directory carries a separate signed and
target-encrypted `environment-v1` stream with independent event sequences,
predecessor chains, cursors, acknowledgements, staging, and verified Skill
packages. The five-minute task validates incoming data without AI. Transfer
only stages an update; it never installs a Skill or rewrites a Rule by itself.
Since 2.4.1, each batch member has its own stable export identity, including
project registrations. Received projects remain read-only peer metadata and
are never created or activated locally. Skill packages use a safe full YAML
parser: legal nested metadata is supported, while duplicate keys and unsafe
tags are rejected. Installers provide the required PyYAML 6.x dependency.

Compatible global Rule fast-forwards may be registered only when that policy is
explicitly enabled. Project artifacts, Skills, divergence, identity changes,
permission expansion, persistent components, and incompatible runtimes always
require review. Installers preserve rollback material before mutation, switch
atomically, run post-install checks, and append evidence receipts. Promotion of
a reusable project capability to global scope is a separate evidence-gated
workflow with a complete platform matrix and explicit approval.

Verified local architecture lessons can also be recorded as immutable
governance-insight proposals. Paired devices exchange these proposals in the
same signed, target-encrypted Environment stream, but imported proposals remain
read-only evidence. `work-system-governor` must classify and validate a
proposal before an explicit acceptance can create a new Rule or Skill revision.

Evidence-bound product evolution records can preserve a bounded development
history, verified current state, corrected next-development flow, and reusable
lesson candidates. They are exchanged as read-only evidence; receipt never
triggers product remediation or global governance acceptance. Deterministic
jobs collect and queue changed evidence, while AI is invoked only for bounded
semantic review.

The dashboard Environment view exposes inventory, incoming decisions,
conflicts, promotions, and a manual update check. The complete 2.0 CLI families
are:

```text
environment-init
environment-scan
environment-status
environment-list
environment-projects
environment-show
environment-diff
environment-register
environment-validate
environment-export-delta
environment-exchange-status
environment-profile-capture
environment-profile-status
environment-profile-current
environment-profile-rebuild-current
environment-profile-compare
environment-convergence-plan
environment-incoming-status
environment-process-incoming
environment-accept-incoming
environment-bindings-status
environment-register-root
environment-register-project-binding
environment-register-rule-binding
environment-register-project-rule-binding
environment-register-skill-binding
environment-discover
environment-install-rule
environment-recover-rule-installs
environment-install-skill
environment-recover-skill-installs
environment-conflict-assess
environment-conflicts
environment-conflict-resolve
environment-promotion-propose
environment-promotion-transition
environment-promotions
environment-governance-propose
environment-governance-proposals
environment-product-evolution-record
environment-product-evolution-records
environment-governance-ai-discover
environment-governance-ai-status
environment-governance-ai-enqueue
environment-governance-ai-configure
environment-governance-ai-tick
```

### Bounded governance AI

Memory無限 can queue semantic work without keeping an AI conversation active.
Scripts perform five-minute model-free discovery and due checks; an ephemeral
Codex worker runs only when a compatible micro-batch is due. Product batches
trigger at 3 items or 6 hours (maximum 5), classification batches at 5
same-owner items or 24 hours (maximum 10), with an 80,000-character cap and
6 runs per local day. Urgent items may bypass count and age thresholds.

The feature is disabled by default. Product work remains on its source device,
while global classification requires one explicit coordinator. Every result is
a schema-validated draft requiring human review. The worker cannot accept
rules, install Skills, remediate products, or rewrite archives.

### Explainable configuration and device compatibility

Memory Wuxian compiles the existing YAML into a closed, deterministic
configuration-v1 view without changing the source file or initializing an
archive. Every effective value reports its source and the effective value set
has a stable SHA-256. Unknown keys, duplicate keys, invalid types, and invalid
ranges fail closed.

`environment-capability-status` reports only product, platform, runtime,
protocol, and interface compatibility. A missing legacy offer remains
diagnostic and does not interrupt existing synchronization. Compatibility
never grants installation, trust, permissions, or synchronization authority.
The dashboard System tab presents the same read-only information.

```bash
python3 scripts/memory_cli.py configuration-compile
python3 scripts/memory_cli.py configuration-explain
python3 scripts/memory_cli.py environment-capability-status
python3 scripts/memory_cli.py environment-capability-status --peer-offer /path/to/peer-offer.json
```

## Privacy and integration boundary

- Use `--root` outside the repository for private archives.
- Mutable files under the bundled `memory/` directory are excluded by `.gitignore`.
- The CLI can redact obvious secrets when explicitly configured, but users remain responsible for deciding what may be persisted.
- Automatic capture requires the supplied native LaunchAgent, Windows scheduled task, or another explicitly configured client hook.
- Offline `.mwxb` bundles contain readable archive material. Use SSH or another trusted transfer channel; SHA-256 does not provide encryption or sender authentication.
- Cloud directories contain signed, target-encrypted `.mwxe` envelopes and encrypted acknowledgements. Private device identities never enter the synchronized directory.

## Complete maintenance command surface

The quick-start sections above cover normal operation. The following names are
the complete public maintenance surface and are listed explicitly so releases
cannot silently add an undocumented command:

Since v1.7.4, pull requests and installer releases run a repository-owned
documentation contract. Functional changes must update all three localized
READMEs, `CHANGELOG.md`, and the reviewed feature contract together.

Since v1.7.5, redirected Windows CLI output is always UTF-8 even when the
parent process supplies a legacy `PYTHONIOENCODING` such as GBK. Interactive
legacy consoles escape only unsupported characters instead of terminating the
memory operation.

Since v2.4.2, the Windows native dashboard launcher requests an unused
loopback port from the operating system and opens the actual assigned port.
It does not assume port 8765, so another local application cannot replace the
Memory Wuxian interface by owning that port first.

```text
init
append
sync-codex
import-chatgpt
status
context-refresh-status
context-capsule
backup
make-summary-job
ingest-summary
retrieve
conversation-tail
register-title
rebuild-state
rebuild-conversations
rebuild-indexes
index-generation-build
index-generation-status
index-generation-activate
index-generation-rollback
heartbeat
rebuild-deterministic-indexes
init-node
add-peer
revoke-peer
export-delta
inspect-bundle
import-delta
rebuild-global-index
retrieve-global
federation-status
sync-peer
cloud-configure
cloud-pair-export
cloud-pair-import
cloud-sync
cloud-status
cloud-enable
cloud-disable
configuration-compile
configuration-explain
environment-capability-status
```

Manual semantic-summary recovery additionally uses `semantic_worker.py` and
`semantic_backfill.py`. Run the documentation contract before committing:

```bash
python3 scripts/check_documentation_contract.py
```

## Development

Run the functional test suite without creating bytecode files:

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Candidate CI runs feature branches only through pull requests and runs pushes
only on `main`. Ubuntu and Windows execute one full suite per job; macOS uses
focused platform contracts on pull requests and the full suite on `main`.
Rehearsal scenarios covered by a successful full suite keep individual hashed
reference logs through `--reuse-unittest-evidence` instead of rerunning the
same modules. Installer publication consumes the successful same-SHA `main`
run. This reduces duplicate work without removing release contracts.

Architecture decisions and implementation contracts are documented in [`PROJECT.md`](PROJECT.md) and [`references/`](references/). Changes are recorded in [`CHANGELOG.md`](CHANGELOG.md). `README.md`, `README.zh-CN.md`, and `README.ja.md` are maintained as one documentation contract and must be updated together when documented behavior changes.

Since v2.4.3, [`PRODUCT_ARCHITECTURE.md`](PRODUCT_ARCHITECTURE.md) is the
canonical module-boundary contract and
[`docs/module-architecture.json`](docs/module-architecture.json) is its
machine-readable ownership registry. Every production file must have exactly
one owner, and `scripts/check_architecture_contract.py` rejects unowned files,
overlapping ownership, and declared prohibited dependencies. Windows and
macOS package builds fail if these architecture-gate files are absent.

## Versioned execution roadmap

[`references/version-roadmap-v2.5-to-v3.0.md`](references/version-roadmap-v2.5-to-v3.0.md)
is the implementation authority for the ordered v2.6 through v2.10 work. Each
version requires its predecessor's release and recovery evidence, a bounded
work contract, an exact-candidate macOS and Windows gate, and a proven rollback
before publication. Personal Environment convergence is reserved for v2.10;
v3.0 remains conditional on a separately accepted incompatible public-contract
decision.

### v2.6 index safety

`index-generation-build` creates an immutable shadow generation from an exact,
SHA-256-verified raw and summary source manifest without changing active index
files. `index-generation-status` verifies its closed manifest and payload.
`index-generation-activate --generation-id <id>` is preview-only until
`--apply` is supplied, and `index-generation-rollback` likewise previews the
previous pointer before an atomic pointer-only rollback. The fixed v2.6
retrieval benchmark records its corpus hash, policy lineage and exact
disambiguation cases, and rejects unexplained result deltas. None of these
operations modifies raw history or automatically activates a received index.

## License

Memory無限 is released under the [MIT License](LICENSE.txt).
## v1.9 guarded portability

`migration-preview` reports destination space and the immutable source
manifest without writing. `migration-apply` performs a verified copy and never
deletes the source; `--switch-active` is required to change the active-root
pointer after all manifests agree. `project-package-export` creates a readable
package for selected conversation IDs, while `project-package-import` verifies
it into a read-only replica outside local raw history.
## v1.10 historical views

`as-of` reconstructs a read-only view at a timezone-qualified timestamp.
`decision-graph` derives rule and decision lineage from explicit policy events.
Its `raw_sources` retain message IDs, raw paths, and record hashes; the graph is
never an authority that can overwrite history.
## v1.11 retrieval quality and optional local semantics

`retrieval-evaluate` measures a readable JSONL test set with recall-at-k,
wrong-citation counts, and latency. `semantic-index-build` retains the default
offline `local-hash-v1` provider: it downloads no model and calls no service.
For multilingual neural retrieval, run `python scripts/install_multilingual_e5.py`
and then `semantic-index-build --provider multilingual-e5-small`. The optional
384-dimensional `intfloat/multilingual-e5-small` ONNX model is pinned to an
immutable revision and exact SHA-256 values, runs from an isolated environment,
disables remote model code, and performs inference offline. On Windows the
isolated runtime is explicitly bound to Python 3.12 and accepts Unicode Skill,
archive, worker, and index paths.
`semantic-retrieve` verifies each hit against raw SHA-256 and returns the
conversation/message ID, raw path, and exact line range.
`semantic-index-clear` removes only disposable vectors; raw history and keyword
retrieval continue to work.

The E5 interface can also be registered as an immutable
`global-runtime-contract` in the independent Environment Registry:

```bash
python scripts/memory_cli.py semantic-runtime-status
python scripts/memory_cli.py environment-register-semantic-runtime \
  --origin-node-id <node-id> --apply
python scripts/memory_cli.py environment-realize-semantic-runtime
python scripts/memory_cli.py environment-realize-semantic-runtime --apply
```

## v2.7 background autonomy and diagnostics

Memory Wuxian now persists model-free maintenance work in a closed queue with
stable idempotency keys, leases, bounded retries, restart recovery, and
`quarantined` failure state. `maintenance-status` compares desired and actual
collector/worker state; `maintenance-diagnostics` writes a redacted bundle
without raw dialogue, credentials, or local user paths. A completed dialogue
boundary must first become `semantic-ready` through `semantic_dispatch.py`
before the existing one-shot AI worker may run. Mechanical ticks invoke no AI,
and summary failure does not stop native capture.
`maintenance-requeue` is an explicit operator action for one quarantined job.
It preserves the prior job hash, attempts, and redacted error in an immutable
receipt before returning that same job to the bounded retry path.

```bash
python scripts/memory_cli.py maintenance-enqueue --kind archive-health --idempotency-key health:manual
python scripts/memory_cli.py maintenance-requeue --job-id job-<sha256> --reason "worker contract upgraded"
python scripts/memory_cli.py maintenance-status
python scripts/memory_cli.py maintenance-tick --maximum-jobs 20
python scripts/memory_cli.py maintenance-diagnostics
```

## v2.8 lossless shadow storage and resumable transfer

The optional `exact-byte` shadow store writes content-addressed objects and
closed ordered manifests under `shadow-content-v1`. Each entry retains a stable
source identity, relative path, byte length, and whole-file SHA-256. Build,
reconstruction, disable, and transfer are preview-first. Per-domain
`checkpoint` files resume only contiguous verified ranges; duplicate replay is
idempotent, while gaps, overlaps, corruption, tampering, and destination
conflicts fail closed with explicit explanations. Removing the shadow path
leaves raw history and the existing `archive-v1` and `environment-v1` streams
unchanged.

```bash
python scripts/memory_cli.py content-shadow-build --source-root /snapshot --source-id node:snapshot --file raw/a.md
python scripts/memory_cli.py content-shadow-status
python scripts/memory_cli.py content-shadow-verify --manifest-id <manifest-id> --source-root /snapshot
python scripts/memory_cli.py content-shadow-reconstruct --manifest-id <manifest-id> --destination /restore
python scripts/memory_cli.py content-shadow-disable
python scripts/memory_cli.py content-transfer --manifest-id <manifest-id> --target-archive-root /target --domain archive --target-id <node> --start 0 --count 100
```

## v2.9 unified read-only access and governed updates

`readonly-query`, `readonly-http`, and `readonly-mcp` use one bounded service
and the same `memory.query` contract. Results include confidence, exact raw
provenance, SHA-256, and verification state. HTTP accepts GET only and binds to
loopback; MCP advertises one read tool and no write, installation, pairing,
path, command, or remote-control tool. Hybrid mode falls back to verified
keyword search when a semantic index is stale or unavailable.

```bash
python scripts/memory_cli.py readonly-query --query "prior decision" --mode hybrid --limit 20
python scripts/memory_cli.py readonly-http --host 127.0.0.1 --port 8766
python scripts/memory_cli.py readonly-mcp
python scripts/memory_cli.py summary-budget-status --metrics-json metrics.json --policy-json policy.json
```

Update metadata distinguishes stable, beta, and development channels. A failed
verified delta falls back to a verified full package. Downloads remain
`staged-awaiting-user-approval`; only a second command with `--approve-install`,
`--expected-version`, and `--expected-sha256` may invoke the existing installer.
Governed beta/development or delta metadata is supplied with `--channel` and
`--update-metadata-json`. Release metadata is authenticated with a detached
Ed25519 SSH signature against the pinned `keys/update-allowed-signers` identity
before channel selection or download. Summary-budget checks are deterministic and model-free,
and can enqueue one idempotent completed-round job without invoking AI.

## v2.10 personal Environment convergence

Version 2.10 can inventory explicitly supplied global Rule files and installed
Skill roots into a deterministic, device-independent profile. Profiles retain
stable installation and provider identities, declared versions, exact tree or
file SHA-256 evidence, counts, platform applicability, and managed Rule-block
identities. They never retain source paths, usernames, hostnames, credentials,
environment values, caches, models, archives, conversations, or indexes.

Capture is preview-first. `--apply` creates one immutable generation linked to
its predecessor and atomically advances a rebuildable current pointer.
Unchanged capture creates neither a generation nor an export event. The
existing `environment-v1` stream transports generations only to trusted peers,
where they remain read-only replicas with `automatic_activation=false`.

Comparison reports `same`, `missing-local`, `missing-peer`,
`content-differs`, `platform-inapplicable`, and `inventory-incomplete`.
Convergence plans are bounded previews: system-bundled and plugin-managed
Skills remain provider references, while user-managed items without an exact
existing immutable Environment artifact remain `evidence-only`. A profile can
never invoke the Rule or Skill installer.

```bash
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json --apply
python scripts/memory_cli.py environment-profile-status
python scripts/memory_cli.py environment-profile-current
python scripts/memory_cli.py environment-profile-rebuild-current
python scripts/memory_cli.py environment-profile-compare --peer-node-id node-mac
python scripts/memory_cli.py environment-convergence-plan --peer-node-id node-mac
```

Optional `--artifact-links` input must conform to
`schemas/environment-convergence-artifact-links.schema.json`; see
`examples/environment-convergence-artifact-links.json`. A valid link still
produces only an existing-installer preview and never authorizes activation.

The dashboard Environment tab shows local generation and export counts,
trusted peer profile replicas, and a read-only comparison preview.

The signed and target-encrypted `environment-v1` stream transports the
contract to paired devices. It pins the model revision, artifact hashes,
runtime packages, query/passage prefixes, pooling, normalization, similarity,
and installer entry point. Receiving or accepting it does not install or
download anything. Each device must explicitly realize the accepted contract
into its own compatible local runtime. Model files, virtual environments,
credentials, and semantic indexes remain device-local.
