# Memory Wuxian — compact core

Version: **2.20.0**. This is the formal release of the compact runtime, not a fresh-install wizard.

The core covers conversation archival and summaries; dashboard, search and context recovery; memory synchronization and backups; selected rules, Skills and small-file synchronization; transactional file updates and recovery; and package creation. Runtime Python code has no third-party dependencies. Python 3.14 is the tested version.

## What changed

- One scheduled `core/live.py` tick calls the new `core/core_sync.py` directly. It never launches the old `memory_cli.py cloud-sync` program.
- Authenticated, encrypted `core-v1` queues use the existing paired native envelope identities. Memory and selected environment files share this transport. A published queue item is not evidence of remote delivery; only a receiving device's ACK establishes that.
- Large legacy summaries use bounded lossless compression. Source history and completed summaries are not rewritten to bypass integrity errors.
- Peer history is indexed separately and remains read-only. Local raw archives remain authoritative.
- Environment application uses explicit local bindings and stops on unhandled local changes. Older arriving file revisions cannot replace an already applied newer publication.
- Runtime checks are concentrated at external-input and important-write boundaries. Retired governance workflows and experimental installers are absent from this branch and its upgrade package.

The stable `main` branch and previous `v2.19.6` release remain available. This branch contains the new core only; it does not republish the old Python runtime alongside it.

## Installation boundary

This package upgrades an existing Memory Wuxian installation. Keep the existing native collector, native envelope executable, device identities and original archive. The native binaries are **not** bundled here; use the existing installation from [v2.19.6](https://github.com/Sundried-calomel/memory-wuxian/releases/tag/v2.19.6). New-device installation without these prerequisites is not provided by this release.

Both peers must activate this core to exchange `core-v1` data. The legacy `v1` queue is left unchanged. Windows live publication, scheduled maintenance, backup and MCP retrieval were exercised during the cutover. Mac live installation and a real Windows-to-Mac ACK were **not verified**. The CI matrix checks portable Python behavior; it is not proof of native collector installation or cross-device activation.

1. Back up the existing Skill files and device-local configuration.
2. Extract the release package into a staging directory. Copy its `SKILL.md` and changed `core/` files into the existing Skill, preserving local `core/live-config.json`, native binaries, keys and archives. Pause the core maintenance invocation while replacing code.
3. For a new core archive, follow [peer setup](docs/PEER-SETUP.md). Migration is explicit and preserves the original archive. Never point a fresh core directly at an old-format archive.
4. Disable the old cloud-sync and old semantic-maintenance schedulers when activating the new maintenance tick. Keep the native collector running. Schedule `python core/live.py --config <device-local-live-config.json>` once per invocation; do not add a second persistent Python scheduler.
5. Keep archive, executable, identity and OneDrive paths device-local. Never upload `live-config.json`, keys or raw memory as release files.

Example local-only archive and search:

```sh
python core/runtime.py --root ./demo-archive append --conversation demo --speaker user --id demo-1 --text "Remember this decision"
python core/runtime.py --root ./demo-archive query "decision"
python core/runtime.py --root ./demo-archive status
```

Read-only MCP: `python core/mcp_server.py --root <core-archive>`.
Dashboard: `python core/dashboard.py --root <core-archive> --config <live-config> --port 8765`.
Automatic summaries require an explicitly configured local Codex executable and model. Bootstrap leaves automatic summaries disabled; it does not regenerate old summaries.

Default environment selections are the global `AGENTS.md` and the Memory Wuxian Skill itself. Other rule/Skill/file selections require explicit selection and local bindings; they are not automatically discovered or uploaded. Semantic search requires an encoder; keyword search works without one. Unsupported legacy dashboard controls remain disabled.

## Development and release

Run `python -m unittest discover -s tests -v`. A single tag-triggered workflow runs the portable checks on Windows, macOS and Linux, then packages the explicit file list and publishes the release with SHA-256 checksums. No developer archives, diagnostics, local paths, identities or credentials belong in this repository/package.

License: [MIT](LICENSE.txt).
