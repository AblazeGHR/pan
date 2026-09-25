# 派发×认领融合设计（DESIGN_DISPATCH_CLAIM_FUSION）

- 版本：v0.1（2026-09-25）
- 状态：设计提案，供评审。目标读者：实施 P1 内核的人。
- 前置阅读：PLAN_JOB_UNIFICATION.md §4

---

## 1. 两套机制现状的本质

### 1.1 scheduler（PR #1）的"落盘先于派发 + 幂等键"

```
tick ─→ 落盘(推进 nextFire/lastStatus=dispatched) ─→ 后台task ─→ worker.assign(task_id=dispatch_key)
```

崩溃窗口分析（engine.py 铁律注释 :13-16 自认"崩溃点只有两个"）：

- **崩溃点 A：落盘后、assign 前**。重启 `_recover()`（engine.py:422-448）：last_status
  =dispatched 但查不到终态 → 记 unknown，**宁漏勿重，不补派**。
- **崩溃点 B：assign 已发出、worker 未持久化**。dispatch_key 未进幂等索引 → assign
  会再执行一次？不——`_recover` 对该情形一律不补派，所以 assign 已发出的那次正常跑，
  只是 Pan 侧状态停在 unknown。
- 幂等键 `dispatch_key = task_id:fire_at秒`（engine.py:203）防的是**外部重放**
  （run_now 重试、重启后重扫同一条目）：worker 的
  `_durable_task_id_seen`（worker.py:6216）两级检查（内存注册表 + 持久化队列幂等索引
  `queue_idempotency_index.taskId`）保证同一 task_id 的 assign 只产生一个队列项
  （worker.py:6254-6259）。

**语义：至多一次派发 + 外部重放安全。代价：崩溃点 A 附近可能"漏"一次触发。**

### 1.2 message job 的"claim 状态机 + stale requeue"

```
tick ─→ claim(status→running + runStartedAt) ─→ await send_session ─→ 落终态
```

崩溃窗口分析（background_jobs.py:581-708）：

- **崩溃点 A：claim 后、send 前**。status=running 但 runStartedAt 超过
  `MESSAGE_JOB_REQUEUE_AFTER_SEC`（5s，:329）→ 下一轮 requeue（置回 scheduled/pending
  + nextRunAt=now-ε，:593-604）→ **重投**。
- **崩溃点 B：send 已发出、终态未落**。同上 requeue → send 会**再发一次**。
  message 类动作幂等性弱（send_session 无幂等键），接受 at-least-once。

**语义：至少一次执行。代价：崩溃点 B 附近可能"重"一次发送。**

### 1.3 根本差异一句话

| | scheduler | message job |
|---|---|---|
| 崩溃恢复哲学 | 宁漏勿重（unknown 不补） | 宁重勿漏（stale 即重投） |
| 幂等支撑 | dispatch_key → worker 持久幂等索引 | 无（动作天然近似幂等：发消息） |
| 适用动作 | assign（重跑=双倍成本，靠幂等键硬保护） | send（重跑=多一条消息，软容忍） |

---

## 2. 融合方案：claim 状态机为骨架，幂等键为可选内衬

**统一闭环（所有 kind 走同一骨架）：**

```
扫描到期 → [跨进程 _job_lock + 进程内 RMW 锁] claim: status→running, runStartedAt=now
        → 执行 action（可 await）
        → 落终态（completed/failed）或推进 entry（scheduled + nextFireAt）
崩溃恢复（每轮循环开头）：
  running 且 runStartedAt 超 REQUEUE_AFTER_SEC
    → 按 action.idempotent 分流（见下）
```

**关键设计：每个 action 声明自己的恢复语义，循环骨架不变。**

```
action: {
  api: "assign",
  idempotent: true,          # ← 分流开关
  retrySafe: "skip"          # idempotent=true 时：崩溃后重投发现已见过 → 跳过并记 recovered
                             # idempotent=false 时（如 send）：直接重投，接受 at-least-once
}
```

- **idempotent=true（assign 类）**：requeue 重投时带原 dispatch_key 再走一遍 assign；
  worker 幂等索引若已见过 → 返回已有队列项（worker.py:6254-6257 现成行为），job 侧记
  `recovered: deduped`。**效果：把 scheduler 的"宁漏勿重 unknown"升级成"不漏不重"**——
  这是融合的最大收益，scheduler 原设计里漏掉的那次触发，现在有幂等索引兜底可以安全补。
- **idempotent=false（send 类）**：stale requeue 直接重投，行为与现状 message job 完全
  一致，向后兼容。
- **idempotent=true 且重投也未见（崩溃点在 assign 尚未持久化时）**：正常执行。
  覆盖原 scheduler 崩溃点 B。

## 3. dispatch_key 生成规则（列表化 schedule 后必须改）

PR 现状 `task_id:fire_at秒`（engine.py:203）在 schedule 列表下会碰撞（同 job 两条 entry
同秒触发互相吞）。统一规则：

```
dispatch_key = f"{jobId}:{entryId}:{int(fire_at.timestamp())}"
```

- entryId 使同 job 多 entry 并行触发互不干扰（PLAN §1 schedule entry 已有 id 字段）。
- fire_at 取**预定触发点**而非实际执行时刻（保持与 PR 一致；run_now 用
  `jobId:manual:now`，PR 现语义 :481 保留）。
- 幂等索引是**接收端 session 的属性**（worker.py:6222 挂在 s 上），所以 key 里无需含
  target；target 改变（归属切换）后重投会落新 target 的新索引——**注意：这正是归属
  切换后"未投递便条投新归属"的实现基础，但也意味着切换瞬间的 in-flight 重投对新
  target 是首发**。可接受（切换本来就是显式管理动作）。

## 4. 与终态通知（mailbox）的衔接

- 终态落盘时同步置 `notificationState=pending` + terminalEventId（沿用 :969 惧例）。
- 投递循环（recover_notifications :974-1017 的形态）改为扫 mailbox 便条而非 job 状态，
  幂等键仍是 terminalEventId + enqueue_notice（worker 侧持久索引）。
- **因此崩溃矩阵完整闭合**：触发执行（claim+dispatch_key）、终态通知
  （terminalEventId）两级都 at-least-once + 幂等，不存在裸窗口。

## 5. 需要实施的验证（P1 验收测试清单）

1. claim 后 kill -9 模拟：idempotent=true job 重投后不双跑（mock worker 幂等索引断言）。
2. idempotent=false job 崩溃后重投恰好一次（宽松断言 ≥1 次）。
3. 同 job 双 entry 同秒触发：两个 dispatch_key 各自派发（PR 现状会撞键，此为新行为）。
4. 归属切换：未投递 mailbox 便条投新 target；in-flight 派发不被切换中断。
5. 多实例并发认领：两进程同轮扫到同一到期 entry，只有一个 claim 成功（_job_lock 跨
   进程语义已有测试先例 tests/test_background_jobs.py:221 可参照）。

## 6. 开放问题

1. ~~REQUEUE_AFTER_SEC 按 kind 配置~~ → **已定（第七轮）**：全局默认 5s + action 级可
   覆盖字段 `requeueAfterSec`（不写即继承默认；新 action 零配置）。使用守则：覆盖值
   必须显著大于该 action 正常返回时长。assign 入队即返回、send 毫秒级，默认 5s 均安全。
   周期 job 的每一次触发的认领-执行-落终态同样受此保护（与 grace/misfire 层正交：
   判死超时救「卡住的执行」，grace 策略裁「过期的触发点」）。
2. ~~broadcast 部分失败语义~~ → **已定（第八轮，用户方案与宽松版合体；第九轮收敛存储）**：
   - 状态机：**宽松版**——部分失败 = completed，partial/明细不进状态机（failed 留给
     全部失败）；
   - 存储：**直接进 runs.jsonl**——该次 run 落一行 status="partial" + error 摘要
     （"1/3 failed: ..."）+ results 逐目标明细内联；零新增字段/载体（第九轮定）；
     不写 logs/（部分失败是结论级信息，logs 是过程级载体、仅进程类 job 存在）；
   - 通知：**即时 toast**——执行完当下经统一事件通道广播 `job.partial_failed`
     事件，前端监听弹 toast；不弹积压 toast。信息链 = toast（即时感知）→
     runs.jsonl（逐次可查）→ logs（仅进程类的过程细节，与部分失败无关）。
   - 依据：「算不算 failed」与「要不要让人知道」正交；broadcast 是通知类语义，
     部分失败是"值得知道但不需处理"级别，toast 即时性比列表标红合适。

---

## 7. P1 实施纪要（2026-09-25）

§2 方案已落地（background_jobs.run_due_scheduled_tasks）。两点实施细节：

1. **undeliverable 的判别**：不做派发前 `_sessions.get` 预检（预检会把行为
   测试的假 session 全判 undeliverable，且引入 TOCTOU）；改为识别 assign 的
   稳定字面量 `"Session {id} not found"`（worker.py assign 的缺 session
   返回）→ 走 §2 的 undeliveredFires 积压路径。
2. **同轮多 entry 的计数**：执行循环每个 entry 前重载最新 job 容器，使
   runCount/last* 读到前一个 entry 的落盘结果（否则双 entry 同刻只计 1 次）。

§5 验收清单对应测试：test_scheduler_engine.py（claim 先于派发 / 顺序派发 /
stale requeue 幂等键不变 / 双 entry 不撞键 / undeliverable 积压重投）+
test_scheduler_store.py（迁移幂等 / runs 合并）。
