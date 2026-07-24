# Summary Generation Prompt

Create one summary result for the supplied Memory無限 job.

Read only the job's assigned source payload. Return valid JSON matching this exact structure:

```json
{
  "topics": [],
  "established_conclusions": [],
  "open_questions": [],
  "concepts": [],
  "policy_events": []
}
```

Rules:

- Record only information explicitly present in the assigned source.
- Preserve uncertainty, disagreement, and unresolved status.
- Include only conclusions actually reached or accepted.
- Use explicit terms and user-defined labels as concepts.
- Do not infer preferences, motives, importance, or unstated conclusions.
- Do not strengthen proposals into decisions.
- For a Level-1 job, record explicit operational rules, decisions, corrections,
  withdrawals, reaffirmations, proposals, or uncertainty in `policy_events`.
- For a Level-2 or higher job, return an empty `policy_events` array.
- Each policy event must contain exactly `topic`, `statement`, `scope`,
  `event_type`, `prior_statement`, and `source_message_ids`.
- Use only these event types: `adopted`, `revised`, `withdrawn`, `reaffirmed`,
  `proposed`, or `uncertain`.
- Use `revised`, `withdrawn`, or `reaffirmed` only when the source explicitly
  identifies the prior statement. Copy that prior statement into
  `prior_statement`; otherwise use `uncertain`.
- Every `source_message_ids` value must come from the assigned Level-1 source.
- Do not infer that the newest statement supersedes an older one merely because
  it is newer.
- Do not include prose outside the JSON object.
