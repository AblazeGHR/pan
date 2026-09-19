---
name: pan-ws-protocol
description: Pan /ws/agent 订阅协议与 monitor_workers.py 盯梢模板（测试/排障用引用文档，配合 docs/skills/pan/SKILL.md 使用）。MA 编排完成通知一律走内部 report_subscribe → queue_pending（SKILL.md §3），本文件仅记录 WS 广播协议与外部盯梢工具供测试/排障/外部协调者使用。
---

# Pan /ws/agent 订阅协议与盯梢模板（测试/排障用）

> **重要**：MA 编排 TA（的 Session/Worker）时，完成通知**一律走内部订阅**（MCP `report_subscribe` → 报告入自己的落盘队列 `queue_pending`，见 `docs/skills/pan/SKILL.md` §3），**不依赖 /ws/agent**。本文件只记录 WS 广播协议与 `monitor_workers.py` 盯梢脚本，供**测试 / 排障 / 外部（非 MA）协调者**使用。

## 与内部 report_subscribe 的区别

| 维度 | 内部报告（report_subscribe） | WS 广播（/ws/agent） |
|------|------------------------------|----------------------|
| 送达 | 报告 append 到 MA 落盘队列 `queue_pending`（跨服务重启不丢） | 实时推送，无落盘；断线需 `reconnect` 补发 |
| 关系 | **订阅即接管**：自动建立 managed 关系（claim） | **不建立** managed 关系，只是广播监听 |
| 适用 | MA 编排自己的 TA（**首选**） | 外部 CodeBuddy 会话 / 测试脚本实时盯梢 |
| 异常感知 | zombie 报告也入 queue_pending（`{"status":"error","type":"zombie",...}`） | `worker.zombie` 事件广播 |

## /ws/agent 订阅协议

WebSocket 端点 `ws://127.0.0.1:<port>/ws/agent`。

### 客户端 → 服务端

| 消息 | 格式 | 说明 |
|------|------|------|
| subscribe | `{"type":"subscribe","eventTypes":["worker.result","worker.zombie"],"sessionIds":["ses_..."]}` | `eventTypes`：省略/空数组 → 默认 `["worker.result"]`；`["*"]` 订阅全部。`sessionIds`：省略 → 所有 session；只过滤 `worker.result`。回 `{"type":"subscribed",...}` |
| reconnect | `{"type":"reconnect","sessionIds":["ses_..."],"resultCursors":{"ses_...":12}}` | 断线重连补发：每 session 中 `resultCursor` 大于客户端游标的保留终态，带 `replayed: true`。省略 `resultCursors` 时兼容旧客户端，仅补发当前可见的最新终态 |
| resync | `{"type":"resync","sessionIds":["ses_..."],"includeAllSessions":true}` | 获取权威、有限边界的快照：session summary、存活 worker、指定 session 的 history/queue/lastResult；用于 `resync_required`、服务恢复或游标过期 |

### 服务端 → 客户端事件

| 事件 | 字段 | 说明 |
|------|------|------|
| `worker.result` | `workerId, sessionId, status(done/error/cancelled), result, taskSeq, resultCursor, terminalKey` | **任务完成/失败/取消**，默认订阅；终态结果可按 `resultCursor` 在 reconnect 时补发 |
| `worker.zombie` | `workerId, sessionId, returncode` | 进程退出/被杀/回收瞬间广播（订阅方据此感知异常丢失） |
| `worker.crashed` | `workerId, sessionId, returncode` | 非零退出 |
| `worker.status` | `workerId, sessionId, status, source` | 状态切换（running 等） |
| `worker.stream` | `workerId, sessionId, event` | 原始 stream 事件（默认不订阅，防 context 爆炸） |
| `worker.spawned` | `sessionId, workerId, name, status, model` | worker 生成 |
| `session.created/updated/renamed/deleted` | `sessionId, name?...` | session 生命周期 |
| `session.orderUpdated` | `order: [sessionIds]` | 列表自定义顺序持久化后广播（2026-09 起，拖拽排序） |
| `sessions.deleted` | `sessionIds` | 批量删除 |
| `queue.item_added` / `queue.item_updated` / `queue.item_removed` | `sessionId, queueItemId, queueRevision, item?` | 服务端队列（`queue_pending`）增量事件；`queue.snapshot` 为全量快照 |
| `assign.result` / `send.result` | 含 `status/result` | WS 主动调用（type=assign/send）的同步应答 |
| `subscribed` / `error` | — | 协议握手 / 错误 |

订阅状态：每个连接独立维护 `consumed_cursor`（每 session 已成功送达的 `resultCursor`）。只有 WebSocket `send` 成功后才推进游标；客户端重复 reconnect 使用同一游标不会重复返回已成功送达的结果。服务端只保留每个 Session 最近 64 个终态结果；游标早于保留窗口时返回 `resync_required(reason=result_cursor_expired)`，随后发送 `resync.snapshot`，不构建无限 replay log。

实时事件还带服务实例内的 `eventEpoch` 与单调 `eventSeq`。客户端发现同一 epoch 的序号出现间隙时必须请求 `resync`；epoch 变化（服务重启）也应视为需要快照。快照带当前事件边界 `snapshotId`，其内容是权威投影，不是可无限回放的事件日志。

`resync.snapshot` 的语义如下：

- `sessions` 是有限的 Session summary；`includeAllSessions` 最多返回 512 个。
- `details[sessionId]` 只为请求的 Session 返回 bounded history（最近 50 条）及 summary/lastResult；更早历史继续通过带 cursor 的 HTTP history API 获取。
- `details[sessionId].queue` 来自持久化 `queue_pending`（含 `queueRevision`），不是 `pending_signal` 或 `sent_to_cli` 的推断。`sent_to_cli` 只表示已交给 CLI，不表示 provider/business completion。
- 快照与增量同时存在时，客户端按 `summaryRevision`、`queueRevision`、`resultCursor` 和事件边界收敛，不能用旧快照覆盖较新的本地投影。

## Dashboard `/ws` 交互请求恢复

React dashboard 使用 `ws://127.0.0.1:<port>/ws`。连接建立后发送：

```json
{"type":"resync","sessionIds":["ses_..."],"includeAllSessions":true}
{"type":"sync_interactive"}
```

dashboard 收到 `resync_required` 或检测到事件游标间隙时重复请求 `resync`，并通过现有 HTTP revision/history reader 合并权威结果。`resync.snapshot` 是恢复边界，不代表已经重新发送了每一条 delta；客户端不能把它当作比随后到达的较新 live event 更新的状态。

Dashboard 端其它入站消息（浏览器发送即**持久化入队**，`accepted` = 已落盘而非 Provider 完成）：

| 消息 | 格式 | 说明 |
|------|------|------|
| user_inject | `{"type":"user_inject","sessionId":"ses_...","text":"...","clientMessageId"?: "browser-uuid"}` | 浏览器发消息——进入服务端权威队列（等价 `POST /api/sessions/{id}/queue`），回 `user_inject.accepted` / `user_inject.rejected` |
| worker_control | `{"type":"worker_control","sessionId"|"workerId": "...", "control": {...}}` | 向运行中 worker 注入原生控制（Codex 审批 / user_input / terminal 等） |

服务端会把仍由**存活 worker**持有的 Codex 原生审批、用户输入、MCP elicitation
和 terminal interaction，以带 `replayed: true` 的 `worker.stream` 事件补发，同时回放
最新的原生 thread status 与 token usage。也可传
`sessionIds` 数组限制范围。该机制只恢复 UI 快照；原生 JSON-RPC 请求仍在原 worker
进程中，worker 已重启或死亡的请求不会伪造恢复，避免把旧 response 发给新进程。

## monitor_workers.py 盯梢模板

**监督脚本**（随项目维护，`packages/mcp/monitor_workers.py`）——**双通道**：

1. **WS 事件**（实时）：订阅 `worker.result`（正常完成）**和** `worker.zombie`（意外死亡 / watchdog 回收 / 进程退出）——worker 意外丢失对协调者可见。
2. **健康检查**（防「假 running」，每 30s 一次）：轮询 HTTP `GET /api/sessions/{id}` + 检查 transcript 文件 mtime（`~/.codebuddy/projects/<d-project-<workdir>/*.jsonl`，即 workdir 绝对路径 slug 化后的项目目录）。Pan 报 `running` 但 session `updatedAt` 与 transcript **均**超过 3 分钟无更新 → 输出一行 `STALE`（假 running / 卡死）。**去重冷却**：STALE 只在进入卡死时输出一次；恢复活跃后输出 `RECOVERED`，若再次卡死会再次 STALE。

```bash
# 用 Pan 服务 .venv 的 python 运行（已含 websockets）
python packages/mcp/monitor_workers.py
# 通过 PAN_WS_URL 环境变量指定端口（默认 ws://127.0.0.1:8768/ws/agent；HTTP 基址自动由它推导）
PAN_WS_URL=ws://127.0.0.1:8767/ws/agent python packages/mcp/monitor_workers.py
# 按 sessionId 过滤订阅与健康检查（只盯自己派发的 session，避免其他 session 的事件打扰）
PAN_SESSION_IDS=ses_a,ses_b python packages/mcp/monitor_workers.py
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
Monitor(command="python packages/mcp/monitor_workers.py", persistent=true)
```

每次脚本输出一行 → Monitor 唤醒协调者（秒级感知，替代长轮询）。

**为什么脚本中转，不直接用 Monitor 的 `ws` 模式**：Monitor 的 `ws` 模式**拒绝连接私有/内部地址**（`127.0.0.1`/`localhost` 都被拒）——CodeBuddy 的 WebSocket 安全限制。所以用 `command` 模式跑 python 脚本，由脚本连本机 WS（无此限制），再经 stdout 中转给 Monitor。

依赖：`websockets` 库（Pan 服务 `.venv` 已含，如 `D:/project/Pan/.venv`；缺失时先 `pip install websockets`）。
