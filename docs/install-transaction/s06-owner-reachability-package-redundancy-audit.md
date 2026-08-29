# S06 Owner, Reachability, Package, And Redundancy Audit

Status: the production path audit is complete. No production file has been
modified, merged, or deleted.

## Scope and authority

This audit is bounded by the S03 architecture freeze and the S05 exact root-
cause proof. The supported outer entrypoints are `manual`, `inno`, and
`auto-update`. A direct controller call is valid only as owner-level test
evidence; it is not packaged-chain evidence and is not a supported outer
entrypoint.

## Owner and reachability audit

| Path or symbol | Sole responsibility | Supported reachability | Disposition |
| --- | --- | --- | --- |
| `packaging/windows/MemoryWuxian.iss` | Setup extraction, outer invocation, and terminal Setup exit | `inno`; also reached by `auto-update` | Keep unchanged. It is a thin outer adapter, not a transaction Owner. |
| `packaging/windows/install.ps1` | Resolve product roots and isolated runtime, prepare the closed manifest, invoke the Broker, and propagate its exit | `manual`; called by `inno` and `auto-update` through Setup | Keep unchanged. It does not own product mutations. |
| `scripts/windows_installer_broker.py` | Validate the closed UAC request, hashes, SID, paths, and nonce, then launch one hash-bound child controller | All three entrypoints | Keep as the canonical Broker and repair only its isolated-runtime import bootstrap in S07. |
| `scripts/windows_install_manifest.py` | Define, read, validate, and serialize the closed install manifest | Manifest preparation, Broker dispatch, and child execution | Keep unchanged. Do not create a Broker-specific reduced manifest parser. |
| `scripts/install_windows_transaction.py:ProductInstallAdapter` | Compose the seven product mutations | All three entrypoints after Broker dispatch | Keep unchanged as the sole product-mutation composition Owner. |
| `scripts/install_windows_transaction.py:execute_manifest` | Enter `WindowsInstallerTransaction` from the validated child manifest | All three entrypoints through `--execute-manifest` | Keep unchanged as the canonical child route. |
| `scripts/windows_installer_transaction.py:WindowsInstallerTransaction` | Journal, commit, rollback, and terminal transaction result | All three entrypoints through the Product adapter | Keep unchanged. |
| `scripts/auto_update.py:execute_windows_staged_installer` | Recheck approved Setup bytes, launch Setup once, and persist the outer result | `auto-update` only | Keep unchanged. Its hash check and result state belong to the update trust boundary. |

The reachable production chains are:

1. `manual`: `install.ps1 -> controller --prepare-only -> Broker launch ->`
   `Broker dispatch -> controller --execute-manifest -> transaction`.
2. `inno`: `MemoryWuxian.iss -> install.ps1 ->` the same canonical chain.
3. `auto-update`: `auto_update.py -> Setup /SOURCEENTRYPOINT=auto-update ->`
   the same Inno and canonical chain.

No supported entrypoint uses a second product transaction Owner.

## Exact package-membership audit

The failed installer is fixed at SHA-256
`24ce332059b79731916124e47ab32441d1dde63e610359ab734b37a0535317d8`.
Direct inspection of its hash-linked extracted candidate proves the following
members are present and byte-identical to the frozen source:

| Candidate member | SHA-256 | Runtime role |
| --- | --- | --- |
| `scripts/windows_installer_broker.py` | `ad21930d254492f46f66921933073f18ed49dc51e4aa55a5ae8a7eb9ef2b0feb` | Executed before and after elevation. |
| `scripts/install_windows_transaction.py` | `570b39cae60ddebcfc32972e4569210daf804c40e593223fbc4cfb47bce335e5` | Manifest preparer and child controller. |
| `scripts/windows_install_manifest.py` | `5efb255f91a9d0a0eff656af936a1109e337b3f1d6263e9a7ff6d517c6b451fa` | Sibling manifest module imported by Broker and controller. |
| `scripts/auto_update.py` | `2c3caeb8938b7c1c4c85d15e44ede1a15adfd1aed93cf2979d919254c419b03e` | Installed update adapter; it is not called by the active Setup transaction. |
| `packaging/windows/install.ps1` | `0949d916fb9148e85fbde3d543bd72cb0b9e8358967d0b3a7bcd65f04a92ae84` | Candidate copy is inert during Setup; Inno supplies the executing temporary copy. |
| `packaging/windows/MemoryWuxian.iss` | `cf2945b5c04dfb589167f85d16b7a36b423faed6765d94963f0cb63a17888560` | Build source only; not executed by the installed product. |

The S05 traceback therefore cannot be explained by a missing packaged manifest
module. The manifest module exists beside the Broker; the isolated interpreter
cannot resolve it because the Broker does not establish its executable-local
import roots.

## Redundancy disposition table

| Candidate | Finding | S07 decision | Reason |
| --- | --- | --- | --- |
| New Broker wrapper or fallback launcher | Would duplicate the existing Broker boundary | Do not add | It leaves the canonical entrypoint defective and violates the S03 freeze. |
| Broker-local request hash, nonce, SID, and path checks | Similar primitives exist elsewhere but these checks protect a distinct elevation boundary | Keep | Consolidation would widen the repair and couple privilege validation to unrelated helpers. |
| Controller `--prepare-only` and `--execute-manifest` modes | Both are reachable from every supported production entrypoint | Keep | They are the two intentional sides of the Broker boundary. |
| Controller legacy direct prepare-and-execute branch and `--journal-path` | No supported outer entrypoint reaches it; the parameter is not consumed by `execute_manifest` | Defer, do not delete in S07 | It is unrelated to the S14 import failure. Removal needs its own compatibility scope and focused deprecation proof; mixing it into this repair would violate the smallest-change rule. |
| Manifest/Broker `uninstall` enum mismatch with the Product adapter | A separate uninstall script exists, but changing the closed request contract is unrelated to the failure | Defer | It requires a separate contract decision and negative-path rehearsal. |
| Repeated seven-component tuple in controller composition | Local duplication with no causal relation to the failure | Defer | Cosmetic consolidation would enlarge the diff without improving the broken boundary. |
| Candidate-internal packaging sources | Observed in the exact extracted candidate but not active in the transaction chain | Defer | Package-pruning semantics need a separate exact-build comparison; they are not safe deletion targets for this root-cause repair. |
| Existing Inno, PowerShell, Broker, manifest, controller, transaction, and updater files | Each has a unique live or trust-boundary responsibility | Keep | No whole production file is redundant. |

## Focused coverage gap

`tests/test_windows_installer_runtime.py:ISOLATED_RUNTIME_ENTRYPOINTS` omits
`windows_installer_broker.py`, and its current `--help` checks would not reach
the Broker's lazy manifest import. `tests/test_windows_installer_broker.py`
imports the Broker through the repository package path and injects a dispatcher,
so it also misses the packaged isolated-process seam.

S07 must therefore add a focused isolated-process dispatch regression that:

- starts with repository paths absent from the interpreter's initial import
  search path;
- invokes the existing `--dispatch-request` production branch;
- reaches the canonical manifest reader;
- launches the child exactly once; and
- preserves the child's exit code.

A Broker `--help` test alone is insufficient.

## S06 verdict

The only admitted S07 production change is an executable-local import bootstrap
inside the existing Broker, equivalent to the already working controller
bootstrap. No wrapper, alternate Owner, package-layout change, enum change,
unrelated cleanup, or production deletion is admitted. The S07
`redundant-path-deletion-proof` must record that this audit found no production
deletion that is both necessary and safe within the current repair scope.
