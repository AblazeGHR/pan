# pan-test 分支质量审查报告（ds-v4p）

> 审查日期：2026-08-15
> 审查模型：ds-v4p（deepseek-v4-pro）
> 分支：`pan-test`（基于 `main` @ `9eb464f`，领先 12 个提交）
> 审查方式：独立代码走查 + 测试执行，未参考任何其他审查报告。

---

## 一、变更范围概览

本分支实现「Meta-Agent 编排」能力，共 12 个提交，改动 10 个文件（+1761 / -35）：

| 文件 | 改动 | 内容 |
|------|------|------|
| `packages/core/worker.py` | +328 | Worker 状态机（queued/zombie）、watchdog 超时/空闲回收、编排三原语 handoff/assign/send、taskId 幂等 |
| `packages/web/server.py` | +148 | `/ws/agent` 订阅过滤（eventTypes/sessionIds）、`reconnect` 重连补发、`/api/handoff` `/api/assign` |
| `packages/mcp/server.py` | +61 | 新增 `worker_handoff` / `worker_assign` / `worker_send` MCP 工具 |
| `packages/core/config.py` | +8 | `worker` 段默认配置（timeout_sec / idle_sec） |
| 测试 ×4 | +1064 | watchdog / 三原语 / 状态机 / 订阅过滤 |
| SKILL.md / 文档 | — | Meta-Agent 使用手册 + 实现记录 |

**测试执行结果**：新增 4 个测试文件 32 个用例全部通过；全量 `tests/` 120 个用例全部通过（`pytest -q` → `120 passed`）。

---

## 二、问题清单（按严重度分级）

### 🔴 高严重度（正确性缺陷）

#### H1. Stream 模式 result 序号配对语义不一致，中断后 handoff 永久失配

三处代码用了**三种不同的计数器**表达「任务序号」：

- `handoff` 预分配序号用 `_task_counter`（`packages/core/worker.py:1132-1133`）
- MCP 模式 result 的 `taskSeq` 用 `_current_seq`（`packages/core/worker.py:660-661`）
- **Stream 模式 result 的 `taskSeq` 用 `_result_count`**（`packages/core/worker.py:265-266`）

而 `handoff` 的 waiter 只匹配 `_task_counter` 分配的序号（`worker.py:1140`、`_resolve_result_waiter`）。`_result_count` 与 `_task_counter` 只有在「每个任务恰好产生一个 result 且严格按序」的理想情况下才相等。一旦被打破，二者**永久错位**：

- `interrupt_worker` / `restart_worker` / `respawn_worker` 中断在途任务时，`_task_counter` 已自增但该任务不会产生 result；
- `_restart_tasks`（`worker.py:887-901`）**不重置**这两个计数器；
- `_consumer_stream` 的「process dead」分支（`worker.py:430-439`）广播 result 但**不递增** `_result_count`。

后果：错位后该 worker 上所有后续 `handoff` 都无法匹配到 result，静默悬挂直到 600s 超时。而 MCP 路径作者显然知道正确做法（用 `_current_seq`），stream 路径是遗留实现未同步。

**修复方向**：stream 模式 `_read_stdout` 与 MCP 模式统一改用 `w._current_seq`（consumer 取出 item 时已正确记录），废弃 `_result_count`（它在 MCP 模式里递增后根本没被使用，属死增量）。

---

### 🟠 中严重度（可靠性 / 资源问题）

#### M1. 断线重连补发只覆盖「完全未消费」场景，中途断线丢结果

`reconnect` 处理器（`packages/web/server.py:554-569`）只补发 `s.last_result`（**最后一条**），且仅在 `sub.consumed_seq.get(sid, 0) == 0` 时补发。实际逻辑：

```python
if sub.consumed_seq.get(sid, 0) > 0:
    continue   # 只要消费过任意一条，就整段跳过
```

场景：Agent 消费了 seq=1 后断线，期间产生 seq=2、3、4。重连后 `consumed_seq[sid]=1 > 0` → 直接跳过，**seq 2/3/4 全部丢失**。且 `Session.last_result` 结构里**不存 `taskSeq`**，游标无法与最新结果对齐，无法精确判断「最新一条是否已消费」。docstring 声称「补发未消费 result」，实现与其不符。

**修复方向**：`last_result` 补存 `taskSeq`；补发条件改为 `consumed_seq[sid] < latest_seq`，并维护可回放的 result 环形缓冲（或至少补发最新一条而非跳过）。

#### M2. `_task_status` 幂等注册表无清理，长期运行内存泄漏

全局 `_task_status: dict[str, dict]`（`worker.py:118`）按 taskId 登记后从不删除（仅在 `handoff` 中 `pop` waiter，但 `_task_status` 条目永久保留）。长跑 Meta-Agent 高频 handoff 会持续累积。**建议**：完成/超时后设 TTL，或随 worker 销毁清理。

#### M3. MCP 模式任务完成后 `last_activity` 未刷新，刚忙完即可能被回收

`last_activity` 的更新点只有 `_read_stdout`（stream）、`send_task`、`create_worker`、`_restart_tasks`、`branch_worker`；`_consumer_mcp` 全程不刷新（`worker.py:457-679`）。MCP 任务耗时会被算进 idle 时长——若任务接近 `timeout_sec`（默认 300s）上限，完成瞬间 `idle_for ≈ 300s`，watchdog 下一 tick（30s 内）即把刚完成任务的 worker 回收。语义错误（「刚忙完」≠「空闲已久」），虽因 MCP worker 无长驻进程、重建成本低而影响有限。

**修复方向**：`_consumer_mcp` 收尾时 `w.last_activity = time.monotonic()`。

---

### 🟡 低严重度（健壮性 / 一致性）

#### L1. `broadcast()` 为未订阅连接创建一次性 `AgentSubscription`

`packages/web/server.py:143-144`：

```python
sub = agent_subscriptions.get(ws)
if sub is None:
    sub = AgentSubscription()   # 未写回 agent_subscriptions
```

未写回导致这类连接的 `consumed_seq` 每轮广播都丢失，`reconnect` 补发游标永远为 0。应改为 `agent_subscriptions.setdefault(ws, AgentSubscription())`。

#### L2. `consumed_seq` 在发送成功前推进

`packages/web/server.py:152-155` 在 `try: await ws.send_json(...)` 之前就更新 `consumed_seq`。若发送抛异常（连接已死），该 seq 仍被记为已消费，重连后不会补发。

#### L3. `_result_waiters` 每 worker 单槽位，无并发防护

`worker.py:1140`：`_result_waiters[w.worker_id] = (seq, fut)` 直接覆盖。两个并发 `handoff` 打到同一 worker 时，前者 waiter 被后者覆盖，前者悬挂至超时。文档虽声明「一个 Session 一个任务」，但缺运行时防护（如检测已有 waiter 时返回错误）。

#### L4. `config.example.json` 未记录新增 `worker` 段

`DEFAULT_CONFIG`（`config.py:29-35`）与 `config.json` 已含 `worker.timeout_sec / idle_sec`，但提交的模板 `config.example.json` 未同步，新用户无法发现可调项。

#### L5. `send_task` 状态机精度边缘窗口

`_read_stdout` 在每条 result 后无条件置 `w.status = "idle"`（`worker.py:286`），即使队列仍有排队任务。此时若 `_WORKER_IDLE_SEC` 配置较短，watchdog 可能在 consumer 消费下一个排队任务前把 worker 回收。该「idle 覆盖」是既有行为，但本分支引入的 `queued` 状态与 watchdog 判定放大了影响面。

---

## 三、做得好的地方

1. **测试覆盖扎实**：新增 32 个用例覆盖状态机、watchdog、三原语、订阅过滤，且含关键回归用例 `test_watchdog_self_cancel_regression`（watchdog 自取消竞态）。全量 120 通过。
2. **自取消竞态修复到位**：`kill_worker` 用 `w._watchdog_task is not current` 保护（`worker.py:809-811`），避免 watchdog 自杀时 kill 流程被中断。
3. **zombie 状态可观测**：先广播 `worker.zombie` 再 `workers.pop`（`worker.py:316-327`），订阅方能观测到死亡瞬间，而非直接消失。
4. **崩溃/杀死强制 resolve waiter**：`_read_stdout` EOF、`kill_worker`、`_consumer_stream` 死进程路径均调用 `_resolve_result_waiter(..., task_seq=None)` 强制返回 error，避免 handoff 悬挂至超时（`worker.py:324`、`819`、`439`）。
5. **文档质量高**：SKILL.md 详尽记录了 watchdog 行为、三原语用法、超时重试约定（同 taskId 幂等）；`Meta-Agent实现记录.md` 沉淀了「stream 模式无 init 事件」这一踩坑教训，可复用于后续状态机设计。
6. **序号配对思路正确**（waiter 只匹配自身 seq），且 MCP 路径实现正确——问题仅在 stream 路径未同步（见 H1）。

---

## 四、测试覆盖评估

| 维度 | 覆盖情况 | 缺口 |
|------|---------|------|
| watchdog 超时/空闲/held 跳过 | ✅ 7 用例 | — |
| watchdog 自取消回归 | ✅ 有 | — |
| 三原语基础语义 | ✅ 11 用例 | — |
| 序号配对（waiter 过滤） | ⚠️ 只测 `_resolve_result_waiter` 隔离 | **未测 stream 模式 `_read_stdout` 端到端序号配对**（H1 因此漏网） |
| taskId 幂等 | ✅ 完成/超时两态 | 未测「进行中任务完成后重试」的过渡时序 |
| 订阅过滤 | ✅ 7 用例 | **未测 `reconnect` 补发路径**（M1 因此漏网） |
| 状态机 | ✅ 5 用例 | — |

**建议补充**：H1、M1 各加一条集成测试（mock stdout 流 + 中断 + reconnect 中途断线场景），把当前靠单测隔离掩盖的端到端缺陷锁住。

---

## 五、结论

分支整体工程质量良好：架构清晰、文档完善、测试覆盖到位，核心的 watchdog 竞态修复有针对性且有回归保护。但存在 **1 个高严重度正确性缺陷（H1）**——stream 模式下 handoff 的序号配对在中断/重启后会永久失配，直接导致同步等待静默超时。该缺陷源于 MCP 路径已修正、stream 路径未同步的不一致，修复成本低、风险小，建议合并前优先修复。另有 3 个中严重度问题（重连补发不完整、幂等表泄漏、MCP idle 时长失真）建议一并处理。
