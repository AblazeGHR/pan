# Pan MCP Server 工具增强待办

> 创建：2026-08-15
> 更新：2026-08-15 — 三项已全部实现并端到端验证（stdio 实测 create+workdir / update / get limit），仅改 `packages/mcp/server.py`
> 范围：仅改 `packages/mcp/server.py`（MCP 层）。三项的后端 API 能力均已具备，无需动 `packages/web/server.py`。

## 1. 更新 session 设置暴露成 MCP tool

- [x] 在 `packages/mcp/server.py` 新增 `session_update` tool，透传 `PATCH /api/sessions/{session_id}`
- [x] 支持的字段（对齐后端 `_apply_session_updates`，`server.py:377`）：
  - `model`、`permissionMode`、`alwaysThinkingEnabled`、`effort`、`maxThinkingTokens`、`mcpEnabled`、`mcpServers`、`gameId`
- [x] `mcpEnabled` 变化时后端返回 `requireRestart: true`（`server.py:773-781`），tool 返回值需原样带上该标志，提示调用方 worker 需重启才生效
- 后端已就绪：`PATCH /api/sessions/{session_id}`（`server.py:760`）
- 验收（已通过）：`session_update(session_id=..., effort="medium")` 后 `session_get` 的 `effort` 字段已更新

## 2. 创建 session 的 tool 允许传 workdir

- [x] `session_create` 增加可选参数 `workdir`（当前签名只有 `name` / `adapter` / `model` / `permission_mode`）
- [x] `worker_spawn` 走"带 name 建会话"路径时同步透传 `workdir`
- [x] 语义确认：后端 `_build_session_params` 已支持 `workdir`，缺省回退为 session name，经 `_resolve_workdir` 解析到 `data/workdirs/<name>`（`server.py:318`、`server.py:325`）
- 验收（已通过）：`session_create(name="x", workdir="my-wd")` 后 `session_get` 返回的 `workdir` 指向 `data/workdirs/my-wd`

## 3. 只获取 n 条历史的 MCP tool（方案已定：A）

- [x] 方案 A：`session_get` 加可选参数 `limit`（默认 0 = 全量，向后兼容），仅在返回时截断 `history` 字段，保留 `lastResult` / `usage` 等元数据
- [ ] 方案 B：新增独立 tool（如 `session_get_recent(session_id, limit)`），与 `session_history` 的分页语义区分
- 现状参考：
  - `session_get` 全量返回 history（`server.py:733`）
  - `session_history` 已有 `limit` / `before` 分页，返回 `hasMore` / `start` / `total`（`server.py:741`）
- 决策点：方案 A 不动现有工具数量、改签名（默认值保兼容）；方案 B 不碰现有签名、多一个工具。倾向方案 A（理由：`session_history` 已覆盖纯翻页场景，缺的是"元数据 + 截断历史"的单次调用）
- 验收（已通过）：`session_get(id, limit=2)` 返回 hist_len=2 + `historyTruncated`/`historyTotal` 标记，`lastResult` 保留
