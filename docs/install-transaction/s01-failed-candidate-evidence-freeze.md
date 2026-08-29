# S01 Failed Candidate Evidence Freeze

Status: frozen input evidence for the evidence-first installer recovery.

## Candidate identity

- Source commit: `319b1b23e90deaba92757da381de985e031b9853`.
- CI installer: `MemoryWuxian-2.20.0-Windows-x64-Setup.exe`.
- Installer SHA-256:
  `24ce332059b79731916124e47ab32441d1dde63e610359ab734b37a0535317d8`.
- Current local evidence copy exists at the path recorded in
  `pending/s14-install-result.json`, is 18,338,281 bytes, and recomputes to the
  same SHA-256.

## Observed boundary

- The exact CI installer was launched through the Inno entrypoint on
  2026-08-28 and returned exit code `1`.
- The isolated packaged runtime and candidate-tree bootstrap diagnostics both
  returned exit code `0`.
- The closed broker diagnostic consumed the request and returned exit code `1`.
- The requested child receipt
  `pending/s14-broker-diagnostic-child.json` does not exist.
- These facts localize the unresolved evidence gap to the broker-to-child
  controller boundary. They do not yet prove the line-level root cause.
- The earlier S11 JSON proves direct transaction-owner behavior only. It does
  not prove the Inno, PowerShell, broker, and child-controller chain.

## Frozen evidence hashes

| Evidence | SHA-256 |
| --- | --- |
| `pending/s14-install-result.json` | `1d1b16170c402a4ac67b2ced3150d261cbf7934485daabfa13bb2fca1c7a2f3c` |
| `pending/s14-broker-diagnostic-request.json` | `c191b8db4df793f2d28f17d7ed2e1d97715c49504976968c5f4b42423f648a09` |
| `pending/s14-broker-diagnostic-result.json` | `2f9418cf6b2a03d15ed35d539986761164bc225c536a512157d4677af82f08c7` |
| `pending/s14-bootstrap-diagnostic-result.json` | `95b6ce2bab9a2e032585a221771a883a822c8f289b4b53e69583cf0f4e60d431` |
| `pending/s14-bootstrap-runtime-tree-result.json` | `92c41c175ea9f119bade57c27a157cbc4d366ed2eaf190630ab3e621438266da` |
| `s13-candidate-source-commit.md` | `2fe891e8e17dff0b73740226d669c79389bd27033b682f2b6545e11bae8df3c4` |
| `s13-ci-artifact-sha256.md` | `027e8080c5013d2cbef6f7efc95122c7cc0b6123b6379b938d38e6e204312d1a` |
| `receipts/replan-24.json` | `1e5c352644e080e424f30e6b75966d7edf562d0fc62c279a4e4f9622ffc32c6d` |

## Preservation rule

These records are evidence, not repair targets. Later diagnostics may append
new receipts but must not overwrite or reinterpret these bytes. Any claim that
the failure is fixed must use a newly built candidate and must compare it with
this frozen baseline.
