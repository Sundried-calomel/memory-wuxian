# Traceable Summary-v2 Parent Prompt

Create one source-grounded parent candidate for Memory Wuxian `summary-v2`.
Return JSON only and match the supplied schema exactly. This contract applies
to L2 and every higher level; it is intentionally different from L1.

The payload contains validated direct child sidecars. Each `source_ref` is one
direct child summary ID. Use no information outside the payload.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Match the predominant natural language of the child summaries.
- Create a short overview of the interval represented by the direct children.
- Make `scenes` chronological phase and navigation routes. Every direct child
  summary ID must occur in at least one scene, so a reader can descend to it.
- Carry every entry in `source_manifest.promotion_manifest` into `atoms`
  verbatim for `atom_type`, `statement`, `epistemic_status`, and `scope`, citing
  its `child_summary_id`. These entries contain active decisions and rules,
  tasks or commitments, open questions, uncertainty, withdrawals, and explicit
  corrections or conflicts that must survive compression.
- Do not copy ordinary child details into atoms merely to prove coverage. Their
  durable home is the child summary, reached through its scene route.
- Do not create retrieval anchors. Exact commands, paths, identifiers, and
  other locators remain in lower-level children and are reached by navigation.
- Do not omit a direct child and leave `omissions` empty.
- Keep relations only when the child summaries explicitly establish them.
- Preserve uncertainty and correction status. Recency alone never accepts,
  rejects, or supersedes an earlier statement.
- Do not infer personality, motive, priority, hidden intent, or absent facts.
- Do not output final IDs, paths, timestamps, hashes other than the copied
  `source_sha256`, storage instructions, or prose outside the JSON object.

The parent is a lossless navigation layer, not a lossless repetition of all
child prose: every child remains reachable, while only mandatory durable state
is repeated upward.
