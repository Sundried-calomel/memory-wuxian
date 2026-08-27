# S11 Real Windows Rehearsal Evidence

Status: passed with explicit UAC consent on the target Windows device.

## Release Identity

- Python package version: `2.20.0`
- native collector version: `2.20.0`
- release-contract tests: 6 passed
- runtime bundle ID: `5089dd5d4f3c79182b90bc03850e8d06f405625ce4b1428105e23802c7240149`

## Elevated Receipt

- receipt: `docs/rehearsals/v2.20.0-s11-windows-rehearsal.json`
- receipt SHA-256: `2c3867db1b5f1b206fdf7617a2faeff1c0a86aea63f5dfa95e9c615d0c9cca64`
- candidate tree SHA-256: `284d648a59f8ffcccc13d02bfb696f3b8f41e255e58493c0879867dfec0450ed`
- clean install: committed, exit 0
- repeat install: committed, exit 0
- injected failure: rolled back, exit 34
- v2.15.0 upgrade: committed, exit 0
- rollback exact: true
- production resources unchanged: true
- rehearsal task, Run-value, and shortcut cleanup: verified absent

Earlier elevated attempts are retained as pre-apply failure evidence. The
original attempts exposed unbounded federation and collector projection paths.
The resumed S10-S13 run then rejected a reused work root, a raw Git projection
containing excluded native build caches, and an incomplete package projection
missing Git-ignored release binaries. The S14 recovery rerun additionally
proved that Windows PowerShell 5.1 corrupted a no-BOM launcher path containing
non-ASCII characters and that a candidate projection had accidentally retained
`native-collector/target`. The launcher now derives the repository from
`PSScriptRoot`; the final candidate excludes native build caches, contains all
three hash-checked Windows binaries, uses bounded ProgramData staging roots,
and passed all four scenarios. Every failed attempt stopped during prepare and
did not apply a formal product resource.

S11 does not claim production-name installation. The complete committed
candidate is frozen at S13 and the exact CI artifact is installed at S14.
