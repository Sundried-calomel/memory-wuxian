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

### MW-MAC-005：来源签名幂等键阻止重新生成的摘要任务重放

- 症状：旧语义任务已完成后，同一来源范围重新生成了新的持久化摘要任务；维护队列仍按来源签名认定为旧任务，导致新的 L2 一直留在待处理目录。
- 根因：幂等身份只描述语义来源，没有包含持久化摘要任务这一代的身份。
- 永久门槛：完全相同的旧 payload 必须复用；同一来源签名但不同 `summary_job_id` 的重放必须获得独立稳定队列项。维护器保持串行、有限批量，不得用并发掩盖积压。
- 回归：覆盖旧 payload 复用、新任务重放、批量上限 8、吞吐落盘，以及事务成功后退役旧启动项。
- Families: `MW-R05`, `MW-R07`, `MW-R09`, `MW-R10`.

### MW-MAC-001：不稳定 Python 身份使后台权限失效

- 证据：`Mac 已核验`。Mac 副本中记录了将硬编码或 Homebrew Cellar 版本路径改为
  LaunchAgent 和事务更新共用的稳定 Python 入口。

### MW-MAC-002：root 依赖探测误判并触发 PEP 668

- 症状：macOS PKG 已取得管理员授权，但 `postinstall` 以 root 身份探测
  PyYAML，忽略登录用户可用的 site-packages，随后用 `pip --user` 写入
  Homebrew 管理环境并被 PEP 668 拒绝。
- 根因：安装脚本把登录用户运行时与 root 安装器环境混为一体，且安装包
  没有离线依赖后备路径。
- 永久门槛：依赖探测必须以登录用户身份执行；缺依赖时只能使用 PKG 内置
  源码创建隔离运行时，禁止全局 pip、`--break-system-packages` 和安装时联网。
- 回归：静态断言无 pip 写入，并模拟用户态事务更新继续绕过系统 Installer。
- 复发机制：解释器路径技术上存在，但升级后进程身份改变，Full Disk Access 不随之继承。
- 永久门禁：LaunchAgent 使用稳定入口；切换前证明候选采集；切换后验证真实归档写入。

### MW-MAC-006：日常更新错误依赖特权安装

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

### MW-MAC-007：同尺寸来源改写与外层事务超时逃逸

- 证据：仅比较文件尺寸不能识别同尺寸 rollout 内容改写；外层更新等待时间短于内部采集器就绪与回滚窗口时，会提前判定事务失败。
- 永久门槛：完整来源游标持久化全文件 SHA-256；已记录哈希的同尺寸内容变化必须失败关闭。外层更新超时必须覆盖内部就绪、安装后检查和回滚余量。
- 回归：分别验证 mtime-only、同尺寸内容变化、旧游标哈希补录，以及外层超时大于完整内层事务窗口。
- Families: `MW-R05`, `MW-R07`, `MW-R10`.

### MW-MAC-008：只读胶囊被错误绑定到已读写入

- 症状：其他 Codex 对话在读取运行时记忆胶囊后尝试写入已读确认；沙箱拒绝归档锁或状态文件写入时，对话错误地请求用户授权、暂停历史检索或宣称 Memory无限 阻塞。
- 根因：去重水位被设计成持久化 ACK，并被 Skill 写成正常读取流程的必需步骤；只读恢复因此意外继承了写权限与归档锁依赖。
- 永久门槛：胶囊读取必须完全只读；触发点由最新轮次、利用率跨越和压缩遥测确定，并输出稳定 `refresh_id` 供当前推理上下文去重。正常规则不得要求 ACK、写权限或用户授权。旧 ACK 命令只能是不写入的兼容空操作。
- 回归：在持有主归档锁时重复生成同一胶囊，要求输出完全一致且不创建状态文件；旧 ACK 命令同时返回 `not-required`，并证明不写入。
- Families: `MW-R05`, `MW-R07`, `MW-R10`.

### MW-WIN-014：独立同步流未贯通原生加密合同

- 症状：`project-evidence-v1` 已能生成不可变包和独立导出账本，但真实云同步先因
  导出结果只返回 `bundle_sha256` 而通用传输层读取 `sha256` 失败，兼容字段后又被
  原生 envelope 拒绝未知的 `project-evidence-v1-bundle` kind。
- 根因：新流只分别验证了 Python 包导出、认证导入和既有 Environment 加密流，没有用
  新流自身贯通“导出结果 -> 通用传输 -> 原生 seal/open -> 跨流拒绝”的生产路径。
- 逃逸边界：单元测试直接调用项目证据 manager，绕过了通用云传输；原生测试只覆盖
  archive 与 Environment 两种 kind，所以三层各自通过仍不能证明新流可发布。
- 永久门槛：每个新增独立流必须返回通用传输标准字段，原生 helper 只能增加显式枚举，
  并同时验证正确往返、错误流拒绝、真实 bundle 字节哈希、目标节点绑定和实际加密发布。
- Families: `MW-R06`, `MW-R09`, `G03`.

### MW-REL-015：历史版本合同测试绑死当前产品版本

- 症状：升级产品版本后，多个 v2.11-v2.14 历史合同测试同时失败，唯一原因是它们
  都断言当前 `pyproject.toml` 必须等于旧版本；每次发布都需要机械重写历史测试。
- 根因：历史功能不变量与当前发布身份混在同一断言里，导致历史证据随新版本漂移，
  并在完整回归中淹没真正的二进制或行为失败。
- 永久门槛：历史合同只验证自身固定合同、文件和行为；对当前产品版本只能验证不低于
  已验证基线。当前 Python、Rust、文档和已打包二进制的精确相等由唯一版本合同负责。
- 回归：升级版本时不修改历史合同的固定版本身份；完整测试必须仍能发现未重建的
  collector、dashboard launcher 或 envelope 二进制。
- Families: `G05`, `MW-R09`, `MW-R10`.

### MW-WIN-017：状态台依赖漂移通过了虚假的启动器自检

- 证据：`本机已核验`。2.15.0 候选第一次事务切换时，原生启动器忽略
  `--self-check`、分离子进程且不输出 `status=ready`，安装器因此回滚到 2.14.5。
- 根因：2.15 候选从远端 2.14.5 分支演进，没有继承本机稳定化工作中已完成的状态台
  自检协议；功能测试验证了状态台代码，却没有验证事务安装器与候选启动器的协议配对。
- 永久门槛：候选运行时必须在停止旧运行时前验证 `pywebview`；切换后原生启动器运行
  无窗口状态台自检、传播非零退出，并输出可解析的 `status=ready`。普通启动还必须在
  观察窗口内检查子进程是否提前退出。
- Families: `MW-R01`, `MW-R03`, `MW-R05`, `MW-R06`.

### MW-WIN-023：读取时补入空默认字段阻断旧摘要云台账

- 证据：`本机已核验`。Windows 的 `archive-v1` 自 2026-07-31 起停在事件 35916；
  计划任务持续以代码 1 失败，手动同步报 `Immutable local artifact changed:
  summary:L1-000001`。全台账审计发现 108 个早期摘要同类漂移。
- 根因：早期已发送摘要记录没有 `policy_events`；新版读取器在内存中补入
  `policy_events: []`，联邦导出器却把读取投影当作原始不可变载荷重新哈希。
- 逃逸边界：策略字段迁移测试验证了本地读取，却没有用迁移前导出台账重放一次后续
  云导出。普通新归档测试从一开始就包含新字段，无法触发旧哈希冲突。
- 永久门槛：已存在的摘要事件若只因新增空 `policy_events` 默认值而漂移，导出器必须
  精确复现旧载荷；非空策略事件或任何其他字段变化仍失败关闭。回归必须同时验证兼容
  导出成功和真实内容变化仍被拒绝。
- Families: `MW-R05`, `MW-R06`, `MW-R09`, `G03`.

### MW-REL-024：Rust 功能验证通过但格式门未在提交前执行

- 证据：`仓库已核验`。v2.15.0 PR #68 的三平台候选均在
  `cargo fmt --check --manifest-path native-collector/Cargo.toml` 失败；功能测试和原生
  流绑定测试此前均通过，失败集中于状态台启动器的纯格式差异。
- 根因：增量验证覆盖了行为和合同，却没有执行 CI 中独立的 Rust 格式门。
- 逃逸边界：本地“相关测试通过”被误当作候选完整；同一处未格式化源码因此在 macOS、
  Ubuntu 和 Windows 重复失败。
- 永久门槛：任何 `native-collector/**/*.rs` 变更在提交前必须执行与 CI 完全一致的
  `cargo fmt --check --manifest-path native-collector/Cargo.toml`；若失败，先运行 `cargo fmt`
  并重新执行受影响测试，不能依赖编译或单元测试间接覆盖格式。
- Families: `MW-R06`, `G09`.

### MW-REL-025：分层候选通过局部测试但没有证明安装后的真实路径

- 证据：`本机候选已核验`。v2.16.0 初始实现把运行效果门的默认遥测位置写成
  `runtime/collector-status.json`，而生产采集器实际写入
  `imports/codex/collector-telemetry.json`；同时 macOS 安装事务可以在遥测尚未进入
  `ready` 时形成看似完整的生命周期收据。
- 根因：安装器、采集器与验证器分别测试了各自的局部合同，但真实入口使用的路径、
  就绪阶段和水位推进没有绑定成同一份安装后证据。
- 逃逸边界：文件存在、进程存在和局部单元测试均可通过，却不能证明安装后的启动所有者
  读取了正确候选、写入正确归档根并推进真实水位。
- 永久门槛：每个发布候选必须从正式安装入口启动，使用生产遥测路径，要求明确的
  `ready` 阶段，并在有界时间内证明来源水位与归档水位收敛；任一条件失败都必须回滚，
  不得以进程存活或退出码零替代。
- 回归：Windows 与 macOS 分别覆盖精确启动参数、缺失启动项恢复、遥测路径漂移、
  未就绪拒绝、真实水位推进和失败后的旧代恢复。
- Families: `G02`, `G06`, `MW-R03`, `MW-R05`, `MW-R06`, `MW-R10`.

### MW-REL-026：模块提取后历史检查仍绑定旧源码位置

- 证据：`本机候选已核验`。v2.17.0 将原生采集实现从 `main.rs` 提取到 `lib.rs` 和
  分层模块后，若干静态历史测试与发布脚本仍只读取 `main.rs`，因此把行为保持不变的
  新候选误判为缺失实现。
- 根因：验证器绑定了旧文件布局，而不是稳定的行为入口、模块所有权或编译产物。
- 逃逸边界：原生测试和字节合同能够通过，但布局敏感的历史门禁会在发布阶段才失败；
  反过来，单纯修改静态搜索也可能掩盖真正的行为回归。
- 永久门槛：模块化重构前必须清点全部消费者；行为门验证真实入口和产物，架构门验证
  依赖方向，只有确实属于源码布局合同的检查才允许绑定具体文件。
- 回归：提取后同时执行 Rust 测试、Python/native 字节一致性、架构合同、历史发布检查
  和正式启动器测试。
- Families: `G03`, `G05`, `G09`, `MW-R06`, `MW-R09`, `MW-R10`.

### MW-REL-027：旧平台夹具未承接新增的安装效果合同

- 证据：`GitHub CI 已核验`。v2.16.0 PR #70 的 macOS 平台合同在生产代码新增
  content-free 水位探针后失败；旧 `wait_for_collector` 测试桩始终返回固定水位，六个
  原本验证切换、锁释放、幂等和回滚的测试因此在效果验证阶段提前失败。
- 根因：生产入口和新测试已要求 `minimum_watermark`，但历史平台夹具仍模拟旧版固定
  遥测合同；Windows 本地完整套件因平台条件跳过这些 macOS 测试，未能提前暴露漂移。
- 逃逸边界：新合同的聚焦测试和本机完整套件均通过，但真实 macOS CI 运行历史夹具时
  才发现模拟边界未升级。
- 永久门槛：新增跨平台安装、回滚或遥测合同后，必须清点并更新所有历史平台测试桩；
  测试桩应回显调用方要求的水位、代际和 PID 约束，而不能继续返回与新合同无关的固定值。
- 回归：macOS PR 平台合同必须覆盖正式切换、锁释放、二次安装、后置失败回滚和新格式
  就绪拒绝；任一测试不得在目标行为之前因陈旧夹具失败。
- Families: `G03`, `G05`, `G09`, `MW-R05`, `MW-R06`.

### MW-REL-028：watcher-first 启动后重建基线会遮蔽游标债务

- 证据：`独立审查与本机回归已核验`。v2.17.0 已在启动枚举前建立 watcher，但事件循环
  又以启动追赶后的文件状态重建 `known_stamps`；若 rollout 在追赶期间追加，排队事件到达
  时当前时间戳与新基线相同，旧逻辑会得到空的 `changed_paths`，从而把仍落后的游标留到
  下一次文件变化。
- 根因：事件循环把文件元数据差异当成是否需要同步的唯一权威，而持久游标才是已经归档到
  哪个字节的权威；watcher 就绪顺序正确并不等于启动窗口内的事件一定被消费。
- 逃逸边界：静态调用顺序、一次性采集和普通 watcher 事件测试均可通过，只有在“watcher
  已建立、启动追赶未结束、同一 rollout 再追加、随后基线刷新”的交错下才会漏采。
- 永久门槛：事件循环每次得到当前 rollout 集合后都必须使用持久游标判定同步债务；时间戳
  只用于活动与水位遥测，不得取代 cursor。模块提取后的无 AI 执行门必须扫描 Capture Core
  全部生产模块，不能只扫描原来的单一源码文件。
- 回归：Rust 测试必须模拟刷新后时间戳完全相同但 cursor 落后的状态，并证明该 rollout
  仍被选择；Windows/macOS 事件循环都必须调用同一游标债务选择器；发布彩排扫描全部
  Capture Core 生产 Rust 文件。
- Families: `G03`, `G05`, `G07`, `G09`, `MW-R03`, `MW-R05`, `MW-R06`.

### MW-REL-029：事件循环隔离了坏来源，但启动批次仍首错即停

- 证据：`独立审查与本机回归已核验`。v2.18.0 候选的事件循环已逐来源捕获错误，
  启动预筛选和启动同步却仍使用聚合 `Result`；首个消失或损坏的 rollout 会阻止后续
  有效来源进入归档，并阻止持续 collector 进入事件循环。
- 根因：故障隔离只应用在新增的事件入口，没有提升为所有来源枚举和同步入口共享的合同。
- 永久门槛：来源 metadata 错误必须保留为待同步候选，由逐来源同步记录错误并继续；
  若失败发生在事务开始后，处理下一来源前必须先清偿全局 recovery debt，修复失败则停止；
  启动批次、事件循环和显式单次同步均须覆盖“首个来源失败、第二个来源成功”的相邻用例。
- Families: `G03`, `G05`, `G09`, `MW-R03`, `MW-R05`, `MW-R06`.

### MW-REL-030：Capture Core 新模块未自动进入 no-AI 门

- 证据：`独立审查与本机回归已核验`。`store/wal.rs` 已注册为生产模块，但 Rust、Python
  历史合同和发布彩排的手写模块清单中至少两处遗漏它，导致新模块可绕过 no-AI 扫描。
- 根因：模块提取后的门禁仍依赖多份手写清单，未校验清单与生产源码集合相等。
- 永久门槛：所有 no-AI 入口必须包含 `store/wal.rs`；发布合同还必须枚举生产 Rust 文件，
  扣除明确属于 dashboard/envelope 的非 Capture Core 二进制后，与门禁清单做集合相等断言。
- Families: `G03`, `G09`, `MW-R06`, `MW-R09`, `MW-R10`.

### MW-REL-031：WAL 先追加后压缩可越过硬上限并自锁

- 证据：`独立审查与本机边界测试已核验`。旧实现先把事件持久化，再在超过 2 MiB 时读取
  并压缩；若追加后越过 4 MiB，读取门会先拒绝该文件，collector 后续启动也无法恢复。
- 根因：4 MiB 只被实现为读取上限，没有成为追加前不变量；恢复、压缩和容量边界只做了
  静态字符串检查，没有构造真实 2–4 MiB WAL 或 Store 重启。
- 永久门槛：每次追加前计算预计长度，必要时先原子压缩再复核，任何写入落盘前都必须证明
  不越过 4 MiB；只有 `NotFound` 可解释为新 WAL 的零长度，其他 metadata 错误必须失败关闭；
  启动恢复结束也压缩既有 WAL。动态测试必须覆盖恢复幂等、真实重启、2 MiB 压缩和近
  4 MiB 追加。
- Families: `G05`, `G06`, `G09`, `MW-R05`, `MW-R06`, `MW-R10`.

### MW-REL-032：幂等安装把新效果水位误当成生命周期身份漂移

- 证据：`GitHub macOS CI 与可移植回归已核验`。v2.18.0 旧候选第二次安装会创建新的
  content-free 效果探针；若两次调用跨过一秒，新遥测水位与首次持久化清单不同，旧逻辑
  因逐字段全等比较随机拒绝同一代际，处于同一秒时则碰巧通过。
- 根因：代际、归档根、启动命令和 owner 等稳定身份，与 PID、水位、更新时间等运行快照
  被压进同一个相等合同；真实效果验证必然更新后者。
- 永久门槛：幂等重入先严格核验稳定身份，再用已经通过效果门的新遥测刷新生命周期清单；
  回归必须使用显式不同的旧/新水位，不能依赖测试是否恰好在同一秒完成。
- Families: `G05`, `G09`, `MW-R05`, `MW-R06`, `MW-R10`.

### MW-REL-033：跨平台黄金夹具绑定宿主路径、时区与进程名

- 证据：`GitHub PR #73 Ubuntu/Windows CI 与本机定向回归已核验`。同一 scheduler
  黄金测试在本机通过，却在 Ubuntu 因 `Path` 使用正斜杠、在 Windows Runner 因本地时区
  和 8.3 短路径差异失败；Environment 生命周期测试还把未解析输入路径与生产 Owner 返回的
  canonical resolved path 直接比较。合并后的 macOS 主线测试进一步证明，CLI 解析器默认从
  `sys.argv[0]` 推导程序名时会把 `python -m unittest` 写进精确帮助文本，而其他 Runner 可
  因启动方式不同偶然保留 `memory-wuxian`。
- 根因：R8 测试基础设施把合成 Windows 路径交给宿主 `Path` 解释，并让固定时间再次转换到
  Runner 本地时区；期待值与生产值没有在同一 canonical path 边界构造，公开 CLI 的程序名
  也没有在解析器 Owner 内固定。
- 永久门槛：合成 Windows 黄金夹具必须使用与宿主无关的 Windows lexical path；固定时区时间
  不得被无参数 `astimezone()` 改写；生命周期期待值必须使用与生产 Owner 相同的 resolved
  canonical target；公开 CLI 解析器必须显式固定稳定程序名，不得继承测试运行器或宿主进程
  的 `argv[0]`。精确 XML、命令、帮助文本、收据和 SHA-256 断言不得放宽，并须在 Ubuntu、
  Windows 与 macOS CI 运行。
- Families: `G05`, `G09`, `MW-R03`, `MW-R06`.

### MW-REL-034：采集器计划任务 XML 绕开共享身份 Owner

- 证据：`Windows 2.19.0 官方安装器与事务 journal 已核验`。候选采集器能够启动，
  但 `schtasks /Create` 在真实 Windows 上拒绝任务 XML；同仓库 `platform_scheduler`
  已生成 `<UserId>`，采集器安装入口却重复拼装 `<Principal>` 并漏掉该必需身份；修复
  身份后，Windows 真实解析器还证明 `RestartOnFailure/PT30S` 低于允许的 `PT1M` 下限。
- 根因：R1-R8 完成了共享 Platform 调度器，却没有把采集器生命周期入口的旧 XML
  构造器纳入同一身份 Owner；FakeRunner 只回读 XML，没有让 Windows Task Scheduler
  解析它，因此 mock 通过替代了真实安装效果。
- 永久门槛：所有 `InteractiveToken` 任务必须从共享 Platform 身份 Owner 取得明确
  `UserId`，并把它纳入安装后 XML 等价核验；任务注册失败必须在回滚 journal 保留
  按本机编码可读的原生 stderr；重启间隔必须符合真实 Windows schema；Windows 发布
  候选必须至少完成一次旧版到候选版的真实安装、任务查询、
  唯一 Owner 和活动水位推进检查，不能只验证打包或 FakeRunner。
- Families: `G03`, `G05`, `G09`, `G11`, `MW-R06`, `MW-R10`.

### MW-REL-035：事务探针嵌套完整目录树触发 Windows 长路径失败

- 证据：`Windows 2.20.0 真实 UAC 彩排已核验`。联邦节点资源在 `prepare` 阶段把完整
  `archive/federation` 与副本目录树放入已经很深的事务备份目录，尚未执行任何正式
  `apply` 就在创建 `token-usage-snapshots` 时触发 `WinError 206`；同一探针生成的
  `node.json` 还会把临时副本路径写入预期节点身份。
- 根因：事务设计把用于计算预期字节的短生命周期探针误当成了持久目录证据，导致探针
  路径长度叠加事务、资源和目标目录层级；同时没有把临时生成上下文与正式目标身份分开。
- 逃逸边界：短路径单元测试、模拟事务和 UAC 之前的全部静态门均可通过，只有真实用户
  临时目录、事务 UUID 与完整资源层级组合后才越过 Windows 目录创建边界。
- 永久门槛：用于计算安装预期字节的探针必须位于短生命周期系统临时目录，退出
  `prepare` 前删除；可恢复证据只保存哈希绑定的小型字节载荷，不持久化完整探针树；
  `node.json` 必须绑定正式副本根，禁止携带探针路径。所有需要展开完整候选树的资源必须
  使用固定深度的 `%ProgramData%` 暂存根，不得继承 manifest 所在目录；正式安装和彩排根
  必须分离。Windows 回归必须使用足以让旧实现越界的深事务备份根，并证明 `prepare`
  不写正式归档、证据可序列化、正式节点不含探针路径，且两个正式入口都调用短根 Owner。
- Families: `G05`, `G06`, `G09`, `G11`, `MW-R06`, `MW-R10`.

### MW-REL-036：本机通过的字节收据与路径夹具在 CI Runner 漂移

- 证据：`GitHub PR #75 Ubuntu/Windows CI 已核验`。Ubuntu 对 S01 治理收据计算的
  SHA-256 与 Windows 工作区冻结值不同；Windows checkout 又把 capability registry
  转成 CRLF，使所有 workflow fixture 在 admission 阶段提前失败。其余 Windows 失败
  来自 Runner 暴露的 8.3 短路径与生产 Owner 返回的长规范路径直接比较，以及图标夹具
  在目标“启动器哈希漂移”断言之前先触发了图标哈希门。
- 根因：精确字节收据没有声明 Git checkout 的换行策略；路径测试比较了宿主字符串表示，
  没有在生产 `resolve` 边界两侧构造期待值；负向夹具没有先满足目标断言之前的所有前置门。
- 逃逸边界：本机完整套件使用创建收据时的工作树字节和长路径用户目录，全部通过；只有
  Ubuntu LF checkout、Windows `core.autocrlf` 与 Runner 8.3 临时目录组合后才暴露漂移。
- 永久门槛：所有被原始 SHA-256 收据绑定的文本必须在 `.gitattributes` 声明精确 checkout
  字节策略；跨平台路径断言必须保留精确路径语义，但比较双方都经过生产 Owner 使用的
  canonical/resolve 边界；每个负向夹具必须显式满足目标失败点之前的全部独立完整性门。
  S13 必须在 Ubuntu、Windows 与 macOS 的真实 checkout 上运行完整相关套件，任一失败
  都不得生成可安装候选。
- Families: `G04`, `G05`, `G09`, `G11`, `MW-R02`, `MW-R06`, `MW-R10`.

### MW-REL-037：组合断言与原始恢复文件使安装失败既不可定位又泄露权限材料

- 证据：`GitHub S09 run 33239128752 已核验`。完整 Inno 生产链到达
  `dashboard-shortcut` 后，目标、工作目录、图标和目标存在性被压成一个布尔断言；
  脚本先恢复或删除快捷方式再抛出通用错误，因此产物无法指出具体失败字段。CI 随后递归
  复制完整事务目录，把内部 journal 的 `transaction_token.secret` 和 broker request 的
  nonce 一并上传为证据。
- 根因：恢复所需的私有状态与发布所需的诊断证据没有分层；组件 Owner 没有在回滚前提交
  结构化断言，CI 也没有封闭字段投影和禁止键检查。
- 永久门槛：每个复合效果断言必须逐项记录 bounded `expected`/`observed`，在任何补偿动作
  之前原子写入事务 failure；事务 Owner 在回滚后只追加 rollback 结果。CI 只能导出合同
  列出的 journal、mutation 和 broker 投影，nonce 只允许导出存在性布尔值；
  `transaction_token`、`secret`、nonce 值、凭据、环境转储、任意异常正文、stdout/stderr
  和 traceback 均不得进入产物。未知失败只导出异常类别和受限源码位置。首次结构化证据
  尚未指出具体字段前，不得修改被测行为。
- Families: `G02`, `G05`, `G09`, `G11`, `MW-R01`, `MW-R05`, `MW-R06`, `MW-R10`.

### MW-REL-038：Unicode 快捷方式终态被 COM 直接复开时字段静默为空

- 证据：`GitHub S09 run 33252150380` 中，ASCII 临时快捷方式的目标、工作目录和图标
  均通过生产检查；同一文件移动为 `Memory无限状态台.lnk` 后，由 WScript.Shell 直接按
  Unicode 终态路径复开时三个字段均变为空，事务因此正确回滚。随后在 Codex 隔离
  Python 启动的 PowerShell 中复现；第一版修复又暴露该隔离环境没有可用
  `Get-FileHash` 模块。
- 根因：安装后核验把“最终文件字节正确”与“当前 COM 路径绑定能够直接读取 Unicode
  文件名”混成同一个条件，并隐含依赖完整 PowerShell 模块搜索路径。生产创建路径本身
  已采用 ASCII 临时文件后原子移动，核验路径却没有沿用同一兼容边界。
- 逃逸边界：静态脚本检查、ASCII 临时链接核验和普通 PowerShell 会话全部可通过；只有
  GitHub Windows Runner 或 Codex 隔离 Python 启动的 PowerShell，加上最终中文文件名，
  才会同时暴露 COM 路径问题与模块依赖问题。
- 永久门槛：最终快捷方式核验必须先逐字节复制到同目录 ASCII 临时投影，使用模块无关的
  .NET SHA-256 同时验证源文件与投影完全相等，再只从投影读取 COM 字段；无论成功失败都
  删除投影。回归测试必须由实际隔离 Python 启动生产 PowerShell 安装脚本，检查中文最终
  路径、精确字段、源/投影哈希和无残留；禁止重新引入对最终 Unicode 路径的直接 COM
  复开，也禁止依赖 `Get-FileHash` 等可选模块。
- Families: `G05`, `G11`, `MW-R06`, `MW-R10`.

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
