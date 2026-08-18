# Defect Prevention Packet

- Task: `memory-wuxian-v218-lean-dedup-v2`
- Packet: `packet:80a967dbf77e5adfce65444e`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G04 默认编码与字符串命令破坏特殊字符

- Class: `blocking`; score: 98; severity: `high`
- Guidance: Use UTF-8 and structured argument or parser boundaries; test multilingual, currency, emoji, spaces, leading hyphens, and long paths.
- Forbidden: Concatenating untrusted or path-bearing values into a shell command string.; Using fuzzy path matches for irreversible or authoritative selection.
- Required: Use argument arrays and structured parsers.; Test Chinese, Japanese, currency symbols, emoji, spaces, leading hyphens, and long paths.
- Checks: unicode-path-fixtures (L2): Round-trip multilingual and special-character paths and values through the production boundary.

## Authorization Boundaries

- Do not change persisted bytes, hashes, error messages, permissions, fsync behavior, lock behavior, command-line behavior, or public interfaces.
- Do not modify summary generation, deterministic indexes, historical backfill, cloud or federation protocols, live archives, installed components, or running processes.
- Reject a consolidation when two implementations differ in newline, allow_nan, duplicate-key, permission, directory-fsync, or rollback semantics.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `283aa1fdb30649353e68860d6ccdd5c866a8ca34433507755025427514f3010a`
