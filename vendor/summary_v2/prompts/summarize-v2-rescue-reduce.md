# Traceable Summary-v2 Level-1 Rescue Reduce Prompt

Create one source-grounded Level-1 candidate for Memory Wuxian `summary-v2`.
Return JSON only and match the supplied schema exactly.

The payload contains validated temporary map sidecars that form one exact,
ordered partition of the formal Level-1 raw-message range. Every declared
`source_ref` is still an original raw message ID. The maps are compression
evidence, not permanent hierarchy children. Use no information outside them.

## Required Behavior

- Copy `job_id`, `summary_level`, and `source_sha256` exactly.
- Preserve the formal Level-1 identity and cite only
  `source_payload.ordered_source_refs`; never cite a temporary map summary ID.
  The task binds the same ordered refs and their catalog by count and SHA-256
  instead of duplicating those local proof structures in the model payload.
- Account for every source ref. Every represented ref must appear in a scene.
  Preserve durable semantic content and required exact locators through the
  canonical atoms, injected anchors, or hash-bound internal routes; ordinary
  detail may remain reachable through its scene route without becoming a
  synthetic atom. Put intentionally excluded refs in `omissions`; never
  silently drop one.
- The payload's `canonical_atoms` and `canonical_relations` are the bounded
  top-level projection of a hash-bound ledger compiled from every accepted map
  item. The deterministic projector injects that projection after the model
  call; additional map state remains available through immutable internal
  routes. Use the projection and map narratives to consolidate chronology. The
  Level-1 response schema requires at least one atom, so copy exactly one
  canonical atom as a schema placeholder and return no relations. The projector
  discards all model-owned atoms and relations. Do not invent, merge, rewrite,
  or selectively copy semantic facts in those arrays.
- Return no more than `task.maximum_model_scenes` scenes. The projector reserves
  the remaining scene capacity for grouped source routes, so a raw message ref
  never requires its own synthetic scene.
- Preserve every map-authoritative omission as an omission. Never cite an
  omitted ref in overview, scenes, atoms, relations, or retrieval anchors; the
  projector rejects any attempt to resurrect omitted content.
- Preserve all accepted decisions, explicit facts, thresholds, identifiers,
  corrections, open questions, uncertainty, withdrawals, artifact routes,
  tasks, and commitments found in the maps. Do not flatten their epistemic
  status.
- Merge duplicate wording across maps, but do not merge distinct facts merely
  to shorten output. Follow chronology in scenes.
- Return an empty `retrieval_anchors` array. Exact required locators and the
  accumulated anchor collection are not repeated in this rescue prompt; their
  count and SHA-256 remain bound while the deterministic projector injects
  every formal locator after the model call. Use the supplied map narratives
  and deterministic scene routes for navigation.
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
