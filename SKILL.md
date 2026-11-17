---
name: memory-wuxian
description: Persist, summarize, search, restore, and synchronize conversation memory; synchronize selected AGENTS.md rules, Skills, and working files across devices.
---

# Memory Wuxian

Use for relevant memory retrieval and explicitly selected environment synchronization.
Raw history is append-only authority; summaries locate evidence. Preserve originals.
Do not capture hidden reasoning, tool outputs, subagent or internal execution sessions.

`core/live.py --config <local-config>` directly captures top-level Codex sessions, runs
eligible summaries, core-v1 synchronization and backups once per scheduled invocation.
Use `sessions_root` for the Codex session directory. The old collector and old archive
bridge are not normal runtime dependencies. `core/collector.py` is the sole input parser.
The bundled `bin/memory-wuxian-envelope` supplies device identity and authenticated
encryption. Device keys, paths and local configuration remain local.

`core/configure.py --help` configures a new device; existing local configuration and
archive checkpoints require explicit migration, never silent resetting.
For a 2.19.x upgrade, preview `core/migrate.py --old-root OLD --sessions SESSIONS`.
Quiesce old writers before applying with `--new-root NEW --config OLD_CONFIG --apply`.
Keep the old archive until raw, summary, capture and launcher checks pass. Do not run
both capture schedulers against the new archive. File-change records retain full diffs.
`core/runtime.py --root <archive> status|query|context|backup` is the local CLI.
`core/mcp_server.py --root <archive>` provides read-only tools; recover only needed context.
`core/dashboard.py --root <archive> --config <local-config> --port 8765` serves localhost.

Rules, Skills and selected files synchronize only to explicitly bound destinations;
Global rules use `environment.path_bindings` (stable name to local path) to replace
device paths with `{{MW_PATH:name}}` references in transit. The accompanying
`path-keys.json` declares required names. Receivers must configure those names;
missing mappings block application without changing AGENTS.md or sending an ACK.
Missing source mappings are reported as `waiting-source`, not a sync failure;
other selections continue, and the pending selection is retried on later ticks.
Local AGENTS.md remains rendered and unchanged on publication. Old receivers reject
the two-file template; upgrade them before receiving it. Never guess remote paths.
Environment synchronization compares local and incoming files against a verified
content baseline: accept remote-only edits, retain local-only edits for publication,
and use Git three-way merge for non-overlapping UTF-8 changes. Missing bases,
overlapping edits or unavailable merge tooling retain originals and report a conflict.
Skill trees commit atomically after merged dependency checks. Each accepted incoming
version and its base content are recorded in the same transaction as changed files.
The existing adaptive worker checks environment synchronization each completed tick;
short polling reduces divergence but does not resolve concurrent edits by timestamp.
Publishing a package is not receiving or applying it.
Only receiving-device ACKs establish delivery. Summary accuracy is not proven by hashes;
retrieve original evidence when accuracy matters. Keep locks and atomic commit recovery.
The complete distribution bundles stable installer 1.1.0. The normal dashboard entry enables explicit update checks; no background release polling. Do not reintroduce retired governance or installer paths.
