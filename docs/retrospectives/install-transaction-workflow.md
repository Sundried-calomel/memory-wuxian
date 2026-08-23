# Unified Installer Transaction Retrospective

<!-- workflow-governance: current=WF-20260822-001 -->

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
