# Defect Prevention Packet

- Task: `summary-v2-schema-hotfix-20260818`
- Packet: `packet:35ca3fce0d67f33a7cb7978d`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G11 被动文档没有执行力

- Class: `blocking`; score: 75; severity: `critical`
- Guidance: Persistent rules need target-based machine discovery, preflight hashes, completion receipts, and fail-closed drift handling; passive Markdown is not enforcement.
- Forbidden: Treating documentation existence as proof that future tasks will read or follow it.
- Required: Register applicable documents and rules by target scope.; Freeze input hashes at preflight and verify applicable families and required updates at completion.
- Checks: registered-preflight (L1): Prove target-based discovery and hash-bound preflight work without chat memory.; fail-closed-completion (L2): Reject missing receipts, registry drift, unknown families, and unchanged required project workbooks.

## Authorization Boundaries

- The canaries use only two explicit synthetic records and write derived sidecars only below the repository temporary directory.
- Existing infra-blocked states remain evidence and are not silently rewritten.
- Raw, Summary V1, and successful Summary V2 remain byte-immutable.
- Heartbeat stays paused until both real API paths accept the corrected schemas and the new revisions pass all gates.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `a781760a35a4e6bd5ff134ec05580756e95ae00a56da950b68a09481a6aaf39e`
