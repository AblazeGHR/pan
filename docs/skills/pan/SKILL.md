---
name: pan
description: Pan CLI Agent 编排中间层——冷启动操作手册。通过 MCP 工具管理会话（session）和 Worker 进程（cbc/kimi/opencode/claude/codex 等多 CLI adapter）。当需要创建会话、并行派发 worker、订阅完成通知（report_subscribe → queue_pending）、读取结果、清理 session 或了解 Pan 编排坑与约定时使用。
---

# Pan — CLI Agent 编排中间层（冷启动操作手册）

Pan 是 Supervisor/Worker 架构的 CLI Agent 编排器。你（Meta-Agent）通过 Pan MCP 工具调度多个 CLI Worker 进程（cbc / kimi / opencode / claude / codex 等多 adapter，持续增加中），每个 Worker 拥有独立的会话（Session）和记忆（workdir）。

> **这份 SKILL.md 是 Pan 编排知识的单一事实源**（立项 `docs/archive/Pan冷启动Agent编排skill立项.md`）。**主源**：`docs/skills/pan/SKILL.md`（git 版本控制）；`.codebuddy/skills/pan/SKILL.md` 是**同步副本**（CodeBuddy 编辑器加载 skill 用，不进 git）——改内容先改主源，再复制到副本保持同步。MCP 工具 / HTTP API / workdir 约定变化时必须同步更新本文件。
>
> **技术细节引用子文档**：HTTP API 清单与字段映射 → [`references/http-api.md`](references/http-api.md)；/ws/agent 订阅协议与 monitor_workers.py 盯梢模板（测试/排障用）→ [`references/ws-protocol.md`](references/ws-protocol.md)。本文件保留编排流程主线，细节见子文档。

## 0. 快速开始（30 秒冷启动）

1. MCP 工具接线（命名空间 `mcp__pan__`，G2 实测 2026-08-17）：
   - **`--mcp-config` 路径（meta-agent 常态）**：由 Pan adapter 自动注入 `data/mcp-configs/<session_id>.mcp.json`，工具 **direct connected，直接调用即可，无需 ToolSearch**。"工具列表里没看到"≠未连接，先直接试调一次。
   - **项目级 `.mcp.json` 发现路径**：工具是 deferred 的 → `ToolSearch`（查询词 `pan`/`mcp`）→ `DeferExecuteTool` 调用。
   - **拿手册**：MCP 工具 `pan_handbook()` 直接返回本文件全文（§5「其他」）——接线完成后若不清楚编排流程，先调它再动手。
   - 前置三对齐：MCP server 目标端口（`PAN_API_URL`，默认 8768）**必须**与 `PAN_AGENT_SESSION_ID` 所在服务同实例，否则 `report_subscribe` 失效（§3 / §10.2 G9）。
2. 编排主链路：`session_create → report_subscribe（订阅）→ worker_assign → queue_pending 收完成报告 → session_get 查结果 → session_delete 收尾`。
3. **完成通知只有一条编排路径**：MCP `report_subscribe` → 报告落到自己的**落盘队列 `queue_pending`**（meta-agent 内部订阅，§3）。外部 WS 盯梢（`/ws/agent` / `monitor_workers.py`）仅**测试/排障/外部协调者**用，不是编排路径（§4）。
4. 端口约定：main 分支默认 **8768**（test 分支 8767）；MCP server 默认连 `PAN_API_URL`（8768）。**关键**：MCP server 目标端口必须与 `PAN_AGENT_SESSION_ID` 所在服务**同实例**（§3 三对齐），否则 `report_subscribe` / `qq_bind` 失效（§10.2 G9）。端口不符时用 `PAN_API_URL` 覆盖。

## 1. 核心概念

| 概念 | 说明 |
|------|------|
| **Session** | 持久化的对话容器，包含 history、model、adapter、workdir 等配置。独立于 Worker 生命周期。 |
| **Worker** | 临时的 CLI 子进程（cbc/kimi/opencode/claude/codex），绑定到一个 Session。可被 kill、回收、重建。 |
| **Adapter** | CLI 工具类型：`cbc`（CodeBuddy CLI）、`kimi`（Kimi CLI）、`opencode`（OpenCode CLI）、`claude`（Claude Code CLI）、`codex`（OpenAI Codex CLI）——五个已内置注册。**adapter 列表持续增加——以实际为准**：用 `model_list` 或查注册表 `packages/core/adapters/__init__.py` 确认当前可用 adapter |
| **Model** | AI 模型名称，如 `hy3`、`deepseek-v4-flash` |
| **workdir** | Session 的工作目录，也是 Worker 进程的 `cwd`（见 §7.1） |
| **taskSeq** | 每个任务的序号；用于配对任务与结果（完成报告里带 `taskId`） |

关键规则：
- Session 是持久化的——kill/回收 Worker 不会删除 Session 数据。
- 一个 Session 同一时间只有一个 Worker（spawn 时若有旧 worker 先 kill）。
- Worker 回复是异步的——`worker_assign` 返回 `queued`，随后 `report_subscribe` 订阅收完成报告，或 `session_get` 读取。
- Worker 会被 watchdog 自动回收（空闲/静默超时），用前若 `workerStatus` 为 `null` 需重新 spawn。
- 握手前提：`PAN_API_URL`（HTTP）必须指向实际运行端口。

## 2. 编排工作流（全景）

```
session_create → report_subscribe → worker_assign → queue_pending 收报告 → 查结果 → 收尾
```

### 2.1 并行 fan-out（推荐主流程：assign + report_subscribe）

```
1. 为每个任务创建/复用 session
   session_create(name="fix-h1", adapter="cbc", model="hy3")
   → 返回 id: "ses_abc123..."（后续请求体的 session_id / MCP 的 session_id 用它，字段映射见 references/http-api.md）

2. 订阅完成报告（派发前或派发后均可；§3 前置条件见 §3）
   report_subscribe(session_id="ses_a...")
   → {"subscribed": true, "reportSubscriptions": [...]}

3. 异步分派（立即返回，不阻塞）
   worker_assign(session_id="ses_a...", text="任务A")
   worker_assign(session_id="ses_b...", text="任务B")
   → 都返回 {"status": "queued", "workerId": "...", "sessionId": "..."}

4. worker 完成（done/error）→ 完成报告自动入你的落盘队列 queue_pending（§3）

5. 收到全部完成报告后，逐个 session_get 读最终结果汇总

6. 收尾：session_delete / session_batch_delete 释放资源（§2.5 / §5）
```

**不需要手动轮询**。assign 之后 worker 会自动 spawn（如果该 session 无活 worker）。

### 2.2 串行依赖步骤（worker_handoff 已移除）

> `worker_handoff` 与 `POST /api/handoff` 已于 2026-08-26 **彻底移除并归档**（原为立项 4.7 弃用的阻塞原语）。串行依赖同样用 `worker_assign` + `report_subscribe`（§3）：派发后订阅完成报告，报告入你的落盘队列 `queue_pending` 即「串行下一步」的信号——"等"是 meta-agent 的默认 idle 状态，而非阻塞调用。派发带 `task_id` 幂等（§7.4）。

### 2.3 在已有会话上继续对话（worker_assign / worker_send / worker_send_force）

三种向已有 session/worker 派活的方式，区别如下：

| 方式 | 目标 | 行为 | 适用 |
|------|------|------|------|
| `worker_assign(session_id, text, task_id?)` | 以 **session** 为目标派**新任务** | 异步分派，立即返回 queued；worker 自动 spawn（该 session 无活 worker 时）；完成经 `report_subscribe` 内部报告回调（§3）；传 `task_id` 幂等（§7.4） | **新任务 / 并行 fan-out / 幂等重试（默认首选）** |
| `worker_send(worker_id, text)` | 向**已有活 worker** 发消息（多轮协作） | 消息排队，worker 空闲（当前任务完成后）才处理，**不打断**进行中任务；worker 已死会报错，需先 `worker_spawn` | 多轮追问 / 补充线索 / 不着急的后续指令（排队等待） |
| `worker_send_force(worker_id, text)` | 向**已有活 worker** 强制送达 | **restart + send**：重启 worker 进程再发消息，立即生效，**打断**进行中任务 | 操作约束 / 方向变更 / 紧急指令 / worker 卡死·忙·连接异常时兜底 |

```
1. session_list()  → 找到目标 session_id 与 workerStatus
2. 按需选择：
   - 新任务 → worker_assign(session_id, text)      # worker 自动 spawn，无需手动 spawn
   - 已有 worker 的补充指令 → worker_send(worker_id, text)      # 排队，不打断
   - 需打断 / 紧急 / 卡死兜底 → worker_send_force(worker_id, text)   # restart+send，立即送达
   - workerStatus 为 null（已回收/已死）→ 先 worker_spawn(session_id) 重建，再视情况 assign / send
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
2. session_delete(session_id=...)               # 单个
   session_batch_delete(session_ids=["ses_a", "ses_b"])   # 批量（MCP 工具已覆盖，逐个过 managed 隔离检查）
```

**及时清理**：不再需要的 session 用 delete 释放进程与磁盘；watchdog 只回收进程，不删 session。注意：delete 不删 workdir 磁盘目录（残留见 §7.1 G11）。

### 2.6 派发规范：worker 无记忆，session 有记忆

**记忆模型**：Worker 是**临时进程**——每次 spawn 都是全新进程，**无记忆**；Session 是**持久化容器**——保存 `history` + `cliSessionId`。重新 spawn 时 adapter 检测到 `cliSessionId` 非空即传 `cbc --resume <cliSessionId>`（`packages/core/adapters/cbc/adapter.py` `resume_args`），cbc 从 transcript（JSONL）恢复**完整上下文**（原任务描述、进度、历史对话都在）。session 的记忆来自持久化，不来自 worker。

**派发判定**：派发任务前先 `session_get(session_id)` 查 **`cliSessionId`** 字段：

| `cliSessionId` | 含义 | 任务文本写法 |
|----------------|------|-------------|
| **非空** | worker 将 `--resume` 恢复已有完整上下文 | **一律用简短指令**（追加任务 / 恢复中断 / 串行下一步 / 追问修正）：指出现有上下文里要做什么即可，**不要重发完整任务描述**——上下文已有原任务与进度，重发浪费 token，且措辞差异可能让 worker 误判为新任务/新要求 |
| **为空 / null** | 新 session 或 worker 从未建立，worker 无上下文 | **任务描述必须自包含**：背景 / 目标 / 涉及文件（相对 workdir）/ 边界 / 验收标准 |

- 与 §2.3 的关系：§2.3 解决「找 session + 选择派活方式」，本小节解决「任务文本怎么写」——`cliSessionId` 非空的 session 追加/恢复任务时：`worker_spawn`（`workerStatus` 为 null 时）→ 简短指令。

### 2.7 替身交接（session_handoff）：精简上下文 / 切换 adapter

> 场景：当前 session（A）上下文过大需要精简，或想中途切换 adapter（普通 session **不能**中途切换 adapter）。A 保留为可阅读上下文（归档重命名 `(archive) <原名>`），创建孪生 session B 接管 A 的名字与全部 pan 关系网。

```
session_handoff(session_id="ses_a...",
                handoff_prompt="【必填】A 的 agent 编写的交接简报：现状、重点、重要开发习惯、原 system_prompt 内容、上下文精华……",
                copy_settings=true,          # 1:1 复制 A 的设置（不含 system_prompt）
                adapter="kimi", model=..., permission_mode=...)   # 切换 adapter 时传
→ {"ok": true, "archivedSession": {...}, "session": {...B...}}
```

行为要点（session_handoff v1）：
1. **关系网接替（自动、必然）**：B.managed = A.managed，A 的子会话 `managed_by` 改 B；A 的 `report_subscriptions`、QQ postbox 绑定（`session_qq_subscribe` 的 inbox 提醒）全部转移给 B。A 若曾被某 manager 管理，B 接替 A 在该 manager 下的位置。
2. **B 自动 manage A**：B.managed 追加 A，A.managed_by = B——A 归档为 B 的被管理会话，B 收到 A 的完成报告（可 `session_get(A)` 持续读取旧上下文）。
3. **设置复制**：`copy_settings=true` 复制 adapter / adapter_config / model / permission_mode / session_template / pan_access / mcp_servers 等，**明确不含 system_prompt**，且 `cli_session_id` 清空（B 是全新会话，不继承 A 的 CLI 上下文与 history——精简上下文的关键）；`false` 用默认设置（此时**必须**显式传 `adapter`）。
4. **B.system_prompt = handoff_prompt 与 A 原 system_prompt 拼接**（分「交接上下文 / 原 system prompt」两节）。
5. **重命名**：A → `(archive) <原名>`，B → `<原名>`；A 的原关系网解除。

交接后 B 即可 `worker_assign` 派活；切换 adapter 的典型用法：`copy_settings=false + adapter="kimi" + handoff_prompt=...`。

## 3. 完成通知：report_subscribe → queue_pending（meta-agent 内部订阅，唯一编排路径）

meta-agent 编排 worker 时，完成通知**一律走内部订阅**：MCP `report_subscribe` 把目标 session 的完成报告（done/error）推送到你的**落盘队列** `queue_pending`，由 consumer 批量拼成一条消息唤醒你。主链路：`session_create → report_subscribe（订阅）→ worker_assign → queue_pending 等完成 → session_get → session_delete`（订阅在 assign 前或后均可）。

> **为什么是唯一路径**：异步、落盘可恢复（跨服务重启不丢）、跨协调者、不依赖外部会话/WS。外部 WS 盯梢（`/ws/agent` / `monitor_workers.py`）不再作为编排路径——只供测试 / 排障 / 外部协调者使用（§4 → `references/ws-protocol.md`）。

**前置条件（G5 实测 2026-08-17，不满足则本路径失效，退回 references/http-api.md 的轮询兜底）**：

| 前置 | 说明 | 不满足时的现象 |
|------|------|---------------|
| `PAN_AGENT_SESSION_ID` 存在 | **由 Pan adapter 注入 MCP server 进程环境**（写在 `data/mcp-configs/<session_id>.mcp.json` 的 `env` 段，随 stdio 启动带入）。**不在你的 shell `env` 里**——用 `env \| grep` 查不到属正常，别据此判断缺失；可从 `CODEBUDDY_MCP_CONFIG` 文件名/内容确认自己的 session id | 工具报缺少 manager id |
| **同实例**：manager session 与被管 session 在**同一个 Pan 服务**（同端口） | MCP server 默认连 `PAN_API_URL`（默认 **8768**），而 `PAN_AGENT_SESSION_ID` 可能属于**另一个**服务（如 8767 pan-test）。三者（mcp-config `cwd`/`PAN_API_URL`/manager session 所在服务）必须一致 | manager session 在目标服务上"不存在"，订阅无效（§10.2 G9） |
| 服务端含 `report-subscribe` 路由 | 该端点较新；**运行中的服务可能落后于 MCP 工具版本** | `report_subscribe` 返回 `{"detail":"Not Found"}`（404，实测于 8768/main；本分支 `server.py` 已含）（§10.2 G10） |

```
1. worker_assign 前先订阅（前置见上表）：
   report_subscribe(session_id="ses_managed...")
   → {"subscribed": true, "reportSubscriptions": [...]}

2. worker 完成（done/error）→ 报告 append 到你的落盘队列 queue_pending：
   {"status","result","sessionId","taskId","workerId"}

3. 你的 consumer 被报告信号唤醒，积压报告批量拼接为一条消息：
   ───── 子任务报告（来源 sessionId=ses_...）─────
   {"status": "done", ...}
```

`queue_pending` 是**落盘真源**，`pending_signal` 只是唤醒信号（§7.6）。报告可跨进程重启恢复。

> **订阅即接管**：`report_subscribe` 同时把目标 session 归为调用方（meta-agent）管理（自动 claim，见 `packages/mcp/server.py`）；`report_unsubscribe` 仅能退订**自己管理**的 session。

## 4. HTTP API 与 WS 协议（技术细节 → 引用子文档）

- **HTTP API 清单**（批量删除 / rename / branch / PATCH 更新 / handoff / 字段命名映射 / Windows curl 中文编码坑 / 轮询兜底策略）→ [`references/http-api.md`](references/http-api.md)。
  - MCP 能覆盖的编排操作一律走 MCP（§5）：批量删除用 `session_batch_delete`（**MCP 已覆盖**）；仅 **rename / branch** 无 MCP 工具，需 HTTP 直调（见子文档）。
- **/ws/agent 订阅协议与 monitor_workers.py 盯梢模板**（测试 / 排障 / 外部协调者用）→ [`references/ws-protocol.md`](references/ws-protocol.md)。
  - meta-agent 编排完成通知**不走 WS**，一律用 §3 `report_subscribe`；WS 仅当确实需要**外部**（非 meta-agent）实时盯梢时才用。

## 5. 可用 MCP 工具

> 调用方式见 §0.1：`--mcp-config` 注入路径下工具 **直接可调**（无需 ToolSearch）；仅项目级 `.mcp.json` 发现路径才是 deferred（`ToolSearch("pan")` → `DeferExecuteTool`）。工具命名空间 `mcp__pan__`。**当前共 27 个工具**（对照 `packages/mcp/server.py` 的 `@mcp.tool()` 全量核对）。
>
> **巡检优先 `session_list(summary=true)`**：旧版 `session_list` 返回全部 session 完整 history，实测 310KB 会撑爆工具输出上限（§10.2 G8）。**现在 `session_list(summary=true)` 只返回精简字段（id/name/adapter/workerStatus/updatedAt/managedBy），用于巡检/查归属**；确认某个 session 详情再用 `session_get(session_id, limit=15)`。查"自己管了哪些"直接用 `session_managed()`。
>
> **pan-qq 独立 MCP（2026-08-22 起）**：QQ 能力不在本 server。`packages/qq/mcp.py`（manifest `mcp_servers` 加 `pan-qq`）提供 6 个工具：`qq_send_message` / `qq_read_conversation` / `qq_list_contacts` / `qq_read_inbox` / `qq_bind` / `qq_unbind`。selective 模式下 meta-agent 用它做 QQ 选择性收发与 inbox 订阅——`qq_bind` 后该 QQ 会话新消息会以 `@@@@by qq` 提醒推入你的 `queue_pending`（§7.6）。SMA session template 已默认挂载 pan-qq。

### 会话管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `session_create` | `name`, `adapter?`, `model?`, `permission_mode?`, `workdir?`, `session_template?`, `character_id?`, `system_prompt?`, `game_id?`, `pan_access?` | 创建会话。`session_template` 用模板创建；`pan_access` 传能力字段 dict（`restrict_to_managed`/`can_claim_unmanaged`/`auto_claim_created`）；显式字段 > 模板值 > 默认值。workdir 默认 `data/workdirs/<name>`，Pan 外目录用绝对路径（§7.1） |
| `session_import` | `action`, `adapter?`, `project_dir?`, `cwd?`, `query?`, `limit?`, `session_id?`, `name?`, `session_template?`, `pan_access?` | **导入外部 CLI 历史会话**（cbc 项目 / kimi 工作区 / opencode 会话，adapter 以实际为准）。action: `list_projects`（cbc 项目）/ `list_workspaces`（kimi 工作区）/ `list_sessions` / `import`。import 仅建 session 不 spawn，workdir=外部项目路径（不在 data/workdirs/）；同一 `cli_session_id` 重复导入 = reimport 覆盖原 session 历史（受限 caller 只能覆盖自己管理的）；套用 `session_template`/`pan_access` 需后端支持（已实现）。导入后接主链：`report_subscribe → worker_assign → session_get` |
| `session_list` | `summary?` | 列出所有会话；`summary=true` 只返回精简字段（id/name/adapter/workerStatus/updatedAt/managedBy），不含 history |
| `session_managed` | (无) | 返回调用者管理的 session 摘要 `[{id, name, workerStatus, updatedAt}]`（需 `PAN_AGENT_SESSION_ID`） |
| `session_get` | `session_id`, `limit?` | 会话详情（history + lastResult）；limit>0 截断 |
| `session_update` | `session_id`, 各设置项 | PATCH 封装；改进程相关配置（model/effort/thinking/MCP/outputMode）时 **idle worker 自动 respawn 生效**、running worker 回 idle 时自动重启（references/http-api.md） |
| `session_delete` | `session_id` | 删除会话并 kill worker |
| `session_batch_delete` | `session_ids` | 批量删除多个会话（逐个过 managed 隔离检查，等价 HTTP `POST /api/sessions/batch-delete`） |
| `session_handoff` | `session_id`, `handoff_prompt`(**必填**), `copy_settings?`(=true), `adapter?`, `model?`, `permission_mode?` | **替身交接**（§2.7）：创建孪生 session B 接替 A，精简上下文或切换 adapter。B 接管 A 的关系网并自动 manage A；`handoff_prompt` 由 A 的 agent 编写（交接简报），B.system_prompt = 它与 A 原 system_prompt 拼接；`copy_settings` 复制 A 的设置（不含 system_prompt，cli_session_id 清空），false 时须显式传 `adapter` |
| `session_claim` | `session_id` | 当前 agent（`PAN_AGENT_SESSION_ID`）认领会话，建立 managed 关系（立项 4.2）。**claim 自动 report_subscribe**（后端实现）。走 `POST /api/claim`（带 `_check_access(claim=True)` 隔离检查）；目标已被他人管理则拒绝。需 `PAN_AGENT_SESSION_ID` |
| `session_claim_many` | `session_ids` | 批量认领：逐个处理，返回 `{"ok": true, "claimed": [...], "failed": [{"sessionId", "error"}]}`，单个失败不影响其余 |
| `session_unclaim` | `session_id` | 当前 agent 解除对会话的 managed 关系（自动退订报告，后端实现）。走 `POST /api/unclaim`（带 `_check_access` 隔离检查，受限 caller 只能解绑自己管理的）；仅当前 manager 可解绑。需 `PAN_AGENT_SESSION_ID` |
| `session_unclaim_many` | `session_ids` | 批量解绑：语义同 `session_claim_many`，返回 `unclaimed`/`failed` 列表 |
| `session_qq_subscribe` | `target_type`, `target_id` | 给当前 agent session 订阅某 QQ 会话的 inbox 更新提醒（`@@@@by qq` 提醒入 `queue_pending`，§7.6）。走 `POST /api/qq/subscribe`，body sessionId=自己（无需 `_check_access`，但需 `PAN_AGENT_SESSION_ID`）。`target_type` 仅 `"user"`/`"group"`；`target_id` 为 QQ 号/群号（转 str） |
| `session_qq_unsubscribe` | `target_type`, `target_id` | 退订 QQ inbox 更新提醒，走 `POST /api/qq/unsubscribe`，参数同上 |
| `session_history` | `session_id`, `limit?`, `before?` | 分页历史 |

> **复用已删除的 Pan session（2026-08-23 实测）**：Pan session 被 `session_delete`/`session_batch_delete` 删掉后，其底层 **CLI 会话（`~/.codebuddy/projects/` 或 `data/workdirs/<name>/`）仍保留**。可 `session_import(action="list_projects")` 找到对应 project_dir → `list_sessions` 找到该会话 → `import` 恢复成新 Pan session（含全部历史上下文）。**节省资源**：不用重建后重新探索/初始化，尤其适合「worker 已完成任务但需继续排查/跟进」的场景——把刚删的 worker session 恢复后继续派活，worker 带着全部上下文直接上手。

### Worker 管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `worker_spawn` | `session_id?`, `name?`, `adapter?`, `model?`, `workdir?` | 生成 worker；给 name 则先建 session。已有 worker 会先 kill（一个 session 一个 worker） |
| `worker_task` | `session_id?`, `worker_id?`, `text`, `source?` | 发任务（异步，返回 queued）；worker 不存在时自动 spawn；`source` 默认 `"agent"` |
| `worker_assign` | `session_id`, `text`, `task_id?` | **异步分派**（并行 fan-out / 新任务默认首选）：立即返回 queued，worker 自动 spawn；完成经 `report_subscribe` 内部报告回调（§3）/ `session_get` 读取。传 `task_id` 幂等（同 taskId 重发不双跑，见 §7.4） |
| `worker_send` | `worker_id`, `text` | 向已有活 worker 发消息（多轮协作，§2.3）；**仅用于非即时补充**：消息排队送达，worker 空闲（当前任务完成后）才处理，不打断进行中的任务；需打断/立即生效用 `worker_send_force`；Pan 内 session 自动加 `////by agent` 前缀（§7.5）；worker 已死会报错（需先 `worker_spawn`） |
| `worker_send_force` | `worker_id`, `text` | **强制推送** = restart + send（§2.3）：目标 worker 卡死/忙/连接异常导致普通 `worker_send` 无法送达时兜底；**也用于需要打断 worker 当前执行的时效性消息**（如操作约束、危险操作警告）——restart 后立即送达，不等当前任务排完。先重启 worker 进程再发消息，保证送达；自动加 `////by agent` 前缀（§7.5），restart/send 任一失败返回后端错误 |
| `worker_kill` | `worker_id` | 终止 worker 进程（session 保留） |
| `worker_list` | (无) | 列出所有运行中 worker |

### 报告订阅（meta-agent 内部）

| 工具 | 参数 | 说明 |
|------|------|------|
| `report_subscribe` | `session_id` | 订阅被管理 session 的完成报告（需 `PAN_AGENT_SESSION_ID` 环境变量，仅 Pan 内 session 生效）。**订阅即接管**：自动 claim 目标 session（§3） |
| `report_unsubscribe` | `session_id` | 取消订阅（仅能退订**自己管理**的 session） |

> **zombie 通知**：被管 session 的 worker **异常死亡**（running/queued 状态被 watchdog 回收或进程崩溃/EOF）时，也会向你的 `queue_pending` 推一条报告：`{"status":"error","type":"zombie","sessionId","workerId","result":"worker died: <原因>"}`——可据此感知 worker 意外丢失（正常完成后的 idle 回收**不**报 zombie）。报告拼接时 `type` 字段单独一行。

### 其他

| 工具 | 参数 | 说明 |
|------|------|------|
| `model_list` | `adapter?` | 列出可用模型 |
| `pan_handbook` | (无) | **返回本 SKILL.md 全文**（读文件实时返回，单一事实源，立项 C）。冷启动 agent 不确定编排流程时先调它；内容与 §0–§11 完全一致 |

## 6. 状态判断

`session_get` 的 `lastResult.status`：

| status | 含义 | 操作 |
|--------|------|------|
| `"queued"` | 任务已入队 | 等待 5-10 秒后重查 |
| `"running"` | Worker 执行中 | 继续等，**不要重复提交** |
| `"done"` | 任务完成 | 读 `result` 字段 |
| `"error"` | 任务失败 | 读 `result` 字段取错误 |
| `"pending"` | 同 task_id 的任务仍在进行中（assign 幂等返回） | 用同 task_id 重试或 session_get 补查 |

`session_list` 的 `workerStatus`：
- `"queued"` / `"running"` / `"idle"`（可发任务）/ `"error"` / `"held"` / `"zombie"`（跳过回收）
- `null` → 无 worker（watchdog 已回收或未 spawn，需 `worker_spawn`）

## 7. 坑与约定

### 7.1 workdir 机制

- 默认：`data/workdirs/<name>`（session 名 slug 化，非法字符替换为 `-`）。
- **相对基准 = 实际运行的那个 Pan 服务实例的数据根**，不是你的当前项目目录、也不是 mcp-config 的 `cwd`（G3/G12）。实测：8768 服务（cwd `D:\project\Pan`）→ 落 `D:\project\Pan\data\workdirs\<name>`；8767（pan-test）→ 落 `D:\project\pan-test\data\workdirs\<name>`。**以 `session_create` 返回的 `workdir` 字段为准**。
- 相对值一律按 slug 规则清理后放进 `data/workdirs/`。
- `session_delete` **不删 workdir 目录**（只 kill worker + 删 session 元数据），磁盘留空目录，需要时自行清理（G11）。
- **绝对路径可指定 Pan 外目录**（如 `D:/some/project`）——Worker 的 `cwd` 就是 workdir，cbc 把 workdir 当项目目录（JSONL + resume 都在这里）。
- 同名 session 名必须唯一；workdir 默认取 session 名。
- 文件系统 API（`/api/cbc/browse` 等）限在 workdir 内，`..` 逃逸会被拒绝。

### 7.2 mcp-config 收敛到 `data/mcp-configs/`

- 会话启用 MCP 后，配置写在 **`data/mcp-configs/<session_id>.mcp.json`**（立项 4.9），由 adapter 在 spawn 时自动生成，并传 `--mcp-config <path>`。
- **绝不写 `<workdir>/.mcp.json`**：workdir 可能在 Pan 外（污染外部目录/不可写），且 cbc 会把 project-scope 的 `.mcp.json` 注册为 MCP server，启动失败时**阻断** `--mcp-config` 注入（踩坑 #15）。
- 注入唯一通道是 **`--mcp-config` 显式传入**；`-d` **不会**自动发现 `.codebuddy/mcp.json`（踩坑 #6/#15）。
- `--mcp-config` 路径下 MCP 工具为 **direct connected（非 deferred）**，无需 ToolSearch；项目级 `.mcp.json` 发现才是 deferred。

### 7.3 watchdog 行为（静默超时 vs 空闲回收）

配置在 `config.json` 的 `worker` 段（改后重启生效）：

| 条件 | 行为 | 默认 / 本分支实测 |
|------|------|------------------|
| stream `running` **任务运行时长**超过 `worker.task_timeout_sec` | 判定卡死 → kill | 默认 **1800s**（2026-08-17 起与静默超时分离，长思考/大文件读取不再被误杀） |
| `queued` 持续**无任何 stdout 输出**超过 `worker.timeout_sec` | 静默超时 → kill | 默认 **300s**；运行环境 config.json 实测 **1200s** |
| `idle` 持续超过 `worker.idle_sec` | 空闲回收 → kill（session 保留） | **300s** |
| `held`（takeover）/ `zombie` | **跳过**，不回收 | — |

- `last_activity` 每次 stdout 有事件即刷新；stream `running` 的卡死判定基于**任务运行时长**（`_task_started_at` 起算）而非静默时长——**长思考 / 大文件读取不会触发超时**，只有任务整体超时才判卡死。
- **坑 A（历史）**：旧版静默超时（无输出即 kill）会误杀深度推理/大文件读取；2026-08-17 已改为任务运行时长判定（L1 修复），现只须关注任务总时长是否超出预算。仍建议复杂任务拆小、读大文件分段。
- 回收后 `workerStatus` 变 `null`，session 数据完好；下次 `worker_spawn`/`worker_assign` 自动重建并恢复上下文。
- MCP one-shot 模式由读取超时承担（同一 `timeout_sec`），watchdog 只做 idle 回收。
- **全局 watchdog**（服务级，生命周期=Pan）：周期扫描"落盘队列 `queue_pending` 非空但没有活 worker 的 session"，自动 `create_worker` 恢复——自愈（立项 4.4）。

### 7.4 taskId 幂等（assign）——worker_handoff 已移除

- `worker_handoff`（MCP）与 `POST /api/handoff` 已于 **2026-08-26 彻底移除**（原为立项 4.7 弃用后归档）。串行依赖与并行 fan-out 一律 `worker_assign` + `report_subscribe`（§3）。
- 理由（原立项 4.7）："等"应是 meta-agent 的默认 idle 状态，而非阻塞调用；阻塞会占用协调者、易被中断。
- **幂等**：`worker_assign` 的 `task_id` 是幂等键——重发同 task_id：已完成 → 返回缓存结果；进行中 → 返回 `{"status":"pending",...}` 不重复入队（防双跑）。taskId 注册表有 TTL 惰性清理。

### 7.5 `////by agent` 前缀

- `worker_send` / `worker_send_force` 在 Pan 内 session（环境注入 `PAN_AGENT_SESSION_ID`/`PAN_AGENT_SESSION_TITLE`）下发消息时自动加前缀（立项 4.8）：

```
////by agent : ses_xxx | session-title
{text}
```

- 用途：目标 worker 区分"meta-agent 编排消息"与真实用户消息。
- 编排时注意：目标 worker 收到带该前缀的消息应识别为编排指令；`worker_assign` 不发此前缀（只有 `worker_send` / `worker_send_force` 拼）。
- **时效性选择规则**：普通补充信息/线索 → `worker_send`（排队送达，worker 空闲时处理）；**需要打断当前执行的时效性消息**（如操作约束、危险操作警告）→ `worker_send_force`（restart+send，立即生效，不等当前任务完成）。

### 7.6 pending_signal 队列（+ 落盘 queue_pending）

- 每个 Worker 有一个内存 `pending_signal`（asyncio.Queue），consumer 循环阻塞在它上面。
- **普通任务**：入队 `{text, source, seq, taskId}` → consumer 取出 → 执行。
- **报告信号**：入队 `{"type":"report_signal"}` ——**只负责唤醒**，报告正文在 meta-agent 的**落盘队列** `Session.queue_pending`（真源）。consumer 被唤醒后从落盘队列批量拉取，拼接成一条消息（`─────` 分隔 + 来源标注）处理，消费即删。
- **QQ 提醒信号（2026-08-22 起）**：`/api/qq/notify` 被 QQ 插件调用后，`enqueue_qq_reminder` 对所有订阅了该 QQ 会话的 session append `{"type":"qq","qqTarget":...}` 到其 `queue_pending` 并唤醒（同一 `report_signal` 通道）——即订阅者 worker 会收到 `@@@@by qq` 抬头提醒（镜像 report 链路，见 §3）。
- 落盘真源 + 内存信号：服务重启不丢报告；全局 watchdog 看到 `queue_pending` 非空无活 worker 会自动拉起。

### 7.7 其他约定

- **端口**：`main` 分支默认 **8768**；test 分支 8767。MCP server 默认 `PAN_API_URL=http://127.0.0.1:8768` ——**MCP 目标端口必须与 `PAN_AGENT_SESSION_ID` 所在服务一致**，否则 `[WinError 10061] 连接被拒`（踩坑 #11）或 report_subscribe / qq_bind 失效（§10.2 G9）。
- **API 无鉴权、绑 loopback**（127.0.0.1）——不要在非本机环境暴露端口。
- **MCP deferred 判定**：工具搜不到 ≠ 未连接。`ToolSearch` 搜得到 = deferred（`.mcp.json` 路径）；搜不到 = 未连接（多半 `--mcp-config` 没传或 cwd 错）。`--mcp-config` 路径下工具应直接可见。
- **带 character 的 session 首次任务**会被 memory 加载阻塞（embedding 首次加载 + 网络重试），可配 `memory.enabled: false` 或依赖 15s 超时降级（踩坑 #12）。

## 8. 最佳实践

1. **先查后做**：`session_list()` 了解当前状态再操作。
2. **命名规范**：Session 名短横线连接、有语义（`fix-h1`、`debug-auth`）；同名不可重复。
3. **并行 fan-out 用 `worker_assign` + `report_subscribe`**（§3）；串行依赖同样走 assign + report_subscribe（§2.2/§7.4）。
4. **上下文过大 / 要切 adapter 用 `session_handoff`**（§2.7）：替身交接创建孪生 session B 接替 A，A 归档可读。
5. **完成通知走 `report_subscribe` → `queue_pending`**（内部订阅，§3）；外部 WS 盯梢仅测试/排障用（§4）。
6. **订阅即接管**：`report_subscribe` 后完成报告自动入队；不想要时 `report_unsubscribe` 退订（仅自己管理的 session）。
7. **一个 Session 一个任务**：避免混多个不相关任务（taskSeq/result 配对依赖此约束）。
8. **长任务防误杀**：stream running 卡死判定基于任务运行时长（`worker.task_timeout_sec`，默认 1800s）——长思考/大文件读取不会被静默超时误杀；仍建议复杂任务拆小、读大文件分段。
9. **及时清理**：`session_delete` / `session_batch_delete` 释放资源；watchdog 只回收进程不删 session。
10. **不依赖长驻 Worker**：watchdog 自动回收空闲 worker，用完即走，下次调用自动重建。
11. **workdir 用绝对路径指 Pan 外目录**：多 worker 共改一个项目时，让所有 session 指向同一 workdir。
12. **错误重试**：返回 `error` 时先查原因（session_get 的 lastResult / queue_pending 里的 zombie 报告），修复后重发。
13. **复用已删除的 session（session_import）**：Pan session 删除后其 CLI 会话仍保留，用 `session_import(list_projects → list_sessions → import)` 恢复完整上下文复用（§5 会话管理说明）——省去重建后重新探索/初始化，尤其适合「worker 完成但需继续排查/跟进」的场景。清理时 `session_batch_delete` 的 session 都走这条回收路径，不浪费上下文。

## 9. 常见问题

**Q: worker_task 返回 "Worker process dead"？**
A: Worker 崩溃/已回收。`worker_spawn(session_id=...)` 重新生成（自动恢复上下文）。

**Q: worker_task 后长时间无回复？**
A: 检查 `workerStatus`——`"idle"` 说明任务已完成但结果未读取，`session_get` 即可；`"running"` 且静默超时可能已被 watchdog 回收。

**Q: 想切换模型？**
A: 重新 `session_create` 并指定新 `model`；或 `session_update` 改 model——idle worker 自动 respawn 生效，running worker 回 idle 时自动重启（不能热切换运行中的 Worker）。

**Q: Worker 被 watchdog 回收了？**
A: 回收只杀进程不删 session。`workerStatus` 变 `null` 后直接 `worker_spawn` 或 `worker_assign`，自动重建。

**Q: MCP 工具连不上 Pan？**
A: `PAN_API_URL` 端口要指向实际运行的 port（main 分支 8768，MCP 默认 8768）。MCP server 用 `--pan-url` 或环境变量覆盖。

**Q: report_subscribe 后没收到完成报告？**
A: 检查 §3 前置条件：目标 session 是否有 `managed_by`、是否已 `report_subscribe`、你的环境是否有 `PAN_AGENT_SESSION_ID`（report 工具仅 Pan 内 session 可用）、manager 与目标是否**同实例**（§10.2 G9 / G10）。

## 10. 冷启动实测记录与待补充清单（D5）

> 立项 D5 要求同时验证 **HTTP 路径** 与 **MCP 路径**。下表 G1–G7 为 `docs/archive/冷启动Agent编排实测报告.md` 中 HTTP 路径实测结果；**MCP 路径实测（2026-08-17）见下方「MCP 路径实测」小节**，并据此更新 G2/G5、新增 G8–G12。

### 10.1 HTTP 路径实测缺口（原 D5）

| # | 缺口 | 建议补充位置 | 状态 |
|---|------|-------------|------|
| G1 | `POST /api/sessions`、`POST /api/spawn`、`POST /api/assign`、`DELETE /api/sessions/{id}` 的**请求体字段未记录**（原 §5 只说"见 server.py"）——纯 HTTP 冷启动在第 1 步就需读代码 | references/http-api.md 补核心端点 body 表 | ✅ |
| G2 | **MCP server 接线步骤缺失**：实测 ToolSearch 搜不到 `mcp__pan__` 工具时，手册无"如何启动/注入 MCP server 使工具可见"的动作序列 | §0.1/§5（MCP 路径实测） | ✅ §0.1/§5 |
| G3 | **workdir 相对基准未明确**：默认落点实测为 Pan server 数据根 `D:\project\pan-test\data\workdirs\<name>`，非当前项目目录 | §7.1 | ✅ §7.1 |
| G4 | **Windows curl 内联 UTF-8 JSON 报 body 解析错误**（实测 `{"detail":"There was an error parsing the body"}`）；应改用 `--data-binary @file` 或 urllib/requests | references/http-api.md | ✅ |
| G5 | `report_subscribe` 前置 `PAN_AGENT_SESSION_ID` 的**来源/注入方式未说明**（谁注入、何时有） | §3 | ✅ MCP 路径实测 |
| G6 | **字段映射未说明**：create 返回 `id`，spawn/assign 入参用 `sessionId`；`sessionId` vs `session_id` 命名不一致 | references/http-api.md | ✅ |
| G7 | 轮询**放弃/超时策略缺失**（多久、几次轮询算失败） | references/http-api.md 轮询兜底策略 | ✅ |

### 10.2 MCP 路径实测（2026-08-17，meta-agent `mcp__pan__` 直连）

**实测链路（一次走通）**：
`session_create`(ses_d66c08611936941b) → `worker_assign`(queued/worker-2) → 轮询 `session_get`(limit=30) → `lastResult.status="done"`, `result="391"`（子 worker 算 `17×23`）→ `session_delete`(deleted)。
**第二次**覆盖 `worker_spawn` 显式路径：create → `worker_spawn`(idle) → assign(queued) → 轮询 → `143÷11=13` done → delete。参数名、状态判断、轮询兜底均充分，**MCP 路径主链路顺畅**。

**G2 / G5 处理结论（已覆盖，更新原描述）**：

- **G2（MCP 接线）已解决且写入 §0/§5**：`mcp__pan__` 工具由 Pan adapter 在 spawn worker 时**自动注入**——通过 adapter 生成的 `data/mcp-configs/<session_id>.mcp.json`（含 `{"mcpServers":{"pan":{"command":"...python","args":["-m","packages.mcp.server"],"cwd":"...","type":"stdio"}}}`），经 `--mcp-config` 显式传入，**直接 connected 可见，无需 ToolSearch/DeferExecuteTool**。`ToolSearch`+`DeferExecuteTool` **仅**用于项目级 `.mcp.json` 路径发现的工具（见 §7.7）。手册原 §0/§5 "MCP 工具是 deferred 的"表述对 `--mcp-config` 路径不准确，已修正。

- **G5（`PAN_AGENT_SESSION_ID` 来源）已查实（2026-08-17）**：该变量**由 Pan adapter 注入到 MCP server 进程环境**（写入上述 mcp-config 的 `env` 段：`PAN_AGENT_SESSION_ID=<manager session id>`、`PAN_AGENT_SESSION_TITLE=<title>`）。**来源**：manager（meta-agent 自身）session 被创建并启用 MCP 时，adapter 生成 mcp-config 时填入；**何时**：MCP server 以 stdio 拉起的那一刻就带在环境里，对 meta-agent 透明（其亲 shell `env` 里查不到这些变量，属正常）。

- **G5 关键约束（新发现，致命）**：`report_subscribe` 要求 **manager session 与被管 session 同处一个 Pan 服务实例**。本实测中 MCP server 默认连 **8768**（见 §7.7/§0），而 `PAN_AGENT_SESSION_ID=ses_8f7825d50d340dad` 实际只存在于 **8767**（pan-test）。该环境变量值指向 8767，与 MCP server 的默认目标 8768 **跨端口**，导致 §3 内部报告路径在本布局下失效。**对齐前提**：`--mcp-config` 的 `cwd`/启动端口、`PAN_API_URL`、**与 `PAN_AGENT_SESSION_ID` 所在服务**必须三者一致。

**MCP 路径新发现缺口（G8–G12；2026-08-27 核对：各项建议均已在主文对应位置补齐 ✅——G8 见 §5/§6、G9/G10 三对齐见 §0/§3、G11 见 §2.5/§7.1、G12 见 §7.1）**：

| # | 缺口 / 现象 | 后果 | 建议补充位置 |
|---|-------------|------|--------------|
| G8 | `session_list` **无过滤参数**，默认把全部 session 的**完整 history** 序列化返回（实测 310KB / 265906 chars，触发工具输出 token 上限溢出） | 冷启动第一步"先查后做"反而撑爆上下文；必须先 `Read` 落盘文件分段看 | §5/§6：改用 `session_get(limit=)` + `worker_list` 代替全量 `session_list` |
| G9 | **MCP server 默认端口（8768）与 `PAN_AGENT_SESSION_ID` 所在服务（8767）跨端口**（见 G5 约束） | `report_subscribe` 内部报告路径不可用 | §0/§3：明确"MCP 目标端口 = PAN_AGENT_SESSION_ID 所在端口"三对齐 |
| G10 | **`report_subscribe` 在本布局返回 `404 "Not Found"`**：实测 8768(main) 服务 `server.py` 无 `report-subscribe` 路由（0 次命中），而本工作树 `server.py` 有（1 次）——即**运行中的服务落后于 MCP 工具版本**（版本 skew） | §3 内部报告走不通；只能退回 references/http-api.md 的轮询兜底 | §3：标注"需运行含 `report-subscribe` 的服务端（本分支 server.py 已含）"；否则用轮询兜底 |
| G11 | `session_delete` **只 kill worker + 删 session 元数据，不删 workdir 磁盘目录**（实测删除后 `data/workdirs/mcp-cold-probe` 空目录残留） | 磁盘累积空目录，需另清理 | §2.5/§7.1：说明残留，必要时 `rm -rf workdir` |
| G12 | workdir 默认基准 = **服务进程的数据根**，非固定 pan-test。本实测（8768 服务 cwd=`D:\project\Pan`）落点为 `D:\project\Pan\data/workdirs/<name>`，与 G3 记的 `D:\project\pan-test\...` 不同 | §7.1 的"Pan server 数据根"需指明是**实际运行实例的**数据根，可经 create 返回的 `workdir` 字段确证 | §7.1 |

**MCP 路径推荐操作顺序（据实测固化）**：
1. 确认 MCP server 目标端口 = `PAN_AGENT_SESSION_ID` 所在端口（否则 G9/G10 命中，退回 references/http-api.md 的轮询兜底）。
2. `session_create(name, adapter="cbc", model="hy3")` → 记下返回 `id`（= 后续 `session_id` 入参）。
3. `report_subscribe(session_id)`（§3；若可用）。
4. `worker_assign(session_id, text)` 立即返回 `queued`（自动 spawn，无需手动 `worker_spawn`；§2.1）。
5. 等 `queue_pending` 完成报告（兜底：轮询 `session_get(session_id, limit=15)` 直到 `lastResult.status=="done"`，≤30s 内完成，按 §6 不重发）。
6. 读 `lastResult.result`。
7. `session_delete(session_id)` 收尾（注意 §7.1 G11 workdir 残留）。
8. **避免**全量 `session_list`（G8）；状态巡检用 `worker_list` + 定向 `session_get`。

---

## 11. skill 维护规范

> 本 skill 是**项目维护的重点内容**（立项 `docs/archive/Pan冷启动Agent编排skill立项.md` §二·5），随代码演进，变更走 git PR/提交与代码同轨。以下规范不仅适用于本 skill，也适用于所有**文档/手册类任务**。

### 11.1 自包含可验证：编写与验证闭环

- **文档/手册任务必须"自包含可验证"**：写的人**模拟目标用户完整走一遍再交付**，不能只写完就交付。
- 目标用户画像（本 skill）：冷启动 agent——**无对话上下文、只有 MCP 工具 + skill**，不会主动去读代码。
- 编写流程：
  1. 按手册从零走一遍主链路（本 skill 即 §0 快速开始 → §2 编排链路 → §3 完成通知 → §5 收尾）；
  2. 走不通 / 有歧义处 = 手册缺口，补完**再走一遍**（闭环）；
  3. 交付前核对："用户**不读代码**能否完成？"——凡"先 Read 代码才能继续"的表述都是缺口（反例：G1，手册原只说"见 server.py"）。
- 手册内容禁止只引用代码文件而不给关键结论（参数表 / 返回格式 / 状态语义必须落字）。
- 验证闭环作为变更验收项：冷启动 agent 测试（仅 MCP + skill、无上下文完成一次编排，立项 §二·5「验证」）。

### 11.2 SKILL.md 双份管理

- **主源**：`docs/skills/pan/SKILL.md`（**git 版本控制**，随代码变更走 PR/提交）。
- **同步副本**：`.codebuddy/skills/pan/SKILL.md`（CodeBuddy 编辑器加载 skill 用，**不进 git**）。
- **技术细节子文档**：`docs/skills/pan/references/http-api.md`、`references/ws-protocol.md`（与主源同目录，相对链接引用）。
- **改内容先改主源，再复制到副本保持同步**——只改副本会让主源落后，下次 checkout/合并会把改动冲掉。
- 内容变化触发同步：MCP 工具 / HTTP API / workdir 约定 / 编排流程变更时，两处一并更新（本文件 §0 头注）。

---

## 关联文档

- `docs/archive/Pan冷启动Agent编排skill立项.md` — 本 skill 的立项依据
- `docs/plans&overviews/Worker监督与事件驱动模式.md` — 监督/盯梢实战、完成通知设计
- `docs/cbc-mcp-踩坑记录.md` — MCP 接入过程、/ws/agent 订阅协议、deferred 判定
- `docs/skills/pan/references/http-api.md` — HTTP API 速查（请求体、字段映射、curl 坑、轮询兜底）
- `docs/skills/pan/references/ws-protocol.md` — /ws/agent 订阅协议与 monitor_workers.py（测试/排障用）
- `packages/web/server.py` — HTTP API 与 /ws/agent 实现（单一事实源）
- `packages/mcp/server.py` — MCP 工具实现（`_api` 与各工具）
- `packages/core/worker.py` — watchdog / assign / 报告队列实现
