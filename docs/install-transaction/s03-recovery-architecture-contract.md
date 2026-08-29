# S03 Recovery Architecture Contract

Status: candidate architecture freeze for independent evaluation.

## Objective

Recover the v2.20.0 Windows installer from the exact S14 broker-to-child failure
without adding a parallel installer path, rewriting unrelated working modules,
or treating a direct transaction-owner test as an end-to-end installation.

## Frozen owners

| Responsibility | Sole Owner |
| --- | --- |
| Ordered workflow state and path gates | `scripts/install_transaction_workflow.py` |
| Setup orchestration | `packaging/windows/MemoryWuxian.iss` |
| Runtime resolution and manifest preparation | `packaging/windows/install.ps1` and the controller's `--prepare-only` mode |
| Closed elevation request and nonce validation | `scripts/windows_installer_broker.py` |
| Product mutation composition | `scripts/install_windows_transaction.py:ProductInstallAdapter` |
| Commit, rollback, and transaction journal | `scripts/windows_installer_transaction.py:WindowsInstallerTransaction` |
| Approved update staging and outer result | `scripts/auto_update.py` |

No recovery step may create a second Owner for any row.

## Non-negotiable invariants

1. Raw conversation archives, summaries, cloud exchange, and Environment
   payloads remain outside installer repair scope.
2. The failed S13 commit, installer SHA-256, S14 logs, request, nonce result, and
   missing child receipt remain immutable historical evidence.
3. S04 diagnostics run only in a disposable namespace and do not modify product
   roots, tasks, Run values, Desktop shortcuts, archive pointers, or raw bytes.
4. S05 requires two hash-linked evidence lanes. A frozen earlier run may supply
   the complete packaged Inno and PowerShell lane only when its exact installer,
   extracted candidate, manifest, runtime, broker, exit boundary, and
   missing-child state bind to the replay. The second lane replays the exact
   extracted candidate at the broker boundary to preserve child stdout, stderr,
   traceback, and exit code. The replay cannot replace complete-chain evidence;
   both must reproduce the same boundary result, and absence of a child launch
   must itself be recorded. Any new full-installer execution must run inside a
   disposable Windows boundary that cannot address host product resources.
5. S07 is the first production-edit step. Its diff must repair the nearest
   proven shared boundary and must not add another wrapper, fallback, or state
   machine.
6. A file or function may be deleted only when S06 proves its supported-entrypoint
   reachability, package membership, Owner overlap, and replacement coverage,
   and tests prove that no supported entrypoint still depends on it.
7. S08 must prove supported entrypoint equivalence and all applicable historical
   defects. S09-S11 must traverse the packaged Inno, PowerShell, broker, child,
   and transaction route.
8. S13 freezes one committed source and one uniquely hashed CI artifact. S14
   installs that exact artifact. S15 promotes the same bytes without rebuilding.
9. A failure invalidates the earliest contradicted receipt and its dependants,
   not unrelated completed work. One integrated remediation is the maximum
   before explicit replan.
10. Installed v2.15.0 remains authoritative until a later step proves target
    installation and runtime effects. Build, tests, or receipt existence alone
    do not establish activation.

## Diagnostic architecture

S04 may add one project-local diagnostic harness whose only production imports
are the packaged broker/controller modules under test. The harness must preserve
the detailed boundary-replay lane and prepare a disposable complete-chain lane
for any new full-installer execution:

- take the exact installer as the complete-chain input and bind any extracted
  candidate used for replay to that installer with a derivation receipt;
- when a new complete-chain run is required, run the exact installer inside
  Windows Sandbox or an equivalently disposable Windows VM whose tasks,
  registry, Desktop, archive pointer, and product roots cannot address the host;
- create all writable paths below one disposable root;
- in the replay lane only, substitute a child probe at the existing controller
  dispatch seam; this lane cannot satisfy complete-chain evidence by itself but
  may be hash-bound to frozen earlier complete-chain evidence;
- capture process command, environment identity, stdout, stderr, traceback,
  exit code, request hash, nonce state, and receipt existence;
- emit append-only JSON evidence; and
- assert a host pre/post snapshot proving no product-resource write.

The harness is test infrastructure, not a supported installer entrypoint and not
part of the shipped package.

## Repair selection rule

After S05, rank candidate causes by direct evidence. Select the first change
that repairs the canonical Owner or existing boundary. If the same result can be
achieved by correcting an argument, path, environment, exception boundary, or
receipt propagation in existing code, adding a new adapter or fallback is
forbidden.

## Completion boundary

This architecture can freeze only if an independent evaluator confirms that it
preserves the existing security boundaries, makes the missing production-chain
evidence observable, delays production edits until root-cause proof, and does
not silently authorize installation, elevation, publication, or archive writes.
