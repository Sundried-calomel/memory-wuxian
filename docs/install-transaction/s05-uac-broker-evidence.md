# S05 Minimal UAC Broker Evidence

## Contract

- The request schema is closed to transaction ID, allowlisted operation, target
  SID, exact manifest and controller paths and SHA-256 values, and one nonce.
- Manifest and controller paths must resolve beneath caller-supplied trusted
  roots. Relative paths, missing files, symlink escapes, and hash drift fail
  before dispatch.
- The nonce ledger binds each nonce to one canonical transaction UUID and target
  SID. Validation consumes it atomically only after all field, path, and hash
  checks pass.
- The broker receives one typed dispatcher callback. It cannot accept or build
  an arbitrary command line.
- Elevation cancellation, access denial, nonce rejection, path escape, hash
  mismatch, invalid request, and controller failure retain distinct exit codes.

## Verification

`python -m unittest tests.test_windows_installer_broker -v` passed six tests:

- one allowlisted dispatch followed by replay rejection;
- unknown field and unknown operation rejection;
- hash drift rejection without prematurely consuming the nonce;
- path escape and target-SID mismatch classification;
- expired nonce rejection;
- stable cancellation and denial exit classification.

No test invokes UAC, PowerShell, ShellExecute, an installer, the network, or the
active Memory Wuxian installation.
