# Project Attachment Contract

`project-attachment-v1` transfers explicitly selected large project
deliverables without changing the bounded `project-evidence-v1` package.

## Boundaries

- Selection is closed and explicit. There is no workspace discovery.
- Allowed types are PDF, PPTX, DOCX, XLS, XLSX, TIF, TIFF, PNG, JPEG, and WebP.
- One file is at most 256 MiB, one generation is at most 1 GiB, and one
  generation contains at most 256 files.
- Source files remain ordinary readable files. They are never moved, renamed,
  rewritten, deleted, normalized, or replaced by the object store.
- Scripts, executables, archives, credentials, keys, tokens, caches, and
  unsupported types fail closed.

## Integrity And Transfer

Files are divided into exact 4 MiB chunks. Every chunk and complete logical
file has a SHA-256 digest. Manifests are immutable and bind project identity,
conversation identity, relative path, role, byte length, full-file digest, and
ordered chunk metadata. Source-root paths are never persisted.

The independent signed and target-encrypted stream uses bounded bundles,
sequence cursors, predecessor hashes, acknowledgements, retries, and
content-addressed deduplication. An older peer can ignore this stream without
blocking archive, Environment, or Project Evidence synchronization.

## Reconstruction

Reconstruction is preview-first. The receiver verifies every chunk, ordered
coverage, complete length, and full-file SHA-256 before writing any destination
file. Missing or corrupt data produces no partial file. Existing conflicting
files are never overwritten. A successful applied reconstruction writes an
append-only verification receipt containing only generation and exact file
hash evidence, not the destination path. Reconstructing the same generation
again reuses the byte-identical receipt; a conflicting or malformed existing
receipt fails before any destination file is written.

Each registered device-local owner stores an explicit
`current_generation_id`. Refresh and status never guess the current generation
from manifest filename or hash ordering. Legacy or missing pointers are rebound
only by a successful explicit refresh.

Local creation, encrypted publication, peer acknowledgement, and verified
reconstruction are distinct states. None implies a later state.

## Commands

```text
project-attachment-build --spec SPEC [--apply]
project-attachment-owner-register --spec SPEC [--apply]
project-attachment-owner-refresh --project-id ID [--apply]
project-attachment-owner-status
project-attachment-status
project-attachment-sync [--force]
project-attachment-reconstruct --generation-id ID --destination PATH [--apply]
```
