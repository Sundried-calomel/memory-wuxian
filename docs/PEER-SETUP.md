# Activate an existing paired device

This is an explicit upgrade of an existing native Memory Wuxian installation. It does not create or copy private identities and does not silently switch archive roots.

Keep the installed native collector and `memory-wuxian-envelope` executable (the Windows executable has `.exe`). Keep the original archive, federation metadata and local identity file. Select a separate new core archive and the existing OneDrive exchange directory on each device.

After staging the package, pause the maintenance invocation and copy the changed package files into the existing Skill. Preserve native binaries and device-local configuration. Disable the old Python cloud-sync and semantic-maintenance tasks when enabling the new tick; do not disable native capture.

Run the following commands with **your own device-local paths**. The native source is the directory consumed by `NativeArchiveBridge`, normally the original archive's `raw` directory. Do not point it at the new archive.

```sh
python core/bootstrap_core.py import-native --new-root "<new-core-archive>" --source "<old-archive-raw-directory>"
python core/bootstrap_core.py configure-and-sync --new-root "<new-core-archive>" --native-source "<old-archive-raw-directory>" --old-archive-root "<old-archive>" --exchange-root "<OneDrive-root-or-MemoryWuxianExchange>" --identity "<local-private-identity-file>" --binary "<local-native-envelope-executable>" --peer-id "<trusted-paired-node-id>" --environment-root "<local-Codex-root>" --live-config "<local-Skill>/core/live-config.json"
```

Configuration reads the existing local `federation/node.json`, `cloud.json` and selected `peers/<id>.json`. It writes local paths and peer public keys to the local config and performs one new-protocol sync. Never publish that config or copy the private identity to another device.

Old completed-summary migration is a separate model-free operation. Run `python core/summary_migration.py --help` for its explicit source options; preserve old artifacts. Do not use automatic model backfill as a migration substitute.

For the two selected environment targets, create the local bindings **once**, after choosing the intended targets. Do not repeat binding to dismiss conflicts: binding records the current local baseline.

```python
import sys
from pathlib import Path
skill = Path.home() / '.codex' / 'skills' / 'memory-wuxian'
sys.path.insert(0, str(skill / 'core'))
from environment import EnvironmentService
env = EnvironmentService(Path.home() / '.codex')
env.bind('global-codex-agents', 'AGENTS.md', 'whole-file')
env.bind('global-memory-wuxian-skill', 'skills/memory-wuxian', 'skill-tree')
```

The new tick is `python core/live.py --config <local-config>`. Use the local operating system scheduler to invoke it periodically. On Windows the activated installation uses `MemoryWuxianCoreMaintenance`; on macOS an example LaunchAgent is included below. Use a stable Python path. Existing schedulers are not altered merely by extracting this package.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.memorywuxian.core</string>
  <key>ProgramArguments</key><array>
    <string>ABSOLUTE_STABLE_PYTHON_PATH</string>
    <string>ABSOLUTE_SKILL_PATH/core/live.py</string>
    <string>--config</string><string>ABSOLUTE_LOCAL_CONFIG_PATH</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
</dict></plist>
```

Replace the placeholders before registering the plist. This template is not an installed LaunchAgent and is not evidence of Mac activation.

Bootstrap defaults to `auto_summary: false`. To enable future summaries, explicitly add a local Codex executable path (`codex`), model (`model`), `summary_rounds` and a deliberate `summary_start_sequence`, then enable `auto_summary`. Set `backup` to a local external backup directory and `retention` to the desired complete-snapshot count. Do not copy another device's config.

After activation, confirm a successful tick, retrieve an original record through the installed MCP or query service, and inspect new-protocol remote ACKs. A local queue file, a running process or a passing CI run alone does not prove cross-device delivery. Rollback requires restoring the previous code/config and explicitly selecting the old schedule; do not run both sync schedulers together or rewrite original history.
