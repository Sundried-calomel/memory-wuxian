# S08 Inno placeholder equivalence and regression evidence

Status: passed for the bounded S07 change.

## Entrypoint equivalence

The canonical manifest and controller tests confirm that `inno`, `manual`, and
`auto-update` still build the same closed install contract after normalizing the
source entrypoint. Inno still calls the official PowerShell wrapper, which still
prepares the manifest and routes through the same broker and seven-resource
controller. No direct product mutation was added to the wrapper.

The new wrapper behavior occurs before manifest creation and has one purpose:
carry Inno's own uninstall metadata into the candidate bytes that the existing
generation mutation commits. The regular expression is closed to direct regular
files named `unins[0-9]+.(exe|dat|msg)`.

## Historical regression coverage

A clean local test run passed 77 tests covering:

- clean Inno placeholder admission and exact metadata preservation;
- existing-install metadata preservation without copying an unrelated file;
- all three source entrypoints and the single-controller route;
- all seven resource boundaries, rollback ordering, commit recovery, and
  rollback-incomplete behavior;
- bounded product composition and the isolated rehearsal namespace;
- explicit Skill-root/service-SID behavior and dashboard ownership;
- offline runtime packaging and broker bootstrap contracts;
- GitHub candidate/release workflow gates.

The local workspace does not contain the dynamically assembled Windows runtime,
so the one test that executes four entrypoints inside that generated runtime was
not counted in the 77-test pass. GitHub S09 assembles that runtime from the lock,
runs the complete Python suite, builds the exact Inno candidate, and is the
authoritative evidence for that boundary.

## Diff simplification review

The review scope was limited to `packaging/windows/install.ps1` and
`tests/test_windows_inno_bootstrap.py`.

- Reuse: the existing Skill-root validator, service-SID fallback, candidate
  projection, broker, and transaction controller remain in place.
- Quality: no new parameter, resource Owner, scheduler, registry value, or
  alternate install path was introduced.
- Efficiency: the wrapper performs one shallow enumeration of the target Skill
  root during installation only; it does not recurse.
- Clarity: separate predicate and copy functions keep admission distinct from
  byte preservation, and the test names state clean versus existing behavior.

No further simplification was applied. Combining the admission and copy loops
would obscure their different trust conditions, while extracting a new packaged
module would enlarge this two-function repair without changing behavior.
