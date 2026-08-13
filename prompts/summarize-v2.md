# Traceable Summary-v2 Level-1 Prompt

Create one source-grounded candidate for Memory Wuxian `summary-v2`. Return
JSON only and match the supplied schema exactly.

The payload declares `source_refs`, a source manifest, required locator strings,
and complete Level-1 raw records.
Use no information outside that payload.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Treat every `source_ref` as an evidence unit that must be accounted for.
- Match the predominant natural language of the source dialogue for overview,
  scenes, atoms, scopes, and omission reasons. Exact retrieval anchors keep
  their original bytes and are never translated.
- Every represented source ref must appear in at least one scene and in at
  least one atom or retrieval anchor.
- Within every `source_refs` array, cite only declared refs, remove duplicates,
  and preserve the exact order in which those refs appear in the supplied
  `source_refs` list.
- A source ref that carries no durable meaning may appear in `omissions`, but
  the reason must say what was omitted. Never silently drop a source ref.
- Preserve exact names, identifiers, file paths, commands, tool names, artifact
  names, dates, and user-defined labels as retrieval anchors when useful.
- For this staged worker, output exactly the declared `required_locators` as
  retrieval anchors, one anchor per declared locator, and create no optional
  anchors. Copy each anchor as one exact contiguous source substring. Never
  paraphrase, combine, translate, normalize, or add context inside an anchor.
  Put explanations in scenes or atoms instead.
- Copy every declared `required_locator` exactly into a retrieval anchor and
  cite its declared source ref. Do not shorten, normalize, translate, or repair
  those locator strings.
- Keep proposals, open questions, uncertainty, withdrawals, accepted decisions,
  and explicit facts distinct. Recency does not imply acceptance or
  supersession.
- Create `revises`, `contradicts`, or `supersedes` only when the source states
  that relationship explicitly.
- Relations are optional. A relation's `source_refs` must be a non-empty subset
  of the union of the two referenced atoms' `source_refs`; otherwise omit the
  relation. Do not use a relation to provide message coverage.
- L1 source refs are raw message IDs. Preserve their evidence routes so the
  final summary can navigate back to the exact source messages.
- Overview text should explain what happened and why it matters within the
  source, but it must not replace the detailed scenes, atoms, and anchors.
- Message-level coverage is not fact-level coverage. Preserve every explicit
  quantitative threshold, named entity or gene list, correction, accepted
  decision, limitation, uncertainty, withdrawal, and open question that could
  change future interpretation or work. When one source ref contains several
  durable claims, create several atoms instead of collapsing them into one.
- Scenes should follow the source chronology and retain enough context that a
  reader can distinguish separate tasks, corrections, decisions, and outcomes.
- Do not infer personality, motivation, importance, priority, hidden intent, or
  facts absent from the source.
- Do not output final IDs, paths, timestamps, hashes other than the copied
  `source_sha256`, storage instructions, or prose outside the JSON object.

Allowed atom/status combinations:

- `work_fact`: `explicit_fact`, `uncertain`, `withdrawn`
- `work_task`: `accepted_decision`, `proposal`, `open_question`, `uncertain`, `withdrawn`
- `work_method`: `accepted_decision`, `proposal`, `uncertain`, `withdrawn`
- `work_artifact`: `explicit_fact`, `proposal`, `uncertain`, `withdrawn`
