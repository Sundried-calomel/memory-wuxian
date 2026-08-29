# Unified Installer Transaction Procedure

<!-- workflow-governance: current=WF-20260830-008 -->

## Resume protocol

1. Run `python scripts/install_transaction_workflow.py status`.
2. Confirm the contract hash, current step, frozen checkpoints, and receipt
   chain. Stop if the state is `needs_replan` or any hash has drifted.
3. Read only the current step from `docs/install-transaction/contract.json`.
4. Run the project-local pre-edit hook for all intended paths.
5. Make one cohesive implementation change within the declared path scope.
6. Run the post-edit hook, targeted checks, and the step's required evidence.
7. Run `verify Sxx`, then `complete Sxx`, then `next`.

## Freeze protocol

- S03 creates the architecture freeze after contract and invariant review.
- S13 creates the candidate freeze only for a committed source revision and
  records the CI artifact SHA-256.
- S15 creates the release freeze only after the exact S14 artifact is promoted
  without rebuild and the official asset is reinstalled successfully.

## Evidence-first recovery protocol

1. S01 freezes the failed candidate commit, installer SHA-256, command line,
   nonce/broker/child receipts, and the current defect-prevention packet. It
   also binds this corrected sequence through workflow governance and capability
   admission.
2. S02 maps every supported outer entrypoint to Inno Setup, PowerShell, the UAC
   broker, the child controller, the transaction Owner, and the resulting
   receipt. Mark any shortcut or direct-call test that bypasses a boundary.
3. S03 freezes the recovery architecture and invariants. Independent evaluation
   checks the architecture only; it does not propose or apply repairs.
4. S04 creates a disposable, no-product-write harness that invokes the exact
   failed packaged artifact and retains stdout, stderr, exit codes, and child
   traceback evidence.
5. S05 binds complete-chain and detailed-replay evidence to prove the nearest
   failing boundary. A frozen exact historical run may supply the complete-chain
   lane when every relevant artifact and boundary is hash-linked to the replay.
   Do not rerun a full installer on host product resources merely to recreate
   evidence that already exists. Any new full-installer run requires a
   disposable Windows boundary. Do not edit production installer behavior
   before this proof exists.
6. S06 produces one disposition table for relevant files and functions:
   canonical Owner, reachable entrypoints, package membership, duplicate or
   unique responsibility, keep/merge/delete decision, and supporting tests.
7. S07 applies the smallest shared-owner repair. Delete only entries marked
   redundant by S06; do not add a parallel wrapper or alternate state path.
8. S08 compares supported entrypoint behavior and reruns every applicable
   historical defect case. Review the actual diff for simplification after
   correctness is established.
9. S09 uses a GitHub-hosted ephemeral Windows runner when the target device has
   no local disposable backend. One lane builds and executes the packaged Setup
   for clean and repeat installation, verifies byte identity and outer exit
   propagation, then uninstalls inside the disposable runner. A second,
   explicitly labelled namespaced lane uses the existing transaction rehearsal
   to inject a failure and prove exact rollback and idempotency. Upload both
   receipts and runner logs as immutable CI artifacts; never represent the
   direct-controller rollback lane as complete packaged-chain evidence.
   Validate failures against `installer-diagnostic-v1.json`: persist each
   structured expected/observed check before rollback, append rollback status,
   and upload only the closed journal and broker projections. Transaction
   tokens, nonces, credentials, arbitrary exception bodies, environment dumps,
   and unrestricted command output are forbidden artifact fields.
   Shortcut creation and post-install inspection share one canonical inspector.
   When `WScript.Shell` cannot reopen the Unicode final path, copy the exact
   `.lnk` bytes to a transaction-private ASCII leaf, verify the two SHA-256
   values match, inspect that projection, report the original path, and delete
   the projection in `finally`.
   The Windows checkout must fetch complete history before build and must prove
   `v2.15.0^{commit}` resolves before entering the packaged-chain lane. Workflow
   state uses a baseline commit plus overlay hashes, so a normal commit cannot
   create a false delta merely by making a formerly dirty file clean.
10. S10 runs the targeted Unicode, isolated-runtime, broker, cancellation,
    rollback, and idempotency matrix and proves that each result traversed the
    exact packaged chain.
11. S11 runs clean install, v2.15.0 upgrade, repeat install, and injected
    rollback through the same production chain. S12 verifies live runtime
    effects. S13-S15 then freeze, install, and promote the same bytes.

## Failure protocol

One failed gate may enter one integrated remediation cycle. The repair must
address the shared owner or contract boundary and rerun all affected checks.
Another failure requires a new plan and explicit user confirmation; it must not
be answered by another narrow patch.

For a late-stage failure, identify the earliest receipt contradicted by the new
evidence and invalidate only that step and its dependants. Preserve unrelated
completed evidence. A missing production-chain rehearsal invalidates the
rehearsal claim, not the entire installer architecture by default.

When a failure lacks assertion-level evidence, preserve completed predecessor
steps and correct the diagnostic boundary first. Run the exact disposable lane
once to identify the failed check, then authorize at most one root-cause
behavior repair. Do not alternate speculative behavior patches with generic
reruns.
