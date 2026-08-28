# Changelog

本项目所有显著变更将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-08-28

首个公开版本。Pan 是一个光谱式的可扩展中间层：往浅用是最小可用的「Session 与 Agent CLI 管理器」，往深用是完整可扩展的「Agent 集群管理协作系统 + MCP 工具层」。

### Added

- **Agent 编排（supervisor / worker）**：一个 Meta-Agent 主管拆解并调度一整支 CLI Agent 工人团队并行干活；Worker 在独立 git worktree 里干活，卡死 / 静默超时由 watchdog 自愈，进程异常死亡后落盘队列自动重建 Worker 接着干；session_handoff 替身交接，跨 CLI 无缝接管上下文
- **多 CLI 适配**：cbc / kimi / opencode / claude / codex 五个内置 adapter（wrapper + stream 长驻、模型列表 TTL 缓存、sessions provider 导入历史会话），编排层对底层 CLI 无感知
- **MCP Server**（`packages/mcp/server.py`）：向外部 AI（Meta-Agent / 编排 skill）暴露 agent_assign / agent_send / claim / report_subscribe / QQ 订阅等 `agent_*` 工具，支持 stdio 与 SSE / streamable-http
- **双前端**：React SPA（`/react/`，主开发目标）+ legacy Vanilla JS（`/vanilla`，稳定备份），后端按 `frontend` 配置路由
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

[Unreleased]: https://github.com/AblazeGHR/pan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AblazeGHR/pan/releases/tag/v0.1.0
