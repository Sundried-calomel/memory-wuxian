# Summary Generation Prompt

Create one summary result for the supplied Memory無限 job.

Read only the job's assigned source payload. Return valid JSON matching this exact structure:

```json
{
  "topics": [],
  "established_conclusions": [],
  "open_questions": [],
  "concepts": []
}
```

Rules:

- Record only information explicitly present in the assigned source.
- Preserve uncertainty, disagreement, and unresolved status.
- Include only conclusions actually reached or accepted.
- Use explicit terms and user-defined labels as concepts.
- Do not infer preferences, motives, importance, or unstated conclusions.
- Do not strengthen proposals into decisions.
- Do not include prose outside the JSON object.
