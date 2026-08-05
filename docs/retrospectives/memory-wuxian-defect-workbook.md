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
