# Memory Wuxian 2.20.1

One runtime for conversation capture and summaries, dashboard/search/context recovery,
memory backup and sync, selected rules/Skills/files sync, file update/recovery, and release packaging.

Codex JSONL goes directly through `core/collector.py` into the existing core archive.
The scheduled `core/live.py` tick performs capture, summary, synchronization and backup.
There is no old collector process or intermediate legacy archive in the normal path.
Capture latency follows the scheduler interval (one minute on the current Windows installation).

Each platform ZIP includes `bin/memory-wuxian-envelope` (Windows: `.exe`), built from
the minimal `native-envelope/` source. It generates device identities and provides
signed/encrypted bundle and ACK exchange. No v2.19.x installation is required.
Python 3.14 and an authenticated Codex CLI are external prerequisites; OneDrive or
another selected shared folder is required for cross-device exchange.

## Start

Extract the archive for your platform. On Unix, ensure the bundled helper is executable:
`chmod +x bin/memory-wuxian-envelope`.
Use `python core/configure.py --help` to create a device-local configuration and identity.
Pass an explicitly trusted peer public identity with `--peer` and a shared folder with
`--exchange` to enable synchronization. Private identities never belong in release packages.
Then run `python core/live.py --config core/live-config.json` once, or schedule the same
command every minute using the operating system scheduler. Existing installations keep
their scheduler and local configuration after migrating capture checkpoints.

`python core/dashboard.py --root ARCHIVE --config core/live-config.json --port 8765`
starts the loopback dashboard. `core/mcp_server.py --root ARCHIVE` provides read-only
memory tools. Selected environment files use explicit device-local bindings.
See [peer setup](docs/PEER-SETUP.md).

Legacy archive readers remain available for explicit historical conversion only.
Do not reset an existing archive's capture checkpoints during an upgrade: old and new
event identities must be reconciled before direct capture starts.
The experimental independent installer is excluded. Platform CI exercises local
two-node encrypted exchange; it does not establish real remote-device activation.
