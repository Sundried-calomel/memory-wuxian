# S11 Real Windows Rehearsal Evidence

Status: passed with explicit UAC consent on the target Windows device.

## Release Identity

- Python package version: `2.20.0`
- native collector version: `2.20.0`
- release-contract tests: 6 passed
- runtime bundle ID: `5089dd5d4f3c79182b90bc03850e8d06f405625ce4b1428105e23802c7240149`

## Elevated Receipt

- receipt: `docs/rehearsals/v2.20.0-s11-windows-rehearsal.json`
- receipt SHA-256: `01a8944e1f7868bb7f617c2c2f996e0893bbb7fa2f108090dc51b6861f3afe15`
- candidate tree SHA-256: `dd9251bf57edd6917bdadc6029e5e9e2a51acef3a3c759592742a1988ac69787`
- clean install: committed, exit 0
- repeat install: committed, exit 0
- injected failure: rolled back, exit 34
- v2.15.0 upgrade: committed, exit 0
- rollback exact: true
- production resources unchanged: true
- rehearsal task, Run-value, and shortcut cleanup: verified absent

Earlier elevated attempts are retained as pre-apply failure evidence. The
original two attempts exposed unbounded federation and collector projection
paths. The resumed S10-S13 run then rejected a reused work root, a raw Git
projection containing excluded native build caches, and an incomplete package
projection missing Git-ignored release binaries. The final candidate follows
the Inno exclusion contract, contains all three hash-checked Windows binaries,
uses bounded ProgramData staging roots, and passed all four scenarios. Every
failed attempt stopped during prepare and did not apply a formal product
resource.

S11 does not claim production-name installation. The complete committed
candidate is frozen at S13 and the exact CI artifact is installed at S14.
