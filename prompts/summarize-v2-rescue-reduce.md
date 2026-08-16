# Traceable Summary-v2 Level-1 Rescue Reduce Prompt

Create one source-grounded Level-1 candidate for Memory Wuxian `summary-v2`.
Return JSON only and match the supplied schema exactly.

The payload contains validated temporary map sidecars that form one exact,
ordered partition of the formal Level-1 raw-message range. Every declared
`source_ref` is still an original raw message ID. The maps are compression
evidence, not permanent hierarchy children. Use no information outside them.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Preserve the formal Level-1 identity and cite only the declared raw-message
  `source_refs`; never cite a temporary map summary ID.
- Account for every source ref. Every represented ref must appear in a scene
  and in an atom or required retrieval anchor. Put genuinely content-free refs
  in `omissions`; never silently drop one.
- Preserve all accepted decisions, explicit facts, thresholds, identifiers,
  corrections, open questions, uncertainty, withdrawals, artifact routes,
  tasks, and commitments found in the maps. Do not flatten their epistemic
  status.
- Merge duplicate wording across maps, but do not merge distinct facts merely
  to shorten output. Follow chronology in scenes.
- Return an empty `retrieval_anchors` array. Exact required locators are not
  repeated in this rescue prompt; the deterministic projector injects every
  locator from the formal source after the model call. The task reports their
  count and source refs so scenes can still route those messages.
- Within every `source_refs` array, remove duplicates and preserve the formal
  source order.
- Relations are optional and must be explicit in the map evidence. Do not use
  a relation to provide coverage.
- Do not infer personality, motive, importance, priority, hidden intent, or
  facts absent from the maps.
- Do not output temporary map IDs, final IDs, paths, timestamps, storage
  instructions, or prose outside the JSON object.

Allowed atom/status combinations:

- `work_fact`: `explicit_fact`, `uncertain`, `withdrawn`
- `work_task`: `accepted_decision`, `proposal`, `open_question`, `uncertain`, `withdrawn`
- `work_method`: `accepted_decision`, `proposal`, `uncertain`, `withdrawn`
- `work_artifact`: `explicit_fact`, `proposal`, `uncertain`, `withdrawn`
