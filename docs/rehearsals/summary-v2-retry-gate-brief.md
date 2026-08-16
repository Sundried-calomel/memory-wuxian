# Defect Prevention Packet

- Task: `summary-v2-rescue-single-attempt`
- Packet: `packet:987c756b15b5d5caa3798cd0`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G02 文件或进程存在不等于功能生效

- Class: `blocking`; score: 75; severity: `high`
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
- A failed node may be retried only after an explicit runner revision change or a separately approved recovery action.
- The currently running Rescue process must finish before runner code changes are applied.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `fe70cc07885cc6b19376b52e2cbd7ab3878a520181055c1464190c6842fc242c`
