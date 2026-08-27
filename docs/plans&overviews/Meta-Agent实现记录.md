# Meta-Agent 功能实现记录

> 记录 2026-08-14 pan-meta-agent 分支上 Meta-Agent 编排能力的实现过程、关键教训与待办。
> 对应 `Meta-Agent与Worker通信机制设计立项.md` 的三条建议落地情况。
> **历史记录（2026-08-23 注记，08-27 更新数字）**：本文档是 2026-08-14 的实现快照（当时 MCP 工具 10 个）；此后工具已扩充至 **27 个**（含 `session_import`/`session_managed`/`session_batch_delete`/`session_handoff`/`worker_send_force` 等，另有 pan-qq MCP 6 个）、完成通知已定型为 report_subscribe 订阅制；旧 `worker_handoff` 已于 2026-08-26 移除（下文相关表述仅具历史价值）。SKILL.md 是当前编排手册单一事实源，MCP 工具现状见 `docs/skills/pan/SKILL.md` §7。

## 一、已实现能力（对应三建议）

| 建议 | 实现 | 提交 |
|------|------|------|
| 建议 3（理解 Pan） | `packages/mcp/` 10 个 MCP 工具 + `.codebuddy/skills/pan/SKILL.md` | 既有 |
| 建议 2（任务管理） | 三原语 `handoff`/`assign`/`send` + `worker.result` 事件 | `7bc114a` |
| 建议 2（Agent 视图） | `/ws/agent` 事件订阅过滤（`subscribe` 命令） | `772df0a` |
| 建议 1（状态机） | `queued`/`zombie` 状态 + watchdog 超时/空闲回收 | `c1fa5f7`/`89aa860` |

### 编排三原语

- **`handoff`**：同步阻塞，发任务后等**该任务对应的** result 事件返回。默认 10min 超时。串行依赖步骤用。
- **`assign`**：异步分派，立即返回 `queued`，完成时通过 `worker.result` 事件回调。并行 fan-out 用。
- **`send`**：向已有 worker 发消息，多轮协作。

三个原语通过三条通道暴露：`worker.py` 内部函数、HTTP API（`/api/handoff`、`/api/assign`）、MCP 工具（`worker_handoff`/`worker_assign`/`worker_send`）。

### result 与 task 的序号配对

`_result_waiters` 从 `dict[worker_id, Future]` 升级为 `dict[worker_id, (seq, Future)]`：

- `send_task` 分配/预分配自增 `taskSeq`，入队消息携带
- `worker.result` 广播带 `taskSeq`
- waiter 只匹配自己任务的序号——**修复**了"worker 队列有在途任务时 handoff 拿到别的任务结果"的缺陷
- worker 崩溃/kill 时 force-resolve（忽略序号），避免悬挂

### watchdog 生命周期管理

配置在 `config.json` 的 `worker` 段（启动时读一次缓存）：

```jsonc
"worker": {
  "timeout_sec": 300,  // running/queued 静默超时 → kill
  "idle_sec": 300      // idle 空闲回收
}
```

- 每 worker 一个 30s tick 循环；`last_activity` 每次 stdout 事件刷新
- `held`（takeover 模式）/`zombie` 跳过不回收
- 仅 stream 模式启用；MCP one-shot 由读取超时承担（同一 `timeout_sec`）
- 超时 kill → resolve waiter(error)，任务不回队列（丢失，Meta-Agent 自行决策）

## 二、关键教训：stream 模式没有 init 事件

**背景**：Phase A 最初为 worker 引入了 `spawning` 状态，假设"CLI 就绪后会发 init 事件转 idle"。

**实测发现**（2026-08-14 端到端测试）：

- **stream 模式**（`--input-format stream-json`）启动时只输出 `serve 0`，**不产生 init 事件**
- **init 事件只在 one-shot 模式**（`-p` + prompt 参数）出现
- main 分支从不依赖 init 事件：`create_worker` 直接设 `idle`，spawn 即就绪

**后果**：`spawning` 状态下 worker 永远等不到 init 事件，卡在 `spawning` 直到任务完成被 result 事件拉回 `idle`。功能正常但状态错误。

**修复**（`0203905`）：移除 `spawning` 状态，stream 模式 spawn 即 `idle`。保留有真实信号的 `queued`（send_task 入队）和 `zombie`（EOF）。

**教训**：
1. **状态转换必须有真实信号**——不能假设 CLI 会发某种事件。设计状态机前先确认每种模式的真实输出行为。
2. **main 分支的 `idle` 语义是对的**——它不依赖任何就绪事件，spawn 即就绪。
3. 引入新状态前，用真实 CLI 验证事件流（one-shot vs stream 模式行为可能完全不同）。

## 三、遗留不完善项

### 已补齐（2026-08-14 第二轮）

| 项 | 实现 |
|----|------|
| **MCP 模式 watchdog** | 只做 idle 回收（running 由 `_consumer_mcp` 读取超时兜底），长期空闲的 MCP worker 不再泄漏 |
| **订阅按 sessionId 过滤** | 订阅升级为 `AgentSubscription` dataclass（event_types + session_ids + consumed_seq），worker.result 按 session 定向推送 |
| **断线重连补发** | `reconnect` 命令按 session 补发未消费的 result（consumed_seq 游标） |
| **handoff taskId 幂等** | 同一 taskId 重发不重复入队：已完成返回缓存结果，进行中返回 pending。超时后安全重试防双跑 |

### 设计上有意保留的限制

- **星形拓扑**：Meta-Agent ↔ Worker 的纯星型结构，Worker 间不直接通信，编排逻辑由 Meta-Agent 承担。现阶段设计如此，不改。
- **handoff 一个 Session 一个任务**：SKILL.md 最佳实践第 7 条。序号配对已修复"拿错结果"缺陷，但多任务并发仍不推荐。
- **任务丢失不自动重试**：watchdog 超时 kill 后任务不回队列，Meta-Agent 收到 error 自行决策。避免副作用任务重复执行。

## 四、验证记录

- **单测**：119 passed（含 worker 状态机 5、订阅过滤 7、三原语 11、watchdog 7）
- **端到端**（hy3 真实模型，pan-test 分支）：
  - spawn worker → 立即 `idle` ✅
  - `POST /api/handoff` → `{"status":"done","result":"OK"}` ✅
  - `/ws/agent` subscribe → 只收订阅类型 ✅
  - `POST /api/assign` → queued → running → result 事件流 ✅
  - `send` 多轮协作 → 第二次对话返回正确结果 ✅
  - **taskId 幂等**：超时→pending，重试不双跑，完成后同 taskId 返回缓存 ✅
  - **订阅 sessionId 过滤**：只订阅 session A，B 的 result 不收 ✅
  - session 清理 ✅

## 五、关联文档

- `Meta-Agent与Worker通信机制设计立项.md` — 设计立项与同类产品调研
- `阶段计划与进度.md` — P1（状态机）/P4（Worker 间通信）排期（原 Phase2-收尾功能计划，已合并）
- `.codebuddy/skills/pan/SKILL.md` — Meta-Agent 使用手册（含 watchdog 行为）
