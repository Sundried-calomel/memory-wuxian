# Unified Windows Installer Architecture Freeze

## S06 resource and interface refinement

The S03 direction remains unchanged, but S06 implementation is governed by
`resource-interface-contract.json`. The former six-resource composition was
not closed because it dropped the legacy local federation node initialization
effect. The frozen S06 composition has seven transaction resources, gives the
maintenance task and local node identity separate Owners, binds Codex CLI identity in manifest v2,
excludes failure injection from production manifests, and requires every outer
entrypoint to cross the same closed UAC broker before the transaction Owner.

This does not change S01-S15 order, release identity, archive authority, or any
downstream acceptance gate.

## Owner and dependency direction

`scripts/windows_installer_transaction.py:WindowsInstallerTransaction` is the
single production mutation Owner. It may import Platform Foundation primitives
only. Product Shell entrypoints may call it; it must not import Product Shell,
Capture Core, Memory, Control, Exchange, Environment, or Project Evidence.

The Product Shell entrypoint composes the following subordinate adapters and
passes them through controller-owned protocols. The controller never imports
their modules. Each invocation receives one transaction token and may mutate
only its declared resource; ordering, durable checkpoints, commit, and rollback
remain owned by the controller:

- Platform Scheduler owns Task Scheduler XML, query, registration, and removal.
- The collector adapter owns command policy and readiness probing.
- The config adapter owns additive defaults while preserving user values.
- The federation-node adapter owns only a clean-install local identity and
  empty federation layout; existing identity, peers, replicas, raw records, and
  summaries are immutable to it.
- The shortcut adapter and effect observer own one bounded operation each.
- Inno, manual bootstrap, and auto-update provide one closed request manifest
  plus the adapter set, then return the controller's exact terminal result.

Adapters cannot commit themselves. Their prepared changes and compensations
are recorded in the controller journal before application. A mutation without
the matching transaction token fails closed.

## Closed request manifest

Every invocation declares schema version, operation, source entrypoint,
candidate root, target Skill root, archive pointer, sessions root, runtime
bundle, Codex CLI path and hash, expected package/version hashes, and requested
components. Failure injection is test-only state outside the production
manifest. Unknown fields, paths outside declared roots, mutable package
identity, or an unsupported operation fail before mutation.

## Transaction phases

`planned -> prepared -> elevated-if-required -> applied -> effect-verified ->
committed` is the only success path. Any failure after preparation reaches
`rolling-back -> rollback-verified -> rolled-back`. An interrupted journal is
resumed idempotently from durable checkpoints. Outer callers return the exact
terminal code and receipt path.

## Privilege boundary

Normal installation remains per-user and least-privileged. The broker is not a
second installer. It accepts only an allowlisted transaction ID, operation,
target SID, exact manifest path and hash, exact controller path and hash, and
one-time nonce. Cancellation, denial, SID mismatch, path escape, command drift,
or unknown action returns a distinct failure without falling back to an
uncontrolled shell command.

## Runtime boundary

The installer selects a package-declared isolated Python runtime and validates
its interpreter and dependency lock offline before mutation. PATH discovery
may diagnose a developer machine but cannot determine the installed production
runtime. Online dependency installation is outside the install transaction.

## Migration boundary

Migrations are registered as ordered `from -> to` adapters with immutable
fixtures, preconditions, idempotence checks, exact backups, and rollback
receipts. The v2.15.0 installed fixture is the minimum supported source. Raw
archives are references only and never migration targets.

## Same-byte promotion

S13 freezes a committed source revision and creates one uniquely hashed CI
installer. S14 installs that exact artifact on the target Windows machine. S15
publishes the identical bytes without rebuilding and reinstalls the official
Release asset after matching its SHA-256.

## Red lines

- Never rewrite raw conversation records or the active archive contents.
- Never equate process existence, package construction, or exit zero with an
  installed effect.
- Never add a fallback scheduler, Run key, wrapper, or second transaction Owner.
- Never continue after hash drift, incomplete rollback, or a second integrated
  remediation failure.
