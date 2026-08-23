# S07 Isolated Offline Runtime Evidence

Status: verified against the completed S06 production route and a freshly
assembled Python 3.14.7 runtime bundle.

## Reused implementation

`scripts/install_windows_runtime.py` is retained with a bounded correction
under the hash-bound reuse map. A real import probe exposed that Python could
create `__pycache__` files inside the content-addressed runtime after its first
validation. Assembly now installs a manifest-bound `sitecustomize` guard with
a deterministic checked-hash bytecode file. Activation also sets
`PYTHONDONTWRITEBYTECODE=1` and validates the complete bundle again after the
probe. The package lock retains its approved correction and pins Python 3.14.7
x64 plus every packaged dependency. CI remains the only networked asset-fetch
stage; installation validates and activates supplied bytes without PATH
discovery or pip.

The S06 manifest v2 now additionally binds the Codex CLI path and SHA-256 while
retaining the runtime interpreter, dependency lock, bundle manifest, bundle ID,
and their hashes. The official PowerShell route prepares that manifest with the
validated package interpreter and launches the broker with the same runtime.

## Current verification

Thirty-four tests passed across runtime, manifest/controller, broker, and
reuse-map modules.
The seven runtime cases cover offline assembly from supplied assets, empty-PATH
activation, deterministic bytecode suppression, post-probe immutability,
extra-file and byte-drift rejection, absence of online pip in the production
installer, the closed eight-entrypoint isolated-Python matrix, and pinned
dependency versions. The adjacent tests prove all three outer entrypoint
sources produce the same manifest semantics and that broker dispatch preserves
the runtime-bound controller route.

The reuse-map gate passed for S07 and verified both the runtime implementation
and runtime lock as applied corrections while preserving their rejected
baseline hashes. It also verified the current seven-resource composition as
changed from the rejected baseline.

A fresh bundle was assembled at
`C:\Users\56453\AppData\Local\Temp\memory-wuxian-s11-cc359401a11d456ea6d36c68c1881f4b\runtime-windows-immutable`.
Its bundle ID is
`5089dd5d4f3c79182b90bc03850e8d06f405625ce4b1428105e23802c7240149`;
the manifest binds 339 files and 10 packages. With `PATH` empty and without an
external `PYTHONDONTWRITEBYTECODE` setting, the packaged interpreter imported
the application successfully and printed `runtime-ready True`. A complete
post-probe validation then passed with no missing, extra, reordered, or drifted
file. The package runtime is rebuilt and checked again at S13 CI and exercised
from the frozen installer at S14; this step does not claim a product install.
