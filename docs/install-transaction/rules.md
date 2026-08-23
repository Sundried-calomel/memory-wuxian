# Unified Installer Transaction Rules

<!-- workflow-governance: current=WF-20260822-001 -->

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

## Required operator sequence

Before a protected edit, run `hook pre-edit` with every intended path. After
the edit, run `hook post-edit`. Use `verify`, `complete`, and `next` in that
order. `status` is read-only and is the required resume entrypoint after an
interruption.
