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
`core/runtime.py --root <archive> status|query|context|backup` is the local CLI.
`core/mcp_server.py --root <archive>` provides read-only tools; recover only needed context.
`core/dashboard.py --root <archive> --config <local-config> --port 8765` serves localhost.

Rules, Skills and selected files synchronize only to explicitly bound destinations;
stop on unhandled local edits. Publishing a package is not receiving or applying it.
Only receiving-device ACKs establish delivery. Summary accuracy is not proven by hashes;
retrieve original evidence when accuracy matters. Keep locks and atomic commit recovery.
Do not reintroduce old governance, repeated health audits or the unfinished installer.
