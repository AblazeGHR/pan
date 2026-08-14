---
name: pan
description: Pan CLI Agent 编排中间层。通过 MCP 工具管理会话和 Worker 进程。当需要创建会话、生成 Worker、发送任务、读取结果或管理 CLI Agent 进程时使用。
---

# Pan — CLI Agent 编排中间层

Pan 是一个 Supervisor/Worker 架构的 CLI Agent 编排器。你（Meta-Agent）通过 Pan MCP 工具调度多个 CLI Worker 进程（cbc/kimi），每个 Worker 拥有独立的会话和记忆。

## 核心概念

| 概念 | 说明 |
|------|------|
| **Session** | 持久化的对话容器，包含 history、model、adapter 等配置。独立于 Worker 生命周期。 |
| **Worker** | 临时的 CLI 子进程（cbc/kimi），绑定到一个 Session。可被 kill 或 respawn。 |
| **Adapter** | CLI 工具类型：`cbc`（CodeBuddy CLI）或 `kimi`（Kimi CLI） |
| **Model** | AI 模型名称，如 `hy3`、`deepseek-v4-flash` |

**关键规则**：
- Session 是持久化的——kill Worker 不会删除 Session 数据
- 一个 Session 同一时间只有一个 Worker
- Worker 回复是异步的——`worker_task` 返回 `queued`，需要随后 `session_get` 读取结果
- Worker 会被 watchdog 自动回收（空闲/卡死），用前若 `workerStatus` 为 `null` 需重新 spawn

## 可用 MCP 工具

### 会话管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `session_create` | `name`, `adapter?`, `model?`, `permission_mode?` | 创建新会话 |
| `session_list` | (无) | 列出所有会话及 Worker 状态 |
| `session_get` | `session_id` | 获取会话详情（含 history 和 lastResult） |
| `session_delete` | `session_id` | 删除会话并 kill Worker |
| `session_history` | `session_id`, `limit?`, `before?` | 分页获取对话历史 |

### Worker 管理

| 工具 | 参数 | 说明 |
|------|------|------|
| `worker_spawn` | `session_id?`, `name?`, `adapter?`, `model?` | 为会话生成 Worker |
| `worker_task` | `session_id?`, `worker_id?`, `text` | 发送任务文本（异步，返回 queued） |
| `worker_handoff` | `session_id`, `text`, `timeout?` | **同步**：发任务并阻塞直到返回结果（串行依赖步骤用这个） |
| `worker_assign` | `session_id`, `text` | **异步分派**：发任务立即返回，完成时通过 `worker.result` 事件回调（并行 fan-out 用） |
| `worker_send` | `worker_id`, `text` | 向已有 Worker 发消息（多轮协作） |
| `worker_kill` | `worker_id` | 终止 Worker 进程 |
| `worker_list` | (无) | 列出所有运行中 Worker |

### 其他

| 工具 | 参数 | 说明 |
|------|------|------|
| `model_list` | `adapter?` | 列出可用模型 |

## 标准工作流

### 流程 1：创建会话并执行任务（推荐：handoff 同步等结果）

```
1. session_create(name="my-session", adapter="cbc", model="hy3")
   → 返回 session_id: "ses_abc123..."

2. worker_handoff(session_id="ses_abc123...", text="你的任务描述")
   → 阻塞直到 Worker 完成，直接返回最终结果
   → {"status": "done", "result": "...", "workerId": "worker-1"}
```

**不需要手动轮询**。handoff 默认 10 分钟超时，超时返回 `{"status": "error", "result": "handoff timed out after 600s"}`。

### 流程 1b：异步分派多个任务（并行 fan-out）

```
1. 为每个任务创建/复用 session
   worker_assign(session_id="ses_a...", text="任务A")
   worker_assign(session_id="ses_b...", text="任务B")
   → 两个都返回 {"status": "queued"}

2. 通过 /ws/agent 订阅 worker.result 事件接收完成回调
   subscribe { eventTypes: ["worker.result"] }

3. 收集所有完成的 worker.result 事件后汇总
```

### 流程 2：在已有会话上继续对话

```
1. session_list()
   → 找到目标会话的 session_id

2. worker_spawn(session_id="ses_abc123...")  # 如果 Worker 已死
   → Worker 启动，恢复历史上下文

3. worker_handoff(session_id="ses_abc123...", text="继续之前的话题...")
   → 同步拿到新回复
```

### 流程 3：检查状态

```
1. session_list()
   → 查看所有会话的 workerStatus:"idle"/"running"/"error"

2. 对感兴趣的会话:
   session_get(session_id="ses_abc123...")
   → 读取完整 history 和 lastResult
```

### 流程 4：清理

```
1. session_list()
   → 找到完成任务的会话

2. session_delete(session_id="ses_abc123...")
   → 删除会话 + kill Worker
```

## 状态判断

Worker 任务提交后，通过 `session_get` 的 `lastResult.status` 判断：

| status | 含义 | 操作 |
|--------|------|------|
| `"queued"` | 任务已入队，Worker 准备处理 | 等待 5-10 秒后重查 |
| `"running"` | Worker 正在执行中 | 继续等待，不要重复提交 |
| `"done"` | 任务完成 | 读取 `result` 字段获取回复 |
| `"error"` | 任务失败 | 读取 `result` 字段获取错误信息 |

也可以通过 `session_list` 返回的 `workerStatus` 快速判断：
- `"queued"` → 任务已入队
- `"running"` → 正在执行任务
- `"idle"` → Worker 空闲，可发送任务（spawn 即就绪，无需等待）
- `"error"` → Worker 异常
- `null` → 无 Worker（需 `worker_spawn`；也可能是 watchdog 自动回收后尚未重建）

## 自动回收（watchdog）

Pan 会对 **stream 模式**的 Worker 自动回收，无需手动清理：

| 条件 | 行为 | 默认 |
|------|------|------|
| `running`/`queued` 持续 **无任何输出** 超过 `worker.timeout_sec` | 判定卡死 → kill（等待中的 handoff 收到 error） | 300s |
| `idle` 持续超过 `worker.idle_sec` | 空闲回收 → kill（session 保留，可重建） | 300s |
| `held`（takeover 模式）/ `zombie` | **跳过**，不回收 | — |

**要点**：
- `last_activity` 每次 stdout 有事件即刷新——**长任务只要持续输出就不会被误杀**，超时只针对"进程活着但完全静默"的卡死
- 回收后 `workerStatus` 变 `null`，session 数据完好；下次 `worker_spawn`/`worker_handoff` 自动重建并恢复上下文
- MCP one-shot 模式由读取超时承担（同一 `worker.timeout_sec`），无独立 watchdog
- 配置在 `config.json` 的 `worker` 段，改后重启生效

## 最佳实践

1. **先查后做**：使用 `session_list()` 了解当前状态再操作
2. **命名规范**：Session 名称用短横线连接，有语义（如 `code-review`、`debug-auth`）
3. **及时清理**：不再需要的 session 用 `session_delete` 释放资源
4. **串行依赖用 `worker_handoff`**：同步阻塞拿结果，不要轮询 `session_get`
5. **并行 fan-out 用 `worker_assign` + 订阅 `worker.result`**：一次分派多个任务，收集完成事件后汇总
6. **错误重试**：返回 `error` 时检查原因，修复后重新调用 `worker_handoff`/`worker_assign`
7. **一个 Session 一个任务**：避免在同一 Session 中混合多个不相关任务
8. **利用 history**：`session_get` 返回完整对话历史，上下文自然累积
9. **不依赖长驻 Worker**：watchdog 会自动回收空闲 Worker，用完即走，下次调用自动重建

## 常见问题

**Q: worker_task 返回 "Worker process dead"？**
A: Worker 崩溃了，执行 `worker_spawn(session_id=...)` 重新生成。

**Q: worker_task 后长时间无回复？**
A: 检查 `workerStatus`——如果是 `"idle"` 说明任务已完成但结果未读取。执行 `session_get`。

**Q: handoff 超时了？**
A: 默认 10 分钟。任务复杂可传更大 `timeout`；超时后结果仍可能稍后到达，可 `session_get` 补查。

**Q: Worker 被 watchdog 回收了？**
A: 回收只杀进程不删 session。`workerStatus` 变 `null` 后直接 `worker_spawn` 或 `worker_handoff`，会自动重建并恢复上下文。

**Q: 想切换模型？**
A: 重新 `session_create` 并指定新 `model`。不能热切换运行中 Worker 的模型。
