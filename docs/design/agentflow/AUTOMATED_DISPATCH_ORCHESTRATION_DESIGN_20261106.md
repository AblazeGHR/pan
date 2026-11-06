# Agentflow 与 Pan MCP 自动化派发设计

状态：方案初稿，未授权实现。

日期：2026-11-06

本文只讨论 MA 的任务派发和收尾编排，不修改 Action 统一方案，也不改变 Job、Action、Group、Trigger 的产品语义。

## 1. 目标

当前一次普通 TA 任务需要分别完成任务建账、模型校验、worktree 准备、Session 创建、effort 设置、报告订阅、`agent_assign`、agentflow 同步、等待路由和 handoff 检查。固定步骤较多，且每一步都可能留下半完成状态。

本方案将这些固定步骤收敛为幂等的派发、续作和终态对账流程，同时保留 MA 对范围、证据、合入和人工验收的判断权。

目标是：

- 新任务从一个任务 manifest 生成完整的派发计划；
- 模型、adapter、effort、权限、Git 策略和 worktree 在派发前形成快照；
- Pan Session、报告订阅和 Worker 派发形成一个可对账的 dispatch receipt；
- quota、Worker 回收、服务重启和网络重试不会导致重复 Session、重复任务或重复业务效果；
- 终态报告自动写回 agentflow，但不会自动接受候选或合入分支。

## 2. 不变的边界

agentflow 仍是任务、依赖、策略、路由和验收状态的唯一调度真源。Pan MCP 仍是 Session、Worker、报告订阅和派发的执行面。两者不能因为自动化而互相伪造事实。

以下行为必须保持人工或显式授权：

- 接受 TA 候选；
- 合入 main 或 practical；
- 部署、服务重启和真实 data/profile 操作；
- 把 focused test、build 或本地结果认定为 CI、生产浏览器或人工验收；
- 删除 dirty/untracked 文件；
- 对 unknown 业务效果重放；
- 因容量错误自动切换已有 TA 的模型；
- 在用户停止后自动唤醒任务。

自动化只减少固定编排步骤，不扩大任务授权。

## 3. 统一任务 Manifest

新任务以 agentflow 任务记录为基础，并允许附加一个派发配置。示例：

```json
{
  "id": "SESSION-MULTISELECT-DRAG-20261106",
  "baseline": "aed15965fd169d6cfface9f7e4a042633561b120",
  "worktree": "D:/project/pan-worktrees/session-multiselect-drag-20261106",
  "branch": "fix/session-multiselect-drag-20261106",
  "mergeTarget": "practical",
  "modelProfile": "ds",
  "difficulty": "general",
  "session": {
    "name": "session-multiselect-drag-20261106",
    "adapter": "cbc",
    "model": "deepseek-v4.1-flash",
    "effort": "max",
    "permissionMode": "bypassPermissions",
    "thinking": true
  },
  "assignment": "实现并验证……",
  "reportSubscription": true
}
```

Manifest 必须明确：

- taskId、scope、acceptance 和依赖；
- baseline、branch、worktree 和 Git 策略；
- model profile、difficulty 以及实际 adapter/model/effort 设置；
- 是否允许最终合入哪个目标分支；
- assignment 文本和验收层次；
- 是否需要报告订阅。

没有 baseline、Git 策略或模型快照的任务不能自动派发。

## 4. 第一阶段：agentflow 侧 `dispatch-plan`

第一阶段不让 agentflow 直接操作 Pan。新增只读/账本命令生成派发计划：

```text
python -B agentflow/flow.py dispatch-plan <task-id> --candidate <manifest.json>
```

它执行：

1. 检查任务依赖、有效 policy 和同 scope 冲突；
2. 执行 `model-check`，核对 profile 与实际设置；
3. 检查 baseline、受保护分支、worktree 路径和路径所有权；
4. 生成稳定的 `dispatchId`、taskId、模型快照和 assignment digest；
5. 输出供 Pan 使用的 dispatch packet；
6. 将计划摘要记录到 agentflow，不把 queued 当成 running 或 done。

该阶段可以自动做只读的 Git/worktree 检查。创建 worktree 是否自动执行需要显式的 `worktreePolicy`，默认只生成计划，不执行 Git 写操作。

## 5. 第二阶段：Pan 组合派发 MCP

在 Pan MCP 增加组合接口 `agent_dispatch`，把固定的 Session/Worker 步骤集中处理：

```json
{
  "taskId": "SESSION-MULTISELECT-DRAG-20261106",
  "sessionSpec": {
    "name": "session-multiselect-drag-20261106",
    "adapter": "cbc",
    "model": "deepseek-v4.1-flash",
    "effort": "max",
    "permissionMode": "bypassPermissions",
    "workdir": "D:/project/pan-worktrees/session-multiselect-drag-20261106"
  },
  "assignment": "实现并验证……",
  "subscribeReports": true,
  "clientMessageId": "dispatch-SESSION-MULTISELECT-DRAG-20261106-1"
}
```

服务端顺序：

```text
校验 taskId/clientMessageId
→ 复用或创建 Session
→ 校验 adapter/model 不变
→ 设置允许变化的 effort
→ 建立 managed 关系
→ 订阅完成报告
→ agent_assign
→ 返回 dispatch receipt
```

Session 创建接口应直接接受 effort、thinking 和完整模型设置，避免创建后再单独 `session_update`。已有 Session 的 adapter/model 不得被新默认覆盖，effort 才允许按策略调整。

### 5.1 Dispatch receipt

返回值必须包含每个阶段，而不是只有 `queued`：

```json
{
  "dispatchId": "…",
  "taskId": "…",
  "sessionId": "ses_…",
  "status": "queued",
  "stages": {
    "session": "created",
    "settings": "verified",
    "reportSubscription": "subscribed",
    "assignment": "queued"
  },
  "modelSnapshot": {
    "adapter": "cbc",
    "model": "deepseek-v4.1-flash",
    "effort": "max"
  },
  "worktree": "D:/project/pan-worktrees/session-multiselect-drag-20261106"
}
```

`queued` 只表示 Pan 已接受派发。Worker 是否运行、任务是否完成仍由后续实时状态和报告确认。

### 5.2 半完成状态

组合派发不能把阶段失败压扁成一个布尔值。例如 Session 已创建但报告订阅失败时返回：

```json
{
  "status": "partial",
  "recoveryRequired": true,
  "stages": {
    "session": "created",
    "reportSubscription": "failed",
    "assignment": "not_started"
  }
}
```

使用同一 taskId/clientMessageId 重试时，只补未完成阶段。不能因为订阅结果 unknown 就创建第二个 Session，也不能重复执行已经确认入队的 assignment。

## 6. 续作 MCP：`agent_resume`

用户说“继续”、Worker 被 watchdog 回收、quota 恢复或任务需要补充约束时，使用原 taskId 和 Session：

```json
{
  "taskId": "…",
  "sessionId": "ses_…",
  "mode": "continue",
  "text": "继续原任务……",
  "clientMessageId": "resume-…"
}
```

服务端自动检查：

- 原 Session 是否仍存在；
- Worker 是回收、idle 还是 running；
- 应使用新 `agent_assign` 还是当前任务的 `agent_send_force`；
- 原消息是否已入队、消费或处于 unknown；
- 是否必须保留原身份并禁止重发。

同一 TA 的 adapter/model 固定。只有用户明确授权替身交接、账号轮换等例外，才允许更换模型身份。

## 7. 并行派发 MCP：`agent_batch_dispatch`

对于 P3/P4 这类拥有独立 worktree 的并行任务，可一次提交多个 dispatch item：

```json
{
  "batchId": "ACTION-UNIFICATION-20261104-P3-P4",
  "items": [
    {"taskId": "P3", "sessionSpec": {}, "assignment": "…"},
    {"taskId": "P4", "sessionSpec": {}, "assignment": "…"}
  ]
}
```

每个子任务拥有独立幂等键和 receipt。一个子任务失败不能自动重派整个 batch；批量结果必须明确列出成功、部分完成和失败项。

## 8. 终态对账：`agent_terminal_reconcile`

完成报告到达后，组合接口只做账本和 receipt 对账：

```json
{
  "taskId": "…",
  "reportId": "…",
  "candidateSha": "…",
  "worktreeStatus": "clean",
  "evidence": "D:/…/REPORT.md"
}
```

它可以自动：

- 写入 `record --kind report`；
- 将任务转入 `review`；
- 清除旧等待路由；
- 保存候选 SHA、worktree 状态、测试层次和未验证范围；
- 运行 `next` 和 `handoff-check`。

它不能自动接受候选、合并分支、部署或将局部验证升级为完整门禁。

## 9. 幂等和未知结果

所有组合接口使用双层幂等：

- `taskId`：任务身份和业务操作的稳定键；
- `clientMessageId`：一次编排消息的稳定键。

未知结果的处理规则：

- 查询原 taskId/clientMessageId；
- 不创建替代 Session；
- 不重复发送同一 assignment；
- 不把 operation unknown 变成失败或成功；
- 只有服务端确认原尝试未接受且明确 `retryable=true`，才允许用原身份重试。

## 10. Worktree 自动化边界

可以自动化只读检查：

- Git 根、branch、HEAD、status 和 worktree registration；
- baseline 是否存在且祖先关系正确；
- main/practical 是否被保护；
- 目标路径是否与其他任务冲突。

创建 worktree、提交、push、合并和部署分开建模。默认只生成 plan；只有 manifest 明确 `worktreePolicy=prepare` 且目标在隔离目录时，才允许自动创建。不得自动覆盖 dirty 工作树。

## 11. 事件驱动等待

派发组合完成后，MA 只等待 `report_subscribe → queue_pending` 的终态报告。不要用后台 Job 轮询、sleep、tail、PID 盯梢维持活跃。实时状态检查只用于一次性对账，不替代报告通知。

quota 错误、Worker 回收和服务重启都使用原 Session/原 taskId 续作；不因容量报告自动创建新模型或替换现有 TA。

## 12. 分阶段实施

### A0：只读和账本层

- 增加 `dispatch-plan`；
- 标准化 model snapshot、worktree snapshot、assignment digest；
- 自动生成 route 和 handoff receipt；
- 不新增 Pan 写接口。

### A1：Pan 组合派发

- `agent_dispatch`；
- `session_create` 直接接收 effort/thinking；
- report subscribe 与 assignment 阶段化回执；
- taskId/clientMessageId 幂等和半完成恢复。

### A2：续作和终态对账

- `agent_resume`；
- `agent_terminal_reconcile`；
- 自动记录报告、转 review、刷新路由。

### A3：并行和受控 worktree

- `agent_batch_dispatch`；
- 独立 worktree 只读检查和可选 prepare；
- 不自动合入 protected branches。

## 13. 验收门禁

每阶段都要证明：

- 同一 taskId 重试不会双创建、双订阅或双派发；
- Session 创建、订阅失败和 assignment 失败的半完成状态可恢复；
- 同一 TA 不会因全局默认变化被换模型；
- quota、Worker 回收、Pan 重启后可按原身份续作；
- unknown 结果不会盲重试；
- stop/pause 后不会被自动唤醒；
- main/practical 保护和 dirty worktree 保护仍生效；
- 终态对账不会自动宣称验收、合入或部署完成。

## 14. 非目标

本方案不改变：

- Action/Group/Job 的产品生命周期；
- Job/Run/Attempt/effect 的业务账本；
- 用户授权矩阵；
- P5/P6/P7 任务的实现范围；
- 旧入口迁移或 legacy Group writer handoff；
- 真实服务、真实 profile 和生产部署行为。

