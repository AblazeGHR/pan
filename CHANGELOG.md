# Changelog

本项目所有显著变更将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.4.0] - 2026-10-22

### Added

- 共享 Action/Group 定义、稳定 ID、Action 复制与缓存列表；Group build 通过既有插件运行时生成不可变 release，Worker 启动时固定版本。
- Pan CLI 的启动、退出、重启、状态、诊断、日志与控制台显示控制；生命周期写入校验目标实例身份。
- MCP 增加模型缓存目录、历史筛选/搜索/单消息读取、报告订阅状态、原报告批次消费，以及现有 Worker 的延迟重启和只读状态接口。
- 持久 Jobs 增加取消控制、排序、标签与进程命名；导入列表支持原生 CLI Session ID 搜索，CBC 模型选择器显示按模型配置的倍率。
- 原生附件选择器、替身交接提示词覆盖与快捷键录制反馈。

### Changed

- Illegal 状态下的新消息保持锁定，显式 Wake 或有效恢复动作才允许继任；保留 Pause、Manual、lease 和 owner 等独立门禁，退役 Oneshot 入口。
- 自动汇报与 Force 按结构化消息来源配置，队列预览显示 Agent 来源与锁定原因；报告读取保持正文私有，原报告批次通过实际 CLI 交接进入上下文。

### Fixed

- 跨 Session 并发启动及 branch 不再覆盖 Worker 注册；成功 Kill 按服务 epoch、Worker ID、generation 清理两份前端投影，迟到回执保留后继 runtime，未知身份保守处理。
- 未注册但仍存活或状态未知的 owner 导致 Kill 明确失败，继续保留 second-writer 门禁，避免把 idle 或 Kill 回执当作进程已退出。
- 修复 Session 摘要水合、Codex branch 物理谱系、报告/队列投影与 Actions 缓存的异步所有权竞态。
- CLI 实例 guard 使用直接 ASGI middleware，保留普通请求的任务身份与流式响应语义。
- 对齐回归 fixture 的原子持久化、报告策略、Job 名称及 generation 契约；假 CLI 显式使用 UTF-8，进程/浏览器检查等待实际 handoff 与回执。

本版本为第一检查点；不包含后续 ETA Event/Trigger/Action、持久 Event Store、query、serial/parallel、有限重试、Job Template 或自动换号。

## [0.3.4] - 2026-10-19

### Added

- Shortcuts 设置页支持已有快捷键、自注册插件 Panel、队列展开与锁定入队、Lock all、Pause everything、Stop report 和通知铃；设置分类与登录自启动保持兼容。
- Chat TopBar 用量标签自适应收纳，支持复制 Session 名称；Appearance 增加桌面侧边栏折叠把手和视图切换按钮的显示设置。
- README 中英文文档索引带 CLI 的 `cc-switch-pan` 仓库；Panel 在列表、配额或写入操作缺少 CLI 时显示安装仓库入口，并保留结构化错误信息。

### Changed

- 生命周期恢复统一通过 Illegal 与 pending recovery 表达；支持启动时显式将旧 Illegal 事件安全退役为 offline，安全解锁后立即派发待处理队列。
- CC Switch 面板按应用缓存并自动刷新过期配额，显示刷新倒计时。

### Fixed

- Takeover 后 Kill/Restart 正确停止外部进程树并持久化所有权退休证据；真实 CLI 会话尚未建立时禁止接管并说明原因。
- 降低冷历史读取时的事件循环阻塞，稳定终端续约节拍；Windows Session 元数据原子替换兼容短暂读句柄冲突。

- 切换 Chat、Editor 和 Jobs 等页面时保留草稿附件；删除书签后即时更新所有消费者。
- Session 卡片激活后返回 Chat；Codex 任务完成时封存临时聚合内容；CC Switch 跨组件重新挂载时保留共享缓存中的最新状态。

<!-- Earlier unreleased entries predate the v0.3.3 tag and are retained below. -->

## Historical unreleased notes

### Added

- **定时任务插件（scheduler）HTTP API**：`/api/scheduler/*` 提供定时任务的增删改查、暂停/恢复、立即执行、执行历史与下次触发预览，统一返回 `{"ok":...}` 包络并广播 `scheduler.task.*` 事件；调度循环接入服务 lifespan，到点以 `source="automation"` 把任务文本派发给目标 session。

### Changed

- **React-only 前端**：退役并归档 Vanilla 前端，根路径固定跳转 `/react/`；移除 `/vanilla` 路由和 `frontend` 配置，React 构建缺失时返回 503。
- **Windows 系统通知升级为真实 toast**：完成通知与提醒改用 WinRT toast 并录入 Windows 通知中心（Action Center）持久保留；点击「打开会话」按钮由默认浏览器打开对应 Session 深链，发送进程退出后仍可点击。替换原 NotifyIcon balloon（不进入通知中心、仅 60 秒内可点）。同一 Session 的完成通知按 Tag 替换、重复提醒按内容去重；不注册自定义协议或快捷方式。

## [0.3.0] - 2026-09-03

### Added

- **Session ordering and drag-and-drop management**：支持持久化自定义排序、拖拽排序，以及将 Session 拖入 manager 建立管理关系。
- **Durable session queue**：补齐服务端队列的查看、编辑、排序、删除和重试 API，并同步队列事件。
- **Source metadata 扩展**：开放 `meta-agent` 与 `automation` 来源类型，为外部 meta-agent 和自动化调用预留入口。
- **Manage controls**：在 Session 管理界面支持解除管理、停止/恢复汇报和 readonly 控制。
- **Documentation refresh**：同步 HTTP/WS/MCP/API 文档、用户手册和当前待办，归档已完成的一次性调研文档。

### Changed

- Codex adapter 使用原生 `codex app-server --stdio` 长驻桥接，保留 thread/turn 语义。
- React Dashboard 的拖拽行为已连接真实 `/api/sessions/order`、`/api/claim` 和 `/api/unclaim` 后端接口。

### Fixed

- 清理 legacy consumer MCP alias，减少重复编排入口。
- 修正 source metadata 在 HTTP 边界被无条件覆盖的问题，非法来源会在 worker 启动前拒绝。

## [0.1.0] - 2026-08-28

首个公开版本。Pan 是一个光谱式的可扩展中间层：往浅用是最小可用的「Session 与 Agent CLI 管理器」，往深用是完整可扩展的「Agent 集群管理协作系统 + MCP 工具层」。

### Added

- **Agent 编排（supervisor / worker）**：一个 Meta-Agent 主管拆解并调度一整支 CLI Agent 工人团队并行干活；Worker 在独立 git worktree 里干活，卡死 / 静默超时由 watchdog 自愈，进程异常死亡后落盘队列自动重建 Worker 接着干；session_handoff 替身交接，跨 CLI 无缝接管上下文
- **多 CLI 适配**：cbc / kimi / opencode / claude / codex 五个内置 adapter（wrapper + stream 长驻、模型列表 TTL 缓存、sessions provider 导入历史会话），编排层对底层 CLI 无感知
- **MCP Server**（`packages/mcp/server.py`）：向外部 AI（Meta-Agent / 编排 skill）暴露 agent_assign / agent_send / claim / report_subscribe / QQ 订阅等 `agent_*` 工具，支持 stdio 与 SSE / streamable-http
- **前端**：React SPA（`/react/`）；当时的 Vanilla 前端历史说明见 `docs/archive/vanilla-frontend-retirement.md`。
- **QQ Bridge**：NoneBot2 bot 接入 QQ，通道插件化（NapCat / LLOneBot），session 绑定、inbox 推送提醒、NapCat 不可达自动降级
- **Remote（Cloudflare Tunnel）**：quick tunnel / named tunnel 将 Pan 主端口暴露到公网，状态服务 8769
- **Memory / Character**：向量 + 全文（jieba）混合检索，开工自动注入相关记忆；人设跨 Session 保持同一身份，可选 ML 依赖缺失时懒加载自动降级
- **配置热重载**：`POST /api/config/reload`，配合 App Settings UI 无需重启
- **用户手册**：`docs/USER_MANUAL.md`（安装、操作、编排、API、配置、排障）

### Fixed

- 交付标记剥离：delivery 标记不再混入消息内容（`814c642`）
- queued 消息显示：发送队列入队即上屏，队列消息不再「发出后消失」（`7c0e691`、`229a727`）
- watchdog 回收回归：恢复空闲回收 watchdog（`eb1f223`）
- CI 测试自包含：偶发失败的测试改用 `tmp_path` / `monkeypatch` 隔离（`6f7952f`）
- TTL 单调时钟：TTL 过期模拟改用单调时钟回退，修复 CI 偶发失败（`86b1e20`）

### Changed

- 命名演进：`worker_handoff` → `agent_*` 一等工具，`worker_*` 保留为兼容别名（`cbb80ac`）
- delivery 语义收敛：reports / tasks 改为 session 级投递（`51c6d6f`）
- QQ bot 解释器路径收敛为 `config.json` 的 `qq.python` 单一事实源（`aa430a0`）

[Unreleased]: https://github.com/AblazeGHR/pan/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/AblazeGHR/pan/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/AblazeGHR/pan/compare/v0.3.3...v0.3.4
[0.3.0]: https://github.com/AblazeGHR/pan/releases/tag/v0.3.0
[0.1.0]: https://github.com/AblazeGHR/pan/releases/tag/v0.1.0
