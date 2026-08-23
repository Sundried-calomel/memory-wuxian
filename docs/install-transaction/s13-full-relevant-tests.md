# S13 Full Relevant Tests

Status: locally passed with runtime-specific CLI formatting lane separated.

## Python product suite

- Development runtime: Python 3.12.13.
- Command: `python -m unittest discover -s tests -q`.
- Observed: 865 tests, 19 declared skips, one CLI snapshot mismatch, and no
  other failure.
- The sole mismatch was `argparse` usage wrapping before the final `...`; the
  command set, actions, defaults, exit categories, and all product behavior
  remained identical.
- Frozen package runtime: Python 3.14.7.
- Command: `python -m unittest discover -s tests -p test_cli_contract_snapshot.py -q`.
- Observed: 6 tests passed, including the exact frozen v2.18 CLI snapshot.

The combined accepted result is 846 non-skipped logical contracts passed and
19 declared platform skips. The complete repository discovery under the
embedded package runtime is not an acceptance lane because its `_pth`
isolation deliberately omits the source tree from subprocess import paths;
the exact CLI module is the bounded runtime-dependent check.

## Installer and native gates

- Windows installer S10 matrix: 156 passed, 1 declared platform skip.
- Rust `cargo test --manifest-path native-collector/Cargo.toml`: 38 passed,
  0 failed.
- Architecture contract: passed.
- Seven-resource reuse map: passed.
- PowerShell parser and `git diff --check`: passed; line-ending warnings only.
- Real UAC Windows scenarios: clean, repeat, rollback, and v2.15.0 upgrade all
  passed in the hash-bound S11 receipt.

No production code was changed to accommodate a host-only help-layout
difference.
