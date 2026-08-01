# Memory 无限项目错题本

本文件是 Memory 无限重复缺陷的跨设备、追加式项目错题本。原始对话、提交、发布证据和
运行日志仍是权威来源；本文件负责把分散事件聚成复发链，防止后来的功能绕开旧修复。

## 证据覆盖

- Windows：本机 Git 历史、CHANGELOG、发布证据、运行状态和截至 2026-08-02
  可回查原文的 Memory 无限记录。
- macOS：当前只采用只读 Mac 联邦副本中可回查的记录，以及共享仓库提交。
  “Mac 直接发生”和“共享仓库证明但设备来源不明”必须分开。
- 本文件不声称已经恢复全部历史。缺失或过期的跨设备证据标为 `待补证`，不得猜测。

证据等级：

- `本机已核验`：在本 Windows 设备复现或由本机原文、运行证据核验。
- `Mac 已核验`：Mac 只读副本中存在可回查原文。
- `仓库已核验`：Git/发布历史证明发生过，但证据不足以指定设备。
- `待补证`：仅是覆盖缺口或候选线索，不算既定事故。

## 复发矩阵

| 错误族 | 反复症状 | 被绕开的边界 | 今后的硬门禁 |
|---|---|---|---|
| `MW-R01 启动身份` | 快捷方式消失、Python 图标、杀毒删除、控制台闪烁、用户身份错误 | 新安装器或启动入口绕开旧激活逻辑 | 解析真实用户和重定向桌面；核验目标、工作目录、图标、空参数、哈希、无控制台进程树和真实打开 |
| `MW-R02 文本编码` | 中文/日文/货币符号乱码或 GBK 错误 | 新文件读取或子进程使用 Windows 默认代码页 | 所有文本边界显式 UTF-8；Windows 特殊字符 fixture |
| `MW-R03 运行时移植` | `fcntl`、`kqueue`、LaunchAgent、PATH Python、缺包 | 只在开发设备和开发运行时验证 | 目标平台冷启动、固定 runtime registry、依赖声明、调度器和进程实测 |
| `MW-R04 后台抢前台` | PowerShell 抢焦点；设置和刷新等待数秒 | UI 刷新路径调用 shell/Python 维护任务 | 刷新/设置不启 shell；进程内 API、缓存或 SSE；断言无 console host |
| `MW-R05 健康假象` | 实际停滞却显示健康，或正常追赶却报警 | 用进程/文件/登记状态代替实际效果 | 验证真实状态变化；分别报告采集、覆盖、语义、备份和完整性 |
| `MW-R06 发布证据漂移` | tag、包、文档、二进制、测试 SHA 不一致 | 候选、合并、标签、发布包被当作同一阶段 | 场景目录校验、同 SHA main CI、包清单、二进制版本、三语文档合同 |
| `MW-R07 游标与投影` | 旧会话跳过、Token 重放、单文件覆盖全局进度 | 一个游标承担多种语义，局部事件写全局投影 | 拆分解析/归档/Token 游标；保留所有旧格式迁移 corpus；全局投影单一 Owner |
| `MW-R08 语义自治` | 采集在运行但摘要债务不下降 | 把 worker 存在或任务登记当成完成 | 独立租约 dispatcher；真实 pending 下降、summary 增长；可重建派生漂移不能阻塞原文有效任务 |
| `MW-R09 备份与同步` | 完整备份阻塞采集、云路径失败、重叠/重放错误 | 重 I/O 同步执行或传输身份不明确 | 合并债务、精确 checkpoint/revision、平台路径 fixture、远端可见后再报成功 |
| `MW-R10 结构与命令` | 嵌套 YAML、缺 PyYAML、前导连字符参数失败 | 临时文本解析或拼接命令跨越结构化边界 | 结构化解析、参数数组、显式依赖、恶意形态输入 fixture |
| `MW-R11 无界工作` | 每个 tick 重扫数万条；CI/恢复运行数小时 | 周期或局部事件暗藏全历史工作 | 有界可续批次、水位、深审计缓存、生产量级耗时门禁 |
| `MW-R12 Owner/文档漂移` | README、路线图或代码块被相邻更新挤掉 | 相邻内容没有唯一 Owner 与合同检查 | 每个生产文件唯一 Owner、受管文档合同、移动代码块回归测试 |

## Windows 事故账

### MW-WIN-001：名义支持 Windows，运行时仍是 Unix

- 证据：`本机已核验`。早期安装因 Unix 专用 `fcntl`、平台 watcher 假设和安装后
  没有自动采集而失败；后来又出现通过 PATH 猜 Python、Git、GitHub CLI 的问题。
- 复发机制：把开发机“有这个工具”误写成产品合同。
- 永久门禁：用只包含发布包路径的干净 Windows profile，测试 CLI 冷启动、collector
  激活、计划任务归属和关键 import。

### MW-WIN-002：快捷方式、用户身份、图标、杀毒和抢焦点连锁复发

- 证据：`本机已核验`；相关修复包括 `60f9289`、`53f8348`、`98de555`、
  `8bef57f`、`7ce6b3d`、`5193bb1`、`b992254`。
- 症状：OneDrive 重定向桌面上没有快捷方式、默认 Python 图标、`.lnk` 直接指向
  `pythonw.exe` 和长脚本参数而被火绒报 `HEUR:Trojan/LNK.Agent.b`，以及
  PowerShell 窗口瞬间出现并抢走输入焦点。
- 为什么会回来：每次只修一个入口，后来状态台、设置、更新器或快捷方式又建立新入口。
- 永久门禁：唯一 UI 入口必须是原生无控制台 launcher；真实升级后解析最终快捷方式，
  检查进程树，并用保留归档实际打开。

### MW-WIN-003：Unicode 修过以后，默认编码又从新边界回来

- 证据：`本机已核验`；相关修复 `3573ba7`、`8530ebd`、`0aafab1`。
  日文货币符号曾触发 GBK 错误；后来读取 active-root pointer 时若未显式 UTF-8，
  中文路径仍会乱码。
- 复发机制：只给发生故障的命令补 UTF-8，没有把编码提升为所有文本边界的不变量。
- 永久门禁：让中文、日文、日元/全角货币符号、emoji、空格和长路径完整通过
  CLI stdout/stderr、YAML/JSON、pointer、PowerShell、Python 和 Rust。

### MW-WIN-004：后台工作多次重新进入前台

- 证据：`本机已核验`。15 秒 Python polling 曾改成持久 watcher，v1.0.1 也降低了
  被动刷新频率；后来状态台和设置功能又生成可见 PowerShell，控件等待数秒。
- 复发机制：响应测试只看返回值，没有观察真实桌面入口的焦点、子进程和延迟。
- 永久门禁：刷新与设置只能使用缓存或进程内 API；断言不产生 console-host 后代，
  并对交互延迟设上限。

### MW-WIN-005：健康标记只能证明“存在”，不能证明“前进”

- 证据：`本机已核验`。曾有错误健康警告、SSE 启动竞态，以及 v2.11.4
  “进程存在即成功”的假阳性，随后才增加 runtime effect gate。
- 复发机制：进程、计划任务或文件存在比真实结果容易测试。
- 永久门禁：每个后台能力记录前后计数，并在限定窗口内证明它承诺的状态发生变化。

### MW-WIN-006：游标迁移每次只修一种旧形态

- 证据：`仓库已核验`。v2.11.1 修单源覆盖全局 projection，v2.11.2 修零新增时
  legacy convergence，v2.11.3 修 excluded/pre-v2.11 cursor，v2.12.1 修旧 Token
  ledger，v2.12.2 又修 ledger 重放冲突和父任务重叠。
- 复发机制：cursor identity、byte position、coverage、completion、Token projection
  演进时没有一套覆盖所有已发布格式的 fixture corpus。
- 永久门禁：每种旧格式迁移后连续重放两次，第二次必须字节、任务和计数均稳定。

### MW-WIN-007：语义自治多次在真正消债之前被宣布完成

- 证据：`本机已核验`。v1.7.1 恢复消失的 backfill runner；v2.4.5 前 due AI 会
  阻塞 collector 启动；v2.11.4/v2.11.5 才独立调度并做效果门禁；v2.12.3 又发现
  156 个任务因可修复 transcript/index drift 全被挡住，而且每五分钟先重建约
  47,000 条派生记录。
- 复发机制：单元测试验证“任务生成”，却没有把生产量级、漂移分类和真实队列下降
  放在同一个安装后测试里。
- 永久门禁：固定 source hash 的任务不受可重建派生漂移阻塞；单 tick 不得深度重建
  全归档；真实门禁必须看到 pending 下降、summary registry 增长。

### MW-WIN-008：发布流程通过，但真正交付的字节仍可能陈旧

- 证据：`仓库已核验`。曾修复 rehearsal UTF-8、v2.5.2 候选流程、v2.5.1
  checked-in native 版本、安装清单和同 SHA 发布顺序；后来发布仍先后碰到未知
  scenario ID，以及 squash merge SHA 尚无 push CI 就触发 release。
- 永久门禁：push 前校验场景目录；合并后等待 exact main SHA；包清单、native
  `--version`、状态台版本、三语文档和证据全部绑定该 SHA 后才能发布。

### MW-WIN-009：周期任务把全历史扫描伪装成增量

- 证据：`本机已核验`。whole-file recovery、过宽 CI、semantic raw pointer
  lookup，以及 v2.12.3 每 tick 的 47,000 条重建，都使成本随总历史增长。
- 永久门禁：记录每 tick 检查的行数、字节数和耗时；批次有硬上限；必须用生产量级
  归档副本彩排。

### MW-WIN-010：稀疏结构被空值补齐，审计读取到半事务状态

- 证据：`本机已核验`。v2.12.4 的真实二级摘要任务由10份不同年代的一级摘要组成；
  部分元数据字段不存在，另一些字段显式为 `null`。旧的表格打包器把两者都编码为
  `null`，本地往返哈希在调用AI前失败，任务重试4次后隔离。
- 同轮检查确认 heartbeat 未取得原生采集器使用的 `archive.lock`，可能在原文已追加而
  transcript、index、state 尚未完成时读取，并把瞬时差异缓存为一小时的派生漂移。
- 逃逸边界：原有测试只覆盖字段完全同构的两条记录；维护测试验证返回值，但没有证明
  审计发生在采集事务边界内。
- 永久门禁：无损表格必须用存在位图区分字段缺失与显式空值，并拒绝位图行数或列宽
  不匹配；原始记录和父摘要各保留一组稀疏字段回归。Heartbeat 必须自行持有统一归档锁，
  调用方不得重复包锁，真实 CLI heartbeat 必须在时限内返回；真实隔离载荷必须在不改
  原摘要的前提下完成往返并成功重排。
- 安装后效果：本机热更新候选后，19个原始归档文件前后哈希清单完全一致，heartbeat
  清除了全部派生问题；原隔离任务通过正式重排收据恢复，自动计划任务一次尝试生成
  `L2-000005` 并完成，待处理由修复前146降至141。
- 错误族：`MW-R05`、`MW-R07`、`MW-R08`、`MW-R10`。

### MW-WIN-011: stale repair dropped one open conversation and allowed round reuse

- Evidence: a pre-v2.12.4 unlocked heartbeat repair removed conversation
  `codex:019f7b17-1217-7aa1-b82c-b3a6828dbaa8` from `pending_rounds` while its
  raw user message remained authoritative. A later conversation then reused
  round 1133. The v2.12.4 transaction lock prevents new occurrences but its
  recovered-state audit still skipped unresolved records at or below the
  global completed-round watermark.
- Escape boundary: concurrent-conversation tests allocated unique rounds and
  closed both conversations; they did not preserve a legacy duplicate round
  where one conversation was complete and another remained open.
- Permanent gate: pending-round recovery must scan every positive-numbered raw
  record and pair completion by conversation ID plus round number. Python and
  Rust implementations must share this rule. A live repair may replace only
  derived `state.json`, must retain a rollback backup, and must leave existing
  raw bytes unchanged.
- Installed effect: the repaired Python audit restored both round-1133
  conversations to derived state, created a state rollback backup, returned
  heartbeat `status=ok`, and left quarantine at zero.
- Families: `MW-R05`, `MW-R07`, `MW-R10`.

### MW-WIN-012: duplicate-round regression asserted partial completion

- Evidence: v2.12.5 added the correct duplicate-round trigger but asserted that
  one completed conversation made the shared round globally complete. Live
  post-install audit exposed the remaining `completed_rounds_out_of_order`
  drift.
- Escape boundary: the test checked that the second conversation stayed
  pending, but did not require global completion to remain false until every
  user-bearing conversation in that round had a final answer.
- Permanent gate: for conversation-scoped records, a round is complete only
  when the non-empty user-conversation set is a subset of the final-answer
  conversation set. The regression must assert both the partial and fully
  completed states.
- Families: `MW-R05`, `MW-R08`, `MW-R10`.

### MW-WIN-013: recovery fix did not cover the native live-write path

- Evidence: v2.12.6 repaired recovered state, but the running native collector
  again advanced a shared round after the first conversation final and
  reintroduced the same derived drift.
- Escape boundary: recovery tests covered reconstruction only; they did not
  exercise the adjacent live append entry point that owns normal state writes.
- Permanent gate: Python and Rust live append paths must suppress global round
  completion while another pending conversation shares the same round number.
  The regression must close the first conversation, inspect partial state,
  then close the last conversation and inspect completed state.
- Families: `MW-R05`, `MW-R08`, `MW-R10`.

## macOS 事故账

### MW-MAC-001：不稳定 Python 身份使后台权限失效

- 证据：`Mac 已核验`。Mac 副本中记录了将硬编码或 Homebrew Cellar 版本路径改为
  LaunchAgent 和事务更新共用的稳定 Python 入口。
- 复发机制：解释器路径技术上存在，但升级后进程身份改变，Full Disk Access 不随之继承。
- 永久门禁：LaunchAgent 使用稳定入口；切换前证明候选采集；切换后验证真实归档写入。

### MW-MAC-002：日常更新错误依赖特权安装

- 证据：`Mac 已核验` 与 `仓库已核验`。v2.4.7 将日常更新改为验证后的用户空间
  transaction，只在首次安装和恢复时使用完整 PKG。
- 永久门禁：隔离候选、证明采集、原子切换、验证 collector/状态台；任一失败恢复旧
  Skill、plist 和进程。

### MW-MAC-003：合法的 macOS 包路径别名被过度拒绝

- 证据：`仓库已核验`。包展开位置和 Apple 固定的 `/var`、`/tmp`、`/etc`
  别名需要兼容，但不能因此放行任意 symlink。
- 永久门禁：在 macOS 原生检查包；只允许解析到精确 `/private` 目标的固定别名，
  不用 Windows 模拟替代。

### MW-MAC-004：状态台版本与回滚假定唯一工具形态

- 证据：`Mac 已核验`。Mac 端修改增加标准 `CFBundleShortVersionString` fallback，
  并在 `/usr/bin/ditto` 不可用时增加保留 symlink 的原位复制回滚。
- 永久门禁：同时测试标准和自定义 Info.plist 字段；分别执行一次 ditto 路径和 fallback
  路径的真实回滚。

## 跨设备结论

1. 跨平台需要“结果合同相同、平台实现分别验证”，不能要求实现文本相同。
2. 所有后台功能都必须有结果计数；进程存在只能作为诊断信号。
3. 所有持久格式都要保留全版本迁移 corpus，并验证第二次重放幂等。
4. 周期工作只能随新增债务增长，不能随总归档增长。
5. 后来功能只要建立了新入口，就自动继承该技术边界的全部旧回归用例。

## 当前未闭合证据与运行债务

- 2026-08-02 Windows 状态曾显示约 154 个 semantic-ready、2 个 running、
  1 个 quarantined，maintenance 为 stale/attention。v2.12.3 已证明普通一级摘要债务
  能继续下降；其中一个二级任务另由 v2.12.4 的稀疏字段缺陷导致隔离。安装后已完成
  正式重排和真实父摘要入库，该隔离债务关闭；其余摘要债务继续由有界后台批次处理。
- 当前联邦缓存不能证明 Mac Environment 已完整同步。没有最新 manifest 与 receipt
  对比前，不得声称 Mac 全部 Skill 和设置均已同步。
- federation 展示的 `last_sync_at` 与较新的 imported event 需要统一口径，不能只凭
  一个时间字段宣称跨设备数据已追平。

## 每次缺陷更新流程

1. 分配或复用 incident ID 与错误族。
2. 记录证据等级、平台/版本、精确触发器、逃逸边界、用户可见后果、修复提交和不确定项。
3. 加入原始触发测试，以及一个从相邻新入口进入的回归测试。
4. 后台、安装器、UI、同步必须加入安装后或生产量级的真实效果检查。
5. 同一变更更新本文件；只有去掉项目路径、权限和版本后仍成立的教训，才提升到本设备
   Skill 错题本。

## 未来版本机器门禁

从 v2.12.4 起，每个 `docs/work-contracts/vX.Y.Z.json` 必须包含
`defect_workbook` 对象：

- `preflight_receipt` 与 `preflight_sha256`；
- `completion_receipt` 与 `completion_sha256`；
- 非空 `applicable_families`；
- `project_workbook_updated` 布尔值；
- `original_triggers`，说明继承了哪些历史触发器。

两个收据必须存在且哈希匹配。修复类版本的 `project_workbook_updated` 必须为
`true`。这样发布门禁读取机器证据，不依赖当前对话是否记得本文件。
