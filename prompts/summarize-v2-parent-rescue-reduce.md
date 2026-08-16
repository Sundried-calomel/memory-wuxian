# Traceable Summary-v2 Parent Rescue Reduce Prompt

Create one source-grounded parent candidate for Memory Wuxian `summary-v2`.
Return JSON only and match the supplied parent schema exactly.

The payload contains validated temporary parent-map sidecars. Together they
form one exact ordered partition of the formal parent's direct child summaries.
The declared `source_refs` are the original direct child summary IDs, never the
temporary map IDs. Use no information outside the payload.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Cite only declared direct child summary IDs. Never output a temporary map ID.
- Create chronological navigation scenes that cover every direct child ID.
- Carry every formal `source_manifest.promotion_manifest` entry into atoms
  verbatim for type, statement, status, and scope, citing its original direct
  child summary ID.
- Do not create retrieval anchors or omissions.
- Preserve uncertainty, open questions, withdrawals, tasks, artifact routes,
  and explicit correction relations. Recency alone never supersedes state.
- Merge duplicate map wording without copying all ordinary child detail upward.
- Do not infer personality, motive, priority, hidden intent, or absent facts.
- Do not output final IDs, paths, timestamps, hashes other than the copied
  source hash, storage instructions, or prose outside the JSON object.
