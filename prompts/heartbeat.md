# Heartbeat Prompt

Run a short Memory無限 maintenance check.

1. Read state and pending jobs.
2. Check whether completed unassigned rounds reach the Level-1 threshold.
3. Check whether ungrouped child summaries reach the parent threshold.
4. Validate that indexed raw and summary files exist.
5. Verify available raw-record, summary-source, and summary-file SHA-256 values.
6. Detect duplicate or overlapping source assignments.
7. Report failed jobs and state/index inconsistencies.
8. Create at most the due deterministic jobs; do not generate summary content autonomously.
9. Apply deterministic state or index reconstruction only when repair mode was explicitly requested.

Heartbeat is a recovery and validation process. Count-based events remain the primary triggers. Never repair a hash mismatch by rewriting source history.
