# pan-test 分支质量审查（Meta-Agent 编排）

> **审查模型：ds-v4f（deepseek-v4-flash）**
>
> 审查日期：2026-08-15
> 审查范围：`main..pan-test`（**12 ahead, 0 behind**，HEAD `98080f2`）
> 审查内容：worker 状态机三态、watchdog 超时/空闲回收、编排三原语 handoff/assign/send、taskId 幂等、/ws/agent 订阅过滤 + 重连补发、MCP 工具扩展、SKILL.md/实现记录
> 审查方法：逐文件 diff 审查（`packages/core/worker.py`、`packages/web/server.py`、`packages/mcp/server.py`、`packages/core/config.py`）+ 4 个新增测试文件 + 全量 pytest
> 审查状态：**分支尚未合入 main**，本报告为合入前评估

## 总体评价

**良（中偏上），具备合并条件，建议先处理 1 项高 + 2 项中后合并。**

与前两轮审查（memory 分支 43 项问题、全项目审查 D0-D14）相比，本分支是**完成度最高的一轮**：主题集中、实现完整、测试充分（34 个新测试，覆盖主要路径与关键竞态）、自动化全绿、无代码异味残留。主要短板集中在三处边界：

- **taskId 幂等注册表没有失败组合路径和生命周期管理**（高）——超时后任务 crash，幂等表永久卡 pending，且无限增长
- **并发 handoff 的 waiter 单槽位覆盖**（中）——同 worker 并发两个 handoff 时先发者结果丢失
- **MCP one-shot 模式 idle 回收计时起点错误**（中）——长任务完成后几乎立即被回收

分维度：

| 维度 | 评价 | 说明 |
|------|------|------|
| 功能完整性 | 好 | 三原语 + 幂等 + 订阅过滤 + watchdog 全部落地，有对应测试 |
| 并发/竞态 | 中 | 自我发现并修复 watchdog 自取消竞态（98080f2）；但 waiter 单槽位、幂等表无清理仍开放 |
| 测试 | 好 | 34 个新测试 + 全量 120 passed；watchdog 回归测试用真实 kill_worker 非 stub |
| 文档 | 好 | SKILL.md 同步更新、实现记录详尽、配置有注释；但 config.example.json 未同步 |
| 代码卫生 | 好 | 无新增 print、提交信息规范（fix/feat/docs/debug 前缀 + 根因说明） |
| 安全 | 好 | 无新增路径穿越/注入面（鉴权缺口是项目级 #1 遗留，非本分支新增） |

## 自动化检查结果

| 检查 | 结果 |
|------|------|
| 测试 `.venv` `pytest`（120 个） | ✅ **120 passed**（3.91s），含 34 个新测试 |
| 新增 print/调试残留 | ✅ 无（`debug:` 提交的打印已随 98080f2 移除） |
| 前端检查（pre-commit） | ✅ 不适用——本分支改动全为 Python/docs，不触发 |
| 工作区状态 | ✅ 干净，无未提交改动 |
| 提交规范 | ✅ 12 commits 全部 conventional 前缀，多数带根因分析 |

## 优点（本轮做得好）

1. **自我发现并修复竞态**（`98080f2`）：watchdog 空闲回收调 `kill_worker` 时，`kill_worker` 第一步 cancel 自己的 `_watchdog_task`，watchdog 被 `CancelledError` 中断导致进程杀不掉、worker 永不 pop。用 `asyncio.current_task()` 判断跳过自取消，且有**真实 `kill_worker`（非 stub）的回归测试**覆盖。
2. **taskSeq 序号配对设计正确**：`_result_waiters` 只匹配期望序号（`test_waiter_ignores_other_tasks_result`），避免队列中其他任务的结果错配；worker 退出时 `task_seq=None` 强制 resolve 防悬挂。
3. **订阅防 context 爆炸**：`/ws/agent` 默认只推 `worker.result`，按 sessionId 过滤 + consumed_seq 游标 + reconnect 补发，思路正确（`test_default_only_worker_result` 等 7 个测试）。
4. **watchdog 分支覆盖完整**：stream 超时 kill / idle 回收 / held 跳过 / worker 移除退出 / MCP 模式不误杀 running（实测 MCP 任务期间 status 为 queued，`w.process is None` 分支天然避开）——每个分支都有测试。
5. **幂等覆盖了核心场景**：handoff 超时返回 pending + taskId，重试不双跑（`test_handoff_task_id_idempotent_after_complete` / `_pending_on_timeout`）。
6. 注释充分且解释 why（如 MCP `-d` workdir、自取消竞态注释），可读性好。

---

## 高（合入前建议修复）

### H1. taskId 幂等注册表：超时+crash 组合永久卡 pending，且无限增长

**位置**：`packages/core/worker.py:118`（`_task_status`）、`:1136`（登记 pending）、`:819-822`（kill_worker resolve）、`:312-315`（worker 退出 resolve）

**问题 A（卡死）**：`kill_worker` 和 worker 退出路径只调用 `_resolve_result_waiter` 解决 handoff future，**不更新 `_task_status[task_id]`**。当「handoff 已超时」（fut 已取消、waiter 已 pop，返回 pending）之后任务再因 watchdog/crash 终止：

```
handoff(task_id="abc") → 登记 pending → 任务在跑
   └─ 600s 超时 → fut 取消、waiter pop → 返回 {"status":"pending","taskId":"abc"}
        └─ 任务随后被 watchdog kill / 进程退出
             └─ _resolve_result_waiter 找不到 waiter → 无操作
             └─ _task_status["abc"] 永远停在 pending
                  └─ 重试同一 taskId → 永远返回 pending，任务永不执行（幂等设计的重试承诺失效）
```

而幂等 taskId 的**唯一文档化用途就是超时后安全重试**（commit `26475ce` / MCP 工具 docstring），此失败组合恰好击穿该场景。

**问题 B（无生命周期）**：`_task_status` 是进程级全局 dict，插入后**永不删除**——无 TTL、无 worker 销毁清理、无大小上限。长期运行的 server 上每个唯一 taskId 永久驻留内存。

**修复建议**：
1. `kill_worker` / worker 退出路径中，把该 worker 名下所有 `status=="pending"` 的 taskId 标记为 `error`（或移除）；
2. 注册表加 TTL（如保留 24h）或按 worker 清理，防无限增长；
3. 补测试：「handoff 超时 → kill worker → 重试同 taskId 返回 error/可重跑」。

## 中

### M1. 并发 handoff 到同一 worker 时 waiter 被覆盖

**位置**：`packages/core/worker.py:1140`

```python
_result_waiters[w.worker_id] = (seq, fut)   # 单槽位
```

第二个并发 handoff 覆盖第一个。先发任务的 future 被孤儿化：其结果的 `taskSeq` 与槽内新 waiter 的期望 seq 不匹配（`_resolve_result_waiter` 返回保留），先发 handoff 只能等满 10min 超时返回 error——**任务本身正常执行并广播，仅阻塞等待丢失**。

**触发面**：多个 Meta-Agent / 并行重试同时对同一 session handoff。文档约定「一个 Session 同一时间只有一个 Worker」，但未禁止并发 handoff。

**修复建议**：改为 `dict[int, asyncio.Future]`（按 seq 索引）或 queue 化，逐个 resolve。

### M2. MCP one-shot 模式 idle 回收计时起点错误

**位置**：`packages/core/worker.py:1079`（`send_task` 刷新 `last_activity`）vs `:663`（`_consumer_mcp` 完成置 idle，**未刷新**）

stream 模式在 `_read_stdout` 每个事件都刷新 `last_activity`（`:201`），正确；MCP one-shot 只在**入队时**刷新。任务完成后 idle_for 仍从入队时刻起算，idle 窗口被任务耗时侵蚀：

- 任务耗时 T → 完成后实际 idle 宽限期 = `idle_sec - T`
- 长任务（只要输出 chunk 间隔 < read_timeout 就不会超时，可运行任意时长）完成后 **几乎立即被回收**，`idle_sec=300s` 语义失效

**修复建议**：`_consumer_mcp` 置 `w.status = "idle"` 时同步 `w.last_activity = time.monotonic()`。

## 低

### L1. reconnect 补发游标不推进，重复补发

**位置**：`packages/web/server.py:559-568`

重连补发消息**不更新** `consumed_seq`（只有 live broadcast 才推进，见 `:155`）。若会话 last_result 为 done 且游标为 0，**每次重连都重复补发同一 last_result**（仅靠 `"replayed": True` 让客户端去重）。且 replayed 消息的 `taskSeq` 恒为游标值 0，不是真实结果序号，语义失真。

**修复建议**：补发后推进游标；`last_result` 落盘时记录真实 taskSeq。

### L2. config.example.json 未同步 worker 配置

`packages/core/config.py` `DEFAULT_CONFIG` 新增 `worker.timeout_sec / idle_sec`（含注释），但 `config.example.json` 无对应节。用示例配置做模板的用户无法感知生命周期可调。

### L3. 新 API 错误信封不一致

`/api/handoff` `/api/assign` 缺参返回 `{"error": ...}`（HTTP 200），而 mcp `_api` 对成功响应原样透传、对 HTTPError 包装成 `{"ok": false, "error": {...}}`。缺参时 MCP 工具会拿到**无 `ok`/`status` 字段**的裸 dict，与 `worker_handoff` docstring「Returns the final result dict」的 `{"status": ...}` 形状不一致。

### L4. `debug:` 提交留在历史

`91cd4dc`（debug: watchdog idle_for 打印）内容已随 `98080f2` 清理，可 squash 进前一个 fix 提交保持历史干净（非阻塞）。

### L5. 测试缺口（3 处）

- **reconnect 补发**：`test_agent_subscription.py` 7 个测试均覆盖广播过滤，无 reconnect 路径测试（L1 因此漏网）
- **并发 handoff**：无同 worker 双 handoff 测试（M1 因此漏网）
- **超时+crash 幂等卡死**：幂等测试只有「完成」和「pending 超时」，无「超时后 kill → 重试」组合（H1 因此漏网）

---

## 建议优先级

1. **H1**：幂等注册表失败组合路径 + TTL/清理（工作量小，直接决定幂等功能在故障下的可靠性）
2. **M1**：waiter 改多槽位（并发安全）
3. **M2**：MCP 完成时刷新 last_activity（一行）
4. **L1-L3**：顺手修复（reconnect 游标、config.example.json 同步、错误信封）
5. **L5**：3 个补测随对应修复一起提交

## 备注

- 本分支是 `main` 上「质量审查整改立项」的延续：`main` 已合入 8 项机械修复（`9eb464f`），本分支实现 D0-D14 中与 worker 生命周期/MCP 编排相关的决策。
- 与 `docs/项目质量审查-2026-08-13.md`（全项目）及 `docs/memory-分支质量审查.md`（memory 专项）互补，后两者已完结。
- 安全鉴权（全项目 #1）、workdir 限制（#2）等**项目级遗留项**不在本分支范围，但 H1 修复时注意不要扩大 API 暴露面。
