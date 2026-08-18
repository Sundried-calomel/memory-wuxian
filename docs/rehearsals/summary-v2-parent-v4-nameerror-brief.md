# Defect Prevention Packet

- Task: `summary-v2-parent-candidate-reasons-scope`
- Packet: `packet:65712fcf4ef91cd921b9e2bf`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G02 文件或进程存在不等于功能生效

- Class: `blocking`; score: 78; severity: `high`
- Guidance: Do not equate existence or exit zero with success; exercise the real entrypoint and capture before/after evidence of the promised effect.
- Forbidden: Reporting success solely from existence, registration, process state, or exit code.
- Required: Run the real user entrypoint.; Record before and after state for the promised count, waterline, file, or UI behavior.
- Checks: real-entrypoint-effect (L3): Exercise the actual entrypoint and prove the expected state change.

## G08 优化或压缩破坏证据和作用域

- Class: `blocking`; score: 125; severity: `critical`
- Guidance: Optimization must be explicit, type-allowlisted, local, and evidence-preserving; never globally proxy tools or discard code, diffs, conversations, or raw archives.
- Forbidden: Globally proxying commands, MCP, code, diffs, searches, conversations, or archives.; Using an undisclosed external model or network path.
- Required: Retain raw files and fail back to raw evidence.; Activate only for explicitly allowed repetitive structured data.
- Checks: evidence-roundtrip (L2): Prove every compact item can locate retained raw evidence and unsupported types remain unmodified.

## G11 被动文档没有执行力

- Class: `blocking`; score: 125; severity: `critical`
- Guidance: Persistent rules need target-based machine discovery, preflight hashes, completion receipts, and fail-closed drift handling; passive Markdown is not enforcement.
- Forbidden: Treating documentation existence as proof that future tasks will read or follow it.
- Required: Register applicable documents and rules by target scope.; Freeze input hashes at preflight and verify applicable families and required updates at completion.
- Checks: registered-preflight (L1): Prove target-based discovery and hash-bound preflight work without chat memory.; fail-closed-completion (L2): Reject missing receipts, registry drift, unknown families, and unchanged required project workbooks.

## Authorization Boundaries

- Raw archive and Summary V1 remain read-only.
- Existing successful Summary V2 artifacts remain read-only.
- No parent node attempt is consumed by candidate-selection failures before state creation.
- Parent nodes are re-admitted only after the parent runner revision changes.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `55a41eee71651b925f0df43855d32c586632cc09a6a2db6793765499c946b240`
