# Defect Prevention Packet

- Task: `memory-wuxian-v2.19.0-release-install`
- Packet: `packet:f8b5b4c7d60cf6ea5bf4a295`
- Status: `ready`
- Blocking limit: 12
- Projection limit: 2500 estimated tokens

## G01 环境存在不等于能力可用

- Class: `blocking`; score: 95; severity: `high`
- Guidance: Resolve the declared runtime and prove the exact capability, imports, permissions, version, and working directory; never infer readiness from PATH or process existence.
- Forbidden: Treating command existence or process existence as sufficient proof.; Installing a global dependency before checking a registered project runtime.
- Required: Resolve an explicit runtime or tool registry when available.; Probe version, protocol, critical imports, permissions, and working directory.
- Checks: runtime-capability-probe (L2): Execute the exact required capability with the selected runtime and working directory.

## G05 模拟测试替代真实平台效果

- Class: `blocking`; score: 88; severity: `high`
- Guidance: Mocks and schemas cannot prove UI, animation, installation, or background effects; run at least one real target-platform case.
- Forbidden: Using a mock, file count, schema pass, or process launch as the sole proof of platform behavior.
- Required: Validate UI visually and interactively, background work by state transition, and installers by upgrade and rollback.
- Checks: target-platform-effect (L3): Exercise the production platform path and capture its visible or persisted result.

## G09 文档、版本、二进制和发布证据漂移

- Class: `blocking`; score: 128; severity: `critical`
- Guidance: Bind code, docs, manifest, package, installed artifact, tag, tests, and release evidence to one exact candidate SHA.
- Forbidden: Publishing a package built from a different SHA than the tested candidate.; Claiming a documented feature exists without verifying the packaged artifact contains it.
- Required: Verify scenario IDs and documentation tokens before release.; After merge, wait for CI on the exact main-branch SHA before building release artifacts.
- Checks: exact-candidate-release (L4): Bind source, docs, manifest, package, installed artifact, tag, and CI evidence to one SHA.

## G11 被动文档没有执行力

- Class: `blocking`; score: 85; severity: `critical`
- Guidance: Persistent rules need target-based machine discovery, preflight hashes, completion receipts, and fail-closed drift handling; passive Markdown is not enforcement.
- Forbidden: Treating documentation existence as proof that future tasks will read or follow it.
- Required: Register applicable documents and rules by target scope.; Freeze input hashes at preflight and verify applicable families and required updates at completion.
- Checks: registered-preflight (L1): Prove target-based discovery and hash-bound preflight work without chat memory.; fail-closed-completion (L2): Reject missing receipts, registry drift, unknown families, and unchanged required project workbooks.

## Authorization Boundaries

- The user authorized the 2.19.0 finalization, GitHub publication, and transactional installation on this Windows device in the current task.
- Do not modify, migrate, rewrite, delete, or repair raw archives, existing summaries, derived indexes, cloud envelopes, device identities, or exchange protocol state.
- Summary V2 rescue and atomic summary work are explicitly excluded.
- A failed rehearsal or evaluation must trigger whole-owner and invariant review before any repair; do not add a sidecar, fallback, wrapper, duplicate path, or broad new abstraction when the existing canonical owner can be corrected.
- After any repair, rerun the affected case and every previously passing case that shares its owner or invariant to prevent seesaw regressions.
- No candidate may be published after more than one remediation cycle without stopping for a new user decision.

## Invalidation

- The task manifest bytes or declared scope change.
- A catalog, source workbook binding, rule status, or threshold changes.
- The implementation plan adds an undeclared unit, owner, path, entrypoint, resource, or operation.

Packet SHA-256: `665694119d8256480189950da2f6c4b1dc42db82a7bec5b84237a0d60a695de2`
