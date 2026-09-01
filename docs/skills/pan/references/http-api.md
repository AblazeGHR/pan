---
name: pan-http-api
description: Pan HTTP API 速查（技术细节引用文档，配合 docs/skills/pan/SKILL.md 使用）。覆盖冷启动常用端点请求体、字段命名映射、Windows curl 中文编码坑、轮询兜底策略。MCP 能覆盖的编排操作一律走 MCP 工具，本文件仅用于直调（rename/branch 等无 MCP 工具）与排查。
---

# Pan HTTP API 速查（引用文档）

> 供 `docs/skills/pan/SKILL.md` 引用。**meta-agent 编排一律走 MCP 工具**（见 SKILL.md §6）；本文件是 HTTP 直调与排查用的技术细节，含 MCP 覆盖不到的端点（rename / branch）、以及直调/排查所需的请求体与字段约定。

Pan 的 HTTP API 在 `packages/web/server.py`，基址 `http://127.0.0.1:<port>`（main 分支默认 **8768**、test 分支 8767；`config.json` 的 `port` 字段，`PAN_PORT` 环境变量可覆盖）。全部返回 JSON；错误通常返回 `{"error": "..."}`。**API 无鉴权、绑 loopback（127.0.0.1）**——不要在非本机环境暴露端口。

## 端点清单

### 批量 / 更新 / 特殊操作（MCP 覆盖不到或直调排查用）

| 方法 | URL | Body / 参数 | 返回 |
|------|-----|------------|------|
| `POST` | `/api/sessions/batch-delete` | `{"sessionIds": ["ses_a", "ses_b"]}` | `{"deleted": 2}`（含 kill worker、清理其他 session 的 report_subscriptions/managed 引用）。**MCP 等价工具：`session_batch_delete`（已有，逐个过 managed 隔离检查）** |
| `PATCH` | `/api/sessions/{id}` | `{"model": "...", "permissionMode": "...", "alwaysThinkingEnabled": true, "effort": "high", "maxThinkingTokens": 8192, "mcpServers": ["pan"], "forceMcp"?: true, "outputMode": "stream", "gameId": "..."}` | 更新后 session（设置**即时持久化**，worker 下次 (re)spawn 生效；**可中途更新 `mcpServers`**，中途换 adapter 才需 handoff）。`mcpServers` 传 manifest 中声明的**服务名列表**（服务端解析为完整配置）：非空即启用、`[]` 清空/禁用、**省略该字段 = 保持不变**（部分更新；`mcpEnabled` 仅是响应字段，请求体里传了会被忽略）；未知或不可用的 MCP server 返回 `error`，不会生成无效配置；模板 `mcp_mode=always/never` 锁死增删，body 带 `forceMcp: true` 跳过锁（UI 确认后使用）。改 `mcpServers`/`outputMode`（或有活 worker 时改其他进程相关字段：model/permission/effort/thinking/MCP/outputMode）时带 `requireRestart: true`，重启自动完成：**idle worker 自动 respawn 生效、running worker 回 idle 时自动 respawn、无 worker 下次 spawn 生效**——想立即切换仍可手动 agent_kill + agent_spawn（别名 worker_*）。**MCP 等价工具：`session_update`** |
| `POST` | `/api/sessions/{id}/rename` | `{"name": "new-name"}` | `{"sessionId","name","status":"renamed"}`。**无 MCP 工具，需 HTTP 直调** |
| `POST` | `/api/sessions/{id}/branch` | `{"name": "fork-name"}` | 复制 adapter transcript 新建 session（保留 workdir/character/MCP 绑定）。**无 MCP 工具，需 HTTP 直调** |
| `POST` | `/api/sessions/{id}/handoff` | `{"handoffPrompt": "...", "copySettings": true, "adapter"?: "...", "model"?: "...", "permissionMode"?: "..."}` | **替身交接**：创建孪生 session B 接替 A（见 SKILL.md §2.7）。等价 MCP 工具 `session_handoff`；`handoffPrompt` 必填，`copySettings=false` 时 `adapter` 必填 |
| `POST` | `/api/readonly` | `{"managerId": "...", "sessionId": "...", "readonlySession": true}` | 设置或清除已由 `managerId` 管理的 session 的持久只读状态；不能借此认领 session。只读目标拒绝其他 session 的任务/消息/通知，返回 `readonly_session` |
| `POST` | `/api/notify` | `{"targetSessionId": "...", "text": "...", "source"?: "..."}` | 持久化后台任务完成/状态通知到目标 `queue_pending`；无活 worker 时自动唤醒/spawn。该路由供 MCP `agent_notify` 使用，权限隔离由 MCP 层执行，不是普通任务派发 |
| `POST` | `/api/report-subscribe` | `{"managerId": "<meta-agent session id>", "sessionId": "<managed session id>"}` | `{"subscribed": true, "reportSubscriptions": [...]}`。**等价 MCP 工具：`report_subscribe`（编排首选）** |
| `POST` | `/api/report-unsubscribe` | 同上 | `{"subscribed": false, ...}`。等价 MCP 工具：`report_unsubscribe` |
| `POST` | `/api/claim` | `{"managerId": "...", "sessionId": "..."}` | 认领会话建立 managed 关系（带 `_check_access(claim=True)` 隔离检查；目标已被他人管理则拒绝）。等价 MCP 工具：`session_claim`（claim 自动 report_subscribe） |
| `POST` | `/api/unclaim` | `{"managerId": "...", "sessionId": "..."}` | 解除 managed 关系（同时退订该 session 报告）。等价 MCP 工具：`session_unclaim` |
| `POST` | `/api/worker/{worker_id}/restart` | — | 终止并重新 spawn worker 进程。`agent_send_force`（MCP，别名 worker_send_force）内部走此端点 |
| `POST` | `/api/worker/{worker_id}/steer` | `{"text": "补充指令"}` | 将补充指令注入正在运行的 Codex 原生回合，并在写入成功后落盘到 Pan history |
| `POST` | `/api/worker/{worker_id}/control` | `{"control": {"type": "terminal_input", "process_id": "...", "text": "..."}}` | 向运行中的 Codex 发送终端输入；`terminal_terminate` 可终止对应进程。也支持审批、用户输入、权限和 elicitation 控制 |
| `POST` | `/api/qq/subscribe` | `{"sessionId": "...", "target_type": "user"/"group", "target_id": "..."}` | Pan session 订阅某 QQ 会话 inbox 提醒（`@@@@by qq` 推送到 queue_pending）；等价 MCP 工具 `session_qq_subscribe`（pan server） |
| `POST` | `/api/qq/unsubscribe` | 同上 | 退订（等价 `session_qq_unsubscribe`） |

### 查询

| 方法 | URL | 参数 | 返回 |
|------|-----|------|------|
| `GET` | `/api/sessions` | — | `{"sessions": [...]}`（history 截断为最近 50 条，带 `historyTruncated`/`historyTotal`）——**轮询兜底用** |
| `GET` | `/api/sessions/{id}` | — | 单个 session 完整（含 `lastResult`、`workerStatus`、`managedBy`、`reportSubscriptions`） |
| `GET` | `/api/sessions/{id}/history` | `?limit=50&before=<index>` | `{"history", "total", "hasMore", "start"}` 分页 |
| `GET` | `/api/models` | `?adapter=cbc` | `{"models": [...], "default": "..."}` |
| `GET` | `/api/adapters` | — | 注册的 adapter 与能力（supportsResume/supportsFork） |

### 导入 / 设置 / Manifest（排查 / 维护用，2026-08-27 补录）

| 方法 | URL | 说明 |
|------|-----|------|
| `GET` | `/api/cbc/projects` / `/api/cbc/sessions` / `/api/cbc/browse` / `POST /api/cbc/sessions/import` | cbc 会话导入四件套（等价 MCP 工具 `session_import`，优先用 MCP） |
| `GET` | `/api/kimi/workspaces` / `/api/kimi/sessions` / `POST /api/kimi/sessions/import` | kimi 会话导入 |
| `GET` | `/api/opencode/sessions` / `POST /api/opencode/sessions/import` | opencode 会话导入 |
| `GET` / `POST` | `/api/adapters/{adapter}/sessions[/import]` | **通用导入端点**（claude/codex 等走此端点；各 adapter 的 sessions provider 化产物） |
| `GET` / `PUT` | `/api/settings/ui` | 全局显示设置读写 |
| `GET` | `/api/session-templates` | manifest 中的 Session 模板列表 |
| `GET` | `/api/mcp/servers` | manifest 中可选 MCP Server 列表（含 stdio command / HTTP URL 元数据；不含 env） |
| `POST` | `/api/manifest/reload` | 强制热重载 manifest（新增/修改 session template 后立即生效） |
| `GET` | `/api/manifest/command-routes` | QQ 前缀命令路由列表 |
| `GET` | `/api/worker/{id}/takeover-command` | 生成终端接管命令 |
| `POST` | `/api/qq/notify` / `GET /api/qq/contacts` | QQ 插件上报 inbox 更新 / 最近 QQ 联系人（QQ 通道内部使用） |

## 核心编排端点请求体（直调 / 排查用）

冷启动主链路端点（对应 SKILL.md §2.1 流程）：

| 方法 | URL | Body | 返回 |
|------|-----|------|------|
| `POST` | `/api/sessions` | `{"name":"fix-h1","adapter":"cbc","model":"hy3","permissionMode":"bypassPermissions","workdir":"...","alwaysThinkingEnabled":false,"effort":"","maxThinkingTokens":8192,"mcpEnabled":false,"outputMode":"stream","characterId":"..."}` | 完整 session（关键：`id`、`workdir`、`workerStatus:null`）。**只建 session，不 spawn worker** |
| `POST` | `/api/spawn` | `{"sessionId":"ses_..."}` | `{"workerId","sessionId","name","status","model"}` |
| `POST` | `/api/assign` | `{"sessionId":"ses_...","text":"任务内容"}` | `{"status":"queued","workerId","sessionId"}` |
| `DELETE` | `/api/sessions/{id}` | —（无 body） | `{"sessionId","status":"deleted"}` |

字段说明：
- `POST /api/sessions`：`name` 省略默认 `'default'`（建议始终显式命名），且全局唯一（不能含空格、≤64 字符）；其余字段均可省略。`adapter` 默认 `cbc`；`workdir` 默认取 name（相对基准见 SKILL.md §8.1）；`permissionMode` 默认取 config；`characterId` 会给定时覆盖 adapter/model/permissionMode（见 `packages/web/server.py` `_build_session_params`）。
- `POST /api/spawn`：已有 worker 会**先 kill 再新建**（一个 session 一个 worker）；`sessionId` 省略时等同 create+spawn（body 同 create 字段）。
- `POST /api/assign`：`sessionId`、`text` **均必填**；缺参返回 `{"ok":false,"error":{...}}`。worker 不存在时自动 spawn。完成异步经报告订阅 / `lastResult` 返回。
- `DELETE /api/sessions/{id}`：删除 session 并 kill 其 worker。

其余端点（`POST /api/task`、`POST /api/kill/{worker_id}`、`GET /api/list`）均有对应 MCP 工具，编排用 MCP 即可；HTTP 形态见 `packages/web/server.py`。

## 字段命名映射（id vs sessionId，snake_case vs camelCase）

HTTP JSON body 用 **camelCase**，MCP 工具参数用 **snake_case**——同一个值在不同层字段名不同：

| 概念 | HTTP 响应 | HTTP 请求 body | MCP 参数 |
|------|-----------|----------------|----------|
| session id | `id` | `sessionId` | `session_id` |
| worker id | `workerId` | `workerId` | `worker_id` |
| 权限模式 | `permissionMode` | `permissionMode` | `permission_mode` |
| 思考开关 | `alwaysThinkingEnabled` | `alwaysThinkingEnabled` | `always_thinking_enabled` |
| 最大思考 tokens | `maxThinkingTokens` | `maxThinkingTokens` | `max_thinking_tokens` |
| MCP 开关 | `mcpEnabled` | `mcpEnabled` | `mcp_enabled` |
| MCP servers | — | `mcpServers` | `mcp_servers` |
| 工作目录 | `workdir` | `workdir` | `workdir`（两边一致） |

**要点**：`POST /api/sessions` 返回的 `id` 与后续所有请求体的 `sessionId` 是**同一个值**（`ses_...`）。响应字段叫 `id`，但 spawn/assign/task 的 body 一律用 `sessionId`（MCP 里是 `session_id`）——不要照抄响应字段名传请求。

## Windows curl 内联 UTF-8 JSON 的坑

Windows 下 `curl -d '{"text":"中文…"}'` 内联中文 body 会报 `{"detail":"There was an error parsing the body"}`——终端默认编码（GBK/cp936）或 shell 引号转义导致请求体非 UTF-8。对策（实测可行）：
- `curl -X POST http://127.0.0.1:8767/api/assign --data-binary @body.json`（`body.json` 以 UTF-8 保存）；
- 或 python urllib / requests：`json.dumps(body).encode("utf-8")`。

纯 ASCII body 内联安全；中文/特殊符号一律走文件或脚本。

## 轮询兜底策略（report_subscribe 不可用时的 fallback）

> **编排首选是内部订阅（`report_subscribe` → `queue_pending`，见 SKILL.md §3）**。仅当该路径不可用时（如 SKILL.md §11.2 G9 跨端口 / G10 版本落后）才用 `session_get` 轮询兜底。

`GET /api/sessions/{id}` 看 `lastResult.status`（或 `session_list` 扫描全部 session，对 `done` 的读结果）。轮询粒度建议 ≥5s。

**放弃/超时策略**：
- **结束条件**：`lastResult.status` 变为 `done`（读 `result`）或 `error`（读 `result` 排查）→ 停止轮询。
- **放弃条件一（worker 已死）**：轮询中发现 `workerStatus` 变 `null` 且 `lastResult.status` 仍是 `queued`/`running` → watchdog 已回收或进程已死，任务不会继续 → 停止本轮，`agent_spawn` 后重新 assign。
- **放弃条件二（超时预算）**：为每轮任务设总预算。stream running 卡死判定基于**任务运行时长**（`worker.task_timeout_sec` 默认 1800s，见 SKILL.md §8.3），queued 静默超时 300s（`config.example.json`）、运行环境 config.json 实测 1200s——**轮询超过任务时长上限没有意义**：worker 要么已产出结果，要么已被 watchdog 判定卡死 kill。简单任务预算 60–120s；复杂任务预算取 `worker.task_timeout_sec` + 余量。到点仍无结果且 worker 存活 → 停止盲目轮询，先查卡死原因再决定重发。
