# Defect Prevention Packet

- Task: `summary-v2-parent-v6-convergence`
- Packet: `packet:13cf4e3e4587091e4e66d0cc`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G08 优化或压缩破坏证据和作用域

- Class: `blocking`; score: 115; severity: `critical`
- Guidance: Optimization must be explicit, type-allowlisted, local, and evidence-preserving; never globally proxy tools or discard code, diffs, conversations, or raw archives.
- Forbidden: Globally proxying commands, MCP, code, diffs, searches, conversations, or archives.; Using an undisclosed external model or network path.
- Required: Retain raw files and fail back to raw evidence.; Activate only for explicitly allowed repetitive structured data.
- Checks: evidence-roundtrip (L2): Prove every compact item can locate retained raw evidence and unsupported types remain unmodified.

## G11 被动文档没有执行力

- Class: `blocking`; score: 115; severity: `critical`
- Guidance: Persistent rules need target-based machine discovery, preflight hashes, completion receipts, and fail-closed drift handling; passive Markdown is not enforcement.
- Forbidden: Treating documentation existence as proof that future tasks will read or follow it.
- Required: Register applicable documents and rules by target scope.; Freeze input hashes at preflight and verify applicable families and required updates at completion.
- Checks: registered-preflight (L1): Prove target-based discovery and hash-bound preflight work without chat memory.; fail-closed-completion (L2): Reject missing receipts, registry drift, unknown families, and unchanged required project workbooks.

## Authorization Boundaries

- Raw archive, Summary V1, and successful Summary V2 remain byte-immutable.
- Every model prompt remains below the configured UTF-8 byte limit.
- Every promoted durable atom remains traceable to its original child summary and raw messages.
- Rejected candidates are evidence only and never become a new revision input.
- Only successful hash-bound maps and intermediate reductions may resume within the same revision.
- Each content node receives at most one formal attempt per revision.
- Model concurrency remains at most three and parent nodes remain serial within one batch.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `6db6e90d2b2b7116e1134fee4a9ee5bd3a9e4d2b678bfad2b612ea22dafcacf8`
