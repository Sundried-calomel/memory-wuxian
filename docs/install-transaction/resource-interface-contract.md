# S06 Resource And Interface Contract

This is the human-readable projection of
`resource-interface-contract.json`; the JSON file is the machine contract.

## Ownership layers

`WindowsInstallerTransaction` owns ordering, checkpoints, effect verification,
commit, recovery, and reverse rollback. `ProductInstallAdapter` owns the closed
product composition. Inno Setup, manual PowerShell, and the approved updater
are callers only and may not sequence product mutations.

The package candidate, immutable content-addressed Python cache, and durable
transaction evidence are bootstrap resources. They are not installed-product
success and are not deleted merely because a product transaction rolls back.

Windows staging uses a bounded platform-owned root instead of inheriting the
manifest directory: production uses
`%ProgramData%\MemoryWuxian\installer-resources`, while rehearsals use the
disjoint `%ProgramData%\MemoryWuxianRehearsal\installer-resources` root. Each
transaction still receives its own UUID directory, and the journal beside the
manifest binds every staged path as evidence.

## Seven transaction resources

1. `installed-capture-generation`: Skill generation, collector task,
   command/lifecycle manifests, active-root pointer, collector Run-key cleanup,
   and bounded stale launchers. Existing pointer bytes are preserved; a pointer
   is created only when absent. Config files are delegated overlays.
2. `configuration-overlay`: additive idempotent config migration and receipt,
   with exact byte rollback.
3. `archive-scaffold`: bounded metadata from an isolated `MemoryStore.init`
   probe. Raw records and the active pointer are forbidden. Rollback removes
   only unchanged installer-created scaffolding; files changed by runtime work
   and non-empty directories carrying runtime data are retained as evidence.
4. `local-federation-node`: the local node identity and empty federation
   layout. Existing node bytes are immutable; a clean install uses the current
   computer name. Rollback removes only unchanged files created by the
   transaction; peer records and replica content remain outside installer
   ownership.
5. `maintenance-scheduler`: only `MemoryWuxianMaintenance`, including exact
   prior XML snapshot, restore, and rollback verification.
6. `auto-update-scheduler`: `MemoryWuxianAutoUpdate` plus migration of its
   legacy Run value as one compatibility resource.
7. `dashboard-launcher`: the desktop shortcut and launcher configuration as
   one verified pair.

The prior six-resource claim is superseded because it dropped the legacy
`init-node --display-name $env:COMPUTERNAME` installation effect.

## Protocol

Every mutation declares identity, owned paths/tasks/registry values, forbidden
paths, and compensation. The normal sequence is:

`prepare -> apply -> verify -> commit`

Recovery restores prepare evidence, then runs reverse-order `rollback` and
`rollback_verify`. Prepare may write transaction-private evidence but not
product resources. The journal records intent before each product mutation.
`rollback-incomplete` is terminal failure.

Manifest v2 binds the candidate, target, archive, sessions, isolated runtime,
Codex CLI path and hash, package identity, source entrypoint, and component set.
Failure injection is excluded from production manifests.

Real Windows rehearsals use the same controller and mutation classes with a
test-only `WindowsInstallResourceNamespace` injected directly into
`ProductInstallAdapter`. The manifest and official entrypoints expose no
namespace field. Rehearsal task and Run-value names must start with
`MemoryWuxianRehearsal-`, the desktop is isolated, and the runner proves that
production resources are unchanged after cleanup. The collector child
installer receives the manifest-bound archive pointer explicitly.

The UAC broker accepts one closed hash-bound request. Non-elevated launch
validates it, `runas` crosses privilege once, and elevated dispatch revalidates,
consumes the nonce, and runs only the fixed controller with
`--execute-manifest`.

## Red lines and acceptance

No installer mutation owns raw records or summaries. No entrypoint bypasses the
broker. No arbitrary elevated command or controller arguments are accepted.
S06 passes only when all three entrypoints are equivalent, special-character
paths round-trip, seven resources are unique, maintenance and local node
identity are visible in the outer journal, pointer and raw bytes survive
upgrade and rollback, and the official
`install.ps1` route reaches the broker.
