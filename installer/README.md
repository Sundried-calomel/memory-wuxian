# Memory Wuxian installer

Independent Python 3.10+ installer for the compact-core workflow. No extra Python
dependencies. Keep these Python files together and outside the product's
managed files. This installer does not install Python or Codex, create device keys,
configure peers, or register first-install OS services.

## On-demand updates and dashboard integration

`python cli.py check --target PATH` checks the latest formal product release.
`python cli.py update --target PATH --version 2.20.3` downloads that explicit release
and applies it using the same transaction engine. Add `--offline` only after stopping
all clients/services on platforms without live integration. No periodic checks occur.

When the installer directory is located at `PRODUCT/installer`, point the existing
dashboard task at `installer/dashboard.py` instead of `core/dashboard.py`, retaining
its original Python executable and --root/--config/--port arguments. This adapter
loads the product's dashboard and adds Check for updates / Upgrade controls to its
System page. The adapter survives product replacements. Windows upgrades use the
separate, on-demand MemoryWuxianManualUpdate task with no recurring trigger, so stopping
the dashboard does not terminate the updater. No configuration or archives are sent
to GitHub; only public release metadata and the selected package are downloaded.

## Normal update

Obtain the platform product ZIP and its SHA256 from the trusted GitHub release.
Never accept a checksum supplied only inside an untrusted ZIP.

```text
python cli.py apply --target PATH --package PRODUCT.zip --sha256 TRUSTED_SHA256
```

On Windows the existing MemoryWuxianCoreMaintenance and MemoryWuxianCoreDashboard
tasks are paused and restored. An active maintenance tick has up to five minutes to
finish; a busy tick prevents replacement. Existing MCP clients should reconnect
after an update. On macOS/Linux, stop all product clients/services first and add
`--offline`. That flag asserts they are stopped; it does not stop them for you.
For a new empty target use `apply --offline`, then the product's configuration
entry point and your OS scheduler. Automatic fresh-device service setup is not included.

## Existing installation

For an exact release installation, run `adopt` with the same target/package/SHA256
options before the first update. Add `--accept-local` only when explicitly choosing
to preserve and own existing locally modified program files. This records their
actual hashes as an **unversioned local baseline (0.0.0)**, not as a verified release.
Only existing program paths listed in the reference package are adopted. Missing
files are not created and configurations, keys and archives are not adopted.
Adoption performs the same pause/load/resume check; `--offline` has the same meaning.

## Recovery

```text
python cli.py recover --target PATH
python cli.py rollback --target PATH
```

Use `recover` for an interrupted transaction, `rollback` for the most recent
completed replacement. Use the original live/offline mode. The sibling directory
`.PRODUCT_NAME.installer` holds ownership and one rollback journal; retain it.
Unknown subsequent local edits prevent automatic overwrite or recovery.

## Compatibility and validation

Packages declare installer protocol, archive/configuration/sync schemas and layout
in INSTALL.json. Compatible product versions reuse this engine unchanged. Only
changed files are replaced; only obsolete previously owned program files are removed.
Published 2.20.1 and 2.20.3 packages have explicit format adapters. Other packages
without INSTALL.json require an explicit adapter rather than a guessed layout.

Engine tests run when installer or packaging code changes, independently of normal
product releases. Passing portable tests does not establish live macOS/Linux service
integration, which is not implemented. No AI approval, governance receipts, historical
rescans, or per-source-version compatibility matrices are part of an update.
