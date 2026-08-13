# Defect Prevention Packet

- Task: `summary-v2-historical-backfill-20260813`
- Packet: `packet:7faa1f2e8c427d3070a1bff3`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G08 优化或压缩破坏证据和作用域

- Class: `blocking`; score: 78; severity: `critical`
- Guidance: Optimization must be explicit, type-allowlisted, local, and evidence-preserving; never globally proxy tools or discard code, diffs, conversations, or raw archives.
- Forbidden: Globally proxying commands, MCP, code, diffs, searches, conversations, or archives.; Using an undisclosed external model or network path.
- Required: Retain raw files and fail back to raw evidence.; Activate only for explicitly allowed repetitive structured data.
- Checks: evidence-roundtrip (L2): Prove every compact item can locate retained raw evidence and unsupported types remain unmodified.

## Authorization Boundaries

- Never write, replace, rename, or delete raw history, summary-v1, archive indexes, or peer replicas.
- Plan only from source-message lists whose reconstructed source SHA-256 exactly matches summary-v1 metadata.
- Write jobs, state, receipts, and summary-v2 sidecars only under an explicit external output root.
- Invoke at most three one-shot model calls concurrently, cap each explicit run at twenty summaries, and resume idempotently from persisted sidecars.
- Generate parents only after every declared direct child sidecar validates.
- Quarantine invalid or drifted inputs without blocking unrelated work.
- Do not register a background task or production queue integration.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `7c986b1f514671a67a4a0d099d489b50452a56bb27011c6e68b44e4b381b772a`
