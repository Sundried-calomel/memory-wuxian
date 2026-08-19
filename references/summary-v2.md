# Traceable Summary-v2 Contract

Status: experimental, opt-in, external-sidecar only. It is not connected to
the production summary queue, `summary-v1`, retrieval, context capsules,
federation, cloud sync, dashboard, or maintenance supervisor.

## Problem Addressed

`summary-v1` is useful for broad topics and conclusions, but most of its prose
items do not carry message-level evidence pointers. Repeated higher-level
compression can therefore remain factually plausible while becoming too vague
to understand or use as a route back to raw history.

`summary-v2` adds a parallel, human-readable hierarchy. Format version and
hierarchy level are separate dimensions: V2 is the format; L1, L2, and higher
are levels inside that format.

L1 is the high-recall evidence layer. It contains overview statements,
chronological scenes, typed atoms, explicit relations and state, exact retrieval
anchors, explicit omissions, and deterministic message backreferences.

L2 and higher are progressively shorter semantic routing layers. They preserve
a route to every direct child, but do not repeat every ordinary child detail.
They must carry active decisions and rules, tasks or commitments, open
questions, unresolved uncertainty, withdrawals, and facts involved in explicit
revision, contradiction, or supersession relations, plus work artifacts needed
as retrieval routes. Ordinary detail remains in the child and is reached by
descending the hierarchy.

## No-Silent-Loss Gate

For every L1 source evidence unit, exactly one of the following must hold:

1. it is represented in a scene and in an atom or retrieval anchor; or
2. it is listed as an explicit omission with a nonempty reason.

The validator rejects incomplete coverage, represented/omitted overlap,
unknown refs, out-of-order refs, missing tool/file locators, ungrounded anchors,
invalid state/type pairs, source drift, and malformed UTF-8.

L1 evidence units are raw message IDs. A higher-level summary consumes only
validated summary-v2 children; its evidence units are direct child summary IDs.
Every child must have a chronological scene route. A deterministic promotion
manifest is derived from validated child atoms and relations, and the validator
rejects a parent that loses any required promoted state. Exact locators remain
in lower levels instead of being copied upward. Every parent route still carries
the underlying raw message IDs. Raw history remains the highest authority and
is never rewritten.

This is a lossless navigation tree, not lossless prose repetition at every
level. Existing raw history, summary-v1 files, and valid L1 summary-v2 sidecars
are immutable inputs; the parent projector never rewrites them.

## Storage And Activation

The opt-in worker writes one immutable bundle directory outside the archive:

```text
memory-wuxian-summary-v2/
  level-N/
    summary-v2-<digest>/
      summary.json
      summary.md
```

The JSON is canonical and machine-verifiable. The Markdown contains the same
overview, phase routes, promoted durable state, relations, and raw-message
routes for direct human reading. L1 Markdown also contains exact anchors and
explicit omissions. Identical reruns are idempotent; conflicting bytes at the
same identity fail closed.

Production activation requires a separate governed change after human A/B
review demonstrates that the summaries are understandable, source-locatable,
state-faithful, and materially more useful than summary-v1. Passing structural
coverage alone is not semantic approval.

## Historical Backfill

`scripts/summary_v2_backfill.py` builds an explicit external plan from existing
summary-v1 metadata and authoritative raw records. It reconstructs each L1 job
only when every declared message exists and the complete source SHA-256 matches.
Invalid inputs are quarantined in the plan; source files are never repaired or
rewritten. Existing validated summary-v2 sidecars are reused by their parallel
summary ID.

The `run` command processes at most twenty ready nodes per explicit invocation
with no more than three one-shot model calls concurrently. A node is
quarantined after one content attempt in the active runner revision. L1 nodes are completed before their
matching V1 parent grouping becomes ready; each parent is generated only after
all direct child sidecars validate. Conflicting valid V2 candidates for one V1
node are preserved and quarantined for explicit arbitration while unrelated
nodes continue. Jobs, plans, run receipts, and sidecars stay under an explicit
output root outside the archive. The authoritative raw history is scanned once
when the plan is built; later batches refresh from sidecars and failure receipts
without rescanning the full archive. The runner registers no scheduler or
production queue and can be safely invoked again after interruption.

### Revision-bounded rescue campaigns

Normal, Level-1 map rescue, and parent rescue are one-shot campaigns. A content
failure is terminal for its explicit runner revision; only a revision change
can admit that node again. An infrastructure timeout, network/permission
failure, or CLI process failure is recorded as `infra-blocked`: it does not
consume a semantic content attempt, but it is not automatically looped.

Every rescue artifact is stored beneath its family and revision. Rejected
candidates are immutable diagnostic evidence only. A later revision must call
the model again; only successful partial maps whose source, prompt, schema,
projector, runner, and worker bindings still match may resume.

Parent map prompts use a compact, type-allowlisted projection containing child
identity, projection/source hashes, overview, scenes, atoms, relations,
omissions, and bounded coverage counts. Raw message manifests and message-ID
catalogs remain in the full validated sidecars and are verified locally, not
sent in the compact model payload. Grouping is bounded by UTF-8 prompt bytes.

Parent rescue reduction is hierarchical when the combined map prompt would
exceed the staged byte limit. Each intermediate reduction covers one exact,
ordered partition of the original direct children; its successful bundle is
bound to the source hash, prompt hash, projection hash, and input projection
hashes before it can resume after interruption. A failed or mismatched stage
is terminal for that revision. The compact parent prompt does not duplicate the
formal promotion ledger. Normalization deterministically injects every formal
promoted durable atom from the hash-bound source with its original child-summary
route before validation, so semantic completeness does not depend on model copy.

The worker persists one structured diagnostic receipt per invocation with
revision, stage, source/prompt hashes, byte counts, elapsed time, return code,
exception type, stdout/stderr hashes and bounded head/tail projections, and
candidate/projection hashes when available. Plan entries separately expose
dependency readiness, campaign status, eligibility, and blocking reason.

## First Authorized Real A/B

The first authorized real run used `job-000493`, the 22-message source of
`L1-000450`. It exposed and fixed two response-schema compatibility defects and
an ambiguity that allowed model-created retrieval anchors. The staged worker
now uses the API-compatible schema subset while retaining stronger local
validation, and the model emits only deterministically extracted required
locators.

The first structurally valid candidate contained 12 atoms but was less detailed
than summary-v1. After the prompt distinguished fact-level coverage from
message-level coverage and required source-language output, the second valid
candidate contained 23 atoms and recovered named genes, clone-size thresholds,
method thresholds, evidence limits, and flow-gating details in Chinese. It
retained routes to all 22 raw messages with zero silent source-ref loss.

Human review still found one open question present in summary-v1 but absent
from the valid summary-v2 candidate: whether SRSF2- and ZRSR2-derived peptide
specific T-cell states differ. Therefore the real A/B supports the architecture
and prompt improvement, but does not approve production activation. A future
promotion gate must compare a source-derived fact/open-question ledger, not
only source-ref coverage, before claiming semantic completeness.
