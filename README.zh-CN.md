# Memory無限

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

Memory無限 是一个基于文件的 Codex Skill，用于在活动上下文窗口之外建立持久、分层且可验证的对话记忆。

可安装的 Skill 标识符为 `memory-wuxian`；`Memory無限` 是项目名和显示名称。系统以精确原始记录作为历史权威来源，以摘要作为导航，并在把历史陈述视为已验证事实前回到原文核对。

## 功能

- 带时间戳和 SHA-256 完整性字段的只追加 Markdown 对话记录
- 每个对话一份完整且自动更新的 Markdown 全文
- 并发任务中按对话隔离的待完成轮次与回复关系
- 按对话隔离的一级摘要和更高等级摘要
- 每个对话独立的消息、时间线、概念和摘要索引，以及全局路由索引
- 在完成 5 轮对话或达到 20,000 个可见字符后由脚本判定摘要边界
- 仅在完整对话轮次触发摘要时临时调用 AI
- 在设定轮次、利用率或压缩阈值后执行有界的运行时上下文刷新
- 先查索引、再回到原文验证的检索流程
- 预览优先的状态与索引恢复
- Heartbeat 验证、维护与修复模式
- 使用稳定来源 ID 和逐会话游标增量解析 Codex rollout
- 通过 macOS 原生 LaunchAgent 或 Windows 计划任务进行事件驱动同步
- 一份带 SHA-256 清单和只追加备份日志的最新桌面验证快照
- 一份用于重建派生文件的最新工作区恢复备份
- 使用增量包、产物账本游标和跨设备检索的联邦只读副本
- 并行支持 SSH 与加密云文件夹联邦传输
- 面向 ChatGPT 官方导出 ZIP 和 `conversations.json` 的实验性本地适配器
- 无数据库依赖、可直接检查的文件布局

## 安装

### 单文件安装包

从最新 GitHub Release 下载对应操作系统的安装包：

- macOS：`MemoryWuxian-<version>-macOS-universal.pkg`
- Windows：`MemoryWuxian-<version>-Windows-x64-Setup.exe`

状态台会先显示浏览器本地保存的最近一次成功响应，再使用经来源验证的持久化统计快照。档案未变化时无需重新扫描全部原始历史；快照过期或损坏时会从权威档案自动重建。可选的本地成就系统记录档案大小、档案上下文和纯消息 Token 估算、对话深度、项目增长、摘要层级及原文验证检索。

打开安装文件后，Skill 会安装到当前用户的 Codex 目录，初始化 `Documents/MemoryWuxianArchive`，并启用持续 Codex 采集。重新安装或升级会保留现有配置和档案。卸载会移除程序及后台集成，但保留对话历史。公开构建默认没有代码签名，除非发布流程配置了平台签名凭据，因此操作系统可能要求手动确认安全提示。

### Codex Skill 安装器

从 GitHub 目录安装后重启 Codex：

```text
$skill-installer install https://github.com/Sundried-calomel/memory-wuxian
```

手动安装时，将仓库放到：

```text
~/.codex/skills/memory-wuxian
```

## 快速开始

先阅读 [`SKILL.md`](SKILL.md)。真实对话历史应使用仓库外部的档案根目录，避免源码检出或 Skill 更新与私人记忆数据混在一起。

官方安装包会注册每日稳定版本检查。更新器忽略分支、草稿和预发布版本，同时下载平台安装包及其 SHA-256 文件；校验和或文件名不匹配时拒绝暂存更新。Windows 会在下次登录时静默安装已验证更新；macOS 会保留已验证 PKG，等待操作系统要求的安装授权。使用 `python scripts/install_auto_update.py --uninstall` 可关闭检查。

```bash
ARCHIVE="$HOME/Documents/MemoryWuxianArchive"

python3 scripts/memory_cli.py --root "$ARCHIVE" init
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker user --text "Hello"
python3 scripts/memory_cli.py --root "$ARCHIVE" append --speaker assistant --text "Hello."
python3 scripts/memory_cli.py --root "$ARCHIVE" sync-codex --session-file "$HOME/.codex/sessions/.../rollout-....jsonl"
python3 scripts/memory_cli.py --root "$ARCHIVE" status
python3 scripts/memory_cli.py --root "$ARCHIVE" backup
python3 scripts/memory_cli.py --root "$ARCHIVE" heartbeat --check-only
```

持续采集本身不调用模型。只有完整对话轮次达到配置阈值后，脚本才创建锁定来源范围的摘要任务。一次性语义 worker 随后以临时模式调用已认证的 Codex CLI，导入受约束的 JSON 摘要后退出。

## 运行时上下文刷新

Memory無限 可在不新建替代任务的情况下，定期把压缩后的历史恢复到持续进行的 Codex 任务中。`context-refresh-status` 检测完成轮次间隔、上下文利用率阶段和上下文压缩；需要刷新时，`context-capsule` 选择最高且有用的语义摘要层级，隐藏已被覆盖的子摘要，加入少量近期对话尾部，并生成临时派生上下文。`ack-context-refresh` 记录该胶囊已被读取，避免重复注入。

胶囊预算根据模型上下文窗口计算，默认占 1%，软上限 3,000 Token，绝对上限 10,000 Token。胶囊只是导航上下文，不是历史权威来源；事实仍需回到只追加原文验证，生成的胶囊也不得作为新来源消息归档。可复用的工作区 `AGENTS.md` 规则位于 `agents/` 和 `templates/`。

## 本地状态台

Windows 可用原生应用窗口启动本地状态台。它使用已安装的 Microsoft Edge WebView2、随包提供的 Memory Wuxian 图标，并在没有浏览器边框的窗口中保留完整界面：

```powershell
python scripts/memory_dashboard.py `
  --root "C:\path\to\memory-wuxian-archive" `
  --config "C:\path\to\memory-wuxian\config.yaml" `
  --window
```

如果环境检查提示缺少开源 `pywebview`，运行一次 `scripts/bootstrap_windows.ps1 -InstallMissing`。窗口支持持久化的中文、英文和日文界面，默认每 30 秒静默刷新，并显示各对话的 Codex 标题、消息、完成轮次、摘要等级、每日归档量、待生成摘要、已归档可见来源字符以及明确标注的档案 Token 估算。字符统计包含用户与可见助手对话，不包含生成摘要。档案 Token 使用兼顾 CJK 的大小启发式估算，既不是计费使用量，也不是摘要生成消耗。每个对话还显示最近一次模型请求 Token 与模型标称上下文窗口的比例；该请求可能包含指令、工具、推理和输出，因此比例可能超过 100%，不能视为精确占用率或剩余上下文。

状态台仅绑定 localhost，不向外部服务发送档案。常规状态页面只读；设置页中的明确操作可以开启或关闭加密云文件夹交换、立即执行一次交换，或把用户选择的 ChatGPT 导出包导入本地档案。不使用 `--window` 时仍可使用跨平台浏览器模式；`--no-browser` 只启动本地服务器，`--port` 可指定端口。

## macOS 自动采集 Codex

仅安装 Skill 不会订阅 Codex 客户端事件。先构建一次 Rust 采集器，再安装持久 LaunchAgent：

```bash
scripts/build_native_collector.sh
python3 scripts/install_codex_autosync.py \
  --archive-root "$ARCHIVE" \
  --load
```

LaunchAgent 保持一个优化后的 Rust 进程，接收操作系统文件变化通知，并使用自适应大小/mtime 检查补充深层目录中遗漏的事件。活跃时每 5 秒补检，空闲 2 分钟后降为 30 秒，空闲 15 分钟后降为 5 分钟；原生事件会立即唤醒。采集器保存用户消息、可见助手 commentary/final，以及顶层 Codex 时间线中可见的轻量工具活动。工具活动在可用时保留工具名、嵌套工具名和命令文本；工具输出、系统指令、隐藏推理和子代理会话不归档。逐会话游标和稳定来源 ID 保证重试幂等。

原生采集器直接负责事件驱动 JSONL 解析、原文追加、逐对话全文更新、确定性路由索引、游标写入、到期一级摘要任务和桌面快照。成功的 Codex 文件修改会记录路径、变更类型、移动目标、增删行数、hunk 行范围及精确统一 diff。一般工具输出和隐藏推理继续排除。已有安装会对历史 patch 事件执行一次回填。任务到期时，采集器运行一个 Python wrapper，调用一次临时 Codex CLI 摘要进程，导入后退出。Python CLI 继续负责低频维护、检索、重建和摘要导入。

每个导入对话还会单独写入 `memory/conversations/`。每份全文只包含一个 conversation ID，同时保留精确机器记录和可读消息。独立索引位于 `memory/indexes/by-conversation/<conversation>/`。`raw/` 下不可变文件仍是权威来源；逐对话全文和索引都是可重建的确定性视图。

当档案或备份位于受保护的 `Documents` 或 `Desktop` 时，在 macOS 中应向 `bin/memory-wuxian-collector` 授予完全磁盘访问权限。声称自动采集有效前，应核对生成 plist 中的实际可执行文件。

采集器在 `imports/codex/collector-telemetry.json` 发布轻量运行遥测。状态台显示活跃、空闲或深度空闲模式、当前补检间隔、最近文件事件、最近归档写入、一小时唤醒次数以及 CPU/内存。遥测仅在发生活动或模式转换时写入。

## 导入 ChatGPT 对话

Codex rollout 流不会暴露普通 ChatGPT 对话。可以直接导入官方 ChatGPT 数据导出包，也可以传入解压后的目录或 `conversations.json`：

```bash
python3 scripts/memory_cli.py import-chatgpt --export /path/to/chatgpt-export.zip
```

重复使用 `--conversation-id <native-id>` 可选择特定对话。导入器跟随导出包的当前可见分支，跳过系统消息和被放弃的重新生成分支，保留标题与稳定 ID，并可安全重复导入同一份或更新的导出包而不产生重复。导入对话使用 `chatgpt:<conversation-id>`，进入正常备份、索引、摘要、检索和状态台流程。这是导出适配器，不是实时 ChatGPT 监听器。

同一适配器也位于“状态台 > 设置 > 导入 ChatGPT 对话”。所选 ZIP 或 JSON 只流式传给 localhost 状态台服务器，通过既有导入器解析，并在操作后从临时存储删除。Memory無限 不登录 ChatGPT，不请求账户凭据，也不把导出包上传给其他服务。

此功能为**实验性功能**。自动化测试覆盖合成 ZIP/JSON、可见分支选择、重复导入去重、稳定 ID 和本地状态台上传。由于项目尚未收到真实用户的官方 ChatGPT 导出包，因此**尚未经过真实用户导出包验证**。导出格式可能变化，首次真实导入应视为验证运行，并在依赖结果前检查计数和恢复出的对话。

## Windows 自动采集 Codex

先运行环境引导。它会报告 Python 版本，以及 Python、Codex CLI、随包采集器和 Codex 会话的路径。使用 `-InstallMissing` 时，只有在不存在兼容的 `>=3.9` 运行时或 Codex 自带 Python 时才安装 Python。

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

默认配置下，每次成功修改记忆都会在主档案写入完成后，在 `~/Desktop/Memory無限-记忆归档备份/` 下创建完整新快照、验证清单并移除旧快照。因此备份根目录包含一份最新恢复副本和只追加的 `backup-log.jsonl` 操作历史。

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

1.6.1 起，这些操作也显示在状态台设置页。云同步开关同时控制加密交换和五分钟后台任务；“立即同步”执行一次即时加密交换。面板显示已配置云目录和后台任务状态，日常操作无需 AI 对话或终端命令。

## 隐私与集成边界

- 私人档案应使用仓库外部的 `--root`。
- 随包 `memory/` 目录下的可变文件已被 `.gitignore` 排除。
- CLI 在明确配置时可遮蔽明显秘密，但用户仍需决定哪些内容可以持久保存。
- 自动采集需要随包原生 LaunchAgent、Windows 计划任务或其他明确配置的客户端钩子。
- 离线 `.mwxb` 包含可读档案内容，只能通过 SSH 或其他可信渠道传输；SHA-256 不提供加密或发送者认证。
- 云目录包含已签名、面向目标加密的 `.mwxe` 信封和加密确认；设备私钥不会进入同步目录。

## 开发

运行功能测试且不生成字节码文件：

```bash
$HOME/.cargo/bin/cargo test --locked --manifest-path native-collector/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

架构决策和实现合同位于 [`PROJECT.md`](PROJECT.md) 与 [`references/`](references/)。变更记录位于 [`CHANGELOG.md`](CHANGELOG.md)。`README.md`、`README.zh-CN.md` 和 `README.ja.md` 作为同一份文档合同维护；文档所述行为变化时必须同时更新。

## 许可证

Memory無限 使用 [MIT License](LICENSE.txt) 发布。
