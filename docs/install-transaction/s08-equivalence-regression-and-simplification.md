# S08 Equivalence, Regression, And Simplification Review

Status: focused equivalence and historical installer regressions pass. The
final repair diff needs no further simplification.

## Entrypoint equivalence

The focused matrix verifies that the three supported provenance values produce
the same closed manifest semantics after removing only `source_entrypoint`:

- `manual` enters `install.ps1` directly;
- `inno` enters `MemoryWuxian.iss`, then the same `install.ps1` chain; and
- `auto-update` launches the approved Setup with
  `/SOURCEENTRYPOINT=auto-update`, then rejoins the same Inno chain.

All three continue through:

`--prepare-only -> Broker launch -> Broker dispatch -> --execute-manifest ->`
`ProductInstallAdapter -> WindowsInstallerTransaction`.

The S07 production change is before the Broker's sibling import only. It does
not branch on source entrypoint and therefore cannot alter one entrypoint's
manifest, transaction order, rollback, or exit behavior independently.

## Historical regression matrix

The manifest-bound product runtime executed these focused modules:

- `tests.test_windows_installer_runtime`
- `tests.test_windows_installer_broker`
- `tests.test_windows_installer_transaction`
- `tests.test_windows_installer_composition`
- `tests.test_windows_lifecycle_transaction`
- `tests.test_auto_update`

Result: 85/85 tests passed in 5.188 seconds.

The matrix covers the isolated runtime, closed Broker request, nonce replay,
hash and SID failures, child exit propagation, three-entrypoint manifest
equivalence, Inno/PowerShell wiring, seven-resource composition, Unicode paths,
rollback and rollback verification, interrupted transaction recovery,
idempotency, update approval, package hash drift, and auto-update re-entry.

This is a focused affected-surface matrix, not a full repository test run.

## Diff simplification review

Four read-only review roles evaluated reuse, code quality and ownership,
efficiency and reachability, and clarity and package membership. The main-agent
review then inspected the final diff.

Final disposition:

| Review question | Verdict |
| --- | --- |
| Can an existing helper be imported instead of the local bootstrap? | No. A helper cannot be imported until executable-local reachability already exists. |
| Should Broker and controller bootstrap be merged into a wrapper? | No. That duplicates or moves the privilege-boundary Owner and does not repair the canonical executable. |
| Does the repair add repeated runtime work? | No. The path initialization runs once at Broker process startup. |
| Can the production diff be smaller? | No. It is eight additive lines matching the established controller pattern; no validation or control flow changed. |
| Is the 70-line regression excessive? | No. It creates the separate isolated process required to reproduce the previously untested lazy-import and child-dispatch seam without touching product resources. |
| Should nearby dormant or duplicate code be cleaned now? | No. Those items are non-causal and explicitly deferred by S06. |

`git diff --numstat` for the admitted implementation is:

- `scripts/windows_installer_broker.py`: `+8 / -0` production lines.
- `tests/test_windows_installer_runtime.py`: `+70 / -0` test lines.

`git diff --check` reported no whitespace error. Git emitted only the existing
Windows line-ending advisory; no line-ending rewrite was performed.

## S08 verdict

The minimal Broker bootstrap preserves all supported entrypoint semantics and
passes the focused historical installer regression surface. No simplification,
merge, deletion, or adjacent cleanup is admitted before S09.
