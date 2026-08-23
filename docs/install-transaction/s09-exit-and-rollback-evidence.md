# S09 Exact Exit And Rollback Evidence

Status: verified against the brokered seven-resource route.

## Exact propagation chain

`WindowsInstallerTransaction` returns distinct terminal values for invalid
manifest, prepare failure, apply failure with complete or incomplete rollback,
effect-verification failure, and commit failure. The elevated broker returns
the controller integer unchanged. `install.ps1` exits with the broker result,
Inno exposes it through `GetCustomSetupExitCode`, and the auto-update RunOnce
wrapper persists the exact Inno exit after rechecking package SHA-256.

Cancellation, denial, SID mismatch, path/hash drift, and nonce rejection retain
their broker-specific values and do not fall back to another shell command.

## Rollback journal

Applying intent is durable before each mutation call. Every post-resource
failure boundary reverses all resources applied so far. A successful
compensation reaches `rolled-back`; a failed compensation remains
`rollback-incomplete` and blocks a new transaction. Runtime-modified archive
scaffolding is retained rather than deleted and is named in rollback evidence.

## Current verification

Seventy-one controller, broker, updater, collector-lifecycle, scheduler,
rehearsal, and rollback tests passed across the six directly affected modules.
They include exact controller code preservation by
the broker, package-drift rejection before auto-update launch, exact RunOnce
receipt persistence, seven-resource rollback ordering, interrupted-journal
recovery, and raw/pointer byte preservation.

An earlier Inno syntax build is not reused as current-candidate evidence because
the runtime implementation changed in S07. The current source and immutable
runtime are assembled and compiled together for the S11 real Windows rehearsal.
The earlier elevated failure-rollback receipt predates the seventh-resource
architecture and is not counted for this candidate. S11 must repeat the same
exact-exit and cleanup proof with explicit UAC consent.
