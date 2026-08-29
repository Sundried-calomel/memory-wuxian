# 流程变更日志

本日志由流程治理事务脚本追加维护。它记录流程提案、接受、纠正、细化、回滚和状态变化，不构成文件复制或删除授权。

## WF-20260822-000

- 时间：2026-08-22T19:53:14+09:00
- 类型：status
- 摘要：Bootstrap unified installer transaction governance surfaces
- 项目规则：建立当前有效规则基线。
- 历史复盘：建立实际历史与纠正记录基线。
- 优化流程：建立下一次标准流程基线。

## WF-20260822-001

- 时间：2026-08-22T20:10:51+09:00
- 类型：accepted
- 摘要：Establish hash-bound S01-S15 installer execution workflow, state machine, receipts, and project-scoped hook
- 项目规则：Bound S01-S15, one-remediation limit, freeze semantics, exact-artifact promotion, and project-only hook.
- 历史复盘：Recorded the v2.19.1 repeated installer escape and the accepted correction without claiming publication or installation.
- 优化流程：Established status, pre-edit, post-edit, verify, complete, next, freeze, and needs_replan execution order.
- 文档SHA-256：
  - `rules`：`B3344441C5D1BD85EDB58AFEE28BA31CED6B8DD2A3654A464F9244B615BC7103`
  - `history`：`EA913E46447A82805EF4BECFFD303EFB5479E3742C990F3346DC5D544389B5CB`
  - `optimized`：`B9D4F154FD3092271C1DD0CC596E4956B399C64D826CA90A7CE7B2BE3627E256`

## WF-20260829-002

- 时间：2026-08-29T13:09:25+09:00
- 类型：correction
- 摘要：Replace the S14 patch-loop with an evidence-first installer recovery sequence that proves the exact packaged chain before repair and preserves exact historical evidence.
- 项目规则：Bound evidence-first recovery, exact packaged-chain proof, S07 production-edit boundary, and selective invalidation.
- 历史复盘：Recorded the direct-controller rehearsal escape, S14 broker-child symptom, and patch-loop correction.
- 优化流程：Defined S01-S15 recovery execution from evidence freeze through exact-chain release promotion.
- 文档SHA-256：
  - `rules`：`7460C07E610EE89EED1091423596C9F3D4DBD78E6C26E1F086AC09108097FDD2`
  - `history`：`21ACC632BD65EB797B59FFA73A25E3CA59C10B37C587DA66E5220FC623332675`
  - `optimized`：`CEA600CA636E608EFC00ECB7431BBC90EAB613FEBD05E27D948A275711CF0CF5`

## WF-20260829-003

- 时间：2026-08-29T13:53:42+09:00
- 类型：correction
- 摘要：Allow frozen exact S14 full-chain evidence to bind to a hash-identical isolated broker replay; require a disposable Windows boundary only for any new full-installer rerun.
- 项目规则：Distinguished frozen exact-chain evidence from future full-installer reruns and retained disposable execution for new runs.
- 历史复盘：Recorded the unavailable Windows Sandbox backend and why a kernel-driver install would add risk without causal value.
- 优化流程：Bound frozen S14 evidence to the hash-identical isolated replay and kept future reruns disposable.
- 文档SHA-256：
  - `rules`：`D8E5349CF5218F6ACB7C8BB7F7B351B9E1F61452DC304C9190EDC69717A744E9`
  - `history`：`50836CDC6D68A6E23DE76F586251BB2040D38F4F32E357896018DEC158A740E4`
  - `optimized`：`9053B7A25FF3C998D9CD08E511D3565E5800B9C2DCB50F44606E363FCAECD6C5`

## WF-20260829-004

- 时间：2026-08-29T14:30:24+09:00
- 类型：correction
- 摘要：Preserve S01-S08 and permit S09 to use an explicitly authorized GitHub-hosted ephemeral Windows runner for the complete packaged-chain rehearsal, without running the candidate installer on the target device.
- 项目规则：Permit only an explicitly authorized GitHub-hosted ephemeral Windows runner for S09; keep packaged and direct rollback evidence separate and forbid target-device installation before S14.
- 历史复盘：Record why Windows Home made the prior S09 gate unsatisfiable and the user-approved correction that preserves S01-S08.
- 优化流程：Define the two-lane S09 execution, hash-bound artifacts, fail-closed runner identity, cleanup, and evidence-labeling procedure.
- 文档SHA-256：
  - `rules`：`D4FF8ACDC54C1C65CFC56EAEAB748F28B3D5435BD4E0D7FC16716876274CD7F7`
  - `history`：`9105F27AB4E1B60319C3EEBAF8711B3ED35C2171812BC60140E9892BB05E7726`
  - `optimized`：`974778CAFB810490F7E6C5096DE0BD23C5DB3EDC0A3520D317DA46DE546A0630`

## WF-20260829-005

- 时间：2026-08-29T16:13:24+09:00
- 类型：correction
- 摘要：Add an assertion-level installer-diagnostic-v1 contract before any further S09 behavior repair; preserve S01-S08 and retain failure evidence before rollback.
- 项目规则：Require assertion-level pre-rollback diagnostics, post-rollback status, and closed secret-free S09 evidence projections.
- 历史复盘：Recorded the generic four-condition shortcut failure and the accidental export of ephemeral recovery token and nonce.
- 优化流程：Diagnose first, rerun once on the disposable runner, then permit only one evidence-bound root-cause repair.
- 文档SHA-256：
  - `rules`：`AAE649801A878D33B755D45B92B6A5736FDC54C12E2622F2807B4EDDC8EAE939`
  - `history`：`AF667A98748F2348E5B847CFA58BAF16F5E0D2CC045BF6AD507A3DA5DA0DE256`
  - `optimized`：`3687E207E3641315ED43B8F7D03030B756519D6585734FD78DB81E30190E4500`
