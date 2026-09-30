# v2.20.5 — complete distribution with bundled updater

v2.20.4 和 v2.20.5 候选未完成正式发布；本版取消仅服务本机的状态台启动时不必要的主机名反查，修复 macOS 启动阻塞。

用户下载对应平台的 `-complete.zip` 即可获得产品、安装器 1.1.0 和状态台更新按钮，无须另装安装器。

- 安装器保持独立版本号；本次未修改已发布 1.1.0 的六份 Python 实现。
- 正常状态台入口自动接入“检查更新 / 升级”，不做后台周期检查。
- 首次使用完整包时登记程序文件归属；只在这一步核对解压文件，不重复扫描历史。
- 保留短文件名的程序更新包，兼容现有 1.1.0 安装器。普通更新只改变化的程序文件，不覆盖安装器、配置、密钥或归档。
- 完整包面向安装和分发；已有安装应使用更新按钮或 CLI，避免手工解压覆盖本机差异。

Requires Python 3.14 and authenticated Codex CLI. Device configuration and scheduler
setup remain explicit. Windows live updates use existing CoreMaintenance/CoreDashboard
tasks. macOS/Linux still require stopped services and CLI --offline; no automatic
service setup or remote-device activation is claimed.

## Previous releases

# v2.20.3

Closes the read-only Codex SQLite connection explicitly on all platforms. The
v2.20.2 tag did not produce a formal release after Windows detected an open handle.

- Restore dashboard summary-level counts, actual completed-round counts, storage and incremental text estimates.
- Read local Codex titles, projects and archived status without changing the Codex database.
- Show persisted Token ledger observation dates separately from estimated text tokens; missing usage is not zero.
- Publish scheduled-worker phase, process identity, capture backlog, summary progress/errors, backup and sync status during a tick.
- Preserve historical warnings and receiving-device ACK requirements instead of displaying false health.
- Include the existing Mac migration fixes and desktop launcher adapter; do not reimport raw history during this update.
- Add matching English, Chinese and Japanese README documentation.

Existing installations keep their local configuration and archive. Set the local Codex
executable to its actual installed location and match `interval_seconds` to the OS
schedule. The independent installer remains excluded. This release does not repair
historical capture gaps or add a new billing collector.

## Previous: v2.20.1

- Direct Codex capture replaces the legacy collector and raw-archive bridge in normal maintenance.
- Existing archive storage, summaries, dashboard, search, backup and core-v1 sync remain one runtime.
- Every platform package includes the independently built minimal encryption/signing helper.
- Added a portable configuration entry point for local identity creation and explicit peer pairing.
- Windows x64, Linux x64 and macOS ARM64 packages are built and exercised in CI.

Requires Python and an authenticated Codex CLI. Historical migration preserves original
archives. The independent installer remains excluded. No real Mac peer ACK is claimed.
