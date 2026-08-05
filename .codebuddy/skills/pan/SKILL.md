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
| `worker_task` | `session_id?`, `worker_id?`, `text` | 发送任务文本 |
| `worker_kill` | `worker_id` | 终止 Worker 进程 |
| `worker_list` | (无) | 列出所有运行中 Worker |

### 其他

| 工具 | 参数 | 说明 |
|------|------|------|
| `model_list` | `adapter?` | 列出可用模型 |

## 标准工作流

### 流程 1：创建会话并执行任务

```
1. session_create(name="my-session", adapter="cbc", model="hy3")
   → 返回 session_id: "ses_abc123..."

2. worker_spawn(session_id="ses_abc123...")
   → Worker 启动，状态 idle

3. worker_task(session_id="ses_abc123...", text="你的任务描述")
   → 返回 {"status": "queued"}

4. 等待 10-60 秒后...

5. session_get(session_id="ses_abc123...")
   → 检查 lastResult.status:
      - "done" → 读取 lastResult.result
      - "queued"/"running" → 继续等待
      - "error" → 查看错误信息
```

### 流程 2：在已有会话上继续对话

```
1. session_list()
   → 找到目标会话的 session_id

2. worker_spawn(session_id="ses_abc123...")  # 如果 Worker 已死
   → Worker 启动，恢复历史上下文

3. worker_task(session_id="ses_abc123...", text="继续之前的话题...")

4. session_get(session_id="ses_abc123...")
   → 读取最新回复
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
- `"running"` → 正在执行任务
- `"idle"` → Worker 空闲，可发送任务
- `"error"` → Worker 异常
- `null` → 无 Worker（需 `worker_spawn`）

## 最佳实践

1. **先查后做**：使用 `session_list()` 了解当前状态再操作
2. **命名规范**：Session 名称用短横线连接，有语义（如 `code-review`、`debug-auth`）
3. **及时清理**：不再需要的 session 用 `session_delete` 释放资源
4. **异步等待**：`worker_task` 提交后不要立即 `session_get`，等 10-30 秒让 LLM 处理
5. **错误重试**：`lastResult.status == "error"` 时检查原因，修复后 `worker_task` 重新发送
6. **一个 Session 一个任务**：避免在同一 Session 中混合多个不相关任务
7. **利用 history**：`session_get` 返回完整对话历史，上下文自然累积

## 常见问题

**Q: worker_task 返回 "Worker process dead"？**
A: Worker 崩溃了，执行 `worker_spawn(session_id=...)` 重新生成。

**Q: worker_task 后长时间无回复？**
A: 检查 `workerStatus`——如果是 `"idle"` 说明任务已完成但结果未读取。执行 `session_get`。

**Q: 想切换模型？**
A: 重新 `session_create` 并指定新 `model`。不能热切换运行中 Worker 的模型。
