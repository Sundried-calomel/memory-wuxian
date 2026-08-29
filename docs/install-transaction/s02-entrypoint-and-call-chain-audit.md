# S02 Entrypoint And Packaged Call-Chain Audit

Status: verified against candidate commit
`319b1b23e90deaba92757da381de985e031b9853` and the current evidence-first
contract. This document supersedes the implementation-status claims in the
older `entrypoint-inventory.md`; that file remains historical evidence.

## Supported outer entrypoints

| Entrypoint | Exact production chain | Source identity |
| --- | --- | --- |
| Inno install or upgrade | `MemoryWuxian.iss` -> hidden `powershell.exe` -> `install.ps1` -> manifest preparation -> `windows_installer_broker.py --launch-manifest` -> elevated broker `--dispatch-request` -> manifest-bound Python -> `install_windows_transaction.py --execute-manifest` -> `WindowsInstallerTransaction.execute` | `inno` unless Setup receives an explicit approved source parameter |
| Manual install | operator invokes `install.ps1` -> same manifest, broker, child-controller, and transaction chain | `manual`, the script default |
| Auto-update | `auto_update.py` stages the approved installer by SHA-256 -> RunOnce launcher -> Setup with `/SOURCEENTRYPOINT=auto-update` -> complete Inno chain above | `auto-update` |

No supported outer entrypoint is allowed to call the transaction Owner directly
or to construct a second privileged command path.

## Boundary map

| Boundary | Canonical Owner | Input | Output or evidence |
| --- | --- | --- | --- |
| Setup orchestration | `packaging/windows/MemoryWuxian.iss` | packaged candidate plus Setup parameters | exact PowerShell exit becomes the custom Setup exit code |
| Runtime and manifest preparation | `packaging/windows/install.ps1` plus `scripts/install_windows_transaction.py --prepare-only` | source identity, candidate, target, archive, runtime, and Codex CLI paths | canonical UTF-8 manifest and SHA-256 |
| Non-elevated launch | `scripts/windows_installer_broker.py --launch-manifest` | manifest and controller paths | closed request, request SHA-256, nonce, and elevated dispatch result |
| Elevated dispatch | `scripts/windows_installer_broker.py --dispatch-request` | exact request SHA-256 and nonce ledger | revalidated request followed by one child process |
| Child controller | `scripts/install_windows_transaction.py --execute-manifest` | manifest path only | transaction JSON line, journal, and exact exit code |
| Product transaction | `scripts/windows_installer_transaction.py:WindowsInstallerTransaction` | validated manifest and product adapters | committed, rolled-back, or rollback-incomplete journal |
| Auto-update approval and staging | `scripts/auto_update.py` | approved package SHA-256 and updater state | RunOnce command, Setup exit code, and terminal updater state |

## Single-owner decision

`WindowsInstallerTransaction` remains the sole commit and rollback Owner.
`ProductInstallAdapter` composes bounded product mutations but does not own outer
launch, elevation, or release staging. Inno Setup, PowerShell, the broker, and
auto-update are transport or orchestration boundaries and must return the inner
result unchanged. The recovery does not introduce another transaction Owner.

## Rehearsal coverage classification

| Evidence | Boundaries actually crossed | Classification |
| --- | --- | --- |
| `run_windows_installer_rehearsal.py` / S11 receipt | manifest construction -> `WindowsInstallerTransaction.execute` | valid transaction-owner and namespaced product-resource evidence; bypasses Inno, PowerShell, UAC broker, and child CLI |
| S13 CI | source and installer construction | valid candidate-build evidence; does not execute an installation |
| S14 exact installer run | Inno -> PowerShell -> broker launch; broker returned `1` | first exact candidate evidence at the missing outer boundary; incomplete because no child receipt exists |
| bootstrap runtime-tree diagnostics | packaged runtime -> diagnostic controller | proves runtime and candidate import viability only; does not prove broker dispatch |

Therefore the old S11 receipt cannot satisfy the new
`exact-production-chain-windows-rehearsals` requirement. It remains useful and
must not be deleted or relabeled as false.

## Reachability facts carried forward

1. The exact installer is still present and hash-matches the S13 record.
2. The packaged runtime can run the isolated diagnostic controller.
3. Broker diagnostic exit code is `1` after the request is accepted.
4. The expected child diagnostic receipt is absent.
5. S05 must capture child stdout, stderr, traceback, and exit before a production
   repair can be selected.

## S02 verdict

The three supported outer entrypoints converge before the same broker and child
controller, and the intended transaction Owner is singular. The evidence gap is
not another missing entrypoint; it is the unobserved broker-to-child execution
boundary. S03 may freeze this recovery architecture without changing production
code.
