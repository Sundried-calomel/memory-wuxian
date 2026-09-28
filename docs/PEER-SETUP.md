# Peer setup

Install the platform ZIP on each device; Python 3.14 and Codex CLI must be available.
The package includes the encryption helper. Run `python core/configure.py --help`.
Provide an empty archive path, Codex sessions path, Codex executable, backup path,
device node ID and private identity path. The command outputs public pairing information.
Exchange only public identities through a trusted channel, then configure each device's
`sync` with the peer node ID, encryption/signing public keys and selected shared folder.
Alternatively provide `--peer <public-identity.json> --exchange <shared-folder>` at configuration.

Schedule `python core/live.py --config <local-config>` once per minute. Run the dashboard
or register the MCP server separately as described in the README. Environment synchronization
uses `environment` configuration and explicit local target bindings; receiving files
does not silently authorize their activation. Private keys and device configuration are
never synchronized with the portable Skill.

For existing installations, preserve the original archive and first migrate the old
capture checkpoints. Do not run old and new collectors concurrently. Legacy archive
import commands are for historical conversion, not the periodic runtime.
