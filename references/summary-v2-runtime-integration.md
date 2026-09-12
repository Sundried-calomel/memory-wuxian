# Local Summary V2 integration

Version 2.19.5 integrates the isolated Summary V2 engine into the existing
Control and Memory Plane owners on the released 2.19.4 baseline. All upstream
capture, source reconciliation, completed FileChange and replay-safety fixes
remain present. This integration does not modify Capture Core source.

## Configuration and authorization

V2 remains opt-in. An absent `summary_v2` section retains the existing V1 path.
The following example requires explicit archive and runtime paths:

```yaml
ai_summary:
  enabled: false
  model: gpt-5.6-terra
summary_v2:
  enabled: true
  tick_seconds: 960
  runtime_root: /explicit/archive-sibling-summary-v2-runtime
  bundle_roots:
    legacy: /explicit/reviewed-standalone-results
```

Keep model execution disabled until the requested history-processing route is
authorized and the local adoption/configuration checks pass. Installing or
publishing the software is not authorization to send conversation history.
The V2 worker defaults an absent or empty model to `gpt-5.6-terra` and always
passes `model_reasoning_effort="medium"` explicitly. A non-empty
`ai_summary.model` overrides the model. It uses an ephemeral Codex process with
`--ignore-user-config`, so the current interactive task's model and Thinking
settings do not select the summary worker's settings.

The runtime root must be outside the raw archive. Relative object paths and
bundle bindings reject escapes and symlink/junction traversal. Setting
`summary_v2.enabled: false` retains V2 coverage, reads and reserved aliases;
already typed V2 jobs pause instead of being routed into V1.

## Execution and immutable evidence

Existing scheduling, leases, immutable source snapshots, serialized ingestion,
parent eligibility, derived finalization and backup debt remain the owners.
Each logical V2 job enters the manifest-bound isolated engine. One tick admits
at most one wave, with at most three concurrent calls according to
`ai_summary.maximum_parallel_model_calls`. The timeout is not shortened to fit
a depleted tick. The tick must leave the timeout plus 45 seconds and cannot
exceed 1,200 seconds. Admitted work drains before yielding.

Raw/V1/existing V2 bytes remain authoritative and unchanged. Completion links
bind source ranges, formal aliases, direct children and exact external bundle
hashes. Commit-tail and derived-finalization markers allow a completed model
result to finish ingestion without repeating the model call. Internal map and
reduce objects are not counted as formal summaries.

The closed entry admits an existing rescue route only from exact retained
no-candidate infrastructure-timeout evidence. Source/configuration/runtime
drift, ambiguous claims, missing checkpoints and content failures stay blocked.
Changing the model or its configuration does not rebind an in-progress node.
Preserve that node and its original evidence for explicit recovery; do not
delete claims, overwrite the execution contract or silently retry a new model.

## Adoption and identity repair

Use the existing CLI owners with explicit paths:

```text
python scripts/summary_v2_adoption.py --root ARCHIVE --config CONFIG --binding legacy --plan NEW_PLAN
python scripts/summary_v2_adoption.py --root ARCHIVE --config CONFIG --binding legacy --plan NEW_PLAN --apply
python scripts/summary_v2_identity_repair.py --root ARCHIVE --config CONFIG --job JOB --plan NEW_PLAN
python scripts/summary_v2_identity_repair.py --root ARCHIVE --config CONFIG --job JOB --plan NEW_PLAN --apply-sha256 PLAN_SHA256
```

Adoption validates formal roots, reachable internal bundles and original raw
hashes without running historical backfill. Apply rechecks the preview, and
replay is idempotent. Identity repair records exact physical occurrences in a
side ledger and retains the prior job and quarantine evidence. It never
renumbers raw records or converts an unknown collision into a guessed identity.

## Reads, backup and restore

Typed local retrieval augments raw and deterministic-index search, preserving
source verification and explicit correction/conflict/policy statuses. Context
capsules prefer the highest covering summaries, uncovered new summaries and
recent task state within the existing context budget. Truncation is labeled.

Backups include completion links, exact JSON/Markdown dependency closure and
validated pending checkpoints. Restore requires the matching restored archive:

```text
python scripts/summary_v2_restore.py --snapshot SNAPSHOT --archive RESTORED_ARCHIVE --bundle-root NEW_ROOT --plan NEW_PLAN
python scripts/summary_v2_restore.py --snapshot SNAPSHOT --archive RESTORED_ARCHIVE --bundle-root NEW_ROOT --plan NEW_PLAN --apply
```

Conflicting existing files are not overwritten. Pending jobs restored to changed
paths remain paused for checkpoint review. V2 exchange and remote V2 retrieval
are outside this release; the existing archive-v1 transport is unchanged.

## Installation and rollback

Both platform packages include the same manifest-bound engine. The existing
platform installers and generation transactions remain the installation owners.
Local pause values are preserved; this release operation does not resume tasks
or install into a live archive. The published 2.19.4 capture source is rebuilt
by the release workflow, rather than replaced with an old local collector.

Disabling V2 generation while retaining its readers is the compatible semantic
rollback. Installing an old V1-only reader over adopted V2 state is unsupported.
Keep transaction backups and exact interrupted execution records. Prior local
installation evidence does not substitute for this release's same-SHA CI and
installer artifacts.
