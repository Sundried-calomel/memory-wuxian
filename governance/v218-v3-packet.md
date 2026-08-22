# Defect Prevention Packet

- Task: `memory-wuxian-v218-capture-structure-v3`
- Packet: `packet:49cc77555fe33ba1dbc1d05e`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G04 默认编码与字符串命令破坏特殊字符

- Class: `blocking`; score: 95; severity: `high`
- Guidance: Use UTF-8 and structured argument or parser boundaries; test multilingual, currency, emoji, spaces, leading hyphens, and long paths.
- Forbidden: Concatenating untrusted or path-bearing values into a shell command string.; Using fuzzy path matches for irreversible or authoritative selection.
- Required: Use argument arrays and structured parsers.; Test Chinese, Japanese, currency symbols, emoji, spaces, leading hyphens, and long paths.
- Checks: unicode-path-fixtures (L2): Round-trip multilingual and special-character paths and values through the production boundary.

## Authorization Boundaries

- Preserve the exact public batch JSON fields, nullability, counters, partial-error shape, and error propagation behavior.
- Preserve WAL, archive-lock, cursor, backup-debt, coverage, summary-job, deterministic-index, and telemetry operation ordering.
- Keep watcher acquisition, event draining, debounce, and watcher refresh platform-specific while sharing only the common rollout-cycle state transition.
- Do not modify live archives, installed components, running processes, summary rules, deterministic-index semantics, cloud or federation protocols, packaging, version numbers, or release state.
- Do not claim macOS runtime execution from Windows; use compile/source-contract evidence and require a later native macOS run before release.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `e085d129f28291f1ecb10a8c47d33d37c89271d92eebc1ba7663ce26b692dd64`
