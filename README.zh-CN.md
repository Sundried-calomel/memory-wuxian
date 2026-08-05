# Memory無限

> **2.15.0：**新增独立加密的 `project-attachment-v1` 大型项目附件流。它使用
> 4 MiB 精确分块、SHA-256 清单、可续传交付和原子验证重建；原始文件继续保持
> 普通可读，既有归档、环境和项目证据流均不改变。

> **2.14.5：**补齐项目证据云同步合同：新增绑定独立流的原生加密类型和传输兼容的 bundle 哈希，并强制验证跨流解封失败。

> **2.14.4：**运行时上下文胶囊改为完全只读且不需要已读确认，刷新直接依据最新的确定性遥测转变触发，旧 ACK 命令仅保留为不写入的兼容空操作。**2.14.3** 修复 L2 语义任务重放，将每五分钟的有界批量提高到 8，模型调用最多 3 路并发而归档写入保持串行，将完整恢复审计移出五分钟热路径，在操作台展示分阶段耗时，并仅在事务安装成功后退役旧 macOS 语义回填启动项。**2.14.2** 对同尺寸 rollout 改写执行字节哈希校验，并让 macOS 用户级更新事务完整覆盖采集器就绪窗口。**2.14.1** 修复 macOS 安装，并提供离线、隔离的 PyYAML 后备运行时。**2.14.0** 新增本机 Project Evidence Owner，通过有界、无需模型的刷新维护显式封闭文件清单。**2.13.0** 新增显式、不可变的项目证据包及独立加密的
> `project-evidence-v1` 流，并保留 **2.12.7** 的实时归档修正：Python 与原生入口都会等待共享旧轮次号的最后一个对话
> 闭合后，才推进全局完成状态。

> **2.12.6：**共享同一旧轮次号的多个对话，必须各自都有最终回答后，该轮次才算
> 完成，使恢复后的完成状态与待闭合状态保持一致。

> **2.12.5：**旧版竞态遗留的重复轮次现在会按对话扫描全部不可变原文，分别恢复
> 尚未闭合的轮次。修复只重建派生状态，不改写原始对话。

> **2.12.4：**语义无损载荷现在会区分“字段不存在”和显式 JSON `null`，
> 含不同旧版元数据的一级摘要可以继续生成上级摘要。Heartbeat 审计现在与
> 原生采集器共用归档锁，不再把采集批次写到一半的瞬时状态缓存成派生投影漂移。

> **2.12.3：**Codex 持续归档时，即使 transcript、索引或 state 出现可重建的
> 短暂漂移，后台语义摘要也会继续自动追赶。没有显式恢复债务时，最近 24 小时内的
> 深度恢复结果会被复用；每份冻结来源仍在写入前校验 SHA-256，原始历史完整性错误
> 仍然硬阻断。

> **2.12.2：**每日柱状图的加粗日期恢复到基线下方，本机与全设备柱使用相同宽度，
> 蓝绿配色提高了对比度。Token 账本回填期间，原生恢复会独立保留已归档消息水位，
> 不再重复写入已归档事件。待处理父摘要会预占子摘要；历史重叠任务仅以 SHA-256
> 收据隔离，不修改原始历史或已持久化摘要。

> **2.12.1:** 原生采集器在升级时会从保留的 rollout 重新构建 format-v2 每日 Token
> 账本，不再因已有 format-v1 派生账本而中止采集。原始 rollout 与追加式记忆记录保持不变。

> **2.12.0：**`daily_metrics.py` 在状态台加入本机与全部 trusted synchronized devices
> 的双层每日柱状图，可切换消息和 Codex-reported Token、查看逐设备明细、同步过期提示，
> 并统一按 `Asia/Tokyo` 划分日期。federation protocol v2 同步去路径、不可变的 Token
> 台账修订，同时保留 protocol v1 读取兼容。缺少远端遥测时会明确显示覆盖不完整，
> 不会用字符估算替代，也不会声称这是账户全局总量。
>
> **2.11.6：**Windows 升级现在优先信任经过验证的安装包 Skill 根目录，仅在该目录
> 无效时才查询进程 SID，避免 Codex 沙箱用户路径污染桌面快捷方式。快捷方式安装会
> 原子写入并回读核验最终目标、工作目录、图标、参数和启动配置。安装后实际效果门禁
> 会拒绝“文件虽然存在、却指向错误用户目录或不存在程序”的快捷方式。
>
> **2.11.5：**后台健康现在必须由实际效果证明，不能只看进程是否运行。原生采集器
> 不再启动或等待 AI；独立维护调度器负责创建二级及以上摘要任务、安全修复派生索引
> 空洞，并显式报告永久债务。语义索引绑定当前原始归档水位，陈旧时必须关闭语义路径
> 或以 `semantic-index-stale-keyword-fallback` 明确降级到关键词检索。中断的备份会清理
> 自身临时目录，云同步等待与部分失败不再
> 报成功，升级只补齐缺失配置而不覆盖用户值；`runtime_effect_gate.py` 会拒绝隐藏回退、
> 陈旧水位、孤儿备份及虚假健康状态。
>
> **2.11.4：**持续追平现在可以跨安装和升级保留。原生采集器把最早覆盖边界保存到
> `collector-activation.json`，以有界批次流式读取仍保留的 rollout，从持久游标续跑，
> 并生成 `coverage-status.json`。如果采集在原始追加和派生状态提交之间中断，下一次原生
> 续传必须先完成确定性的 `heartbeat --repair`。`install_maintenance_supervisor.py` 安装一个隐藏的
> 五分钟维护任务来运行 `maintenance_supervisor.py`，因此 Codex 关闭后机械维护与备份债务
> 仍会继续；语义债务在 Codex 可用时恢复。超大任务通过哈希绑定的 `semantic_plan.py`
> map-reduce 路径处理，每次实际提示词都低于 `900,000` 字符和 UTF-8 字节。
> 状态台分别展示覆盖、机械维护、语义摘要和完整备份债务，并把可恢复积压标记为
> `catching-up`。
> 全局覆盖状态只允许由完整激活范围刷新；单个 rollout 的增量事件不能覆盖全部来源视图。
> 无新增内容的成功核验也会收敛旧游标的身份与完成元数据，避免零字节债务永久残留。
> 这次一次性收敛也覆盖旧版已排除的子任务/exec 游标，但不会把其内容导入顶层记忆。
> Windows 语义运行时探针现在会执行展开后的 Codex 绝对路径，不再把带 `~` 的原字符串
> 交给操作系统。语义运行时阻塞时，状态台会显示脱敏原因。后台执行器发布前必须用
> 合成内容在真实计划任务身份下完成闭环，证明待处理 `1 -> 0`、摘要注册表 `0 -> 1`。
>
> **2.4.6：**这个稳定版增加了跨设备语义运行时合同、显式本机 E5 实现，
> 并把首次语义建库的原文定位改为线性扫描。它同步统一接口和固定依赖，
> 不复制平台运行时、模型缓存或语义索引。
>
> Windows v1.7.8 安全说明：桌面状态台快捷方式现在只指向无控制台的原生启动器，
> 且不携带命令行参数。安装器把已验证的 Python 运行时与活动归档路径写入本机
> `.codex` 配置，不再创建直接以长参数调用 `pythonw.exe` 和脚本的快捷方式。
> 打包入口：`memory-wuxian-dashboard-launcher.exe`；快捷方式策略：
> `no command-line arguments`。
> Windows v1.7.9 还会通过当前 Windows SID 解析真实用户目录，避免隔离安装环境
> 把启动目标重新写成 `CodexSandboxOffline` 路径。
> Windows v1.7.10 在调用 Python 时保留已验证的普通 Windows 路径，避免中文路径
> 被转换为扩展路径后导致 `pythonw.exe` 立即退出。
> Windows v1.7.11 移除状态台启动和设置读取中的可见控制台子进程，先显示持久
> 快照、再后台刷新，并为变化指标播放轻量动画。Windows 安装或升级默认重建
> 原生桌面状态台快捷方式。直接复制 Codex Skill 没有传统安装向导，首次启用时
> 会运行随附的环境检查和快捷方式安装流程。
> Windows v1.8.0 进一步移除遗留的 PowerShell 采集循环，采用无控制台直接
> 启动，并加入事件驱动刷新及项目、来源、设备筛选。
> 对应实现契约为 `/api/events`、`project-filter`、`source-filter` 和
> `device-filter`。发布前必须按 `references/release-rehearsal.md` 运行
> `scripts/run_release_rehearsal.py` 并保留逐项证据。

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Memory無限 是一个基于文件的 Codex Skill，用于在活动上下文窗口之外建立持久、分层且可验证的对话记忆。

可安装的 Skill 标识符为 `memory-wuxian`；`Memory無限` 是项目名和显示名称。系统以精确原始记录作为历史权威来源，以摘要作为导航，并在把历史陈述视为已验证事实前回到原文核对。

## 功能

- 带时间戳和 SHA-256 完整性字段的只追加 Markdown 对话记录
- 每个对话一份完整且自动更新的 Markdown 全文
- 并发任务中按对话隔离的待完成轮次与回复关系
- 按对话隔离的一级摘要和更高等级摘要
- 来源感知的层级审计：一级检查原文范围，高层检查直接子摘要身份
- 每个对话独立的消息、时间线、概念和摘要索引，以及全局路由索引
- 在完成 5 轮对话或达到 20,000 个可见字符后由脚本判定摘要边界
- 仅在完整对话轮次触发摘要时临时调用 AI
- 在设定轮次、利用率或压缩阈值后执行有界的运行时上下文刷新
- 先查索引、再回到原文验证的检索流程
- 只追加的策略事件，以及显式修订、撤回和重申演进链
- 防止已明确废弃规则被当作当前规则的 `current-policy` 检索模式
- 预览优先的状态与索引恢复
- Heartbeat 验证、维护与修复模式
- 使用稳定来源 ID 和逐会话游标增量解析 Codex rollout
- 按对话持久化 Codex 报告 Token 用量，并支持安全处理计数器重置的历史回填
- 通过 macOS 原生 LaunchAgent 或 Windows 计划任务进行事件驱动同步
- 一份带 SHA-256 清单和只追加备份日志的最新桌面验证快照
- 一份用于重建派生文件的最新工作区恢复备份
- 使用增量包、产物账本游标和跨设备检索的联邦只读副本
- 并行支持 SSH 与加密云文件夹联邦传输
- 用于跨设备验证并统一规则与 Skill 的独立 Environment Registry
- 面向 ChatGPT 官方导出 ZIP 和 `conversations.json` 的实验性本地适配器
- 无数据库依赖、可直接检查的文件布局

## 安装

### 单文件安装包

从最新 GitHub Release 下载对应操作系统的安装包：

- macOS：`MemoryWuxian-<version>-macOS-universal.pkg`
- Windows：`MemoryWuxian-<version>-Windows-x64-Setup.exe`

状态台会先显示浏览器本地保存的最近一次成功响应，再使用经来源验证的持久化统计快照。档案未变化时无需重新扫描全部原始历史；快照过期或损坏时会从权威档案自动重建。可选的本地成就系统记录档案大小、档案上下文和纯消息 Token 估算、Codex 报告累计用量、对话深度、项目增长、摘要层级及原文验证检索。

打开安装文件后，Skill 会安装到当前用户的 Codex 目录，初始化 `Documents/MemoryWuxianArchive`，并启用持续 Codex 采集。重新安装或升级会保留现有配置和档案。卸载会移除程序及后台集成，但保留对话历史。公开构建默认没有代码签名，除非发布流程配置了平台签名凭据，因此操作系统可能要求手动确认安全提示。

### Codex Skill 安装器

从 GitHub 目录安装后重启 Codex：

Skill ZIP 校验只在 `/var`、`/tmp` 或 `/etc` 解析到其准确的 `/private` 目标时接受固定 macOS system path aliases；其他包路径链接和 junction 仍会被拒绝。

```text
$skill-installer install https://github.com/Sundried-calomel/memory-wuxian
```

手动安装时，将仓库放到：

```text
~/.codex/skills/memory-wuxian
```

## 快速开始

先阅读 [`SKILL.md`](SKILL.md)。真实对话历史应使用仓库外部的档案根目录，避免源码检出或 Skill 更新与私人记忆数据混在一起。

官方安装包会注册每日稳定版本检查。更新器忽略分支、草稿和预发布版本，同时下载平台安装包及其 SHA-256 文件；校验和或文件名不匹配时拒绝更新。Windows 会在下次登录时静默安装已验证更新。已有安装的 macOS 会只展开已验证 PKG 中的 Skill payload，并运行具备自动回滚的用户级事务；不会打开系统安装器，也不需要管理员密码。完整 PKG 仅保留给首次安装和恢复。使用 `python scripts/install_auto_update.py --uninstall` 可关闭检查。

Windows 每次安装或自动升级都会保留
`~/.codex/memory-wuxian-active-root.txt` 指定的真实档案，检查或安装原生窗口
依赖，并使用当前验证通过的 Python 原子重建桌面
`Memory无限状态台.lnk`。因此 Codex 内置运行时升级后，不会继续引用已经失效的
旧 `pythonw.exe` 绝对路径。安装器从 Skill 的实际安装路径推导真实用户目录，
因此桌面客户端的隔离 `USERPROFILE` 不会把 collector 或快捷方式重定向到沙箱
档案。

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill
python3 scripts/memory_cli.py --root "$ARCHIVE" token-usage-backfill --apply
python3 scripts/memory_cli.py --root "$ARCHIVE" status
python3 scripts/memory_cli.py --root "$ARCHIVE" backup
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
python3 scripts/memory_cli.py --root "$ARCHIVE" retrieve --query "摘要触发规则" --mode current-policy
```

持续采集本身不调用模型。只有完整对话轮次达到配置阈值后，脚本才创建锁定来源范围的摘要任务。一次性语义 worker 随后以临时模式调用已认证的 Codex CLI，导入受约束的 JSON 摘要后退出。

## 运行时上下文刷新

Memory無限 可在不新建替代任务的情况下，定期把压缩后的历史恢复到持续进行的 Codex 任务中。`context-refresh-status` 检测最新完成轮次里程碑、上下文利用率刚跨过阈值或最新一次压缩事件；需要刷新时，`context-capsule` 选择最高且有用的语义摘要层级，隐藏已被覆盖的子摘要，加入少量近期对话尾部，并生成带稳定 `refresh_id` 的临时派生上下文。当前推理上下文已经包含同一 ID 时直接跳过；压缩使该 ID 离开上下文后可以重新加载。读取完全只读，不需要已读确认，也不需要任何带权限的写入。已弃用的 `ack-context-refresh` 仅为旧调用方保留为不写入的兼容空操作，正常运行不得调用。

胶囊预算根据模型上下文窗口计算，默认占 1%，软上限 3,000 Token，绝对上限 10,000 Token。胶囊只是导航上下文，不是历史权威来源；事实仍需回到只追加原文验证，生成的胶囊也不得作为新来源消息归档。可复用的工作区 `AGENTS.md` 规则位于 `agents/` 和 `templates/`。

## 策略演进

一级摘要可把原文中明确出现的策略事件记录为 `adopted`、`revised`、
`withdrawn`、`reaffirmed`、`proposed` 或 `uncertain`。只有修订或撤回事件在
同一作用域内精确引用此前有效陈述时，才会替代旧规则；时间较新本身不构成
替代依据。策略索引属于可重建派生文件，原始对话和既有摘要保持不变。

检索可能变化的操作规则、默认值或策略时，使用
`retrieve --mode current-policy`。它会返回策略演进链、恢复对应原文，并同时
检查较新的匹配原文。在该功能加入前生成的旧摘要不会自动拥有策略事件；
尚未单独重分析时，命令会明确提示“未匹配显式演进链”，不会静默把早期陈述
当作当前规则。

## 本地状态台

在 macOS 上，每次安装或升级 PKG 都会重新生成并覆盖
`~/Desktop/Memory無限操作台.app`。原生 WebKit 启动器读取
`memory-wuxian-dashboard-launcher.json`，因此会使用当前 Python、Skill
和已保留的活动档案路径，不在程序中写死某一台电脑的路径。
`install_dashboard_app_macos.py` 会验证应用版本、签名、可执行文件哈希、
配置路径和启动器自检。凡是影响状态台的发布，只有在桌面应用已经被覆盖并
成功打开后才算完成。

Windows 可用原生应用窗口启动本地状态台。它使用已安装的 Microsoft Edge WebView2、随包提供的 Memory Wuxian 图标，并在没有浏览器边框的窗口中保留完整界面：

```powershell
python scripts/memory_dashboard.py `
  --root "C:\path\to\memory-wuxian-archive" `
  --config "C:\path\to\memory-wuxian\config.yaml" `
  --window
```

如果环境检查提示缺少开源 `pywebview`，运行一次 `scripts/bootstrap_windows.ps1 -InstallMissing`。窗口支持持久化的中文、英文和日文界面，默认每 30 秒静默刷新，并显示各对话的 Codex 标题、消息、完成轮次、摘要等级、每日归档量、待生成摘要、已归档可见来源字符以及明确标注的档案 Token 估算。字符统计包含用户与可见助手对话，不包含生成摘要。档案 Token 使用兼顾 CJK 的大小启发式估算，既不是计费使用量，也不是摘要生成消耗。每个对话还显示最近一次模型请求 Token 与模型标称上下文窗口的比例；该请求可能包含指令、工具、推理和输出，因此比例可能超过 100%，不能视为精确占用率或剩余上下文。

Windows 安装器会在每次首次安装或升级后运行
`scripts/install_dashboard_shortcut_windows.ps1`，使用当前 Skill 路径、有效档案、
内置图标和验证通过的 `pythonw.exe` 重新创建
`Memory无限状态台.lnk`。卸载时只移除快捷方式，不删除记忆档案。

状态台仅绑定 localhost，不向外部服务发送档案。常规状态页面只读。“记忆搜索”与 CLI 共用同一套已验证检索引擎，支持关键词、多语语义和混合模式；每条结果保持人类可读，并显示标题、时间、说话者、原文行范围和 SHA-256 回链。设置页中的明确操作可以开启或关闭加密云文件夹交换、立即执行一次交换，或把用户选择的 ChatGPT 导出包导入本地档案。不使用 `--window` 时仍可使用跨平台浏览器模式；`--no-browser` 只启动本地服务器，`--port` 可指定端口。

本地只读接口为 `/api/memory-search`，三种模式值分别为 `keyword`、
`semantic` 和 `hybrid`。

## macOS 自动采集 Codex

仅安装 Skill 不会订阅 Codex 客户端事件。先构建一次 Rust 采集器，再安装持久 LaunchAgent：

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

LaunchAgent 保持一个优化后的 Rust 进程，接收操作系统文件变化通知，并使用自适应大小/mtime 检查补充深层目录中遗漏的事件。活跃时每 5 秒补检，空闲 2 分钟后降为 30 秒，空闲 15 分钟后降为 5 分钟；原生事件会立即唤醒。采集器保存用户消息、可见助手 commentary/final，以及顶层 Codex 时间线中可见的轻量工具活动。工具活动在可用时保留工具名、嵌套工具名和命令文本；工具输出、系统指令、隐藏推理和子代理会话不归档。顶层 rollout 中可用的 `token_count` 遥测单独写入每个对话的派生账本，标记为“Codex 报告模型用量”而非账单用量。累计计数器重置时封存上一段再累加；重复快照不重复计为请求；缓存输入与推理输出是已包含分项，不能再次加入 `total_tokens`。仍保留的 rollout 可精确回填；已经删除的遥测、ChatGPT 网页对话和官方导出包不能恢复实际模型用量。逐会话游标和稳定来源 ID 保证重试幂等。

原生采集器直接负责事件驱动 JSONL 解析、原文追加、逐对话全文更新、确定性路由索引、游标写入、到期一级摘要任务和原子备份债务登记。成功的 Codex 文件修改会记录路径、变更类型、移动目标、增删行数、hunk 行范围及精确统一 diff。一般工具输出和隐藏推理继续排除。已有安装会对历史 patch 事件执行一次回填。任务到期时，采集器运行一个 Python wrapper，调用一次临时 Codex CLI 摘要进程，导入后退出。Python CLI 继续负责低频维护、检索、重建、备份维护和摘要导入。

每个导入对话还会单独写入 `memory/conversations/`。每份全文只包含一个 conversation ID，同时保留精确机器记录和可读消息。独立索引位于 `memory/indexes/by-conversation/<conversation>/`。`raw/` 下不可变文件仍是权威来源；逐对话全文和索引都是可重建的确定性视图。

当档案或备份位于受保护的 `Documents` 或 `Desktop` 时，在 macOS 中应向 `bin/memory-wuxian-collector` 授予完全磁盘访问权限。声称自动采集有效前，应核对生成 plist 中的实际可执行文件。后台定义保留 `/opt/homebrew/bin/python3` 这类稳定 Python 入口，不把它解析成带版本号的 Homebrew Cellar 路径；因此普通 Python 升级不会产生新的隐私身份，也不会再次反复请求桌面或文稿权限。

采集器在 `imports/codex/collector-telemetry.json` 发布轻量运行遥测。状态台显示活跃、空闲或深度空闲模式、当前补检间隔、最近文件事件、最近归档写入、一小时唤醒次数以及 CPU/内存。新进程先报告 `phase=starting` 和 `ready=false`，只有初始同步成功后才进入 `phase=ready`。即使空闲，遥测也会在每个监测周期续写，并分别记录来源水位与归档水位。启动仍在进行、遥测过期、采集器停止或来源水位领先归档水位时，状态台会明确告警。

macOS 既有安装通过 `scripts/install_macos_transaction.py` 更新。它先暂存候选版，在隔离档案中证明候选采集器能够精确写入合成的用户与助手消息，通过后才切换；切换后还要验证采集器 PID 已替换、遥测新鲜且当前操作台自检通过。任何切换后失败都会恢复旧 Skill、旧 LaunchAgent 和旧采集器。日常更新使用该用户级事务，不需要重新运行完整安装器，也不需要管理员密码。

切换文件前，事务会等待共享档案锁，确认不存在原生恢复债务，并在继续持有该锁时停止旧采集器。启动替代采集器前会释放档案锁，且只有替代采集器报告 ready 后才加载定时维护任务。这个空闲边界交接可避免中断中的写入或维护抢锁把日常更新变成全历史恢复审计。若交接或首次目录切换失败，旧采集器会立即恢复。

采集器首次同步不会等待 AI 摘要。如果启动追赶过程达到摘要阈值，系统会持久化不可变的摘要任务，并在采集器进入 ready 后交给既有 semantic-backfill worker 处理。这样既不丢失原文和摘要债务，也不会让一次较长的 Codex CLI 调用阻塞事务切换。

按时间范围生成报告前，先运行 `scripts/archive_waterline.py --cutoff <ISO-8601>`。它核对报告截止时间之前保留的来源是否已被持久化游标覆盖。`--backfill` 必须显式调用，并且只处理被判定为滞后的保留来源文件；只有最终结果为 `covered` 时，报告才可继续使用 Memory无限。

每日归档量柱状图继续以字符数决定柱高。鼠标悬停或键盘聚焦任一柱时，会显示本地化气泡，列出完整日期、精确归档消息数和精确可见字符数。

## 导入 ChatGPT 对话

Codex rollout 流不会暴露普通 ChatGPT 对话。可以直接导入官方 ChatGPT 数据导出包，也可以传入解压后的目录或 `conversations.json`：

```bash
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
```

重复使用 `--conversation-id <native-id>` 可选择特定对话。导入器跟随导出包的当前可见分支，跳过系统消息和被放弃的重新生成分支，保留标题与稳定 ID，并可安全重复导入同一份或更新的导出包而不产生重复。导入对话使用 `chatgpt:<conversation-id>`，进入正常备份、索引、摘要、检索和状态台流程。这是导出适配器，不是实时 ChatGPT 监听器。

同一适配器也位于“状态台 > 设置 > 导入 ChatGPT 对话”。所选 ZIP 或 JSON 只流式传给 localhost 状态台服务器，通过既有导入器解析，并在操作后从临时存储删除。Memory無限 不登录 ChatGPT，不请求账户凭据，也不把导出包上传给其他服务。

此功能为**实验性功能**。自动化测试覆盖合成 ZIP/JSON、可见分支选择、重复导入去重、稳定 ID 和本地状态台上传。由于项目尚未收到真实用户的官方 ChatGPT 导出包，因此**尚未经过真实用户导出包验证**。导出格式可能变化，首次真实导入应视为验证运行，并在依赖结果前检查计数和恢复出的对话。

## Windows 自动采集 Codex

先运行环境引导。它会报告 Python 版本，以及 Python、Codex CLI、随包采集器和 Codex 会话的路径。主程序只支持 Python 3.14.x。使用 `-InstallMissing` 时，只有在不存在受支持的运行时或兼容的 Codex 自带 Python 时才安装 Python 3.14。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_windows.ps1
```

发布包包含 `bin/memory-wuxian-collector.exe`，因此 Rust 和 Visual C++ Build Tools 仅为开发依赖。只有修改原生源码时才需重建采集器，然后安装用户级启动集成：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_native_collector.ps1
python scripts/install_codex_autosync_windows.py `
  --archive-root "$PWD\memory" `
  --python-executable "C:\path\to\python.exe" `
  --codex-cli "C:\path\to\codex.exe" `
  --load
```

计划任务在用户登录时启动，并由 `--load` 立即启动。如果本地策略拒绝注册计划任务，安装器会回退到当前用户的 `Run` 注册表项，使用编码后的隐藏重启命令，无需持久 helper 脚本。档案仍位于选定的工作区根目录。Windows 使用原生文件监视器、同样的 5 秒大小/mtime 补检、档案锁、会话游标、摘要触发器、语义 worker 和验证桌面快照。使用 `python scripts/install_codex_autosync_windows.py --archive-root "$PWD\memory" --uninstall` 可移除任一启动后端。

安装器还会把所选档案写入 `~/.codex/memory-wuxian-active-root.txt`。省略 `--root` 时，CLI 检索和维护命令使用该活动档案，避免把已安装 Skill 中的空模板档案误认为真实档案。`--root` 和 `MEMORY_WUXIAN_ROOT` 仍可明确覆盖。

检索本身不获取档案独占写锁。如果当前 Codex 工作区可读但不可写活动档案，检索仍会成功，只跳过 `last-query.md` 和查询日志更新。

采集器使用明确的 16 MiB worker 栈，使 Windows 上首次全历史导入可以安全解析和索引大型 Codex rollout 集合。

默认配置下，每次原生记忆修改都会在主档案写入完成后原子更新 `pending/backup-debt.json`。低频维护任务把所有待处理修改合并成 `~/Desktop/Memory無限-记忆归档备份/` 下的一份完整验证快照，成功后才清除债务并移除旧快照。采集器不会因为复制整份档案而阻塞启动或采集。备份根目录保留一份最新恢复副本和只追加的 `backup-log.jsonl` 操作历史；存在更新的待生成快照时，状态台会明确告警。

应用重建命令可先把旧派生文件保存在 `memory/archive/`。内部恢复副本使用 `backup.workspace_retention_count`，默认同样只保留最新一份。开发编辑使用一份可替换代码备份，不额外复制实时对话档案。

## 记忆层级

```text
原始对话记录
  -> 完整的逐对话全文
  -> 每个对话独立的索引
    -> 达到完整轮次或字符阈值后的逐对话 AI 一级摘要
      -> 固定数量子摘要归并出的逐对话高层摘要
        -> 全局路由索引
          -> 检索得到的原文证据
```

默认阈值可以配置。初始实现刻意避免主观重要性评分和自动推断长期用户偏好。

一级摘要默认边界为每个对话完成 5 轮或达到 20,000 个可见字符，以先发生者为准。若在回答中跨过 20,000 字符，只会把摘要标记为到期，直到该回答的 `final_answer` 完成轮次后才闭合来源范围。脚本保存精确来源范围、哈希、计数和规范化路由摘录；只有临时 AI worker 生成主题、结论、开放问题和概念。

已安装配置启用自动语义摘要任务和一次性 worker。摘要未到期时没有 AI 进程持续运行。阈值变化不会悄悄重写已有待处理任务的不可变来源范围。

## 联邦记忆

从 1.6.0 起，每台设备的本地档案只由该设备写入。设备把自身新增原文、摘要和已确认标题导出为 `.mwxb` 增量包；可信对端将其导入默认同级目录中的只读副本：

```text
<archive>-federation-cache/
├── peers/<origin-node-id>/
└── global-index/
```

对端记录不会进入接收设备的本地 `raw/`、`state.json`、轮次计数或摘要计数。可重建对端索引按来源节点限定标识符；`retrieve-global` 查询时把这些路由与当前本地权威档案结合。`retrieve` 仍只检索本机。

初始化两个节点并交换离线增量：

```bash
python3 scripts/memory_cli.py --root /path/to/node-a init-node --display-name "Node A"
python3 scripts/memory_cli.py --root /path/to/node-b init-node --display-name "Node B"
python3 scripts/memory_cli.py --root /path/to/node-b add-peer --node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-a export-delta \
  --output /trusted/path/node-a-0001.mwxb \
  --target-node-id <node-b-id>
python3 scripts/memory_cli.py --root /path/to/node-b inspect-bundle \
  --bundle /trusted/path/node-a-0001.mwxb
python3 scripts/memory_cli.py --root /path/to/node-b import-delta \
  --bundle /trusted/path/node-a-0001.mwxb \
  --expected-node-id <node-a-id>
python3 scripts/memory_cli.py --root /path/to/node-b retrieve-global \
  --query "earlier topic"
```

产物账本能识别在原始消息范围之后才创建的本地权威摘要或标题。导入会验证产物 SHA-256，拒绝事件序列缺口和重叠，并要求每个非初始包记录已导入前序包的 SHA-256。重复导入已接受包是幂等的。`revoke-peer` 阻止未来导入和 SSH 拉取，但不会静默删除已导入历史。

大规模积压会导出为有界连续分页。`has_more` 为真时，使用返回的 `to_event_sequence` 和包 SHA-256 作为下一次导出游标与前序哈希。导出状态可在状态缓存写入中断后从只追加产物账本重建。

注册 SSH 对端并拉取下一个增量：

```bash
python3 scripts/memory_cli.py --root /path/to/local add-peer \
  --node-id <remote-node-id> \
  --host user@example-host \
  --remote-root /path/to/remote/archive \
  --remote-config /path/to/remote/config.yaml \
  --remote-cli /path/to/remote/scripts/memory_cli.py \
  --remote-shell posix
python3 scripts/memory_cli.py --root /path/to/local sync-peer \
  --node-id <remote-node-id>
```

Windows 对端使用 `--remote-shell powershell`。SSH 通过严格主机密钥检查和配置的 SSH 用户凭据加密并认证传输，并使用有界连接和命令超时。`.mwxb` 格式本身只压缩，不加密，也没有密码学签名，因此离线包只能通过可信渠道传输。

联邦使用 Memory無限 节点身份和明确对端记录，不复用 OpenAI 账户会话、Codex 凭据或 OpenAI 设备身份。可重建联邦缓存不进入桌面主档案备份。1.6.0 不提供公网自动发现、NAT 穿透或手机客户端。

## 加密云文件夹交换

1.6.0 增加了面向用户指定 iCloud Drive、OneDrive 或兼容同步目录的异步传输。Memory無限 不接收或保存云服务凭据。它先用来源设备 Ed25519 密钥签名内部 `.mwxb`，再用 age/X25519 加密到目标设备，最后只写入目标专属 `.mwxe` 信封。

每台设备把私有身份保存在档案、副本缓存和同步目录之外。配对文件只含公钥与指纹；导入前应通过可信渠道比对指纹：

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"
SHARED="$HOME/Library/CloudStorage/OneDrive-Personal"

python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-configure \
  --directory "$SHARED"
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-export \
  --output /trusted/path/this-device-pairing.json
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-pair-import \
  --pairing-file /trusted/path/other-device-pairing.json \
  --expected-fingerprint <fingerprint-shown-on-the-other-device>
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-sync --force
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-status
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-disable
python3 scripts/memory_cli.py --root "$ARCHIVE" cloud-enable
```

所选目录必须已经存在，避免把拼错路径悄悄创建为未同步的本地目录。Windows 应选择文件资源管理器中显示的本地 OneDrive 或 iCloud Drive 目录。
选择云盘根目录或其已有的 `MemoryWuxianExchange` 子目录，都会归一到同一个
`<云盘根目录>/MemoryWuxianExchange/v1` 队列，避免已配对设备静默扫描不同的
嵌套目录。

配置后注册每五分钟运行一次的短任务：

```bash
python3 scripts/install_cloud_sync.py \
  --archive-root "$ARCHIVE" \
  --skill-root "$HOME/.codex/skills/memory-wuxian" \
  --python-executable "$(command -v python3)" \
  --load
```

任务每次唤醒都会导入可用对端信封。普通本地变化合并 15 分钟；约 1 MiB 待处理材料可提前发送；最早待处理变化在 60 分钟后尝试发送。这些时间描述写入本地同步目录的行为，真正网络上传时机由云服务客户端控制。空检查不创建文件，也不调用 AI。

云文件夹是传输队列，不是共享可写档案。每个节点只写自己的发件箱和确认。导入历史仍位于只读对端副本，`retrieve-global` 对 SSH 和云传输使用相同验证来源路径。`cloud-disable` 可停止交换而不删除档案、密钥或云端加密文件。

在 macOS 上，OneDrive“文件按需”信封可能已经显示在目录中，但本地尚无可读字节。Memory無限 会先探测文件以触发有界下载，并把暂时性的 File Provider 可用性错误作为可重试状态，而不是损坏文件。对于 `environment-v1`，如果发送端从更早游标重发了覆盖范围更大的包，只有在已持久化前缀逐事件完全一致时才会安全接续；任何冲突仍会失败关闭并隔离。

1.6.1 起，这些操作也显示在状态台设置页。云同步开关同时控制加密交换和五分钟后台任务；“立即同步”执行一次即时加密交换。面板显示已配置云目录和后台任务状态，日常操作无需 AI 对话或终端命令。

## 项目证据包

Memory无限可以保存并交换一组显式、受限的项目规则、当前状态、下一步计划、
决策、QA、报告、模板和小型辅助产物。它不会扫描或上传整个工作区。每个证据包
保留精确字节与 SHA-256，并在适用时记录前一代，作为不可变代次保存。源目录
路径不会被持久化，疑似含密钥的文本会被拒绝，对端副本始终只读。

项目证据使用独立的、签名且面向目标加密的 `project-evidence-v1` 流，既不改变
`archive-v1`，也不改变 `environment-v1`；旧客户端可安全忽略新流。操作台显示
证据包数量与流游标。使用 `project-evidence-query` 做有界定位，使用
`project-evidence-reconstruct` 取得完整精确字节。详见
[项目证据合同](references/project-evidence.md)。

```text
project-evidence-build
project-evidence-list
project-evidence-query
project-evidence-reconstruct
project-evidence-status
project-evidence-owner-register
project-evidence-owner-refresh
project-evidence-owner-status
```

本机 Project Evidence Owner 可以维护一组显式、封闭的文件清单。五分钟后台任务
每轮最多刷新 20 个 owner；内容不变时零写入，稳定变化时生成一个带前代链接的
不可变代次。源路径始终只留在本机，失败按 owner 隔离，对端证据不会创建本机
owner。

## 项目大型附件

大型最终成果使用独立的 `project-attachment-v1`，不会抬高既有项目证据包的
有界限制。显式封闭的 JSON 清单可以选择 PDF、PPTX、DOCX、XLS/XLSX、
TIF/TIFF、PNG、JPEG 或 WebP。Memory 無限不改动原始文件，只记录按 4 MiB
分块的精确 SHA-256 清单并对相同分块去重。单个逻辑文件上限为 256 MiB，单代
上限为 1 GiB。

加密传输支持断点续传并绑定独立流。接收端只有在全部分块和完整文件哈希均通过
后才会原子重建，并写入验证回执；缺块、损坏、乱序、错误目标或跨流数据均关闭
失败。只授权附件上传时应使用专用同步命令。详见
[项目大型附件合同](references/project-attachments.md)。

```text
project-attachment-build
project-attachment-owner-register
project-attachment-owner-refresh
project-attachment-owner-status
project-attachment-status
project-attachment-sync
project-attachment-reconstruct
```

## Memory無限 2.0 环境趋同

2.0 新增第二条彼此独立的同步平面，用于全局规则、项目规则、全局 Skill
和项目 Skill。它不会把多台设备合并成同一个共享可写档案：每台设备继续拥有
自己的本地对话主档案，其他设备的对话只作为经过验证的只读副本。

环境产物采用不可变、内容寻址的修订和显式的本机绑定。选定云目录承载独立的
签名、面向目标加密的 `environment-v1` 流，并使用自己的事件序号、前序链、
游标、确认、暂存区和已验证 Skill 包。五分钟后台任务只用确定性脚本验证
传入内容，不调用 AI。传输只会暂存更新，不会自行安装 Skill 或改写规则。
从 2.4.1 开始，同一批注册中的每一项都有独立、稳定的导出身份，项目注册也会
同步。收到的项目只作为只读对端元数据保存，绝不会自动在本机创建或激活。
Skill 包改用安全的完整 YAML 解析器，支持合法的嵌套映射、列表和块文本，同时
拒绝重复键与危险标签；安装器会提供所需的 PyYAML 6.x 运行依赖。

只有显式开启相应策略时，兼容的全局规则快进才可以自动登记。项目产物、Skill、
分歧、身份变化、权限扩大、持久组件增加和运行环境不兼容始终需要人工审阅。
安装器会在修改前持久化回滚材料，原子切换，执行安装后检查，并追加证据回执。
把可复用项目能力提升到全局范围属于另一条治理流程，必须具备完整平台矩阵、
来源证据和显式批准。

经过验证的局部架构经验也可以记录为不可变的治理思想提案。已配对设备会通过
同一条签名、面向目标加密的 Environment 流交换提案，但导入的提案始终只是
只读证据。只有 `work-system-governor` 完成分类和验证并取得显式接受后，
才能据此生成新的规则或 Skill 修订。

经过证据约束的产品演化记录可以保存有边界的开发历史、已验证现状、纠正后的
下一次开发流程和可复用经验候选。它们只作为只读（read-only）证据交换；接收不会触发产品
修复或全局治理接受。确定性任务负责采集变化并排队，AI 只在需要语义复盘时
介入。

操作台的 Environment 视图会显示清单、传入判定、冲突、提升提案和手动更新
检查。2.0 的完整 CLI 命令族如下：

```text
environment-init
environment-scan
environment-status
environment-list
environment-projects
environment-show
environment-diff
environment-register
environment-validate
environment-export-delta
environment-exchange-status
environment-profile-capture
environment-profile-status
environment-profile-current
environment-profile-rebuild-current
environment-profile-compare
environment-convergence-plan
environment-incoming-status
environment-process-incoming
environment-accept-incoming
environment-bindings-status
environment-register-root
environment-register-project-binding
environment-register-rule-binding
environment-register-project-rule-binding
environment-register-skill-binding
environment-discover
environment-install-rule
environment-recover-rule-installs
environment-install-skill
environment-recover-skill-installs
environment-conflict-assess
environment-conflicts
environment-conflict-resolve
environment-promotion-propose
environment-promotion-transition
environment-promotions
environment-governance-propose
environment-governance-proposals
environment-product-evolution-record
environment-product-evolution-records
environment-governance-ai-discover
environment-governance-ai-status
environment-governance-ai-enqueue
environment-governance-ai-configure
environment-governance-ai-tick
```

### 有界治理 AI

Memory無限可以在不维持活动 AI 对话的前提下排队语义任务。脚本每五分钟执行
一次无模型发现和到期检查；只有兼容的微批次到期时，才启动一次临时 Codex
worker。产品批次按 3 项或 6 小时触发（最多 5 项），治理分类按同一 owner
5 项或 24 小时触发（最多 10 项），单批证据上限为 80,000 字符，每日本机最多
运行 6 次。紧急项目可以跳过数量和时龄阈值。

该功能默认关闭。产品任务只在来源设备执行，全局分类必须由一个明确配置的
协调设备执行。所有结果都是通过严格 schema 校验、等待人工审批的草案。
worker 无权接受规则、安装 Skill、修复产品或改写历史档案。

### 可解释配置与设备兼容性

Memory无限会把现有 YAML 编译成封闭、确定性的 configuration-v1 视图，
但不修改源文件，也不初始化档案。每个有效值都会标明来源层，完整有效配置
具有稳定 SHA-256。未知键、重复键、无效类型和越界值都会直接失败。

`environment-capability-status` 只报告产品、平台、运行时、协议和接口兼容性。
旧设备没有能力声明时只标记为诊断状态，不会中断现有同步。兼容结果绝不授予
安装、信任、权限或同步权。状态台的“系统”（System）页显示同一份只读信息。

```bash
python3 scripts/memory_cli.py configuration-compile
python3 scripts/memory_cli.py configuration-explain
python3 scripts/memory_cli.py environment-capability-status
python3 scripts/memory_cli.py environment-capability-status --peer-offer /path/to/peer-offer.json
```

## 隐私与集成边界

- 私人档案应使用仓库外部的 `--root`。
- 随包 `memory/` 目录下的可变文件已被 `.gitignore` 排除。
- CLI 在明确配置时可遮蔽明显秘密，但用户仍需决定哪些内容可以持久保存。
- 自动采集需要随包原生 LaunchAgent、Windows 计划任务或其他明确配置的客户端钩子。
- 离线 `.mwxb` 包含可读档案内容，只能通过 SSH 或其他可信渠道传输；SHA-256 不提供加密或发送者认证。
- 云目录包含已签名、面向目标加密的 `.mwxe` 信封和加密确认；设备私钥不会进入同步目录。

## 完整维护命令面

前面的快速开始章节覆盖日常操作。下面明确列出全部公开维护命令，确保发布流程
无法悄悄加入未写入文档的命令：

从 v1.7.4 开始，拉取请求和安装包发布都会运行仓库内置的文档契约。功能变更
必须同时更新三语 README、`CHANGELOG.md` 和已审阅的功能契约。

从 v1.7.5 开始，即使父进程通过 `PYTHONIOENCODING` 传入 GBK 等旧编码，
Windows CLI 的重定向输出也始终使用 UTF-8。旧式交互控制台遇到无法表示的
字符时只转义该字符，不会再让记忆操作终止。

从 v2.4.2 开始，Windows 原生状态台启动器会向系统申请未占用的本机回环端口，
并打开实际分配的端口，不再假定使用 8765。其他本地软件即使先占用 8765，
也不能再把自己的界面显示到 Memory无限窗口中。

```text
init
append
sync-codex
import-chatgpt
status
context-refresh-status
context-capsule
backup
make-summary-job
ingest-summary
retrieve
conversation-tail
register-title
rebuild-state
rebuild-conversations
rebuild-indexes
index-generation-build
index-generation-status
index-generation-activate
index-generation-rollback
heartbeat
rebuild-deterministic-indexes
init-node
add-peer
revoke-peer
export-delta
inspect-bundle
import-delta
rebuild-global-index
retrieve-global
federation-status
sync-peer
cloud-configure
cloud-pair-export
cloud-pair-import
cloud-sync
cloud-status
cloud-enable
cloud-disable
configuration-compile
configuration-explain
environment-capability-status
```

手动恢复语义摘要时还会使用 `semantic_worker.py` 和
`semantic_backfill.py`。提交前可运行文档契约校验：

```bash
python3 scripts/check_documentation_contract.py
```

## 开发

运行功能测试且不生成字节码文件：

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

候选 CI 对功能分支只响应拉取请求，对 `push` 只响应 `main`。Ubuntu 和
Windows 每个 job 只运行一次完整测试；macOS 在拉取请求中运行平台专属契约，
在 `main` 上运行完整测试。已经由完整测试证明的彩排场景通过
`--reuse-unittest-evidence` 保留各自的哈希证据引用，不再重复执行同一模块。
安装包发布直接使用同一 SHA 的 `main` 成功结果。这个优化只消除重复工作，
不会删除发布契约。

架构决策和实现合同位于 [`PROJECT.md`](PROJECT.md) 与 [`references/`](references/)。变更记录位于 [`CHANGELOG.md`](CHANGELOG.md)。`README.md`、`README.zh-CN.md` 和 `README.ja.md` 作为同一份文档合同维护；文档所述行为变化时必须同时更新。

从 v2.4.3 开始，[`PRODUCT_ARCHITECTURE.md`](PRODUCT_ARCHITECTURE.md)
是模块边界的唯一权威，
[`docs/module-architecture.json`](docs/module-architecture.json)
是机器可读的文件所有权清单。每个生产文件必须且只能属于一个模块；
`scripts/check_architecture_contract.py` 会拒绝未归属文件、重复归属和已声明的
禁止依赖。Windows 与 macOS 安装包如果缺少这些架构门禁文件，发布将直接失败。

## 分版本执行路线

[`references/version-roadmap-v2.5-to-v3.0.md`](references/version-roadmap-v2.5-to-v3.0.md)
是 v2.6 至 v2.10 顺序实施的权威文件。每个版本发布前都必须具备上一版本的发布与
恢复证据、有界工作合同、绑定同一候选 SHA 的 macOS 与 Windows 门禁，以及已经
证明的回滚路径。个人 Environment 收敛固定属于 v2.10；只有另行接受了不兼容的
公共合同变更，才可以进入 v3.0。

### v2.6 索引安全

`index-generation-build` 根据经过准确 SHA-256 验证的 raw 与摘要来源清单创建
不可变影子代际，不改动当前索引文件。`index-generation-status` 验证闭合清单和
全部 payload。`index-generation-activate --generation-id <id>` 在提供
`--apply` 之前只进行预览；`index-generation-rollback` 同样先预览前一指针，再
执行仅切换指针的原子回滚。固定的 v2.6 检索基准保存语料哈希、策略谱系和准确
消歧案例，并拒绝未解释的结果差异。这些操作都不会修改原始历史，也不会自动
激活接收到的索引。

## 许可证

Memory無限 使用 [MIT License](LICENSE.txt) 发布。
## v1.9 受保护的可迁移能力

`migration-preview` 只报告目标空间和不可变源清单，不执行写入。
`migration-apply` 只做逐文件校验的复制，绝不删除源归档；只有三份清单一致且
显式给出 `--switch-active` 才切换活动根目录指针。`project-package-export`
按对话 ID 导出人类可读项目包，`project-package-import` 校验后只放入本地
raw 历史之外的只读副本区。
## v1.10 历史视图

`as-of` 按带时区时间戳重建只读历史切片。`decision-graph` 只根据显式策略事件
派生规则和决定谱系；其中 `raw_sources` 保留消息 ID、原始路径和记录哈希。
图谱不是权威来源，不能覆盖历史。
## v1.11 检索质量与可选本地语义索引

`retrieval-evaluate` 用人类可读 JSONL 测试集统计 recall-at-k、错误引用数和
延迟。`semantic-index-build` 保留完全离线的默认 `local-hash-v1`，不下载模型，
不调用外部服务。需要多语神经语义检索时，先运行
`python scripts/install_multilingual_e5.py`，再运行
`semantic-index-build --provider multilingual-e5-small`。可选的 384 维
`intfloat/multilingual-e5-small` ONNX 模型固定到不可变提交和准确 SHA-256，
使用隔离环境，禁用远程模型代码，并在推理时强制离线。Windows 隔离环境明确
绑定 Python 3.12，并支持中文 Skill、档案、worker 和索引路径。`semantic-retrieve`
会以 raw SHA-256 复核每条命中，并返回
对话/消息 ID、原始路径和准确行范围。`semantic-index-clear` 只删除可重建向量，
原始历史和关键词检索仍然可用。

E5 接口也可以作为不可变的 `global-runtime-contract` 注册到独立的
Environment Registry：

```bash
python scripts/memory_cli.py semantic-runtime-status
python scripts/memory_cli.py environment-register-semantic-runtime \
  --origin-node-id <node-id> --apply
python scripts/memory_cli.py environment-realize-semantic-runtime
python scripts/memory_cli.py environment-realize-semantic-runtime --apply
```

## v2.7 后台自治与诊断

Memory无限现在把不调用模型的维护工作写入封闭的持久队列，使用稳定的幂等键、租约、
有界重试、重启恢复和 `quarantined` 隔离状态。`maintenance-status` 对比采集器与
worker 的期望状态和实际状态；`maintenance-diagnostics` 生成经过脱敏的诊断包，
不包含原始对话、凭据或本机用户路径。只有完整对话边界先经 `semantic_dispatch.py`
进入 `semantic-ready`，既有的一次性 AI worker 才能运行。机械队列 tick 不调用 AI，
摘要失败也不会停止原生采集。

```powershell
python scripts/memory_cli.py maintenance-enqueue --kind archive-health --idempotency-key health:manual
python scripts/memory_cli.py maintenance-requeue --job-id job-<sha256> --reason "worker contract upgraded"
python scripts/memory_cli.py maintenance-status
python scripts/memory_cli.py maintenance-tick --maximum-jobs 20
python scripts/memory_cli.py maintenance-diagnostics
```

## v2.8 无损旁路存储与断点续传

可选的 `exact-byte` 旁路存储在 `shadow-content-v1` 中写入按内容寻址的对象和封闭、
有序的清单。每个条目保留稳定来源身份、相对路径、字节长度和整文件 SHA-256。
构建、重建、停用和传输默认仅预览。各领域独立的 `checkpoint` 只允许连续且已验证的
范围继续；重复投递保持幂等，缺段、重叠、损坏、篡改和目标冲突都会带明确说明地
失败关闭。删除整个旁路目录不会改变原始历史，也不会改变既有的 `archive-v1` 和
`environment-v1` 数据流。

```powershell
python scripts/memory_cli.py content-shadow-build --source-root C:\snapshot --source-id node:snapshot --file raw/a.md
python scripts/memory_cli.py content-shadow-status
python scripts/memory_cli.py content-shadow-verify --manifest-id <manifest-id> --source-root C:\snapshot
python scripts/memory_cli.py content-shadow-reconstruct --manifest-id <manifest-id> --destination C:\restore
python scripts/memory_cli.py content-shadow-disable
python scripts/memory_cli.py content-transfer --manifest-id <manifest-id> --target-archive-root C:\target --domain archive --target-id <node> --start 0 --count 100
```

## v2.9 统一只读访问与更新治理

`readonly-query`、`readonly-http` 和 `readonly-mcp` 共用同一个有界服务与
`memory.query` 合同。结果包含置信度、准确原文来源、SHA-256 和原文复核状态。
HTTP 只接受 GET 且只能绑定回环地址；MCP 只公布一个读取工具，不提供写入、安装、
配对、任意路径、命令或远程控制工具。语义索引陈旧或不可用时，混合模式会降级到
经过原文复核的关键词检索。

```powershell
python scripts/memory_cli.py readonly-query --query "之前的决定" --mode hybrid --limit 20
python scripts/memory_cli.py readonly-http --host 127.0.0.1 --port 8766
python scripts/memory_cli.py readonly-mcp
python scripts/memory_cli.py summary-budget-status --metrics-json metrics.json --policy-json policy.json
```

## v2.10 个人 Environment 收敛

2.10 可把明确指定的全局规则文件和已安装 Skill 根目录盘点为确定性、与设备路径无关的
Profile。Profile 只记录稳定安装身份、provider 来源、声明版本、精确树或文件 SHA-256、
文件数、字节数、平台适用性和 Memory 无限托管规则块身份；不记录源路径、用户名、主机名、
凭据、环境变量值、缓存、模型、归档、对话或索引。

采集默认只预览，只有 `--apply` 才会创建一个与前代相连的不可变代次，并原子更新可重建的
current 指针。环境没有变化时不会重复创建代次或导出事件。现有 `environment-v1` 只向已信任
设备传输代次，接收端保存为 `automatic_activation=false` 的只读副本。

比较结果固定为 `same`、`missing-local`、`missing-peer`、`content-differs`、
`platform-inapplicable` 和 `inventory-incomplete`。收敛计划只提供有界预览：系统内置和
插件托管 Skill 保持 provider 引用；没有精确、既有、不可变 Environment 产物的项目只能标记为
`evidence-only`。Profile 本身永远不能调用规则或 Skill 安装器。

```powershell
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json
python scripts/memory_cli.py environment-profile-capture --specification profile-sources.json --apply
python scripts/memory_cli.py environment-profile-status
python scripts/memory_cli.py environment-profile-current
python scripts/memory_cli.py environment-profile-rebuild-current
python scripts/memory_cli.py environment-profile-compare --peer-node-id node-mac
python scripts/memory_cli.py environment-convergence-plan --peer-node-id node-mac
```

可选的 `--artifact-links` 输入必须符合
`schemas/environment-convergence-artifact-links.schema.json`；完整示例见
`examples/environment-convergence-artifact-links.json`。有效链接也只会生成既有安装器的预览，不会授权激活。

状态台的 Environment 页会显示本机代次数、导出事件数、可信设备 Profile 副本和只读比较预览。

更新元数据明确区分 stable、beta 和 development 通道。已验证的差分包失败后会
回退到已验证的完整包。下载结果保持 `staged-awaiting-user-approval`；只有单独明确的
第二条命令同时提供 `--approve-install`、`--expected-version` 和
`--expected-sha256` 才能调用既有安装器。beta/development 通道或差分元数据通过
`--channel` 与 `--update-metadata-json` 提供。选择通道或下载前，发布元数据还必须
通过 Ed25519 SSH 分离签名，并与固定的 `keys/update-allowed-signers` 身份一致。
摘要预算检查完全确定且不调用模型，只能在
完整轮次边界幂等地排入一次性任务。

签名且面向目标加密的 `environment-v1` 流会把合同传给已配对设备。合同固定
模型 revision、文件哈希、运行时包、query/passage 前缀、池化、归一化、
相似度算法和安装入口。接收或接受合同不会自动安装或下载任何内容；每台设备
都必须显式地在本机实现已接受的合同。模型文件、虚拟环境、凭据和语义索引
始终保留在各自设备。
