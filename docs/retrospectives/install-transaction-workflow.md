# Unified Installer Transaction Retrospective

<!-- workflow-governance: current=WF-20260829-004 -->

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
