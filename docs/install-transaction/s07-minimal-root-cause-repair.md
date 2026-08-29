# S07 Minimal Root-Cause Repair

Status: the one admitted production repair is implemented and the focused
isolated-runtime regression passes. No production path was deleted.

## Minimal root-cause diff

`scripts/windows_installer_broker.py` now establishes its own `SCRIPT_DIR` and
`PROJECT_ROOT` in `sys.path` before any project-local import. The eight added
lines are byte-equivalent in behavior to the already working bootstrap in
`scripts/install_windows_transaction.py`.

No existing Broker validation, request field, hash, SID, nonce, trusted path,
runtime identity check, child command, or exit-code propagation changed. No
wrapper, fallback launcher, alternate manifest parser, state machine, or Owner
was added.

Production source after repair:

- `scripts/windows_installer_broker.py` SHA-256:
  `9b3782720281b88650dd59d25a7a22e1de2e43245e5c6f5d47186e7206ad5f73`.

## Focused regression

`tests/test_windows_installer_runtime.py` now includes the Broker in the
isolated-entrypoint matrix and adds one separate `-I` process regression. The
regression begins without repository paths in the interpreter search path,
loads the production Broker, enters its real `--dispatch-request` function,
imports the canonical sibling manifest module, calls the manifest-reader seam,
dispatches one hash-bound child command, and proves child exit code `37` is
returned unchanged.

Test source SHA-256:

- `tests/test_windows_installer_runtime.py`:
  `373b572c521e1dbeb23156865a98177281a83cc9210ce894a31ee7c3406f509a`.

Using the manifest-bound Memory Wuxian isolated Python at bundle id
`837561f3a7772c76f45bf6fa02a1a2189151e39b0aeb2eb950512a4b7816f6d0`,
the focused Broker and runtime suites passed 17/17 tests.

An earlier run with the Codex bundled Python passed the new Broker regression
but four pre-existing entrypoint checks failed because that interpreter does
not contain `PyYAML`. The same unchanged cases passed under the product's
manifest-bound runtime. No test or production code was weakened to accommodate
the wrong runner.

## Exact historical-manifest replay

The repaired source Broker was replayed with the immutable S14 manifest at
SHA-256
`74e287db44cf49d34943d907e016fe52dc49b31b45511e6ec2df3d461a0a3eb8`
and its manifest-bound Python. The old missing-module traceback is gone: the
Broker successfully imports and enters the canonical manifest validator. The
replay then stops at the later expected check `Codex CLI hash drift`, because
the live Codex executable has changed since the immutable S14 manifest was
created. The historical manifest was not rewritten to hide that drift.

The replay preserved all 615 guarded product entries with identical pre/post
snapshot SHA-256
`54ec8683f7ba4b8aa5364a89e13a2186c1ebecce359477fa88a83f8f0bb02fd8`.
Its receipt SHA-256 is
`fb78fdbaf96e05f151c3bf69a1c84b59d70715e3983a825d8616164d6f457648`.

## Redundant-path deletion proof

The S06 audit found no production deletion that is both necessary and safe in
this repair scope. Therefore S07 deletes no production file, function, branch,
parameter, enum member, or package member. The dormant direct controller path,
operation-contract questions, duplicated component tuple, and candidate package
pruning remain explicitly deferred rather than being mixed into this root-
cause fix.

This is a positive deletion decision: `delete none`, because the only causal
defect is the missing bootstrap at the canonical Broker boundary.

## Defect-rule conformance

- Nearest proven false boundary repaired; no broad replan.
- Existing Owner retained; no competing implementation.
- Minimal production diff; unrelated cleanup deferred.
- Exact historical evidence preserved append-only.
- Wrong-runtime test failures classified instead of patched around.
- No install, UAC, release, archive, cloud, task, registry, shortcut, or active
  Skill mutation occurred in S07.

## S07 verdict

The Broker is now executable under isolated Python and its child dispatch seam
has a focused behavioral regression. S08 may evaluate supported-entrypoint
equivalence, historical installer regressions, and final diff simplification.
