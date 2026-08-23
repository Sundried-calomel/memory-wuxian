# S11 Real Windows Rehearsal Evidence

Status: passed with explicit UAC consent on the target Windows device.

## Release Identity

- Python package version: `2.20.0`
- native collector version: `2.20.0`
- release-contract tests: 6 passed
- runtime bundle ID: `5089dd5d4f3c79182b90bc03850e8d06f405625ce4b1428105e23802c7240149`

## Elevated Receipt

- receipt: `docs/rehearsals/v2.20.0-s11-windows-rehearsal.json`
- receipt SHA-256: `97b01b68b7d0db0b644eff80aa493d113a94d198bb22be3261cdf61def5e127e`
- candidate tree SHA-256: `52f6d3bede7a197b4672c817299f4868587e5b8dce26a170e3e1501906e9fa18`
- clean install: committed, exit 0
- repeat install: committed, exit 0
- injected failure: rolled back, exit 34
- v2.15.0 upgrade: committed, exit 0
- rollback exact: true
- production resources unchanged: true
- rehearsal task, Run-value, and shortcut cleanup: verified absent

Two earlier elevated attempts are retained as pre-apply failure evidence. The
first exposed an unbounded federation probe path; the second proved that the
same path family affected the collector candidate projection. The final
candidate uses a short-lived federation probe plus disjoint bounded ProgramData
staging roots and passed all four scenarios. Neither failed attempt applied a
formal product resource.

S11 does not claim production-name installation. The complete committed
candidate is frozen at S13 and the exact CI artifact is installed at S14.
