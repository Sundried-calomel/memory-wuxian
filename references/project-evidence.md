# Project Evidence Packages

Project Evidence Packages make selected small project records available to
trusted Memory Wuxian devices without copying a whole workspace.

## Contract

- A package is built only from an explicit JSON specification validated by
  `schemas/project-evidence-spec.schema.json`.
- Each selected file has a relative path, role, byte length, SHA-256 digest,
  and exact byte payload. The source root is never persisted.
- Packages are immutable and content addressed. A later generation may name a
  local predecessor, but never overwrites it.
- Local packages are authoritative for their originating device. Imported
  packages remain read-only peer replicas and cannot install, activate, or run
  anything.
- `project-evidence-v1` is independent from `archive-v1` and `environment-v1`.
  Older clients can ignore the unfamiliar stream directory without rejecting
  their supported streams.
- Cloud-folder delivery uses the existing signed and target-encrypted envelope,
  sequence, acknowledgement, retry, and deduplication contract.

## Allowed Evidence

Use explicit selections for project rules, current status, next plans,
decisions, QA records, daily/weekly/phase reports, templates, compact figures,
compact tables, and artifact indexes. The default limits are 256 files,
4 MiB per file, and 16 MiB per package.

Do not select raw datasets, caches, build trees, bulk logs, credentials,
private keys, tokens, or an entire workspace. Text files containing probable
secrets are rejected.

## Commands

```bash
python3 scripts/memory_cli.py project-evidence-build --spec evidence.json
python3 scripts/memory_cli.py project-evidence-build --spec evidence.json --apply
python3 scripts/memory_cli.py project-evidence-list --project-id project-alpha
python3 scripts/memory_cli.py project-evidence-query --project-id project-alpha --role weekly-report --query "current status"
python3 scripts/memory_cli.py project-evidence-reconstruct --generation-id project-evidence:SHA256 --destination ./restored
python3 scripts/memory_cli.py project-evidence-reconstruct --generation-id project-evidence:SHA256 --destination ./restored --apply
python3 scripts/memory_cli.py project-evidence-status
```

Query results expose at most 50 matches and at most 2,000 text characters per
match. Use reconstruct for exact complete bytes. Reconstruction checks all
conflicts before writing and never overwrites different existing content.

## Retrieval Order

1. Resolve the project and requested report or evidence role.
2. Prefer the newest applicable generation while preserving its predecessor
   reference.
3. Use bounded query output only to locate the needed file.
4. Reconstruct or inspect exact bytes for factual work.
5. Treat reports as project evidence, not as proof of current external state.

## Device-Local Owners

A Project Evidence Owner is an explicit device-local binding between one
project source root and one closed file selection. Registering an owner never
exports its source root and never discovers additional files.

```bash
python3 scripts/memory_cli.py project-evidence-owner-register --spec evidence.json
python3 scripts/memory_cli.py project-evidence-owner-register --spec evidence.json --apply
python3 scripts/memory_cli.py project-evidence-owner-refresh --project-id project-alpha
python3 scripts/memory_cli.py project-evidence-owner-refresh --project-id project-alpha --apply
python3 scripts/memory_cli.py project-evidence-owner-status
```

The five-minute maintenance supervisor refreshes at most 20 registered owners
per pass. An unchanged selection creates no persistent record. A changed stable
selection creates one immutable generation linked to the current local head.
Failures are isolated by owner. Imported packages never create a local owner.
