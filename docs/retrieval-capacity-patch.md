# Retrieval Capacity Patch

Base: v2.19.5 (81a0abf).

The local multi-segment capture, token ledger, daily metrics and external
recovery-backup implementation are already present in this release. Preserve
the newer source-generation recovery, Summary V2 and dashboard implementation.
The old checkout and its uncommitted patch remain preserved separately.

Public keyword queries stream raw records and retain only the requested top
candidates. The hash index is streamed and only candidate hashes are retained.
Semantic queries retain compact source identities for full freshness verification,
stream vector records and read matched raw text again. Archive record count no
longer rejects either query mode. Per-record byte, path, framing and response
bounds remain enforced. Semantic identity metadata still scales with archive
size, and scans still cost time proportional to input size; this is not an
indexed random-access implementation.

Validation: read-only CLI/HTTP/MCP parity, stale semantic rejection, source
verification, old record-count boundaries, and existing guarded feature tests.
No live archive mutation is required by this patch.
