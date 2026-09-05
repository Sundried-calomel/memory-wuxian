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

## WF-20260829-006

- 时间：2026-08-29T22:03:29+09:00
- 类型：correction
- 摘要：Admit the existing canonical Windows shortcut inspector into the bounded S09 diagnostic owner so exact final-link bytes are inspected without reopening the Unicode path directly.
- 项目规则：Bound the canonical shortcut inspector to the existing S09 diagnostic owner and reserved replan for real boundary changes.
- 历史复盘：Recorded the exact Unicode-path inspection failure and why repeated inline repairs were the wrong response.
- 优化流程：Defined hash-equal ASCII projection inspection and stage-local remediation for same-owner findings.
- 文档SHA-256：
  - `rules`：`7E9BF1D59A5193AEC975635E17F55F11B2B883412946B419F1E7358CFFA11B37`
  - `history`：`FF7B56191E73EA683947FF555D8106A58247045DD267415BE1C86F6ECC02D264`
  - `optimized`：`BA7DD01C6513C22EF5DE5F72EEDD795B92297834D555241730E953C204A40620`

## WF-20260829-007

- 时间：2026-08-29T22:14:03+09:00
- 类型：correction
- 摘要：Remove the unintended remediation-policy expansion from governance-16 and retain only the bounded Unicode shortcut inspector admission.
- 项目规则：Removed the unadmitted remediation-policy expansion; retained only the canonical Unicode shortcut inspector rule.
- 历史复盘：Recorded the independent C02 denial and removal of the workflow-policy overreach before admission.
- 优化流程：Restored the existing one-remediation-cycle protocol while retaining exact-byte shortcut inspection.
- 文档SHA-256：
  - `rules`：`C0C68AD762AC23FCEA3C9EECA7CAE2B8B74C6B3A7E76025D8D69DD179F09EDC8`
  - `history`：`355A4D5CCE8FAC5C11023A6636F8209814A4552B36BB8083A59E992AFA131DDA`
  - `optimized`：`03A61EA29023C545B090914DD9E3C46462F0EA2B4960B709A038AA6FD685CB43`

## WF-20260830-008

- 时间：2026-08-30T02:01:00+09:00
- 类型：correction
- 摘要：Replace dirty-only workflow baselines with commit-plus-overlay snapshots and make Windows historical-tag rehearsal prerequisites explicit.
- 项目规则：Require commit-plus-overlay baselines and complete Windows history for the v2.15.0 rehearsal prerequisite.
- 历史复盘：Recorded the false dirty-state drift and the shallow-checkout failure after successful clean and repeat packaged installs.
- 优化流程：Compare commit delta plus overlays and resolve historical refs before the expensive Windows packaged-chain lane.
- 文档SHA-256：
  - `rules`：`79A77938B29E09B03156D6A838F6F77392558F9923121B0FA8DDBB3507C351A4`
  - `history`：`93C1D412D062CDC09428DC3708FDCE5D24B071407386CF72F7CC2F5B655DBFC8`
  - `optimized`：`42A0932AF461F8690DD518AFD36CB0D973E123CC25C4ED936EC6EB3DD517C1D5`

## WF-20260904-009

- 时间：2026-09-04T23:47:52+09:00
- 类型：correction
- 摘要：Reconcile the committed but unfinalized workflow-document changes from the installer baseline and direct-clean diagnostic commits before beginning the new recovery epoch.
- 项目规则：Recorded the already-committed baseline and direct-clean rule changes under a current governance revision without treating them as new success evidence.
- 历史复盘：Recorded the missing finalize boundary and preserved the prior hash drift as explicit recovery evidence.
- 优化流程：Restored a clean governance state so the replacement installer workflow can begin from an auditable transaction.
- 文档SHA-256：
  - `rules`：`545FD74CEF4CDE293683C1CEC76F5D7BDD7956920E978305D02BDC78E7235867`
  - `history`：`89A84D5DDE8E7F8414794379B72DF6A415EFB5286076A1DFF61FBF0A2BCA3576`
  - `optimized`：`F606086E645843850CE428FA42EAAA4CFF8EE7D36C11CE14E70BDA391F13AC5E`

## WF-20260904-010

- 时间：2026-09-05T00:13:13+09:00
- 类型：correction
- 摘要：Replace the contradictory S01-S15 controller and self-certifying evidence model with an epoch-scoped exact-candidate workflow before repairing the Windows product transaction.
- 项目规则：Bound installer work to explicit baselines, immutable epoch receipts, candidate-before-rehearsal order, and concrete-only user authorization.
- 历史复盘：Recorded how self-certifying evidence, replan baseline promotion, and late candidate construction caused repeated S09-S14 loops.
- 优化流程：Replaced the operator sequence with source-bound evidence manifests, automatic failure accounting, bounded replan, and same-byte S09-S15 promotion.
- 文档SHA-256：
  - `rules`：`13D341174026A66033366FDC71C4E2C9CF12283B328EF04751EA6437A9644A22`
  - `history`：`7C1FADDE96AACB6E18F201B15B56A5178E54DD869ABAE285E98A59DE3A96235B`
  - `optimized`：`BEB0738BF7263A0DC9C84E39C097632161CFDF54CE8D6F2207C9F5B6EF2D686A`

## WF-20260905-011

- 时间：2026-09-05T00:21:08+09:00
- 类型：refinement
- 摘要：Move installer evidence expectations into a versioned verifier policy so producers submit observations rather than grading themselves.
- 项目规则：Made the versioned verifier policy the sole owner of evidence expectations.
- 历史复盘：Recorded and corrected the residual producer-authored expected/observed self-certification defect.
- 优化流程：Defined observation-only producer manifests and controller-owned JSON policy checks.
- 文档SHA-256：
  - `rules`：`A5D176332A7CCDDB226EE4C8C595C0EA8F18950BB7B9C62B197F333E4A46FBCE`
  - `history`：`C25ECB83CAA3B5DB92F8D1912C4A1F666BB7AF08EF2BC9F007E9CB984EA993ED`
  - `optimized`：`B3DE4C62D9A4DE7CB6A7BE1C013FD7CCA43D0A854B79CE69AB983271B456E423`

## WF-20260905-012

- 时间：2026-09-05T01:03:28+09:00
- 类型：correction
- 摘要：Replace self-locking and self-certifying control-plane rules with provenance-bound evidence, mandatory lifecycle freezes, immutable state recovery, and non-remediation operator errors
- 项目规则：Made admission an S01 quality artifact, made freezes automatic and hash-bound, separated operator errors from gate failures, and required immutable locked state records.
- 历史复盘：Recorded the independent audit failures in the first schema-3 candidate and the accepted structural correction.
- 优化流程：Added legacy-safe resume, exact producer and source binding, S09 candidate freeze, recursive evidence validation, and authorization receipt binding.
- 文档SHA-256：
  - `rules`：`5814320BC8C74431E751A50B90EEF9407AC5D08EC7F830EEBC8C9A74D38A989C`
  - `history`：`84B20638A3F143B271E52FBC0EB666216C2396893B5A52CC2A3A75B2EAC3DF7B`
  - `optimized`：`4B2F3D226BE8408057C0FA1776F4B7FBD9EEFE03FAAEBE43AB0FE336427375AA`

## WF-20260905-013

- 时间：2026-09-05T01:10:26+09:00
- 类型：correction
- 摘要：Require every executable control-plane dependency to be protected, snapshotted, and hash-bound
- 项目规则：Control-plane dependencies must be covered by protected paths and therefore bound by snapshots and hashes.
- 历史复盘：The prior candidate omitted shared helper modules from protected coverage; the contract now rejects that gap.
- 优化流程：Validate every control_plane_files entry against protected_paths before workflow execution.
- 文档SHA-256：
  - `rules`：`B7D997DC906A5C1E310B8207CAE4D9DE9210811A0CC7484E29F0C21FE215CA46`
  - `history`：`D13E8E401F1DE33292BDC550DCD9352CFB393402BE13A2730629FBCC27EB10E8`
  - `optimized`：`9ABE42C7882BD4547FE0BD04DDC07F2C1EC4B51988D26485D33ABBFF13BAABE3`

## WF-20260905-014

- 时间：2026-09-05T01:51:38+09:00
- 类型：correction
- 摘要：Close terminal-state, evidence-identity, candidate-byte, contract-reachability, and late-stage characterization gaps before migration
- 项目规则：Bind terminal completion, evidence schema, control-plane identity, actual installer subjects, nonvacuous evidence, authorization evidence, and deterministic admission.
- 历史复盘：Exact-byte audit exposed terminal bypass, missing schema identity, tool-failure misclassification, unbound installer hashes, late-stage test gaps, and package import failure.
- 优化流程：Validate actual candidate bytes and positive coverage counts, keep tool failures retryable, bind authorization before evidence, and exercise S01-S15 before migration.
- 文档SHA-256：
  - `rules`：`585DD5C4E69A5F752C4EB6F7473D2C502364D926C450F5CE05F1D4A273C008EA`
  - `history`：`3321178AC44FC94E36CAED77FE34BCEB5C4E6F76122AB544866F91097DF0F763`
  - `optimized`：`FFBC13BE3AF31982AADF4A8E474582174269841136BD0D1E743A3EEA3AF35C9E`

## WF-20260905-015

- 时间：2026-09-05T01:54:34+09:00
- 类型：refinement
- 摘要：Make schema policy evidence roots subject roots and control-plane coverage structurally inseparable
- 项目规则：Require executed control files and evidence or subject roots to remain inseparable from snapshot scope.
- 历史复盘：A JSON-only path change could otherwise remove a control dependency or overlap generated evidence with source identity.
- 优化流程：Reject missing excluded or misplaced schema policy controller evidence and subject roots during contract load.
- 文档SHA-256：
  - `rules`：`67D77D7D028FD63E1CB412349FBBA853C1BB8F945157C7FA2CB43C0CA2635250`
  - `history`：`6438F8C9EB96CC371D69EAA80157542EECEEC71525C230BEC45B5EC93D59828E`
  - `optimized`：`705CF832A68E0CDA4C59F5CBB6EE37648E56BFBE6CAFDDDE10A464108703E0B0`

## WF-20260905-016

- 时间：2026-09-05T02:58:52+09:00
- 类型：correction
- 摘要：Bind installer-workflow evidence to exact bytes, closed matrices, attempt identity, and replay-safe transitions before schema migration.
- 项目规则：Require bounded same-byte parsing, strict JSON types, live CI plus result attestations, exact evaluator artifact binding, closed matrices, attempt-scoped receipts, replay-safe failures, and committed-only S09 source.
- 历史复盘：Recorded residual self-declared CI evidence, unbound evaluator reports, type confusion, migration ambiguity, and the orphan-failure crash window with their deterministic regressions.
- 优化流程：Defined exact evidence authentication, evaluator and candidate binding, crash replay, authorization invalidation, committed-source freeze, and exact schema-2 migration.
- 文档SHA-256：
  - `rules`：`A2F7131AE868E95F6D280A7B3F650C41CA86491ED5E0012E22F7B9E9E2BFC6E8`
  - `history`：`3654040AD1B14B7DCB16C7A416EBBBEEE456AB323308FF7BDBC93979DB2C44ED`
  - `optimized`：`4BAB8DD00FCE8580F74D5B0E9D2F2BDCF58574C4F24085A520434558A8D48725`

## WF-20260905-017

- 时间：2026-09-05T03:34:07+09:00
- 类型：refinement
- 摘要：Close remaining control-plane proof gaps with strict same-byte JSON parsing, replay-safe failure recovery, preserved-replan bindings, grouped CI provenance, and exact closed-matrix evidence.
- 项目规则：Bound all control JSON to strict same-byte parsing, frozen replan identities, same-run CI groups, verified attestation subjects, exact orphan receipts, linked events, and an honest evaluator-identity boundary.
- 历史复盘：Recorded the residual parse/hash, preserved-replan, mixed-run, attestation-subject, recovery, event-chain, and evaluator-identity gaps found by four-way review and their bounded corrections.
- 优化流程：Defined the repeatable same-byte evidence, grouped provenance, digest-confirmed attestation, replay-safe failure, linked-event, and preserved-step validation sequence.
- 文档SHA-256：
  - `rules`：`9F88896F2514E356ED35461FD9ABE24BA84963BFE1ED539539C5F469CD6CAA1C`
  - `history`：`CFED2C70EFD766DF6F3BEC31659B86DB2487C56A731219474640B01DEE19FE76`
  - `optimized`：`0D0FEED63CF4E74C964694351229BCA823C79B11DA838E9001663D4EAD4B0301`

## WF-20260905-018

- 时间：2026-09-05T04:08:53+09:00
- 类型：correction
- 摘要：Clarify local process evidence trust boundaries and enforce bounded same-buffer state archival after independent evaluation.
- 项目规则：Declared the feasible trust boundary and required one bounded runtime-state capture for archival transitions.
- 历史复盘：Recorded the independent FAIL, separated the true same-byte defect from unattainable same-user cryptographic identity, and documented the correction.
- 优化流程：Added the future process for evidence trust routing and same-buffer migration/replan archival.
- 文档SHA-256：
  - `rules`：`495D290D4E31A371742F97F0215D33E53A39335A16021CEB6450882D44459173`
  - `history`：`AB0A34A916C6A791DC4015BEF7C2566365E622E37CDFE60533DDB37332D65001`
  - `optimized`：`C436C69507A433652BED2CEFAF8BDC1CB8681734531CF240754AD6EB0A81F837`
