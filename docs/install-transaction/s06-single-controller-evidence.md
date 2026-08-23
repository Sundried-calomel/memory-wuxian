# S06 Single Controller Evidence

Status: verified locally; the architecture-changing candidate will repeat the
real Windows UAC rehearsal at S11.

## Frozen Contract

`resource-interface-contract.json` remains the machine authority. The single
transaction controller owns seven resources: archive scaffold, local federation
node, installed capture generation, configuration overlay, maintenance
scheduler, auto-update scheduler, and dashboard launcher. Raw archive records,
summaries, existing peer identities, and replicas remain outside installer
mutation authority.

Inno, manual installation, and auto-update route through the same manifest,
allowlisted broker, and `WindowsInstallerTransaction`. The collector,
maintenance, and auto-update scheduler owners reuse the shared task XML
comparator. Windows may normalize the current account name to its SID; an
unrelated SID or any other task-definition drift still fails closed.

## Validation

The final post-S11-replan focused matrix ran 110 tests and passed all 110 across
composition, transaction, release-contract, dashboard, defect-contract,
broker, isolated-runtime, workflow-controller, and lifecycle modules. It covers
seven-resource ordering, all seven apply-failure boundaries, repeat-install
identity preservation, runtime-modified federation-file retention, deep Windows
transaction paths, exclusion of temporary probe paths from the installed node,
exact entrypoint routing, and broker exit-code preservation.

The first seven-resource elevated rehearsal is retained as failure evidence: it
stopped in federation `prepare` with `WinError 206` before any formal resource
was applied. The repaired owner creates its canonical probe in a short-lived
system temporary directory, stores only hash-bound Base64 payloads in the
transaction journal, and binds `node.json` to the formal replica root. S11 must
now repeat clean install, repeat install, injected rollback, and v2.15.0 upgrade
through the namespaced real Windows entrypoint with explicit UAC consent.

The second real rehearsal proved the same long-path family also affected the
collector's complete candidate projection. The composition owner now assigns
all production staging to `%ProgramData%\MemoryWuxian\installer-resources` and
all rehearsal staging to the disjoint `MemoryWuxianRehearsal` root, so no
resource inherits an unbounded manifest-directory prefix.
