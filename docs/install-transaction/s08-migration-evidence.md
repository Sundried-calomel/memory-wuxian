# S08 Cross-Version Migration Evidence

Status: verified against the completed S06 composition.

## Reused assets

The immutable v2.15.0 fixture remains exact reuse under the installer reuse-map
gate. The existing migration registry remains the sole version-order Owner and
supports `2.15.0 -> 2.18.0 -> 2.19.0 -> 2.19.1 -> 2.20.0`.

## Production connection

`ConfigurationMigrationMutation.prepare` now invokes the registry's
`migrate_document` on the installed configuration before any product mutation.
It writes expected config and receipt bytes into transaction-private evidence.
`apply` installs those exact bytes, `verify` requires registry-proven
idempotence, and rollback restores the previous config and receipt bytes.

The registry permits additive defaults only. Existing scalars, lists, nested
values, archive pointer bytes, and raw records remain unchanged. Unknown,
malformed, cyclic, or unreachable source versions fail during preparation.

## Current verification

Thirty-five migration, product-composition, and controller tests passed. They
cover the frozen v2.15.0 fixture, clean and repeat installs, ordered planning,
unknown-source rejection, additive migration, second-run no-op behavior,
partial-apply rollback, exact config restoration, raw/pointer preservation,
and the current seven-resource transaction ordering.

The reuse-map gate passed for S08 and verified the fixture as exact reuse and
the production composition as the accepted replacement of the rejected
single composed mutation.

The previous elevated rehearsal predates the seventh-resource architecture and
is not counted for this candidate. S11 must repeat the real v2.15.0 upgrade and
record the exact four-step migration chain through v2.20.0.
