# Defect Prevention Packet

- Task: `summary-v2-rescue-v4-convergence`
- Packet: `packet:0e1846481222dd41b41415b1`
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
- Rejected candidates are evidence only and never bypass a model call in a new revision.
- Only successful hash-bound partial maps may resume within the same revision.
- Each content node receives at most one formal attempt per revision.
- Infra failures stop the node without consuming its content attempt or looping automatically.
- Model concurrency is at most three and each explicit batch contains at most twenty nodes.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `1283fe6d3f2bdaf3cc73bc7c538fbdf6cd0f0d35e8d5f5178b96b513e7427080`
