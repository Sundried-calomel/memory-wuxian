---
name: memory-wuxian
description: Persist, summarize, search and restore conversation memory; synchronize selected rules, Skills and working files across paired devices.
---

# Memory Wuxian compact core

Use this Skill when earlier conversation evidence, memory maintenance or selected cross-device synchronization is needed. Do not perform memory maintenance for every ordinary task.

- Original conversation records are append-only authority. Summaries locate evidence; verify claims against matching original records and identify their origin.
- Capture top-level visible messages and supported lightweight tool events, excluding internal reasoning, general tool output and subagent sessions. A final assistant response completes a round; progress comments do not.
- Keep native capture separate from ephemeral summary generation. Use `core/summary.py` for new summaries and explicit migration readers for old data; never restart the old summary scheduler alongside it.
- Preserve locks, atomic writes and failure recovery. Validate external inputs and important commits; do not reintroduce governance reviews, capability promotion or repeated whole-archive audits.
- Preserve original archives, completed summaries and private identities. Report integrity errors instead of rewriting history to make a check pass.
- Use explicit device-local bindings for selected rules, Skills and files. Stop on unhandled local modifications. Private keys, credentials and device-specific runtime configuration remain local.
- Treat queued publication, remote import and target application as separate facts. A new `core-v1` peer must be activated before it can understand new packages; old `v1` queues remain untouched.
- Report historical confidence as verified, summary-supported, index-only or unverified. Capsules are derived context, not new source messages. Respect the configured context budget.

Read `README.md` and `docs/PEER-SETUP.md` before first activation. This release requires the native collector/envelope tools and paired identities from an existing installation; it contains no independent installer.

## Entry points

- `core/runtime.py --root <archive> status|query|context|backup`: local operations.
- `core/mcp_server.py --root <archive>`: bounded read-only MCP. Peer source lookup accepts origin and digest.
- `core/dashboard.py --root <archive> --config <local-config> --port 8765`: loopback dashboard.
- `core/live.py --config <local-config>`: one incremental collection, eligible-summary, memory/environment-sync and backup invocation.
- `core/bootstrap_core.py`: explicit per-device configuration and native archive import.
- `core/summary_migration.py`: explicit, model-free migration of old completed summaries.

The normal maintenance path calls `core/core_sync.py` directly, using the native envelope tool as a cryptographic primitive. It must not import or launch the old Python cloud-sync program. Keep each device's configuration local and distinguish successful portable tests from verified live activation.
