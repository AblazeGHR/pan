---
name: pan
description: Pan CLI Agent 编排中间层——冷启动操作手册。通过 MCP 工具管理会话（session）和 Worker 进程（cbc/kimi）。当需要创建会话、并行派发 worker、订阅完成通知、读取结果、清理 session 或了解 Pan 编排坑与约定时使用。
---

# Pan — CLI Agent 编排中间层（冷启动操作手册）

Pan 是 Supervisor/Worker 架构的 CLI Agent 编排器。你（Meta-Agent）通过 Pan MCP 工具调度多个 CLI Worker 进程（cbc/kimi），每个 Worker 拥有独立的会话（Session）和记忆（workdir）。

> **这份 SKILL.md 是 Pan 编排知识的单一事实源**（立项 `docs/archive/Pan冷启动Agent编排skill立项.md`）。**主源**：`docs/skills/pan/SKILL.md`（git 版本控制）；`.codebuddy/skills/pan/SKILL.md` 是**同步副本**（CodeBuddy 编辑器加载 skill 用，不进 git）——改内容先改主源，再复制到副本保持同步。MCP 工具 / HTTP API / workdir 约定变化时必须同步更新本文件。

## 0. 快速开始（30 秒冷启动）

1. MCP 工具接线（命名空间 `mcp__pan__`，G2 实测 2026-08-17）：
   - **`--mcp-config` 路径（meta-agent 常态）**：由 Pan adapter 自动注入 `data/mcp-configs/<session_id>.mcp.json`，工具 **direct connected，直接调用即可，无需 ToolSearch**。"工具列表里没看到"≠未连接，先直接试调一次。
   - **项目级 `.mcp.json` 发现路径**：工具是 deferred 的 → `ToolSearch`（查询词 `pan`/`mcp`）→ `DeferExecuteTool` 调用。
   - **拿手册**：MCP 工具 `pan_handbook()` 直接返回本文件全文（§7「其他」）——接线完成后若不清楚编排流程，先调它再动手。
   - 前置三对齐：MCP server 目标端口（`PAN_API_URL`，默认 8768）**必须**与 `PAN_AGENT_SESSION_ID` 所在服务同实例，否则 `report_subscribe` 失效（§3.2 / §12.2 G9）。
2. 编排主链路：`session_create → worker_assign → 盯梢（worker.result）→ session_get 查结果 → session_delete 收尾`。
3. 完成通知**二选一**（详见 §3，同用会重复通知）：
   - 外部协调（CodeBuddy 会话）：`Monitor` + `/ws/agent` 订阅 `worker.result`/`worker.zombie`；
   - meta-agent 内部：MCP `report_subscribe` → 报告落到自己 `queue_pending` 队列。
4. 端口约定：本分支（基于 pan-test）默认 **8767**；MCP server 默认连 **8768**（踩坑 #11）。端口不符时用 `PAN_API_URL` / `PAN_WS_URL` 覆盖。

## 1. 核心概念

| 概念 | 说明 |
|------|------|
| **Session** | 持久化的对话容器，包含 history、model、adapter、workdir 等配置。独立于 Worker 生命周期。 |
| **Worker** | 临时的 CLI 子进程（cbc/kimi），绑定到一个 Session。可被 kill、回收、重建。 |
| **Adapter** | CLI 工具类型：`cbc`（CodeBuddy CLI）或 `kimi`（Kimi CLI） |
| **Model** | AI 模型名称，如 `hy3`、`deepseek-v4-flash` |
| **workdir** | Session 的工作目录，也是 Worker 进程的 `cwd`（见 §9.1） |
| **taskSeq** | 每个任务的序号；`worker.result` 事件用它配对等待中的 handoff、做重连补发游标 |

关键规则：
- Session 是持久化的——kill/回收 Worker 不会删除 Session 数据。
- 一个 Session 同一时间只有一个 Worker（spawn 时若有旧 worker 先 kill）。
- Worker 回复是异步的——`worker_assign` 返回 `queued`，需要随后订阅 `worker.result` 或 `session_get` 读取。
- Worker 会被 watchdog 自动回收（空闲/静默超时），用前若 `workerStatus` 为 `null` 需重新 spawn。
- 握手前提：`PAN_API_URL`（HTTP）、`PAN_WS_URL`（WS）必须指向实际运行端口。

## 2. 编排工作流（全景）

```
session_create → worker_spawn → worker_assign / handoff → 盯梢 → 查结果 → 收尾
```

### 2.1 并行 fan-out（推荐主流程：assign + 订阅 result）

```
1. 为每个任务创建/复用 session
   session_create(name="fix-h1", adapter="cbc", model="hy3")
   → 返回 id: "ses_abc123..."（后续请求体的 session_id / MCP 的 session_id 用它，§5.2 G6）

2. 异步分派（立即返回，不阻塞）
   worker_assign(session_id="ses_a...", text="任务A")
   worker_assign(session_id="ses_b...", text="任务B")
   → 都返回 {"status": "queued", "workerId": "...", "sessionId": "..."}

3. 盯梢（§3 二选一）：
   外部协调 → Monitor + /ws/agent 订阅 worker.result（§3.1）
   内部协调 → report_subscribe 订阅（§3.2）

4. 收集全部 worker.result 后，逐个 session_get 读最终结果汇总

5. 收尾：session_delete 释放资源（批量用 POST /api/sessions/batch-delete，§5）
```

**不需要手动轮询**。assign 之后 worker 会自动 spawn（如果该 session 无活 worker）。

### 2.2 串行依赖步骤（handoff，DEPRECATED 但保留）

> `worker_handoff` / `/api/handoff` 已标 **DEPRECATED**（立项 4.7）："等"应是 meta-agent 的默认 idle 状态，而非阻塞调用。仅当确需严格同步返回时使用；**新编排一律 assign + report_subscribe**。详见 §9.4。

```
worker_handoff(session_id="ses_abc...", text="串行任务", timeout=600, task_id="t1")
→ 阻塞直到完成，返回 {"status":"done","result":"...","workerId":"..."}
```

- 默认 10 分钟超时；超时返回 `{"status":"pending"/"error","result":"handoff timed out after 600s"}`。
- **幂等**：超时后用**同一个 task_id** 重发——已完成返回缓存结果，进行中不重复入队（防双跑）。

### 2.3 在已有会话上继续对话

```
1. session_list()  → 找到目标 session_id 与 workerStatus
2. worker_spawn(session_id="ses_abc...")   # 仅当 workerStatus 为 null（已回收/已死）
3. worker_assign(session_id="ses_abc...", text="继续之前的话题...")
```

任务文本的写法（取决于 `cliSessionId` 有无上下文，见 §2.6）。

### 2.4 检查状态

```
1. session_list()   → 所有 session 的 workerStatus: idle/running/queued/error/null
2. 对感兴趣的 session: session_get(session_id=...) → history + lastResult
3. 大历史分页: session_history(session_id=..., limit=50, before=...) 或 session_get(limit=...)
```

### 2.5 清理（收尾）

```
1. session_list()  → 找到完成任务的会话
2. session_delete(session_id=...)          # 单个
   POST /api/sessions/batch-delete          # 批量（MCP 未覆盖，见 §5）
   body: {"sessionIds": ["ses_a","ses_b"]}
```

**及时清理**：不再需要的 session 用 delete 释放进程与磁盘；watchdog 只回收进程，不删 session。

### 2.6 派发规范：worker 无记忆，session 有记忆

**记忆模型**：Worker 是**临时进程**——每次 spawn 都是全新进程，**无记忆**；Session 是**持久化容器**——保存 `history` + `cliSessionId`。重新 spawn 时 adapter 检测到 `cliSessionId` 非空即传 `cbc --resume <cliSessionId>`（`packages/core/adapters/cbc/adapter.py` `resume_args`），cbc 从 transcript（JSONL）恢复**完整上下文**（原任务描述、进度、历史对话都在）。session 的记忆来自持久化，不来自 worker。

**派发判定**：派发任务前先 `session_get(session_id)` 查 **`cliSessionId`** 字段：

| `cliSessionId` | 含义 | 任务文本写法 |
|----------------|------|-------------|
| **非空** | worker 将 `--resume` 恢复已有完整上下文 | **一律用简短指令**（追加任务 / 恢复中断 / 串行下一步 / 追问修正）：指出现有上下文里要做什么即可，**不要重发完整任务描述**——上下文已有原任务与进度，重发浪费 token，且措辞差异可能让 worker 误判为新任务/新要求 |
| **为空 / null** | 新 session 或 worker 从未建立，worker 无上下文 | **任务描述必须自包含**：背景 / 目标 / 涉及文件（相对 workdir）/ 边界 / 验收标准 |

- 与 §2.3 的关系：§2.3 解决「找 session + spawn」，本小节解决「任务文本怎么写」——`cliSessionId` 非空的 session 追加/恢复任务时：`worker_spawn`（`workerStatus` 为 null 时）→ 简短指令。

## 3. 完成通知：二选一（互斥，勿同用）——**meta-agent 编排一律优先用「内部订阅」**

两种完成通知路径**互斥，同用会重复通知**（立项 `Worker监督与事件驱动模式.md` §4.4）：

| 通知方式 | 适用场景 | 机制 | 优先级 |
|---------|---------|------|--------|
| **外部协调**（Monitor + `/ws/agent`） | 外部 CodeBuddy 会话盯梢 worker | 订阅 WS，`worker.result` / `worker.zombie` 事件，实时推送 | 备选 |
| **内部报告**（`report_subscribe` + `queue_pending`） | meta-agent 管理自己的 subagent | MCP `report_subscribe`/`report_unsubscribe`，完成时报告入 meta-agent 的**落盘队列** `queue_pending`，唤醒其 consumer | **首选** |

> **优先级约定**：meta-agent 管理 subagent 时**一律优先使用「内部报告」（`report_subscribe`）**——异步、落盘可恢复（跨服务重启不丢）、跨协调者、不依赖外部会话；它是主链路的一部分（`session_create → worker_assign → report_subscribe 等完成 → session_get → session_delete`）。外部 Monitor + `/ws/agent` 仅当确实需要**外部**（非 meta-agent）实时盯梢时才用。

- **外部协调者监听 `worker.result` 时，meta-agent 不应再订阅该 session 的报告（反之亦然）。**
- 报告推送是**订阅制（opt-in）**：未订阅不 append `queue_pending`，只保留 `worker.result` 广播供外部用。
- 取舍：外部协调（实时、无落盘、强绑定 CodeBuddy Monitor）vs 内部报告（异步、落盘可恢复、跨协调者、不依赖外部会话）。
- 关联：全局 watchdog 保证"队列非空 → 拉起 worker"（§9.6），本机制保证"worker 完成 → 协调者感知"，两者互补。

### 3.1 外部协调：Monitor + /ws/agent

见 §4 监督模板 `monitor_workers.py`。

### 3.2 meta-agent 内部：report_subscribe + queue_pending（**首选**）

meta-agent 编排 worker 时**默认走本小节**：`session_create → worker_assign → report_subscribe`（或先订阅再 assign），完成后报告自动入 `queue_pending` 推送给你，不需要 Monitor/轮询。

**前置条件（G5 实测 2026-08-17，不满足则本路径直接失效，退回 §5 轮询）**：

| 前置 | 说明 | 不满足时的现象 |
|------|------|---------------|
| `PAN_AGENT_SESSION_ID` 存在 | **由 Pan adapter 注入 MCP server 进程环境**（写在 `data/mcp-configs/<session_id>.mcp.json` 的 `env` 段，随 stdio 启动带入）。**不在你的 shell `env` 里**——用 `env \| grep` 查不到属正常，别据此判断缺失；可从 `CODEBUDDY_MCP_CONFIG` 文件名/内容确认自己的 session id | 工具报缺少 manager id |
| **同实例**：manager session 与被管 session 在**同一个 Pan 服务**（同端口） | MCP server 默认连 `PAN_API_URL`（默认 **8768**），而 `PAN_AGENT_SESSION_ID` 可能属于**另一个**服务（如 8767 pan-test）。三者（mcp-config `cwd`/`PAN_API_URL`/manager session 所在服务）必须一致 | manager session 在目标服务上"不存在"，订阅无效（§12.2 G9） |
| 服务端含 `report-subscribe` 路由 | 该端点较新；**运行中的服务可能落后于 MCP 工具版本** | `report_subscribe` 返回 `{"detail":"Not Found"}`（404，实测于 8768/main；本分支 `server.py` 已含）（§12.2 G10） |

```
1. worker_assign 前先订阅（前置见上表）：
   report_subscribe(session_id="ses_managed...")
   → {"subscribed": true, "reportSubscriptions": [...]}

2. worker 完成（done/error）→ 报告 append 到你的落盘队列 queue_pending：
   {"status","result","sessionId","taskId","workerId"}

3. 你的 consumer 被 report_signal 唤醒，积压报告批量拼接为一条消息：
   ───── 子任务报告（来源 sessionId=ses_...）─────
   {"status": "done", ...}
```

`queue_pending` 是**落盘真源**，`pending_signal` 只是唤醒信号（§9.6）。报告可跨进程重启恢复。

> **订阅即接管**：`report_subscribe` 同时把目标 session 归为调用方（meta-agent）管理（自动 claim，见 `packages/mcp/server.py`）；`report_unsubscribe` 仅能退订**自己管理**的 session。外部协调者走 `/ws/agent` 订阅**不会**建立 managed 关系——这是内部报告路径与外部广播路径的一个重要区别。

## 4. 盯梢模板：monitor_workers.py

**监督脚本**（随项目维护，`packages/scripts/monitor_workers.py`）——**双通道**：

1. **WS 事件**（实时）：订阅 `worker.result`（正常完成）**和** `worker.zombie`（意外死亡 / watchdog 回收 / 进程退出）——worker 意外丢失对协调者可见。
2. **健康检查**（防「假 running」，每 30s 一次）：轮询 HTTP `GET /api/sessions/{id}` + 检查 transcript 文件 mtime（`~/.codebuddy/projects/<d-project-<workdir>/*.jsonl`，即 workdir 绝对路径 slug 化后的项目目录）。Pan 报 `running` 但 session `updatedAt` 与 transcript **均**超过 3 分钟无更新 → 输出一行 `STALE`（假 running / 卡死）。**去重冷却**：STALE 只在进入卡死时输出一次；恢复活跃后输出 `RECOVERED`，若再次卡死会再次 STALE。

```bash
# 用 Pan 服务 .venv 的 python 运行（已含 websockets）
python packages/scripts/monitor_workers.py
# 通过 PAN_WS_URL 环境变量指定端口（默认 ws://127.0.0.1:8768/ws/agent；HTTP 基址自动由它推导）
PAN_WS_URL=ws://127.0.0.1:8767/ws/agent python packages/scripts/monitor_workers.py
# 按 sessionId 过滤订阅与健康检查（只盯自己派发的 session，避免其他 session 的事件打扰）
PAN_SESSION_IDS=ses_a,ses_b python packages/scripts/monitor_workers.py
```

- **`PAN_SESSION_IDS` 按 session 过滤**（逗号分隔）；省略 = 订阅/检查所有。**实践：派发 worker 后用 `PAN_SESSION_IDS` 只订阅自己派发的 session**——否则同一服务上其他协调者/测试 session 的事件会频繁唤醒你（噪音）。
- 健康检查可选环境变量：`PAN_API_URL`（HTTP 基址，默认由 `PAN_WS_URL` 推导）、`PAN_HEALTH_INTERVAL`（检查间隔，默认 30s）、`PAN_STALE_AFTER`（静默阈值，默认 180s）。
- 每事件输出一行（flush），一行一事件，兼容 Monitor 增量输出协议：

```
MONITOR_CONNECTED
MONITOR_SUBSCRIBED
DONE session=ses_... status=done worker=worker-1
DIE  session=ses_... worker=worker-2 returncode=1
STALE session=ses_... worker=worker-3 status=running stale_for=220s   # 假 running：3 分钟无任何活动
RECOVERED session=ses_... worker=worker-3 status=running              # 恢复活跃
MONITOR_DISCONNECTED: ...   # 断线自动 5s 后重连
```

**Monitor 启动**（CodeBuddy Monitor 工具，command 模式）：

```
Monitor(command="python packages/scripts/monitor_workers.py", persistent=true)
```

每次脚本输出一行 → Monitor 唤醒协调者（秒级感知，替代 5 分钟轮询）。

**为什么脚本中转，不直接用 Monitor 的 `ws` 模式**：Monitor 的 `ws` 模式**拒绝连接私有/内部地址**（`127.0.0.1`/`localhost` 都被拒）——CodeBuddy 的 WebSocket 安全限制。所以用 `command` 模式跑 python 脚本，由脚本连本机 WS（无此限制），再经 stdout 中转给 Monitor。

依赖：`websockets` 库（Pan 服务 `.venv` 已含，如 `D:/project/Pan/.venv`；缺失时先 `pip install websockets`）。

## 5. HTTP API 速查（MCP 覆盖不到的）

Pan 的 HTTP API 在 `packages/web/server.py`，基址 `http://127.0.0.1:<port>`（本分支默认 **8767**；`config.json` 的 `port` 字段，`PAN_PORT` 环境变量可覆盖）。全部返回 JSON；错误通常返回 `{"error": "..."}`。

> 以下端点覆盖冷启动最常用操作：**批量删除 / rename / branch 无 MCP 工具**，需 HTTP 直调；PATCH / report-subscribe / 轮询虽有 MCP 工具，这里列出 HTTP 形态供直调（`curl` / urllib）与排查：

| 方法 | URL | Body / 参数 | 返回 |
|------|-----|------------|------|
| `POST` | `/api/sessions/batch-delete` | `{"sessionIds": ["ses_a", "ses_b"]}` | `{"deleted": 2}`（含 kill worker） |
| `PATCH` | `/api/sessions/{id}` | `{"model": "...", "permissionMode": "...", "alwaysThinkingEnabled": true, "effort": "high", "maxThinkingTokens": 8192, "mcpEnabled": true, "mcpServers": ["pan"], "outputMode": "stream", "gameId": "..."}` | 更新后 session；改进程相关字段（model/effort/thinking/MCP/outputMode）时带 `requireRestart: true`，**idle worker 自动 respawn 生效、running worker 回 idle 时自动重启**——无需手动 kill+spawn（想立即生效仍可手动 worker_kill + worker_spawn） |
| `POST` | `/api/sessions/{id}/rename` | `{"name": "new-name"}` | `{"sessionId","name","status":"renamed"}` |
| `POST` | `/api/sessions/{id}/branch` | `{"name": "fork-name"}` | 复制 adapter transcript 新建 session（保留 workdir/character/MCP 绑定） |
| `POST` | `/api/report-subscribe` | `{"managerId": "<meta-agent session id>", "sessionId": "<managed session id>"}` | `{"subscribed": true, "reportSubscriptions": [...]}` |
| `POST` | `/api/report-unsubscribe` | 同上 | `{"subscribed": false, ...}` |
| `GET` | `/api/sessions` | — | `{"sessions": [...]}`（history 截断为最近 50 条，带 `historyTruncated`/`historyTotal`）——**轮询用** |
| `GET` | `/api/sessions/{id}` | — | 单个 session 完整（含 `lastResult`、`workerStatus`、`managedBy`、`reportSubscriptions`） |
| `GET` | `/api/sessions/{id}/history` | `?limit=50&before=<index>` | `{"history", "total", "hasMore", "start"}` 分页轮询 |
| `GET` | `/api/models` | `?adapter=cbc` | `{"models": [...], "default": "..."}` |
| `GET` | `/api/adapters` | — | 注册的 adapter 与能力（supportsResume/supportsFork） |

**轮询模式**（不做 WS 订阅时的兜底）：`GET /api/sessions/{id}` 看 `lastResult.status`（或 `session_list` 扫描全部 session，对 `done` 的读结果）。轮询粒度建议 ≥5s；`worker.result` 事件秒级，优于轮询。

**轮询放弃/超时策略（G7）**：
- **结束条件**：`lastResult.status` 变为 `done`（读 `result`）或 `error`（读 `result` 排查）→ 停止轮询。
- **放弃条件一（worker 已死）**：轮询中发现 `workerStatus` 变 `null` 且 `lastResult.status` 仍是 `queued`/`running` → watchdog 已回收或进程已死，任务不会继续 → 停止本轮，`worker_spawn` 后重新 assign（或查 `worker.zombie` 确认死因）。
- **放弃条件二（超时预算）**：为每轮任务设总预算。静默超时默认 300s（`config.example.json`）、运行环境实测 1200s——**轮询超过静默超时上限没有意义**：worker 要么已产出结果，要么已被 watchdog 判定卡死 kill。简单任务预算 60–120s；复杂任务预算取 `worker.timeout_sec`（§9.3）+ 余量。到点仍无结果且 worker 存活 → 停止盲目轮询，先查卡死原因（静默/大文件读取，§9.3 坑 A/B）再决定重发。
- 首选仍是 WS 订阅 / `report_subscribe`：秒级且 `worker.zombie` 第一时间感知异常，轮询只是没有订阅时的兜底。

> **Windows curl 内联 UTF-8 JSON 的坑（G4）**：Windows 下 `curl -d '{"text":"中文…"}'` 内联中文 body 会报 `{"detail":"There was an error parsing the body"}`——终端默认编码（GBK/cp936）或 shell 引号转义导致请求体非 UTF-8。对策（实测可行）：
> - `curl -X POST http://127.0.0.1:8767/api/assign --data-binary @body.json`（`body.json` 以 UTF-8 保存）；
> - 或 python urllib / requests：`json.dumps(body).encode("utf-8")`。
> 纯 ASCII body 内联安全；中文/特殊符号一律走文件或脚本。

### 5.1 核心编排端点请求体（MCP 直调 / 排查用）

冷启动主链路 4 个端点（§2.1 流程对应）：

| 方法 | URL | Body | 返回 |
|------|-----|------|------|
| `POST` | `/api/sessions` | `{"name":"fix-h1","adapter":"cbc","model":"hy3","permissionMode":"bypassPermissions","workdir":"...","alwaysThinkingEnabled":false,"effort":"","maxThinkingTokens":8192,"mcpEnabled":false,"outputMode":"stream","characterId":"..."}` | 完整 session（关键：`id`、`workdir`、`workerStatus:null`）。**只建 session，不 spawn worker** |
| `POST` | `/api/spawn` | `{"sessionId":"ses_..."}` | `{"workerId","sessionId","name","status","model"}` |
| `POST` | `/api/assign` | `{"sessionId":"ses_...","text":"任务内容"}` | `{"status":"queued","workerId","sessionId"}` |
| `DELETE` | `/api/sessions/{id}` | —（无 body） | `{"sessionId","status":"deleted"}` |

字段说明：
- `POST /api/sessions`：`name` 省略默认 `'default'`（建议始终显式命名），且全局唯一（不能含空格、≤64 字符）；其余字段均可省略。`adapter` 默认 `cbc`；`workdir` 默认取 name（相对基准见 §9.1）；`permissionMode` 默认取 config；`characterId` 会给定时覆盖 adapter/model/permissionMode（见 `packages/web/server.py` `_build_session_params`）。
- `POST /api/spawn`：已有 worker 会**先 kill 再新建**（一个 session 一个 worker）；`sessionId` 省略时等同 create+spawn（body 同 create 字段）。
- `POST /api/assign`：`sessionId`、`text` **均必填**；缺参返回 `{"ok":false,"error":{...}}`。worker 不存在时自动 spawn。完成异步经 `worker.result` 事件 / `lastResult` 返回。
- `DELETE /api/sessions/{id}`：删除 session 并 kill 其 worker。

其余端点（`POST /api/task`、`POST /api/handoff`、`POST /api/kill/{worker_id}`、`GET /api/list`）均有对应 MCP 工具，冷启动用 MCP 即可；HTTP 形态见 `packages/web/server.py`。

### 5.2 字段命名映射（id vs sessionId，snake_case vs camelCase）

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

**G6 要点**：`POST /api/sessions` 返回的 `id` 与后续所有请求体的 `sessionId` 是**同一个值**（`ses_...`）。响应字段叫 `id`，但 spawn/assign/task/handoff 的 body 一律用 `sessionId`（MCP 里是 `session_id`）——不要照抄响应字段名传请求。

**MCP server 自身**：`python -m packages.mcp.server`（stdio）；`--pan-url` / `PAN_API_URL` 覆盖 API 基址；`--transport sse --port 9740` 走 SSE。manifest：`packages/mcp/manifest.json`。

## 6. /ws/agent 订阅协议

WebSocket 端点 `ws://127.0.0.1:<port>/ws/agent`。

**客户端 → 服务端**：

| 消息 | 格式 | 说明 |
|------|------|------|
| subscribe | `{"type":"subscribe","eventTypes":["worker.result","worker.zombie"],"sessionIds":["ses_..."]}` | `eventTypes`：省略/空数组 → 默认 `["worker.result"]`；`["*"]` 订阅全部。`sessionIds`：省略 → 所有 session；只过滤 `worker.result`。回 `{"type":"subscribed",...}` |
| reconnect | `{"type":"reconnect","sessionIds":["ses_..."]}` | 断线重连补发：每 session 未消费的 `worker.result`（`consumed_seq < taskSeq`），带 `replayed: true` |

**服务端 → 客户端事件**：

| 事件 | 字段 | 说明 |
|------|------|------|
| `worker.result` | `workerId, sessionId, status(done/error), result, taskSeq` | **任务完成**，默认订阅 |
| `worker.zombie` | `workerId, sessionId, returncode` | 进程退出/被杀/回收瞬间广播（订阅方据此感知异常丢失） |
| `worker.crashed` | `workerId, sessionId, returncode` | 非零退出 |
| `worker.status` | `workerId, sessionId, status, source` | 状态切换（running 等） |
| `worker.stream` | `workerId, sessionId, event` | 原始 stream 事件（默认不订阅，防 context 爆炸） |
| `worker.spawned` | `sessionId, workerId, name, status, model` | worker 生成 |
| `session.created/updated/renamed/deleted` | `sessionId, name?...` | session 生命周期 |
| `sessions.deleted` | `sessionIds` | 批量删除 |
| `handoff.result` / `assign.result` / `send.result` | 含 `status/result` | WS 主动调用（type=handoff/assign/send）的同步应答 |
| `subscribed` / `error` | — | 协议握手 / 错误 |

订阅状态：每个连接独立维护 `consumed_seq`（每 session 已消费的 result 序号），重连补发据此推进。**订阅可限定 session**：只收关心的 session，减少无关唤醒。

## 7. 可用 MCP 工具

> 调用方式见 §0.1：`--mcp-config` 注入路径下工具 **直接可调**（无需 ToolSearch）；仅项目级 `.mcp.json` 发现路径才是 deferred（`ToolSearch("pan")` → `DeferExecuteTool`）。工具命名空间 `mcp__pan__`。
>
> **巡检慎用 `session_list`**：无过滤参数、返回全部 session 完整 history，实测 310KB 会撑爆工具输出上限（§12.2 G8）。改用 `worker_list` + 定向 `session_get(session_id, limit=15)`。

### 会话管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `session_create` | `name`, `adapter?`, `model?`, `permission_mode?`, `workdir?` | 创建会话。workdir 默认 `data/workdirs/<name>`，Pan 外目录用绝对路径（§9.1） |
| `session_list` | (无) | 列出所有会话及 worker 状态（轮询兜底用） |
| `session_get` | `session_id`, `limit?` | 会话详情（history + lastResult）；limit>0 截断 |
| `session_update` | `session_id`, 各设置项 | PATCH 封装；改进程相关配置（model/effort/thinking/MCP/outputMode）时 **idle worker 自动 respawn 生效**、running worker 回 idle 时自动重启（§5 PATCH） |
| `session_delete` | `session_id` | 删除会话并 kill worker |
| `session_history` | `session_id`, `limit?`, `before?` | 分页历史 |

### Worker 管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `worker_spawn` | `session_id?`, `name?`, `adapter?`, `model?`, `workdir?` | 生成 worker；给 name 则先建 session。已有 worker 会先 kill（一个 session 一个 worker） |
| `worker_task` | `session_id?`, `worker_id?`, `text`, `source?` | 发任务（异步，返回 queued）；worker 不存在时自动 spawn；`source` 默认 `"agent"` |
| `worker_handoff` | `session_id`, `text`, `timeout?`, `task_id?` | **[DEPRECATED]** 同步阻塞。串行依赖/严格同步返回值才用；传 `task_id` 幂等重试（§9.4） |
| `worker_assign` | `session_id`, `text` | **异步分派**（并行 fan-out 用）：立即返回 queued，完成经 `worker.result` 事件回调 |
| `worker_send` | `worker_id`, `text` | 向已有 worker 发消息（多轮协作）；Pan 内 session 自动加 `////by agent` 前缀（§9.5） |
| `worker_kill` | `worker_id` | 终止 worker 进程（session 保留） |
| `worker_list` | (无) | 列出所有运行中 worker |

### 报告订阅（meta-agent 内部）

| 工具 | 参数 | 说明 |
|------|------|------|
| `report_subscribe` | `session_id` | 订阅被管理 session 的完成报告（需 `PAN_AGENT_SESSION_ID` 环境变量，仅 Pan 内 session 生效） |
| `report_unsubscribe` | `session_id` | 取消订阅 |

### 其他

| 工具 | 参数 | 说明 |
|------|------|------|
| `model_list` | `adapter?` | 列出可用模型 |
| `pan_handbook` | (无) | **返回本 SKILL.md 全文**（读文件实时返回，单一事实源，立项 C）。冷启动 agent 不确定编排流程时先调它；内容与 §0–§12 完全一致 |

## 8. 状态判断

`session_get` 的 `lastResult.status`：

| status | 含义 | 操作 |
|--------|------|------|
| `"queued"` | 任务已入队 | 等待 5-10 秒后重查 |
| `"running"` | Worker 执行中 | 继续等，**不要重复提交** |
| `"done"` | 任务完成 | 读 `result` 字段 |
| `"error"` | 任务失败 | 读 `result` 字段取错误 |
| `"pending"` | handoff 超时后任务仍在跑 | 用同 task_id 重试或 session_get 补查 |

`session_list` 的 `workerStatus`：
- `"queued"` / `"running"` / `"idle"`（可发任务）/ `"error"` / `"held"` / `"zombie"`（跳过回收）
- `null` → 无 worker（watchdog 已回收或未 spawn，需 `worker_spawn`）

## 9. 坑与约定

### 9.1 workdir 机制

- 默认：`data/workdirs/<name>`（session 名 slug 化，非法字符替换为 `-`）。
- **相对基准 = 实际运行的那个 Pan 服务实例的数据根**，不是你的当前项目目录、也不是 mcp-config 的 `cwd`（G3/G12）。实测：8768 服务（cwd `D:\project\Pan`）→ 落 `D:\project\Pan\data\workdirs\<name>`；8767（pan-test）→ 落 `D:\project\pan-test\data\workdirs\<name>`。**以 `session_create` 返回的 `workdir` 字段为准**。
- 相对值一律按 slug 规则清理后放进 `data/workdirs/`。
- `session_delete` **不删 workdir 目录**（只 kill worker + 删 session 元数据），磁盘留空目录，需要时自行清理（G11）。
- **绝对路径可指定 Pan 外目录**（如 `D:/some/project`）——Worker 的 `cwd` 就是 workdir，cbc 把 workdir 当项目目录（JSONL + resume 都在这里）。
- 同名 session 名必须唯一；workdir 默认取 session 名。
- 文件系统 API（`/api/cbc/browse` 等）限在 workdir 内，`..` 逃逸会被拒绝。

### 9.2 mcp-config 收敛到 `data/mcp-configs/`

- 会话启用 MCP 后，配置写在 **`data/mcp-configs/<session_id>.mcp.json`**（立项 4.9），由 adapter 在 spawn 时自动生成，并传 `--mcp-config <path>`。
- **绝不写 `<workdir>/.mcp.json`**：workdir 可能在 Pan 外（污染外部目录/不可写），且 cbc 会把 project-scope 的 `.mcp.json` 注册为 MCP server，启动失败时**阻断** `--mcp-config` 注入（踩坑 #15）。
- 注入唯一通道是 **`--mcp-config` 显式传入**；`-d` **不会**自动发现 `.codebuddy/mcp.json`（踩坑 #6/#15）。
- `--mcp-config` 路径下 MCP 工具为 **direct connected（非 deferred）**，无需 ToolSearch；项目级 `.mcp.json` 发现才是 deferred。

### 9.3 watchdog 行为（静默超时 vs 空闲回收）

配置在 `config.json` 的 `worker` 段（改后重启生效）：

| 条件 | 行为 | 默认 / 本分支实测 |
|------|------|------------------|
| `running`/`queued` 持续**无任何 stdout 输出**超过 `worker.timeout_sec` | 判定卡死 → kill（等待中的 handoff 收到 error） | `config.example.json` 默认 **300s**；运行环境实测 **1200s** |
| `idle` 持续超过 `worker.idle_sec` | 空闲回收 → kill（session 保留） | **300s** |
| `held`（takeover）/ `zombie` | **跳过**，不回收 | — |

- `last_activity` 每次 stdout 有事件即刷新——**长任务只要持续输出就不会被误杀**，超时只针对"进程活着但完全静默"的卡死。
- **坑 A：长思考误杀**。深度推理（thinking 阶段长时间无输出）可能超过 `timeout_sec` 被当卡死 kill。对策：调大 `timeout_sec`（实测 1200s），或把任务拆小。
- **坑 B：大文件读取**。单次读超大文件耗时久且无输出，同样触发静默超时。对策：**分段读大文件**（按行/按偏移分批），每段产生输出刷新 `last_activity`。
- 回收后 `workerStatus` 变 `null`，session 数据完好；下次 `worker_spawn`/`worker_assign` 自动重建并恢复上下文。
- MCP one-shot 模式由读取超时承担（同一 `timeout_sec`），watchdog 只做 idle 回收。
- **全局 watchdog**（服务级，生命周期=Pan）：周期扫描"落盘队列 `queue_pending` 非空但没有活 worker 的 session"，自动 `create_worker` 恢复——自愈（立项 4.4）。

### 9.4 handoff deprecated → 用 assign + report_subscribe

- `worker_handoff`（MCP）与 `POST /api/handoff` 已标 **DEPRECATED**（立项 4.7）。新编排用 `worker_assign` + `report_subscribe`（§3.2）。
- 理由："等"应是 meta-agent 的默认 idle 状态，而非阻塞调用；阻塞会占用协调者、易被中断。
- 若确需严格同步返回（串行依赖），仍可用：默认 `timeout=600s`；超时返回 `{"status":"pending"/"error"}`。
- **幂等**：`task_id` 是幂等键——超时后**同 task_id 重发**：已完成 → 返回缓存结果；进行中 → 不重复入队（防双跑）。taskId 注册表有 TTL 惰性清理。

### 9.5 `////by agent` 前缀

- `worker_send` 在 Pan 内 session（环境注入 `PAN_AGENT_SESSION_ID`/`PAN_AGENT_SESSION_TITLE`）下发消息时自动加前缀（立项 4.8）：

```
////by agent : ses_xxx | session-title
{text}
```

- 用途：目标 worker 区分"meta-agent 编排消息"与真实用户消息。
- 编排时注意：目标 worker 收到带该前缀的消息应识别为编排指令；`worker_assign`/`worker_handoff` 不发此前缀（只有 `worker_send` 拼）。

### 9.6 pending_signal 队列（+ 落盘 queue_pending）

- 每个 Worker 有一个内存 `pending_signal`（asyncio.Queue），consumer 循环阻塞在它上面。
- **普通任务**：入队 `{text, source, seq, taskId}` → consumer 取出 → 执行。
- **报告信号**：入队 `{"type":"report_signal"}` ——**只负责唤醒**，报告正文在 meta-agent 的**落盘队列** `Session.queue_pending`（真源）。consumer 被唤醒后从落盘队列批量拉取，拼接成一条消息（`─────` 分隔 + 来源标注）处理，消费即删。
- 落盘真源 + 内存信号：服务重启不丢报告；全局 watchdog 看到 `queue_pending` 非空无活 worker 会自动拉起。

### 9.7 其他约定

- **端口**：本分支（pan-test 基线）`port` **8767**；`main` 分支 8768。MCP server 默认 `PAN_API_URL=http://127.0.0.1:8768` ——**换分支/换端口必查**，否则 `[WinError 10061] 连接被拒`（踩坑 #11）。
- **API 无鉴权、绑 loopback**（127.0.0.1）——不要在非本机环境暴露端口。
- **MCP deferred 判定**：工具搜不到 ≠ 未连接。`ToolSearch` 搜得到 = deferred（`.mcp.json` 路径）；搜不到 = 未连接（多半 `--mcp-config` 没传或 cwd 错）。`--mcp-config` 路径下工具应直接可见。
- **带 character 的 session 首次任务**会被 memory 加载阻塞（embedding 首次加载 + 网络重试），可配 `memory.enabled: false` 或依赖 15s 超时降级（踩坑 #12）。

## 10. 最佳实践

1. **先查后做**：`session_list()` 了解当前状态再操作。
2. **命名规范**：Session 名短横线连接、有语义（`fix-h1`、`debug-auth`）；同名不可重复。
3. **并行 fan-out 用 `worker_assign` + 订阅 `worker.result`（或 `report_subscribe`）**，不要逐个 handoff 串行。
4. **串行依赖才用 `worker_handoff`**（DEPRECATED），超时重试带**同一个 `task_id`**。
5. **完成通知二选一**：外部协调 Monitor+/ws/agent，内部 report_subscribe——同用会重复通知。
6. **订阅可限定 session**：subscribe 传 `sessionIds` 减少无关唤醒；断线后 `reconnect` 补发未消费结果。
7. **一个 Session 一个任务**：避免混多个不相关任务（taskSeq/result 配对依赖此约束）。
8. **长任务防误杀**：持续输出或调大 `worker.timeout_sec`；读大文件分段读。
9. **及时清理**：`session_delete` / `batch-delete` 释放资源；watchdog 只回收进程不删 session。
10. **不依赖长驻 Worker**：watchdog 自动回收空闲 worker，用完即走，下次调用自动重建。
11. **workdir 用绝对路径指 Pan 外目录**：多 worker 共改一个项目时，让所有 session 指向同一 workdir。
12. **错误重试**：返回 `error` 时先查原因（session_get 的 lastResult / worker.zombie 事件），修复后重发。

## 11. 常见问题

**Q: worker_task 返回 "Worker process dead"？**
A: Worker 崩溃/已回收。`worker_spawn(session_id=...)` 重新生成（自动恢复上下文）。

**Q: worker_task 后长时间无回复？**
A: 检查 `workerStatus`——`"idle"` 说明任务已完成但结果未读取，`session_get` 即可；`"running"` 且静默超时可能已被 watchdog 回收。

**Q: handoff 超时了？**
A: 默认 10 分钟。复杂任务传更大 `timeout`；超时后结果仍可能稍后到达——带**同一 `task_id`** 重发（幂等，防双跑），或 `session_get` 补查。

**Q: Worker 被 watchdog 回收了？**
A: 回收只杀进程不删 session。`workerStatus` 变 `null` 后直接 `worker_spawn` 或 `worker_assign`，自动重建。

**Q: 想切换模型？**
A: 重新 `session_create` 并指定新 `model`；或 `session_update` 改 model——idle worker 自动 respawn 生效，running worker 回 idle 时自动重启（不能热切换运行中的 Worker）。

**Q: MCP 工具连不上 Pan？**
A: `PAN_API_URL` / `PAN_WS_URL` 端口要指向实际运行的 port（本分支 8767，MCP 默认 8768）。MCP server 用 `--pan-url` 或环境变量覆盖。

**Q: worker.result 事件没收到？**
A: 确认已 `subscribe` 且 `eventTypes` 含 `worker.result`（默认只订阅它）；订阅了 `sessionIds` 过滤时确认 session 在列表内；断线后发 `reconnect` 补发。

**Q: 订阅制报告没 append 到 queue_pending？**
A: 检查：目标 session 是否有 `managed_by`、是否已 `report_subscribe`、你的环境是否有 `PAN_AGENT_SESSION_ID`（report 工具仅 Pan 内 session 可用）。

## 12. 冷启动实测记录与待补充清单（D5）

> 立项 D5 要求同时验证 **HTTP 路径** 与 **MCP 路径**。下表 G1–G7 为 `docs/plans&overviews/冷启动Agent编排实测报告.md` 中 HTTP 路径实测结果；**MCP 路径实测（2026-08-17）见下方「MCP 路径实测」小节**，并据此更新 G2/G5、新增 G8–G12。

### 12.1 HTTP 路径实测缺口（原 D5）

| # | 缺口 | 建议补充位置 | 状态 |
|---|------|-------------|------|
| G1 | `POST /api/sessions`、`POST /api/spawn`、`POST /api/assign`、`DELETE /api/sessions/{id}` 的**请求体字段未记录**（§5 只说"见 server.py"）——纯 HTTP 冷启动在第 1 步就需读代码 | §5 补核心端点 body 表 | ✅ §5.1 |
| G2 | **MCP server 接线步骤缺失**：实测 ToolSearch 搜不到 `mcp__pan__` 工具时，手册无"如何启动/注入 MCP server 使工具可见"的动作序列 | 新增小节（§9.7 或独立 §） | ✅ §0.1/§7（MCP 路径实测） |
| G3 | **workdir 相对基准未明确**：默认落点实测为 Pan server 数据根 `D:\project\pan-test\data\workdirs\<name>`，非当前项目目录 | §9.1 | ✅ §9.1 |
| G4 | **Windows curl 内联 UTF-8 JSON 报 body 解析错误**（实测 `{"detail":"There was an error parsing the body"}`）；应改用 `--data-binary @file` 或 urllib/requests | §5 或 §9 | ✅ §5 |
| G5 | `report_subscribe` 前置 `PAN_AGENT_SESSION_ID` 的**来源/注入方式未说明**（谁注入、何时有） | §3.2 | ✅ MCP 路径实测 |
| G6 | **字段映射未说明**：create 返回 `id`，spawn/assign 入参用 `sessionId`；`sessionId` vs `session_id` 命名不一致 | §5 / §7 | ✅ §5.2 |
| G7 | 轮询**放弃/超时策略缺失**（多久、几次轮询算失败） | §5 轮询模式 | ✅ §5 轮询模式 |

### 12.2 MCP 路径实测（2026-08-17，meta-agent `mcp__pan__` 直连）

**实测链路（一次走通）**：
`session_create`(ses_d66c08611936941b) → `worker_assign`(queued/worker-2) → 轮询 `session_get`(limit=30) → `lastResult.status="done"`, `result="391"`（子 worker 算 `17×23`）→ `session_delete`(deleted)。
**第二次**覆盖 `worker_spawn` 显式路径：create → `worker_spawn`(idle) → assign(queued) → 轮询 → `143÷11=13` done → delete。参数名、状态判断、轮询兜底均充分，**MCP 路径主链路顺畅**。

**G2 / G5 处理结论（已覆盖，更新原描述）**：

- **G2（MCP 接线）已解决且写入 §0/§7**：`mcp__pan__` 工具由 Pan adapter 在 spawn worker 时**自动注入**——通过 adapter 生成的 `data/mcp-configs/<session_id>.mcp.json`（含 `{"mcpServers":{"pan":{"command":"...python","args":["-m","packages.mcp.server"],"cwd":"...","type":"stdio"}}}`），经 `--mcp-config` 显式传入，**直接 connected 可见，无需 ToolSearch/DeferExecuteTool**。`ToolSearch`+`DeferExecuteTool` **仅**用于项目级 `.mcp.json` 路径发现的工具（见 §9.7）。手册原 §0/§7 "MCP 工具是 deferred 的"表述对 `--mcp-config` 路径不准确，已修正。

- **G5（`PAN_AGENT_SESSION_ID` 来源）已查实（2026-08-17）**：该变量**由 Pan adapter 注入到 MCP server 进程环境**（写入上述 mcp-config 的 `env` 段：`PAN_AGENT_SESSION_ID=<manager session id>`、`PAN_AGENT_SESSION_TITLE=<title>`）。**来源**：manager（meta-agent 自身）session 被创建并启用 MCP 时，adapter 生成 mcp-config 时填入；**何时**：MCP server 以 stdio 拉起的那一刻就带在环境里，对 meta-agent 透明（其亲 shell `env` 里查不到这些变量，属正常）。

- **G5 关键约束（新发现，致命）**：`report_subscribe` 要求 **manager session 与被管 session 同处一个 Pan 服务实例**。本实测中 MCP server 默认连 **8768**（见 §9.7/§0），而 `PAN_AGENT_SESSION_ID=ses_8f7825d50d340dad` 实际只存在于 **8767**（pan-test）。该环境变量值指向 8767，与 MCP server 的默认目标 8768 **跨端口**，导致 §3.2 内部报告路径在本布局下失效。**对齐前提**：`--mcp-config` 的 `cwd`/启动端口、`PAN_API_URL`、**与 `PAN_AGENT_SESSION_ID` 所在服务**必须三者一致。

**MCP 路径新发现缺口（G8–G12，待补）**：

| # | 缺口 / 现象 | 后果 | 建议补充位置 |
|---|-------------|------|--------------|
| G8 | `session_list` **无过滤参数**，默认把全部 session 的**完整 history** 序列化返回（实测 310KB / 265906 chars，触发工具输出 token 上限溢出） | 冷启动第一步"先查后做"反而撑爆上下文；必须先 `Read` 落盘文件分段看 | §7/§8：改用 `session_get(limit=)` + `worker_list` 代替全量 `session_list` |
| G9 | **MCP server 默认端口（8768）与 `PAN_AGENT_SESSION_ID` 所在服务（8767）跨端口**（见 G5 约束） | `report_subscribe` 内部报告路径不可用 | §0/§3.2：明确"MCP 目标端口 = PAN_AGENT_SESSION_ID 所在端口"三对齐 |
| G10 | **`report_subscribe` 在本布局返回 `404 "Not Found"`**：实测 8768(main) 服务 `server.py` 无 `report-subscribe` 路由（0 次命中），而本工作树 `server.py` 有（1 次）——即**运行中的服务落后于 MCP 工具版本**（版本 skew） | §3.2 内部报告完全走不通；只能退回 §5 轮询 `session_get` | §3.2：标注"需运行含 `report-subscribe` 的服务端（本分支 server.py 已含）"；否则用轮询兜底 |
| G11 | `session_delete` **只 kill worker + 删 session 元数据，不删 workdir 磁盘目录**（实测删除后 `data/workdirs/mcp-cold-probe` 空目录残留） | 磁盘累积空目录，需另清理 | §2.5/§9：说明残留，必要时 `rm -rf workdir` |
| G12 | workdir 默认基准 = **服务进程的数据根**，非固定 pan-test。本实测（8768 服务 cwd=`D:\project\Pan`）落点为 `D:\project\Pan\data/workdirs/<name>`，与 G3 记的 `D:\project\pan-test\...` 不同 | §9.1 的"Pan server 数据根"需指明是**实际运行实例的**数据根，可经 create 返回的 `workdir` 字段确证 | §9.1 |

**MCP 路径推荐操作顺序（据实测固化）**：
1. 确认 MCP server 目标端口 = `PAN_AGENT_SESSION_ID` 所在端口（否则 G9/G10 命中，立即退回轮询）。
2. `session_create(name, adapter="cbc", model="hy3")` → 记下返回 `id`（= 后续 `session_id` 入参）。
3. `worker_assign(session_id, text)` 立即返回 `queued`（自动 spawn，无需手动 `worker_spawn`；§2.1）。
4. 轮询 `session_get(session_id, limit=15)` 直到 `lastResult.status=="done"`（≤30s 内完成，按 §8 不重发）。
5. 读 `lastResult.result`。
6. `session_delete(session_id)` 收尾（注意 G11 workdir 残留）。
7. **避免**全量 `session_list`（G8）；状态巡检用 `worker_list` + 定向 `session_get`。

---

## 13. skill 维护规范

> 本 skill 是**项目维护的重点内容**（立项 `docs/archive/Pan冷启动Agent编排skill立项.md` §二·5），随代码演进，变更走 git PR/提交与代码同轨。以下规范不仅适用于本 skill，也适用于所有**文档/手册类任务**。

### 13.1 自包含可验证：编写与验证闭环

- **文档/手册任务必须"自包含可验证"**：写的人**模拟目标用户完整走一遍再交付**，不能只写完就交付。
- 目标用户画像（本 skill）：冷启动 agent——**无对话上下文、只有 MCP 工具 + skill**，不会主动去读代码。
- 编写流程：
  1. 按手册从零走一遍主链路（本 skill 即 §0 快速开始 → §2 编排链路 → §3 完成通知 → §7 收尾）；
  2. 走不通 / 有歧义处 = 手册缺口，补完**再走一遍**（闭环）；
  3. 交付前核对："用户**不读代码**能否完成？"——凡"先 Read 代码才能继续"的表述都是缺口（反例：G1，手册原只说"见 server.py"）。
- 手册内容禁止只引用代码文件而不给关键结论（参数表 / 返回格式 / 状态语义必须落字）。
- 验证闭环作为变更验收项：冷启动 agent 测试（仅 MCP + skill、无上下文完成一次编排，立项 §二·5「验证」）。

### 13.2 SKILL.md 双份管理

- **主源**：`docs/skills/pan/SKILL.md`（**git 版本控制**，随代码变更走 PR/提交）。
- **同步副本**：`.codebuddy/skills/pan/SKILL.md`（CodeBuddy 编辑器加载 skill 用，**不进 git**）。
- **改内容先改主源，再复制到副本保持同步**——只改副本会让主源落后，下次 checkout/合并会把改动冲掉。
- 内容变化触发同步：MCP 工具 / HTTP API / workdir 约定 / 编排流程变更时，两处一并更新（本文件 §0 头注）。

---

## 关联文档

- `docs/archive/Pan冷启动Agent编排skill立项.md` — 本 skill 的立项依据
- `docs/plans&overviews/Worker监督与事件驱动模式.md` — 监督/盯梢实战、完成通知二选一
- `docs/cbc-mcp-踩坑记录.md` — MCP 接入过程、/ws/agent 订阅协议、deferred 判定
- `packages/web/server.py` — HTTP API 与 /ws/agent 实现（单一事实源）
- `packages/mcp/server.py` — MCP 工具实现（`_api` 与各工具）
- `packages/core/worker.py` — watchdog / handoff / assign / 报告队列实现
