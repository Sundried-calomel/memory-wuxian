# Unified Installer Transaction Procedure

<!-- workflow-governance: current=WF-20260905-018 -->

## Resume protocol

1. Run `python scripts/install_transaction_workflow.py status`. Legacy state is
   reported read-only instead of being rejected. If a new baseline is needed,
   run `prepare-migration`, inspect its path and SHA-256, then pass those exact
   values to `migrate`; never silently regenerate an older reviewed baseline.
2. Confirm the contract hash, epoch, current attempt, frozen checkpoints, and
   receipt chain. If the state is `needs_replan` or a contract digest drifted,
   use bounded `replan`; do not reinterpret current files as a new baseline.
3. Read only the current step from `docs/install-transaction/contract.json`.
4. Run the project-local pre-edit hook for all intended paths.
5. Make one cohesive implementation change within the declared path scope.
6. Run the post-edit hook and targeted checks. Store machine-readable raw
   results under the excluded evidence directory and generate one closed
   evidence manifest per required evidence ID, bound to the workflow, epoch,
   step, attempt, source commit, and current source snapshot. Use the exact
   producer profile declared by `verifier-policy.json`; do not place expected
   values or pass/fail assertions in the producer manifest.
7. Run `verify Sxx --evidence-manifest ...`, then `complete Sxx`, then
   `next`.

Before accepting a control-plane candidate, confirm that every path in
`control_plane_files` is covered by `protected_paths`. Shared transaction and
lock helpers are executable dependencies, not invisible library details. Bind
the aggregate control-plane and evidence-schema digests into the epoch before
S01 completes; any later change resumes from S01.
Reject a contract when the executed schema, policy, or controller is omitted
from that set, when a control-plane file is excluded from snapshots, or when
evidence and installer-subject roots overlap governed source bytes.

## Exact-evidence protocol

1. Capture each contract, policy, schema, admission document, manifest, or JSON
   evidence artifact through its configured byte ceiling, then hash and parse
   that one byte buffer. Reject duplicate keys, non-finite constants, oversize,
   malformed, or type-confused values before evaluating claims.
2. Compare policy observations with exact JSON types. Enforce the complete
   route, lane, effect, platform, job, and suite identifiers declared by the
   verifier policy; aggregate counts are supporting evidence, not substitutes
   for matrix membership.
3. For CI evidence, first validate the declared repository, workflow, source
   commit, run ID, attempt, and successful conclusion against the live GitHub
   Actions API. Then verify a GitHub artifact attestation over the exact result
   JSON from that same workflow. Where the policy names an installer subject,
   verify its separate attestation as well. Require the verified statement to
   contain the expected subject SHA-256 and rehash the local file after the
   verifier returns. Require every evidence ID in a policy-owned provenance
   group to share one run identity. Store only hashes of the successful API and
   attestation responses in the verification receipt; replay uses those
   immutable proofs without another network request.
4. For evaluator evidence, use an actually separate evaluator process, then
   validate the standard report structure, independent
   producer/evaluator identities, weighted score, deterministic checks, and
   non-LLM evidence. Then require its artifact list to contain the exact S03
   architecture capture or S09 candidate freeze required by that stage. Record
   that evaluator identity is process-attested unless a future signed invocation
   receipt is available; do not call it cryptographic identity proof.
5. Before S09 verification, require an empty governed overlay. Build only from
   a committed source identity, and freeze the installer by hashing the actual
   subject file. S14 additionally compares reported byte length to that file.

## Crash-recovery protocol

1. Write a content-addressed failure receipt before changing runtime state.
2. Under the same workflow lock, append the receipt reference, consume the one
   integrated remediation cycle, invalidate prior verification and
   authorization, advance the attempt, and atomically persist state.
3. If interruption occurs between steps 1 and 2, read-only status returns
   `failure-recovery-required`. The next mutating command validates and replays
   only a canonical receipt whose exact epoch, step, attempt, phase, state-frozen
   step definition, and digest-derived filename match. It validates all other
   state invariants before mutation and never silently ignores or deletes the
   receipt.
4. Validate every authorization and verification receipt against the current
   workflow, epoch, step, attempt, source snapshot, and candidate. A recovered
   or ordinary retry requires a fresh authorization only at S14 or S15.
5. Migration is a separate operation: accept only schema 2 with the exact
   workflow ID, parse the same bytes that were hashed, preserve them under
   `legacy`, and initialize schema 3 from the explicitly hash-bound baseline.
6. Link every committed event to the prior event file reference and logical
   hash. Treat only the chain reachable from runtime state as authoritative;
   preserve but do not infer state from unreferenced crash residue.
7. When a later replan preserves completed steps, validate their evidence
   against immutable attempt fields such as the original step predecessor, not
   against a successor epoch's replaceable baseline.

## Freeze protocol

- S03 completion creates the architecture freeze from the validated architecture
  evidence capture.
- S09 completion commits source, builds the only candidate installer, and
  automatically freezes its SHA-256. Every S10-S14 evidence projection binds
  that value. S13 promotes the existing freeze and never creates new bytes.
- S15 completion captures the release SHA-256 and succeeds only when it equals
  the S09 candidate freeze after official-asset reinstall.

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
9. S09 commits the complete source candidate and builds one installer exactly
   once. Record its source commit, provenance, and SHA-256 before any platform
   rehearsal. The controller hashes the actual installer subject file under
   `dist`, not merely the JSON report that names its digest. No later step may
   rebuild it.
10. S10 uses a GitHub-hosted ephemeral Windows runner when the target device
   has no local disposable backend. One lane executes the frozen packaged Setup
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
   The Windows checkout must fetch complete history and prove
   `v2.15.0^{commit}` resolves before entering the packaged-chain lane. Workflow
   state uses a baseline commit plus overlay hashes, so a normal commit cannot
   create a false delta merely by making a formerly dirty file clean.
11. S11 runs clean install, v2.15.0 upgrade, and repeat install through the
    exact packaged chain. Controlled rollback may use the canonical transaction
    Owner's separately labelled supplemental lane; it may be called packaged
    evidence only when an external production boundary supplies the failure.
12. S12 verifies live runtime effects. S13 independently evaluates and promotes
    the already hashed candidate. S14 installs it on the target. S15 publishes
    and reinstalls the same bytes.

## Failure protocol

One validated negative gate result automatically enters one integrated
remediation cycle. Operator mistakes, missing inputs, ordering errors, stale
receipts, and provenance drift do not consume it. The repair must address the
shared Owner or contract boundary and rerun all affected checks. A second true
gate failure changes the machine state to `needs_replan`; bounded `replan`
archives the exact previous state and opens a new epoch without asking for user
authorization or promoting failed worktree bytes.

For a late-stage failure, identify the earliest receipt contradicted by the new
evidence and invalidate only that step and its dependants. Preserve unrelated
completed evidence. A missing production-chain rehearsal invalidates the
rehearsal claim, not the entire installer architecture by default.

When a failure lacks assertion-level evidence, preserve completed predecessor
steps and correct the diagnostic boundary first. Run the exact disposable lane
once to identify the failed check, then permit at most one integrated root-cause
repair. Do not alternate speculative behavior patches with generic reruns.

## Authorization protocol

- S03, S13, and S15 independent evaluations are normal quality gates. Invoke
  the installed evaluator directly and retain its manifest; do not ask the user
  to authorize the review.
- Ask for concrete consent only when S14 is ready to invoke target-device UAC,
  and when S15 is ready to push, merge, publish, and reinstall the official
  asset. `authorize` records an immutable receipt for the contract-owned action
  list and binds it to the current epoch, attempt, snapshot, and candidate hash.
- Generate S14/S15 evidence only after the receipt exists and include that
  receipt SHA-256 in every evidence manifest. The receipt records consent; the
  host UAC or remote-write boundary remains the authority that permits the
  external action.
- A code, test, document, manifest, or SHA-256 change never becomes user
  authorization merely because it changes a quality receipt.

## Verifier policy protocol

- Every required evidence ID appears exactly once in
  `verifier-policy.json`. A missing or extra policy entry blocks the
  controller.
- The producer records the exact policy-owned identity, invocation, exit code,
  source commit, source snapshot, and bounded artifact hashes. CI projections
  include GitHub Actions provenance; evaluator projections include the
  independent evaluator identity and evaluated snapshot. Artifacts contain
  observations only.
- A nonzero producer command exit is retryable infrastructure evidence and does
  not spend the quality-remediation allowance. Policy assertions are evaluated
  only after source, artifact, subject, and producer structure pass validation.
- Evidence that reports zero failures must also satisfy the policy's positive
  minimum count. Candidate, downloaded, and release installer identities are
  recomputed from their declared subject files.
- The controller selects the required artifact role, reads the configured JSON
  pointer, and compares it with the policy-owned expected value. A producer
  cannot replace the expected value in its own manifest.
- Local command, test, and inspection records are process-audit evidence within
  the project trust boundary. Do not present their declared invocation fields
  as authenticated provenance. Route adversarial provenance claims to
  workflow-attested GitHub artifacts, and route semantic independence to an
  actually separate evaluator invocation.
- Capture runtime state once through the bounded strict JSON reader before any
  migration or replan. Validate that captured document and archive those exact
  captured bytes; never reopen the live state path to obtain predecessor bytes.
- Accepted receipts are recursively checked again when state is loaded, so a
  later manifest or artifact edit invalidates the receipt. Policy changes are
  compared per evidence ID and resume from the earliest consuming step; unknown
  top-level contract drift resumes conservatively from S01.
- A completed step without its receipt and any terminal state with fewer than
  fifteen completed steps are rejected before `next` can advance.
