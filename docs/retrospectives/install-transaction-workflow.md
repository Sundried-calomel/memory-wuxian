# Unified Installer Transaction Retrospective

<!-- workflow-governance: current=WF-20260905-018 -->

## Trigger

The v2.19.1 candidate repeatedly reached a later real Windows installation
boundary only after an earlier defect was repaired. The latest elevated run
rolled back while parsing a transaction journal whose XML declaration encoding
did not match its actual bytes. Publishing and target installation therefore
remain incomplete; installed v2.15.0 is still the authoritative live version.

## Escaped boundaries

- Installer correctness was distributed among Inno Setup, Python lifecycle
  code, Task Scheduler XML, wrappers, and updater paths instead of one owner.
- Mock and package tests did not prove the exact privileged installed route.
- Candidate construction, local validation, Release publication, and target
  installation were treated as related outcomes instead of distinct states.
- Repeated local repairs risked an additive patch loop and invalidated earlier
  evidence without a machine-readable dependency graph.

## Accepted correction

The correction is a hash-bound, resumable S01-S15 state machine established
before further installer business changes. Existing evidence is preserved;
raw archives and unrelated runtime domains remain outside this refactor.

## 2026-08-29 correction

### Trigger

S11 and S13 evidence did not exercise the complete packaged production chain.
The Windows rehearsal called `WindowsInstallerTransaction.execute` directly,
while S14 was the first stage to traverse the Inno Setup, PowerShell, UAC
broker, and child-controller boundary. S14 then failed after the broker consumed
its nonce and before a child receipt appeared.

### Error

The workflow treated direct transaction-controller rehearsals as sufficient
evidence for the outer packaged route. When S14 exposed that missing boundary,
successive local changes were classified as new implementation defects instead
of first invalidating the earlier incomplete rehearsal claim. This encouraged
repeated replans and additive corrections without one preserved traceback from
the exact packaged child process.

### Accepted correction

The workflow now reserves S01-S06 for evidence and architecture: freeze the
failed bytes, map the exact call chain, build a no-product-write diagnostic
harness, prove the broker/controller root cause, and audit Owner, reachability,
package membership, and redundancy. Production repair begins only at S07 and
must be one shared-owner change plus deletion of independently proven redundant
paths. S09-S11 must traverse the exact packaged chain; direct controller calls
remain useful unit evidence but cannot satisfy that claim.

## 2026-08-29 disposable-boundary correction

The first recovery architecture required a fresh exact-installer run inside
Windows Sandbox or an equivalent disposable VM. S04 then proved that the target
Windows Home device has no Windows Sandbox backend. Installing a third-party
kernel isolation driver solely to recreate an already frozen S14 failure would
increase system risk without adding causal information.

The corrected rule distinguishes historical and new execution. The frozen S14
run already proves the exact Inno and PowerShell route reached broker exit `1`.
An isolated replay using the same candidate broker, manifest-bound runtime, and
manifest captures the missing traceback and reproduces the same pre-child exit.
Those lanes may be combined only under explicit hash linkage. Any future full
installer run still requires a disposable Windows boundary.

## 2026-08-29 GitHub-hosted S09 correction

After S08 passed, the remaining S09 contract still required a new packaged
installer run while permitting only local test and evidence paths. Because the
target is Windows Home without Windows Sandbox, that combination made the hard
gate impossible to satisfy without either touching the target device or
bypassing the path contract.

The user explicitly authorized preserving S01-S08 and moving only S09 to a
GitHub-hosted ephemeral Windows runner. The corrected workflow permits the
minimal CI workflow path and requires two honest lanes: actual packaged Setup
clean/repeat execution on the disposable runner, and separately labelled
namespaced transaction rollback evidence. The latter supplements rollback
coverage but never substitutes for the outer Inno, PowerShell, Broker, and
child chain. No candidate installer may run on the target device before S14.

## 2026-08-29 assertion-diagnostic correction

The first corrected S09 runner crossed the Inno placeholder boundary, created
an `inno` request, and reached the seventh transaction resource. The dashboard
shortcut then failed inside one four-condition activation assertion. The error
identified the component but not whether target, working directory, icon, or
live target existence differed, and rollback removed the observable shortcut.

The exported evidence also copied the internal recovery journal and broker
request verbatim. Those files contain an ephemeral transaction token and nonce.
Disposable-runner destruction limits their lifetime but does not make recovery
authority valid CI evidence. The correction keeps the recovery journal private,
records one assertion-level failure before rollback, appends rollback outcome,
and exports a closed package-bound projection. Shortcut behavior remains frozen
until the receipt identifies the exact failed assertion.

## 2026-08-29 Unicode shortcut-inspection correction

The closed S09 receipt then proved that the ASCII temporary shortcut preserved
all requested activation fields, while reopening the byte-identical shortcut
under its final Chinese desktop name returned empty target, working-directory,
and icon properties on the GitHub Windows runner. The transaction correctly
rolled back all seven resources, so the remaining defect was the inspection
route rather than transaction ordering or compensation.

The earlier contract admitted shortcut creation but omitted the existing
canonical shortcut inspector from the same diagnostic Owner. That omission
blocked the minimal shared-owner repair and encouraged repeated inline checks.
The correction admits the inspector, requires a hash-equal ASCII projection of
the exact final `.lnk` bytes, preserves the visible Unicode filename, and
deletes the projection after inspection.

The first governance-16 draft also tried to broaden same-stage remediation and
narrow when `replan` applies. Independent evaluation rejected that addition
because the active machine contract still permits only one integrated
remediation cycle. The overreach was removed before capability admission. This
revision changes only the Unicode shortcut-inspection Owner boundary; any
future replan-policy redesign requires its own explicit contract revision.

## 2026-08-30 baseline and historical-fixture correction

Runs `33255533206`, `33256543858`, and `33257404944` separated three serial
boundaries. The Unicode shortcut production path and all 71 release scenarios
passed, but S09 then exposed an 8.3 path spelling mismatch, quoted multilingual
`git ls-files` output, and finally a shallow checkout that could not resolve
`v2.15.0`. The final failure occurred after clean and repeat packaged installs
had committed, so it was a rehearsal prerequisite failure rather than an
installer transaction failure.

The controller also stored only the paths dirty at replan time. After those
same bytes were committed, the paths disappeared from the dirty set and were
misclassified as new drift. The corrected baseline stores the exact commit and
overlay hashes, compares only commit-delta and overlay paths, and preserves
legacy state compatibility. Windows S09 now fetches complete history before
running the historical rollback fixture.

## 2026-09-05 control-plane replacement

### Trigger

The workflow repeatedly returned to S06-S09 and reached S14 with claims that
had never been demonstrated by the same installer bytes. The runtime state had
also absorbed generated CI downloads and temporary trees into a 109 KB
baseline. A fixed-name receipt could be overwritten, evidence consisted largely
of producer-authored `passed` labels, and `replan` replaced the baseline with
the current failed worktree. Independent quality evaluation was additionally
misread as a request for new user authorization.

### Error

The state machine certified its own assertions and changed the identity it was
supposed to check. Stage order built the installer after earlier rehearsals,
failure injection was required through a manifest that deliberately excluded
it, and the workflow had two incompatible rollback terminal states. Because a
late failure did not identify the earliest false receipt, each retry expanded
the implementation surface instead of invalidating the inaccurate claim.

### Accepted correction

The schema-3 controller uses an explicit commit-plus-overlay baseline manifest,
never admits generated trees, and never promotes current bytes during replan.
It stores immutable epoch/step/attempt receipts, validates their workflow,
definition, predecessor, source-snapshot, invocation, artifact, and assertion
identity, and records verification failures automatically. A second failed
attempt enters `needs_replan`; contract drift may also open a new epoch without
manufacturing a failure or asking the user for permission.

S09 now builds and hashes the sole candidate before S10-S13 exercise it. Exact
packaged evidence and controlled direct rollback evidence remain distinctly
labelled. Independent evaluation is a quality gate at S03, S13, and S15.
Concrete user authorization is limited to S14 target UAC installation and S15
GitHub publication plus official-asset reinstall.

### Follow-up evidence correction

The first schema-3 draft still allowed a producer to place both `expected` and
`observed` values in its own evidence manifest. Hashing that manifest proved
only that the self-authored judgment had not changed. The corrected design
removes assertions from producer manifests and moves all expected values into
a closed, versioned verifier policy. The controller now reads the designated
JSON artifact field itself, checks the policy-owned expectation, and binds the
policy hash into state and verification receipts.

## 2026-09-05 independent control-plane audit correction

The first replacement candidate still failed independent review despite its
focused tests passing. Admission was checked before every command, so changing
the controller invalidated the recovery commands needed to admit it. Producer
and evaluator identities remained self-declared strings, accepted receipts did
not recursively rehash their evidence, candidate freeze was optional and late,
replan could overwrite a fixed receipt, top-level contract drift had no affected
step, and simple operator mistakes consumed the single remediation allowance.

The corrected control plane makes admission an explicit S01 quality artifact
instead of a command bootstrap lock. The executed JSON Schema owns manifest
shape; exact producer profiles, source identity, bounded artifacts, evaluator
and CI provenance, policy-owned observations, and candidate freeze bindings are
checked by the controller. S09 completion creates the candidate freeze
automatically, and S10-S15 cannot substitute different bytes. Completed
receipts recursively revalidate their manifests and artifacts. State mutations
share one lock and canonical atomic writer, while replan archives exact prior
state bytes and writes content-addressed history. Only an actual negative gate
result spends remediation capacity; control and usage errors remain retryable.

### Control-plane dependency coverage correction

The first corrected contract hashed the shared atomic-write, file-lock, and
transaction helpers as control-plane dependencies but did not include them in
the protected path set used by snapshots and hooks. That split could detect the
change at admission time while omitting it from stage evidence. The contract
now requires every control-plane file to be protected and rejects incomplete
coverage during load.

## 2026-09-05 terminal and evidence-identity correction

A fresh exact-byte audit found that the schema-3 draft could mark a blocked S15
workflow complete, accept a manually relabelled completed step without a
verification receipt, and reinterpret old evidence after the manifest schema
changed. It also treated any nonzero tool exit as a quality failure, accepted a
candidate digest without hashing installer bytes, and had no executed
characterization beyond S10. The package-import fallback was also broken by an
absolute import in the shared transaction module.

The correction makes terminal status depend on all fifteen verified steps,
binds both evidence-schema and aggregate control-plane hashes into each epoch,
and makes tool failures retryable without spending the single quality repair
cycle. Candidate, target-download, and release gates now hash the actual
installer subject under a closed root. Policy-owned minimum counts reject empty
zero-failure reports. Authorization-gated evidence binds the prior receipt,
while host UAC and remote-write permission remain external authorities. The
test harness now imports the package form, validates deterministic admission,
rejects admission and state tampering, exercises schema drift, and completes
the entire S01-S15 lifecycle with candidate-release identity equality.

The follow-up contract check also makes the executed schema, verifier policy,
controller, protected scope, exclusion scope, evidence directory, and binary
subject roots structurally inseparable, preventing a future JSON-only edit from
silently removing one of those guarantees.

## 2026-09-05 exact evidence and replay correction

### Residual defects

The next byte-level audit found that a local JSON file could cite a genuine
successful GitHub run without proving that the run produced that JSON. An
independent report could also carry the frozen candidate digest in one field
while evaluating an unrelated governed file. Several scenario gates checked
only totals and omitted closed route, runtime-effect, platform, job, or suite
membership. Python equality additionally allowed booleans to compare equal to
integer zero or one at untyped boundaries.

Failure handling had a second crash window: the controller wrote an immutable
failure receipt and then updated state. A process stop between those writes left
the negative result unreferenced, so a later attempt could bypass it. Old
authorization and verification identity also needed an explicit attempt check.
Migration parsed separately from hashing and accepted an unspecified legacy
workflow shape, while S09 did not mechanically require a committed-only source.

### Correction

The controller now hashes and parses the same bounded bytes, uses type-exact
policy comparisons, and accepts migration only from schema 2 with the exact
workflow identity. Every CI result JSON requires both a matching live GitHub
Actions run and a workflow-signed artifact attestation; installer subjects keep
their separate attestations. Evaluator reports must include the exact
architecture capture or candidate freeze in their validated artifact set.
Policy-owned exact matrices prevent partial scenarios from passing on totals.

Failure receipts are durable transition intents. Read-only status exposes an
orphan, while the next locked mutation replays it, advances the attempt, and
invalidates stale verification and authorization. S09 rejects any governed
overlay before candidate construction. Negative tests now cover unsupported
migration, foreign workflow identity, oversized inputs, boolean/integer
confusion, malformed state, orphan replay, receipt-attempt drift, uncommitted
S09 source, false GitHub provenance, unbound evaluator output, omitted packaged
route, subject byte-length drift, and authorization invalidation after a failed
installation result.

## 2026-09-05 remaining proof-boundary closure

### What the four-way review still found

The first exact-evidence correction did not cover every control-plane read.
Contract, policy, schema, and admission files could still be parsed and hashed
by separate reads. JSON decoding still accepted duplicate keys and non-finite
constants. A later replan replaced the epoch baseline, so preserved S01 evidence
that referred to that mutable baseline could invalidate itself. Individually
valid CI records could also be assembled from different workflow attempts, and
an attestation command's successful exit was accepted without checking that its
verified statement named the expected subject digest.

Crash handling required two narrower distinctions. Recovery discovered a
failure receipt before fully validating state, compared it with the current
contract instead of the definition frozen in the interrupted state, and did not
prove that the receipt occupied the controller's exact content-addressed path.
The event log authenticated only its tip, leaving predecessor linkage implicit.
Finally, the evaluator report can prove exact bytes and a distinct declared
identity, but current Codex tooling does not provide a cryptographically signed
subagent identity receipt; describing it as cryptographic independence would be
an overclaim.

### Closed design

The controller now uses one bounded strict decoder for all JSON control inputs
and carries hashes from those same bytes. Replan-stable evidence binds the
original step predecessor. CI evidence is checked in closed same-run groups,
the build-once result captures the same candidate hash as the artifact result,
and exact effect/job counts accompany their closed ID sets. Attestation output
must contain the expected subject SHA-256, followed by a local rehash.

Orphan recovery now validates state first, accepts only canonical
digest-addressed controller receipts, and uses the state-frozen step definition
during contract drift. Events carry explicit predecessor file references and
the full state-reachable chain is validated. Unreferenced event files remain
immutable crash residue rather than authority. Regression tests exercise every
one of these boundaries, while evaluator independence is explicitly retained as
a process guarantee backed by deterministic evidence, not mislabeled as a
signature guarantee.

## 2026-09-05 feasibility-boundary correction

### Independent finding

The first frozen-candidate review correctly found that migration used an
unbounded state read and replan validated one runtime-state read but archived a
second. It also demonstrated that a same-user writer could fabricate local
command metadata or type a different evaluator identity into JSON. The latter
two findings exposed a threat-model mismatch: current Codex task separation has
no signed identity receipt, and a repository-local controller cannot defend its
own files from a malicious principal who already has equal write authority.

### Correction

Migration and replan now validate, hash, and archive one bounded runtime-state
capture; regression tests forbid `Path.read_bytes()` on the live state in both
paths. The workflow explicitly limits local command, test, and inspection
records to non-adversarial process-audit evidence. It still recomputes hashes,
state bindings, closed policy values, and external provenance, but no longer
overclaims local invocation metadata as authentication. Claims needing an
adversarial trust boundary must use GitHub-attested CI evidence, while evaluator
independence remains an actual separate invocation whose identity is candidly
process-attested rather than cryptographically signed.
