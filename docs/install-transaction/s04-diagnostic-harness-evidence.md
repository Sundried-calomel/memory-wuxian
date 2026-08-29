# S04 Diagnostic Harness Evidence

Status: harness and no-product-write preparation proof passed. Complete-chain
execution remains an S05 requirement.

## Harness

`scripts/install_transaction_diagnostic.py` implements two fail-closed lanes:

1. `prepare-sandbox` verifies the exact installer SHA-256, creates a read-only
   input mapping and separate writable output mapping, disables networking and
   clipboard redirection, and writes a Windows Sandbox launch contract.
2. `replay-broker` uses the manifest-bound Python, exact candidate broker,
   hash-bound request, one-time nonce, and a diagnostic child at the existing
   controller seam. It captures broker stdout, stderr, exit, request hash, child
   receipt state, and protected-path snapshots.

The harness rejects hash drift, non-empty output roots, and any diagnostic root
that contains or is contained by a protected path.

## Deterministic checks

- `python -m unittest tests.test_install_transaction_diagnostic -v`: 3/3 PASS.
- Hash mismatch fails before creating a bundle.
- Diagnostic/protected path overlap fails closed.
- Unicode paths remain structured.
- The generated sandbox maps exact input read-only, separates output, and
  disables network and clipboard access.

## Exact candidate preparation

- Installer SHA-256 verified as
  `24ce332059b79731916124e47ab32441d1dde63e610359ab734b37a0535317d8`.
- The installed OS is Windows Home and has no `WindowsSandbox.exe`; the exact
  receipt therefore reports `backend-unavailable` and does not claim a
  complete-chain run.
- The three host guards covered the installed Skill, active archive-pointer
  file, and Desktop shortcut.
- Guard snapshot before and after preparation contained 615 entries and both
  hashed to
  `54ec8683f7ba4b8aa5364a89e13a2186c1ebecce359477fa88a83f8f0bb02fd8`.
- The preparation changed no guarded product path.

## Detailed boundary replay

The replay used the exact extracted candidate broker at SHA-256
`ad21930d254492f46f66921933073f18ed49dc51e4aa55a5ae8a7eb9ef2b0feb`,
the S14 manifest at SHA-256
`74e287db44cf49d34943d907e016fe52dc49b31b45511e6ec2df3d461a0a3eb8`,
and the manifest-bound isolated Python.

The broker returned `1` before launching the diagnostic child. Captured stderr
shows that `windows_installer_broker.py:run_controller` first failed to import
`windows_install_manifest`, then failed its fallback import of
`scripts.windows_install_manifest`. The isolated runtime's `sys.path` contains
only its zip, Python root, and `Lib/site-packages`; it contains neither the
candidate `scripts` directory nor the candidate root. The candidate does contain
`scripts/windows_install_manifest.py`, so this is an import-reachability failure,
not a missing packaged file.

This is a strong root-cause candidate, but S05 must bind it to the original
Inno/PowerShell full-chain failure before production repair is permitted.

## Evidence hashes

| Evidence | SHA-256 |
| --- | --- |
| `scripts/install_transaction_diagnostic.py` | `8b2b2e5f15c18589e1625704b2ce052349e8c1672a98cd248093890f1c72e924` |
| `tests/test_install_transaction_diagnostic.py` | `fd2c526b0691937ab0f2b9d67202590c199b5177f46bb71b3942ea12569f316f` |
| `pending/s04-exact-sandbox-v2/prepare-receipt.json` | `f27b7e15f951464b9604491fce8d2cb8a1b6cd5f1d29155df87e868d5b25ab52` |
| `pending/s04-broker-replay/broker-replay-receipt.json` | `c2f97172eb385f4285055c42e958794035da96c104e43b61077e35237192ca9f` |

## Boundary

S04 proves the harness, exact input binding, detailed replay capture, and
no-product-write preparation. It does not claim installation, elevation,
complete-chain execution, root-cause admission, or repair.
