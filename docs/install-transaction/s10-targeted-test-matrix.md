# S10 Targeted Test Matrix

Status: passed

Runtime: `C:\Users\56453\.codex\runtimes\memory-wuxian-e5-py312\Scripts\python.exe`

| Case | Boundary | Production path exercised | Result |
|---|---|---|---|
| S10-MAN-01 | closed manifest | `windows_install_manifest.py` plus product composition root | PASS |
| S10-XML-01 | task XML bytes and normalization | `platform_scheduler.py` task renderer/query parser | PASS |
| S10-ENC-01 | Chinese, Japanese, spaces, and special path characters | scheduler, launcher, manifest, atomic JSON paths | PASS |
| S10-UAC-01 | closed operation allowlist and exact hashes | `windows_installer_broker.py` | PASS |
| S10-UAC-02 | cancellation, denial, wrong SID, replayed nonce | broker fail-closed paths | PASS |
| S10-RUN-01 | isolated runtime with cleared PATH | runtime validate and activation path | PASS |
| S10-RUN-02 | first-import bytecode drift | manifest-bound guard and post-probe bundle validation | PASS |
| S10-MIG-01 | v2.15.0 linear migration and idempotency | migration registry and frozen fixture | PASS |
| S10-RBK-01 | partial apply and verification failure | transaction controller rollback journal | PASS |
| S10-RBK-02 | rollback failure and commit failure | distinct phase and exact exit codes | PASS |
| S10-UPD-01 | approved package drift | hash-bound RunOnce wrapper | PASS |
| S10-UPD-02 | exact outer installer status | updater atomic state receipt | PASS |
| S10-IDM-01 | repeat registration and atomic replacement | scheduler, shortcut, and platform transaction helpers | PASS |
| S10-WFL-01 | step order, path scope, remediation, replan | project workflow controller | PASS |
| S10-CMP-01 | seven-resource production composition | archive, federation node, collector, configuration, maintenance, auto-update, dashboard mutations | PASS |
| S10-REH-01 | rehearsal resource isolation and cleanup | namespaced real-Windows rehearsal runner | PASS |
| S10-LNG-01 | deep Windows transaction path and probe identity | federation-node prepare evidence, installed `replica_root`, and bounded ProgramData staging owners | PASS |
| S10-REU-01 | approved reuse and correction map | exact hashes, corrected artifacts, bounded replacement composition | PASS |
| S10-ARC-01 | architecture ownership and dependency boundaries | architecture contract validator | PASS |
| S10-PSH-01 | PowerShell entrypoint syntax | four installer/bootstrap/shortcut scripts | PASS |
| S10-DIF-01 | whitespace and conflict-marker hygiene | `git diff --check` | PASS |

Command: `python -m unittest` over fifteen installer, migration, scheduler, updater,
shortcut, workflow, reuse-map, and atomic-write modules.

Result: 156 tests passed; one platform-specific case was skipped by its declared
platform guard. Architecture, reuse-map, PowerShell parser, and diff-format gates
also passed against the same current worktree. The verbose invocation produced
more output than the client retained, so the identical matrix was rerun without
verbosity to obtain a bounded, explicit exit code and count. No product code was
changed for this evidence rebuild.

The first seven-resource elevated attempt is retained only as failure evidence:
it exposed the deep-path federation probe before any formal resource was
applied. The repaired bytes now pass S10-LNG-01; clean, repeat, rollback, and
v2.15.0 upgrade cases remain mandatory at S11 against the current candidate.

## 2026-08-25 S10 CI-portability replan

PR #75 exposed three test-and-evidence portability defects without identifying a
production installer semantic change: Git checkout byte normalization, Windows
8.3-versus-long canonical path identity, and a negative dashboard fixture that
failed at the icon gate before reaching its declared launcher-hash gate.

The repaired S10 bytes preserve every existing `.gitattributes` rule and add
explicit checkout policy for v2.20 governance artifacts. Windows path assertions
now canonicalize both actual and expected values through `Path.resolve()` while
retaining exact path identity. The launcher-hash fixture uses the manifest's
canonical icon path so it reaches its intended failure boundary on Windows
runners with 8.3 checkout aliases.

Validation against the final S10 worktree:

- 16-module impact matrix: 136 tests run, 135 passed, one existing
  platform-guarded case skipped;
- original CI failure subset: 44/44 passed;
- v2.18 hash-bound evidence regression remained passing;
- architecture contract and seven-resource reuse-map validation passed;
- four PowerShell installer/bootstrap/shortcut entrypoints parsed without error;
- `git diff --check` passed, with only expected Windows checkout warnings.

No production Python, PowerShell, Inno Setup, Rust, archive, scheduler, cloud,
summary, collector, shortcut, or environment behavior changed in this replan.

## 2026-08-28 S14 package-integrity correction

The S14 failure was localized to the Inno source projection excluding the
manifest-bound `sitecustomize.*.pyc`. The corrected candidate changes one Inno
file rule and one focused regression assertion; all other production modules
remain byte-identical to the restored first-S14 baseline.

The exact 15-module installer impact matrix ran 146 tests: 145 passed and one
declared platform case was skipped. Architecture ownership, S10 reuse-map,
four PowerShell parser checks, and `git diff --check` also passed. A broader
866-test local run had one unrelated frozen CLI usage-format failure: command,
parameter, and behavior contracts matched, while this local Python build wrapped
two long argparse error-usage strings differently. No CLI source or snapshot was
changed for this installer correction; CI remains responsible for the complete
candidate suite before S13 freeze.

## 2026-08-26 candidate-manifest evidence correction

CI run `32966284160` passed the documentation gate, the macOS and Ubuntu jobs,
the Windows Rust build, the packaged Windows binaries, and the complete Windows
Python suite. Inno Setup also compiled the v2.20.0 installer successfully and
logged the bundled `runtime\windows\runtime-manifest.json` and
`runtime\windows\python\python.exe` files.

The job then failed only because its post-build evidence assertion asked for the
nonexistent `runtime\windows\bundle-manifest.json`. The frozen runtime contract,
runtime assembler, transaction installer, rehearsal, and tests consistently use
`runtime-manifest.json`. The assertion now names that contract-authoritative
file. No product or installer behavior changed.
