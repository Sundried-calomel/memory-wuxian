# Unified Installer Transaction Procedure

<!-- workflow-governance: current=WF-20260822-001 -->

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

## Failure protocol

One failed gate may enter one integrated remediation cycle. The repair must
address the shared owner or contract boundary and rerun all affected checks.
Another failure requires a new plan and explicit user confirmation; it must not
be answered by another narrow patch.
