# S02-S15 Evidence Reuse Assessment

The new E001 state starts at S01, but applicable implementation and evidence are
not discarded. A legacy PASS is not inherited when its contract, source
snapshot, evidence schema, or candidate identity differs.

| Step | Reuse decision | Required next action |
| --- | --- | --- |
| S02 | Reuse the entrypoint and Owner topology; rebind it to current bytes. | Emit three E001 inspection manifests and advance. |
| S03 | Reuse the architecture except the stale S13 candidate-freeze wording. | Correct that one statement and independently evaluate the exact new hash. |
| S04 | Reuse the failed artifact and harness design. | Run focused harness checks once on current bytes. |
| S05 | Reuse the immutable failure and traceback as historical causal proof. | Rehash and emit current-schema manifests; do not rerun the failed installer. |
| S06 | Reuse the owner, package, and redundancy analysis. | Compare it with current source and emit E001 audit evidence. |
| S07 | Reuse the existing repair as the implementation candidate. | Reprove its current diff and defect conformance; edit only if a causal defect remains. |
| S08 | Reuse the test selection and review questions. | Run the affected matrix once on current bytes. |
| S09 | Earlier installers are historical only. | Commit final bytes and build exactly one new immutable candidate. |
| S10 | Reuse the hosted harness and diagnostics. | Run them against the S09 candidate. |
| S11 | Reuse the four scenario definitions. | Run all four against the same S09 candidate. |
| S12 | Reuse the seven effect definitions. | Verify all effects for the same candidate. |
| S13 | Reuse suite and evaluator contracts. | Run candidate-bound CI/evaluation and promote without rebuilding. |
| S14 | Reuse the target procedure. | Install the exact promoted bytes after candidate-bound UAC authorization. |
| S15 | Reuse the release procedure. | Publish, reinstall the official same bytes, evaluate, and close defects. |

The detailed machine-readable decisions are in
`s02-stage-reuse-assessment.json`.
