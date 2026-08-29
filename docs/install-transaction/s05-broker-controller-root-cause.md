# S05 Broker-To-Controller Root-Cause Proof

Status: root cause proven from two hash-linked evidence lanes. No production
repair has been applied.

## Complete packaged-chain lane

The frozen CI installer at SHA-256
`24ce332059b79731916124e47ab32441d1dde63e610359ab734b37a0535317d8`
was launched through its Inno entrypoint. The Inno log proves that Setup:

1. extracted the packaged candidate and `install.ps1`;
2. reached the post-install transaction invocation;
3. received transaction exit code `1`; and
4. deinitialized without reporting a successful transaction.

The corresponding S14 result binds source entrypoint `inno`, the same installer
SHA-256, the exact log path, and exit code `1`. Its stdout and stderr logs are
empty because the hidden outer process did not preserve child diagnostics.

## Detailed broker replay lane

The S04 replay used:

- the exact extracted candidate broker at SHA-256
  `ad21930d254492f46f66921933073f18ed49dc51e4aa55a5ae8a7eb9ef2b0feb`;
- the original S14 manifest at SHA-256
  `74e287db44cf49d34943d907e016fe52dc49b31b45511e6ec2df3d461a0a3eb8`;
- its manifest-bound isolated Python; and
- the existing broker `--dispatch-request` path with a hash-bound diagnostic
  child at the controller seam.

The broker consumed the request, returned `1`, and created no child receipt.
Its captured traceback is exact:

```text
ModuleNotFoundError: No module named 'windows_install_manifest'
...
ModuleNotFoundError: No module named 'scripts'
```

Both exceptions originate in
`windows_installer_broker.py:dispatch_request.run_controller`, before
`subprocess.run` can launch the child controller.

## Import-reachability proof

- The candidate contains `scripts/windows_install_manifest.py` at SHA-256
  `5efb255f91a9d0a0eff656af936a1109e337b3f1d6263e9a7ff6d517c6b451fa`.
- The candidate contains `scripts/install_windows_transaction.py` at SHA-256
  `570b39cae60ddebcfc32972e4569210daf804c40e593223fbc4cfb47bce335e5`.
- The isolated runtime `python314._pth` contains only `python314.zip`, `.`,
  `Lib/site-packages`, and `import site`.
- Running that Python reports a `sys.path` containing only the runtime zip,
  runtime root, and runtime `Lib/site-packages`.
- `install_windows_transaction.py` explicitly inserts both its `SCRIPT_DIR` and
  project root into `sys.path` before sibling imports.
- `windows_installer_broker.py` performs no equivalent path bootstrap before
  importing `windows_install_manifest` inside `run_controller`.

Therefore the missing import is not a packaging omission and not a malformed
manifest. It is one inconsistent executable-script bootstrap boundary: the
transaction controller is isolated-runtime safe, while the broker is not.

## Causal binding

The complete Inno/PowerShell lane and detailed replay use the same candidate
broker and manifest-bound runtime and both terminate at broker exit `1` before a
child receipt exists. The replay supplies the traceback hidden by the original
outer process. The observed facts are sufficient to identify the nearest false
boundary without rerunning the exact installer against host product resources.

## Minimal repair boundary for S07

S07 should make the broker executable-script bootstrap equivalent to the
already working controller bootstrap by adding its own script directory and
project root to `sys.path` before sibling imports. It must not:

- add a new wrapper or fallback installer;
- weaken manifest, controller-hash, SID, nonce, path, or runtime checks;
- parse a second reduced manifest schema inside the broker; or
- change the transaction Owner, package layout, or supported outer entrypoints.

S06 must first confirm ownership, reachability, package membership, and whether
any surrounding duplicate bootstrap or wrapper can be deleted safely.

## Evidence hashes

| Evidence | SHA-256 |
| --- | --- |
| `pending/s14-inno-install.log` | `4696c599b3e6ebfc93675e25e49f575ac588186766ae97ac2bbd5c2f702fa4ac` |
| `pending/s14-install-result.json` | `1d1b16170c402a4ac67b2ced3150d261cbf7934485daabfa13bb2fca1c7a2f3c` |
| `pending/s04-broker-replay/broker-replay-receipt.json` | `c2f97172eb385f4285055c42e958794035da96c104e43b61077e35237192ca9f` |

## S05 verdict

Root cause is proven: the isolated runtime cannot resolve the broker's sibling
manifest import because the broker lacks the local import-path bootstrap already
present in the child controller. Production code remains unchanged.
