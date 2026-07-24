# Historical Retrieval Prompt

Use the Memory無限 retrieval result as historical evidence.

1. Check the reported confidence and source metadata.
2. Prefer `verified` raw records for factual claims.
3. Separate historical source material from the current conversation.
4. Preserve uncertainty and disagreement in the retrieved segment.
5. State when only summary or index support is available.
6. Do not extend the retrieved material with unstated assumptions.

For operational rules, strategies, defaults, or decisions that may have changed,
use `retrieve --mode current-policy`.

In current-policy mode:

1. Prefer an `active` policy event over an explicitly superseded or withdrawn event.
2. Keep the full lineage visible when explaining how a rule changed.
3. Do not treat `conflict`, `unresolved`, `uncertain`, or `proposed` as current policy.
4. If no explicit lineage matched, inspect the newest verified raw matches and
   state that current validity still requires review.
