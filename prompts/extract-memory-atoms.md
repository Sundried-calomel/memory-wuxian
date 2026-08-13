# Memory Atom Extraction Prompt

Create one candidate `memory-atoms-v1` result for the supplied closed Level-1
Memory Wuxian summary job. Return JSON only and match the supplied output schema.

Rules:

- Read only the assigned source records.
- Copy `job_id` and `source_sha256` exactly from the supplied job.
- Use only source message IDs from `allowed_source_message_ids`.
- Every atom must be supported by all IDs listed on that atom.
- Record only explicit work facts, tasks, methods, and artifacts.
- Preserve proposals, open questions, uncertainty, and withdrawals as their
  actual `epistemic_status`; do not strengthen them into facts or decisions.
- Use `accepted_decision` only when the source explicitly records acceptance or
  instruction, not merely because a statement is recent.
- Use `withdrawn` only when the source explicitly withdraws or replaces the
  statement.
- Create a relation only when the assigned source explicitly supports that
  relation. Time order alone never proves `revises` or `supersedes`.
- Relation source IDs must be drawn from the two related atoms' source IDs.
- `local_id` is only a candidate-local reference. Do not invent final atom,
  relation, scene, file, path, timestamp, importance, priority, personality, or
  motivation fields.
- Do not include tool outputs, internal reasoning, or content not present in the
  source records.
- An empty `atoms` or `relations` array is valid when evidence is insufficient.

Allowed atom and status combinations:

- `work_fact`: `explicit_fact`, `uncertain`, `withdrawn`
- `work_task`: `accepted_decision`, `proposal`, `open_question`, `uncertain`,
  `withdrawn`
- `work_method`: `accepted_decision`, `proposal`, `uncertain`, `withdrawn`
- `work_artifact`: `explicit_fact`, `proposal`, `uncertain`, `withdrawn`
