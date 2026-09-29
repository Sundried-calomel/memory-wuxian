# Memory無限 2.20.2

[English](README.md) | [日本語](README.ja.md)

单一运行入口负责对话采集、摘要、检索恢复、备份、加密记忆同步，以及明确选定的规则、Skills和文件同步。
原文只追加，摘要保留来源引用。设备身份、密钥、本机配置和归档不进入发布包。

## 使用

需要 Python 3.14、已登录的 Codex CLI；跨设备交换需要双方可访问的共享目录，例如 OneDrive。
解压对应平台包，Unix系统为 `bin/memory-wuxian-envelope` 设置可执行权限。
运行 `python core/configure.py --help` 配置本机路径、设备身份及明确可信的对端。
按操作系统定时任务每分钟运行 `python core/live.py --config core/live-config.json`。
`interval_seconds` 应与真实调度间隔一致，不负责安装定时任务。

状态台：`python core/dashboard.py --root ARCHIVE --config core/live-config.json --port 8765`。
只读工具入口：`core/mcp_server.py --root ARCHIVE`。配对说明见 [PEER-SETUP](docs/PEER-SETUP.md)。
旧版升级须显式迁移采集位置，不能清零游标；独立安装器不在本发行包内。

## 状态台数据

- 摘要层级读取已完成的摘要文件；不存在的高级别不显示。
- 标题、项目和归档状态只读查询本机 Codex 数据库；缺少来源时保留未知。
- 完成轮次统计真实完成记录，不把旧版全局轮次编号当数量。
- 文本Token估算增量更新，与Codex报告用量分开。旧Token账本显示观测日期；本次没有新增计费采集器，不承诺实时完整消耗统计。
- 后台每阶段发布进度、错误、备份与同步结果。活跃/空闲描述调度进程，不证明归档完整。
- 状态台不重新归档、不触发摘要；历史采集错误如实显示。同步投递完成仍以对端ACK为准。
