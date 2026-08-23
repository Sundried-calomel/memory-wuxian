# Windows Installer Entrypoint Inventory

## Frozen basis

- Source revision: `6c60a539b4d7fc45b6fe12945b0393335ebb8b86`
- Inventory scope: Windows packaging, install, update, uninstall, Task Scheduler,
  runtime discovery, shortcut activation, rollback, CI, and Release promotion.
- Excluded semantics: raw archive contents, Summary V2, cloud exchange, and
  Environment payload behavior.

## Entrypoints and effects

| Entrypoint | Call chain | Mutated resources | Existing owner |
| --- | --- | --- | --- |
| Release build | `.github/workflows/release.yml` -> Cargo -> Inno Setup | `bin/*.exe`, installer and checksum | release workflow |
| Full install or upgrade | Setup `[Run]` -> `packaging/windows/install.ps1` | Skill generation, tasks, config, pointer, shortcut | split |
| Collector transaction | `install_codex_autosync_windows.py --load` | generation tree, collector task, journal, lifecycle and pointer | collector lifecycle |
| Manual activation | bootstrap -> collector installer; updater and shortcut separately | same resources without one outer transaction | split |
| Approved update | `auto_update.py` -> RunOnce -> Setup | update state, RunOnce, then full install | updater plus installer |
| Uninstall | Setup `[UninstallRun]` -> `uninstall.ps1` | tasks, Run values, shortcut, launcher config, Skill | split |
| Commit or rollback | collector installer `--commit-journal` or `--rollback-journal` | prior generation, task, pointer, manifests | collector lifecycle |

## Scheduler inventory

| Task | Policy owner | Mechanical owner | Gap |
| --- | --- | --- | --- |
| `MemoryWuxianCodexSync` | collector installer | duplicated local XML and schtasks calls | bypasses shared adapter |
| `MemoryWuxianMaintenance` | maintenance installer | `platform_scheduler.py` | canonical adapter |
| `MemoryWuxianAutoUpdate` | updater installer | direct schtasks and Run fallback | third scheduler implementation |
| `MemoryWuxianCloudSync` | cloud installer | `platform_scheduler.py` | adjacent optional capability |
| `MemoryWuxianGovernanceAI` | governance installer | `platform_scheduler.py` | adjacent optional capability |

## Release artifact chain

The current candidate CI uploads evidence but not the installer. The Release
workflow checks the same source SHA, rebuilds native binaries, builds the Inno
installer, uploads it, downloads it in the publish job, signs update metadata,
and creates the GitHub Release. This is same-source evidence, not same-byte
promotion. S13-S15 replace it with one CI-built installer whose SHA-256 is
installed before the same bytes are promoted without rebuild.

## Single-owner decision

`windows_installer_transaction.py` will be the sole production transaction
controller for install, upgrade, rollback, and installed-effect evidence.
Existing owners remain caller-composed subordinate adapters. Product Shell
constructs them and passes protocol-conforming operations into the controller;
the controller does not import Product Shell or Control Plane modules:

- `platform_scheduler.py`: sole Task Scheduler XML, registration, query, and
  removal mechanics.
- `platform_transaction.py`: canonical JSON and atomic state primitives.
- `migrate_config.py`: additive user-configuration migration only.
- dashboard shortcut scripts and runtime-effect gates: bounded adapters and
  observers invoked by the controller.
- `install_codex_autosync_windows.py`: collector-specific policy and effect
  probe behind an injected transaction adapter; its duplicated scheduler
  mechanics move to Platform Foundation.
- Inno Setup, manual installation, and auto-update become thin callers that
  provide a closed manifest and return the controller's exact result.

Every adapter requires the controller's transaction token and returns prepared
mutation plus compensation evidence; it cannot independently commit. The
controller journal must cover generation switching, tasks, configuration,
updater registration, shortcut state, launcher configuration, runtime choice,
outer exit status, and rollback verification. It may reference the archive
root but may never rewrite archived conversation bytes.

## Confirmed gaps for later steps

1. Two Task Scheduler XML owners and a third updater scheduler path.
2. No allowlisted UAC broker or cancellation/denial contract.
3. No shared controller across Inno, manual install, and auto-update.
4. Runtime bootstrap depends on PATH/system Python and online pip mutation.
5. No cross-version installer migration registry or v2.15.0 fixture.
6. Outer installer exit status is not bound to the inner rollback journal.
7. No real clean/upgrade/repeat/failure Windows rehearsal receipts.
8. No complete installed-effect receipt covering task, process, telemetry,
   watermark, shortcut, focus behavior, and unchanged archive pointer.
