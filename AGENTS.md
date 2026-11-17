# Compact-core development

Implement only the six functions described in README.md. Preserve append-only originals, source references, explicit local bindings, encryption, locks, atomic commits and necessary recovery.

Use targeted checks for changed behavior and one release CI run. Do not restore retired governance, promotion, duplicate schedulers or old Python sync fallbacks. Keep tests on synthetic data; never backfill historical summaries merely for testing.

For a small patch, run only tests selected for changed behavior by scripts/release_checks.py. Do not run local full discovery and then repeat it as a release gate. Changes to shared storage/runtime or an explicitly requested full audit justify broader coverage. Automatic CI runs once on the release tag; installer.yml is manual historical reproduction only. If a new module has no adequate mapped test, add its focused coverage and update the selector rather than silently skipping it.

Do not generate or publish standalone checksum files. Retain installer verification of the GitHub asset digest, archive member hashes, safe paths, size bounds, compatibility and atomic recovery. Do not remove those checks merely to reduce workflow steps.

Packages use an explicit source-file list. Do not include local configuration, keys, archives, device IDs, diagnostics or unfinished installers. Distinguish portable Python checks, installed behavior and remote ACKs. Never claim Mac activation or a fresh-install workflow without actual evidence.
