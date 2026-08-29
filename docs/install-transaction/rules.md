# Unified Installer Transaction Rules

<!-- workflow-governance: current=WF-20260829-004 -->

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
4. A failed verification permits one integrated repair cycle. A second failure
   changes the workflow to `needs_replan`; no additive patch loop is allowed.
5. Architecture, candidate, and release freeze points are immutable. Any
   relevant byte drift invalidates the freeze and every downstream receipt.
6. S13 freezes a committed source candidate and CI builds its uniquely hashed
   installer. S14 installs that exact CI artifact. S15 promotes the same bytes
   to Release without rebuilding, then verifies the official asset again.
7. Independent evaluation occurs only at the architecture freeze, candidate
   freeze, and final promotion gates. It is not an open-ended patch generator.
8. The project-local hook applies only to paths protected by this workflow. It
   must not install a global hook or block unrelated projects and conversations.
9. A contract correction is performed only from `needs_replan`, with the exact
   old state and contract hashes preserved. The corrected contract, capability
   manifest, project binding, independent semantic review, and admission receipt
   must be hash-consistent before `replan` may reactivate S01. Runtime state is
   never edited by hand.
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
13. When the target Windows edition has no local disposable backend, S09 may
    use an explicitly authorized GitHub-hosted ephemeral Windows runner. The
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

## Required operator sequence

Before a protected edit, run `hook pre-edit` with every intended path. After
the edit, run `hook post-edit`. Use `verify`, `complete`, and `next` in that
order. `status` is read-only and is the required resume entrypoint after an
interruption.

When the contract itself must change, first enter `needs_replan` through the
state owner. Begin and finalize the workflow-governance correction, obtain a
fresh capability-admission receipt for the corrected hashes, and only then run
the approved `replan`. This is a controlled gate migration, not a gate bypass.
