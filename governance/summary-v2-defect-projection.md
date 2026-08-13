# Defect Prevention Packet

- Task: `summary-v2-traceable-sidecar-20260810`
- Packet: `packet:52405f017b4f98c078956872`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G08 优化或压缩破坏证据和作用域

- Class: `blocking`; score: 115; severity: `critical`
- Guidance: Optimization must be explicit, type-allowlisted, local, and evidence-preserving; never globally proxy tools or discard code, diffs, conversations, or raw archives.
- Forbidden: Globally proxying commands, MCP, code, diffs, searches, conversations, or archives.; Using an undisclosed external model or network path.
- Required: Retain raw files and fail back to raw evidence.; Activate only for explicitly allowed repetitive structured data.
- Checks: evidence-roundtrip (L2): Prove every compact item can locate retained raw evidence and unsupported types remain unmodified.

## Authorization Boundaries

- Do not modify summary-v1 prompts, schemas, files, indexes, jobs, state, or hashes.
- Do not write into the live archive, summary queue, maintenance queue, retrieval indexes, federation, cloud exchange, or dashboard.
- Model output is an untrusted candidate and cannot choose final IDs, paths, timestamps, importance, priority, or storage actions.
- Every source reference must be represented in a scene and an atom or retrieval anchor, or explicitly omitted with a reason; silent loss is forbidden.
- Every projected item must retain deterministic backreferences to raw message IDs.
- Higher-level summaries consume only validated summary-v2 sidecars and must preserve child evidence references.
- Sidecars are append-only and may be created only outside the declared archive root.
- The feature remains opt-in and is not eligible for retrieval or context-capsule activation without a separate human-reviewed promotion change.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `452ed74561e5b71cde1c50b647aa0b3ad7990200c61073694670eb0b81fdf86c`
