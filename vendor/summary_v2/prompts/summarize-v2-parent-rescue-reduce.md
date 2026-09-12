# Traceable Summary-v2 Parent Rescue Reduce Prompt

Create one source-grounded parent candidate for Memory Wuxian `summary-v2`.
Return JSON only and match the supplied parent schema exactly.

The payload is a deterministic bounded projection of validated temporary
parent-map sidecars. `canonical_atoms` contains the selected top-level durable
state exactly once, while `map_payloads` retains chronological routes back to
the original direct children. Additional durable state is retained by the local
hash-bound ledger and direct-child routes rather than copied into this prompt.
`canonical_relations` uses canonical semantic IDs, so visible relation endpoints
remain resolvable without repeating atom text. The
`source_payload.ordered_source_refs` are the original direct child summary IDs,
never temporary map IDs. The task binds the same ordered refs and their catalog
by count and SHA-256 rather than duplicating local proof structures. Use no
information outside the payload.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Cite only declared direct child summary IDs. Never output a temporary map ID.
- Create chronological navigation scenes that cover every direct child ID.
- Summarize navigation and ordinary parent context from `map_payloads`, using
  `canonical_atoms` and `canonical_relations` to preserve semantic context.
  The selected top-level durable atoms and correction relations are injected
  deterministically by the local projector; do not copy, expand, or paraphrase
  them merely for completeness. Routed durable state remains locally verifiable
  and is re-promoted at the next formal parent without model copying.
- Do not create retrieval anchors or omissions. Accumulated anchors remain
  locally ledger-bound by count and SHA-256 and are not repeated in this model
  prompt.
- Preserve uncertainty, open questions, withdrawals, tasks, artifact routes,
  and explicit correction relations. Recency alone never supersedes state.
- Merge duplicate map wording without copying all ordinary child detail upward.
- Do not infer personality, motive, priority, hidden intent, or absent facts.
- Do not output final IDs, paths, timestamps, hashes other than the copied
  source hash, storage instructions, or prose outside the JSON object.
