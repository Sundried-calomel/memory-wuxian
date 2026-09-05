# Unified Installer Transaction Rules

<!-- workflow-governance: current=WF-20260905-018 -->

## Scope

These rules govern only the Memory Wuxian unified Windows installer refactor.
They do not authorize edits to archive, summary, capture, cloud, or environment
semantics. Existing v2.19.1 candidate bytes and failure journals are frozen as
input evidence until the workflow reaches the step that explicitly replaces
them.

## Hard gates

1. `docs/install-transaction/contract.json` is the sole ordered S01-S15
   contract. Reordering, skipping, weakening, or silently adding a step is
   prohibited.
2. `scripts/install_transaction_workflow.py` is the sole state-transition
   owner. The state file must not be edited by hand.
3. At most one step may be `in_progress`. A step can complete only from a
   hash-bound verification receipt produced against unchanged working bytes.
4. Only a validated negative quality result consumes the single integrated
   repair cycle. Missing arguments, wrong command order, stale evidence,
   identity drift, and other operator or control errors fail closed without
   spending that allowance. A second true gate failure changes the workflow to
   `needs_replan`; no additive patch loop is allowed.
5. Architecture, candidate, and release freezes are created atomically by
   `complete` from policy-validated evidence captures; there is no manual
   freeze command. S09 freezes the candidate, and every S10-S15 receipt that
   exercises or promotes it must bind the same SHA-256.
6. S09 commits the candidate source and builds one uniquely hashed installer.
   S10-S13 may test and evaluate only those bytes. S14 installs that exact
   artifact. S15 promotes the same bytes without rebuilding, then verifies the
   official asset again.
7. Independent evaluation occurs only at the architecture freeze, candidate
   freeze, and final promotion gates. It is not an open-ended patch generator.
8. The project-local hook applies only to paths protected by this workflow. It
   must not install a global hook or block unrelated projects and conversations.
9. A contract correction starts a new epoch from either `needs_replan` or
   detected contract/step-definition drift. The controller preserves the old
   state, receipts, explicit baseline, and current failed changes. It may never
   promote the current worktree merely because `replan` was called. Quality
   review is a gate, not user authorization; runtime state is never edited by
   hand.
10. S01-S06 are evidence-first. They may change governance, diagnostics, tests,
    and evidence, but they must not repair installer production behavior. S07 is
    the first step allowed to modify production installer paths.
11. S05 must bind the exact packaged Inno-to-broker-to-child-controller chain
    to preserved child diagnostics. A frozen earlier exact-chain run may be
    paired with an isolated replay only when installer, candidate, manifest,
    runtime, broker, exit boundary, and missing-child state are hash-linked.
    Any new full-installer run requires a disposable Windows boundary. A direct
    call to `WindowsInstallerTransaction.execute` is not evidence for this
    boundary.
12. S06 must classify every candidate path by Owner, runtime reachability,
    package membership, and disposition. Deletion is allowed in S07 only when
    S06 proves the path redundant and tests prove that no supported entrypoint
    depends on it.
13. When the target Windows edition has no local disposable backend, S10 uses
    a GitHub-hosted ephemeral Windows runner. The
    workflow must fail closed unless `GITHUB_ACTIONS=true` and
    `RUNNER_ENVIRONMENT=github-hosted`, use no repository secret, run the
    packaged Setup only on that runner, upload hash-bound receipts, and destroy
    all runner state with the job. A namespaced direct-controller rehearsal may
    prove injected rollback but must remain labelled separately from the
    packaged outer-chain lane. The target device must not execute the candidate
    installer before S14.
14. A late-stage failure is first classified against the nearest proven
    boundary. Replanning may invalidate only the earliest false receipt and its
    dependants; it must not restart an unrelated completed sequence or expand a
    local defect into an unbounded rewrite.
15. Every installer transaction failure follows
    `installer-diagnostic-v1.json`: persist assertion-level evidence before
    rollback, append the verified rollback outcome afterward, and export only
    the closed safe projection. A combined generic assertion, raw recovery
    journal, transaction token, nonce, credential, arbitrary exception body,
    or environment dump cannot satisfy S10 evidence.
16. Structured assertions report each check independently with bounded
    expected and observed values. An unclassified exception exposes only its
    category and bounded source location until a safe component-specific
    classifier exists; unrestricted stdout, stderr, and tracebacks remain
    internal and are never CI evidence.
17. The canonical Windows shortcut inspector belongs to the same S10
    dashboard diagnostic Owner as shortcut creation. It must inspect the exact
    final `.lnk` bytes through a hash-equal ASCII-path projection when the
    platform API cannot reopen the Unicode final path, and it must remove that
    projection after inspection. This does not authorize renaming the visible
    shortcut or weakening target, working-directory, icon, argument, or live
    target assertions.
18. Workflow baselines are commit-plus-overlay snapshots. The controller must
    retain the baseline commit SHA and the SHA-256 state of paths that differed
    from that commit. Committing unchanged overlay bytes must produce zero
    delta; a byte change committed after the baseline must remain visible.
    Dirty-path presence or absence is never a content identity signal.
19. Before S09 freezes the source candidate, the candidate workflow and its
    historical fixtures must already be complete. Before the expensive S10
    packaged-chain lane starts, its checkout must resolve every historical ref,
    including `v2.15.0`; a shallow checkout is a failed prerequisite, not an
    installer failure.
20. Initial dirty state is accepted only through an explicit, hash-bound
    baseline manifest. `prepare-migration` creates a reviewable candidate
    manifest, while migration may also consume an earlier immutable baseline
    that intentionally predates the repair. Generated trees and unrelated
    unprotected changes are never baseline content. Every verification,
    failure, authorization, replan, prior-state, and event record is immutable.
21. A producer's claim that evidence passed is not authoritative. The executed
    JSON Schema is the sole structural contract. Each manifest binds workflow,
    epoch, step, attempt, source commit, source snapshot, exact producer
    profile, invocation, and bounded artifact hashes. The versioned verifier
    policy owns the observation and expected value; completed receipts
    recursively revalidate every manifest and artifact on every load.
22. Independent evaluation runs at S03, S13, and S15 without asking the user to
    authorize the review. User authorization is reserved for S14 target UAC
    installation and S15 GitHub publication plus official-asset reinstall.
23. Capability admission is an S01 quality prerequisite, not a bootstrap lock
    on every controller command. `status`, `prepare-migration`, hooks, and
    deterministic recovery remain usable while candidate bytes are under
    review; S01 cannot complete without a hash-consistent admission receipt.
24. All state mutations hold the shared cross-platform workflow lock and use
    the repository's canonical atomic-write owner. A replan archives the exact
    prior state bytes before replacement and writes a content-addressed receipt;
    it never overwrites a fixed-name historical record.
25. S14 and S15 authorization receipts bind the exact epoch, step, attempt,
    source snapshot, frozen candidate SHA-256, and contract-owned action list.
    Replan or byte drift invalidates the receipt; an arbitrary scope label is
    not reusable authorization.
26. Every executable file listed in `control_plane_files`, including shared
    atomic-write and locking dependencies, must also match `protected_paths`.
    The controller rejects a contract that would hash a dependency for
    admission while omitting it from workflow snapshots and hook coverage.
27. The evidence-schema SHA-256 and the aggregate control-plane SHA-256 are
    epoch identities. They are stored in state and verification receipts;
    either changing requires a bounded replan from S01. Unsupported schema
    vocabulary fails closed instead of being silently ignored.
28. A `completed` step must carry a valid verification receipt, and a completed
    workflow must contain fifteen completed steps. `next` may never translate a
    blocked terminal step or `needs_replan` state into success.
29. A nonzero evidence command exit is an infrastructure or producer error and
    does not consume remediation capacity. A repair cycle is consumed only
    after a structurally valid, source-bound artifact fails a policy-owned
    quality assertion.
30. The S09 candidate hash is recomputed from the actual installer file under
    the contract-owned subject root. S14 download and S15 release identity
    evidence must repeat that direct byte check; a JSON claim containing a
    plausible digest cannot create or replace a freeze.
31. Zero-failure evidence must also report a policy-owned positive count for
    the tests, jobs, assertions, files, effects, or evaluation artifacts it
    claims to cover. Empty work cannot satisfy a gate merely because its
    failure count is zero.
32. S14 and S15 evidence manifests bind the exact authorization receipt hash.
    The workflow record documents prior consent but does not itself grant host
    permission or execute an external action; UAC and remote-write authority
    remain with their concrete platform boundary.
33. S01 admission evidence must run the deterministic admission checker and
    bind the current project, aggregate control-plane hash, and exact admission
    receipt hash. A producer-authored `status: allowed` field alone is not
    admission.
34. The executed schema, verifier policy, and controller must be named control-
    plane files, protected by snapshots, and absent from exclusion patterns.
    Evidence JSON roots and installer subject roots must stay within their
    contract-owned directories and outside source snapshots.
35. Migration accepts only the exact schema-2 predecessor carrying this
    workflow ID. The controller hashes and parses the same captured legacy and
    baseline bytes, archives those legacy bytes immutably, and rejects an
    unknown schema or foreign workflow instead of guessing a conversion.
36. Evidence manifests and JSON artifacts are read once through contract-owned
    byte limits. The bytes parsed are exactly the bytes hashed; oversized input
    fails before JSON parsing. JSON comparisons are type-exact, so `false`
    cannot satisfy an expected integer zero and `true` cannot impersonate one.
37. Every CI result must bind a live successful GitHub Actions run for the
    exact repository, workflow, commit, and attempt, and the result artifact
    itself must carry a valid GitHub artifact attestation from that workflow.
    Candidate and release installer subjects additionally retain their own
    required artifact attestations. A locally authored result that merely names
    a real run is not evidence.
38. An independent-evaluation report must use the standard evaluator contract
    and inspect the exact stage-bound artifact: the S03 architecture artifact,
    or the frozen installer at S13 and S15. A passing report about another file
    cannot satisfy the gate. Capability admission likewise binds one canonical
    report under `docs/promotion-reviews` to all current control-plane hashes.
39. Policy-owned route IDs, execution lanes, runtime-effect IDs, platform and
    job sets, and relevant-suite sets are closed values. Missing, substituted,
    or partial matrices fail even when an aggregate failure count is zero.
40. A verification failure receipt is durable intent. If the process stops
    after writing it but before updating state, the next mutating command must
    replay that exact receipt under the workflow lock before doing anything
    else; read-only status reports that recovery is required. Replay advances
    the attempt and invalidates both prior verification and authorization.
41. Verification and authorization receipts are attempt-scoped. Any retry,
    recovered failure, replan, source drift, or candidate drift prevents an old
    receipt from authorizing or verifying a later attempt.
42. S09 accepts only a fully committed governed source snapshot. Protected
    overlay bytes cannot be hidden inside candidate construction, and every
    control-plane or promotion-review path needed to adopt a corrected gate is
    explicitly reachable during S01.
43. All controller JSON inputs reject duplicate object keys, non-finite numeric
    constants, malformed UTF-8, and oversized bytes. Contract, verifier policy,
    evidence schema, and capability-admission documents are parsed and hashed
    from the same captured byte buffer; a second filesystem read cannot silently
    become the identity of the first parse.
44. Evidence retained across a later bounded replan binds immutable facts from
    its original attempt. In particular, the S01 failed-candidate freeze binds
    `S01.predecessor_snapshot_sha256`, not the mutable baseline of a successor
    epoch.
45. CI evidence declared in one provenance group must come from one repository,
    workflow, source commit, run ID, and run attempt. S09's source, artifact, and
    build-once claims additionally converge on one captured candidate SHA-256;
    a collage of individually successful runs cannot satisfy a step.
46. Successful `gh attestation verify` exit status is necessary but not enough.
    Its verified statement must name the expected subject SHA-256, and the local
    artifact must still hash to that value immediately after verification.
47. Crash recovery replays only canonical, content-addressed failure receipts at
    the exact epoch, step, attempt, phase, and digest-derived path created by the
    controller. It validates the rest of state before mutation and compares the
    receipt with the step definition frozen in that state, so legitimate replay
    remains possible during a later contract drift without accepting a forged
    receipt.
48. The authoritative event history is the predecessor-linked chain reachable
    from the atomic runtime-state tip. Every linked event binds its exact file
    reference and logical hash. An immutable event file left unreferenced by a
    crash is non-authoritative residue; it is neither silently promoted nor used
    to infer a state transition.
49. Independent evaluator identity is currently process-attested rather than
    cryptographically signed by Codex. A passing gate therefore requires an
    actually separate evaluator invocation plus exact artifact hashes and
    deterministic evidence validation, and must not be described as
    cryptographic proof of model identity.
50. Local `command`, `test`, and `inspection` evidence operates inside the
    non-adversarial project process boundary. Its invocation metadata is an
    audit record, not a cryptographic identity claim. The controller must still
    recompute artifact hashes, source/state bindings, and policy-owned
    assertions; any claim that must withstand a malicious same-user writer must
    instead come from an attested CI artifact or an actually separate evaluator.
51. A transition that archives or supersedes runtime state must validate, hash,
    and archive one bounded captured byte buffer. It must not validate one
    state read and archive a later read of the same path.
## Required operator sequence

Before a protected edit, run `hook pre-edit` with every intended path. After
the edit, run `hook post-edit`. Produce one closed evidence manifest for each
required evidence ID, then use `verify`, `complete`, and `next` in that
order. `status` is read-only and is the required resume entrypoint after an
interruption.

When the contract itself changes, begin and finalize the workflow-governance
correction, independently evaluate the exact control-plane bytes, update the
capability-admission bundle, and run the bounded `replan`. Top-level changes
conservatively affect S01; evidence-specific policy changes resume from the
earliest step that consumes that evidence. This is a controlled gate migration,
not a gate bypass or a new permission request.
