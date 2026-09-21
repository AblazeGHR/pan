# FOLLOWUP_REPORT — 前端一致性收尾（XFAIL 转正 + queue/Steer 矩阵审查）

- 工作树：`D:\project\pan-worktrees\frontend-consistency-followup-glm-20260922`（detached HEAD 自 `3f1b42e`）
- 基线：`3f1b42e`（REPAIR_REPORT 交付点）；两份前置文档：`REPAIR_REPORT.md`、`audit/ta-evidence/protocol/`（含 `tests/test_reaudit_frontend_reconcile_order.py` 原件）
- 边界遵守：全程未访问 8768（E2E 用 8798/8799，harness `assertHarnessSafety` 拒绝 8768）；未操作 practical / Pan-main / 其他 TA 工作树（只读 junction 借用 `frontend-reaudit-history-ds-20260921` 的 node_modules，与 REPAIR_REPORT 相同做法，未改其内容）；未 merge / 未 push / 未创建子 TA；仅本树本地提交。

---

## 1. 提交清单

| commit | 内容 |
|---|---|
| `4b291e0` | **Part A**：`reconcileWorkerResult` 新增 authoritative convergence（terminalCoverage.historyRevision + 权威 offset 窗口收敛无 id live 重放）；探针 `ordered_turn_idless_result_equals_last` 增加权威 revision + terminalCoverage 事件序列；strict XFAIL `test_idless_turn_is_not_duplicated` 转正；pin 测试改为 4 行无重复 |
| `5f04e6c` | **Part B**：`loadAgentQueue` fresh-observation equal-revision 修复路径（Q7/Q8 两个真实缺陷转正）；Q9 同 clientMessageId 业务重试正控、Q10 超时 outcome-unknown 正控；api.test.ts 队列全端点 200+{error} pin |

## 2. Part A：唯一未闭合缺陷（XFAIL → PASSED）

### 2.1 红测构造（先红后绿）

探针 case `ordered_turn_idless_result_equals_last` 改为完整"canonical window 已包含本轮 + 无 provider id 的 live replay/result/recovery"事件序列：

- Session 以 `historyEpoch: 'E', historyRevision: 1` 播种完整轮 `[user q, analysis, tool, final]`（durable window 4 行，anchorOffset=4）；
- `applyLiveStream` 重放 `[analysis, tool, final]`（CBC 形状，无任何 id）；
- `worker.result` 携带 `terminalCoverage: { historyEpoch: 'E', historyRevision: 1 }`。

**红测证据（修复前）**：`node evidence/probe_frontend_reconcile.cjs` 输出 7 行 `[user, analysis, tool, final, analysis, tool, final]`、historyTotal=7 —— 重放被整体再次追加。

### 2.2 修复设计（sessionStore.ts `reconcileWorkerResult`）

收敛判据（全部满足才判定 replay 并丢弃 finalized runtime 行）：

1. 事件携带 `terminalCoverage.historyRevision`（无 coverage revision 的旧 CBC 事件路径完全不变——不损坏旧无 id 兼容）；
2. 已加载 durable window 的 `revision >= coverage.historyRevision`（权威侧确认该任务已落盘）；
3. `finalized` 非空且未追加合成 result 行（`!appendedResult`）；
4. **结构 offset 窗口**：`finalized` 与 window 中 `anchorOffset-K .. anchorOffset-1` 的行按序存在且精确匹配（role+content）。

约束满足情况：

- **不按正文/前缀/任意 ordinal 猜 identity**：offset 窗口是必要条件、精确 role+content 仅作守卫；identity 仍由 taskScopeKey+slot / 显式 id 承担，未新增任何正文派生键；
- **不吞合法新 delta**：result 与 canonical 不一致（`ordered_turn_idless_result_differs` 形状）或行超出 anchor 时判据失败，保持原追加行为；
- converged 时跳过 untracked-row 采纳（否则重放行被重新并入 runtime）并跳过冗余 `recoverSessionHistory`；terminal watermark 照常记录，后续同游标重放仍被幂等挡住。

**绿测证据（修复后）**：同探针输出 4 行 `[user, analysis, tool, final]`、historyTotal=4；其余全部探针 case 输出不变。

## 3. Part B：queue/Steer 矩阵只读审查与最小红测

审查对照 `frontend-reaudit-astra-20260921/audit/REPAIR_EXECUTION_PLAN.md` §5/矩阵 4 与 Q1-Q6 缺口清单，只读核对了前端 `queueStore.ts`/`api.ts`/`InputRow.tsx`/`useWebSocket.ts` 与服务端 `packages/core/session.py`（receipt ledger / idempotency index）/`worker.py`（enqueue_user_message、queue.snapshot 广播）。

| 矩阵项 | 结论 | 依据/测试 |
|---|---|---|
| HTTP 200+{error} | **代码正确**，全队列端点（enqueue/fetch/patch/delete/retry/reorder/steer）均进入业务错误路径 | `api.test.ts` 新增 pin（4 端点断言）；steer 原有 pin 保留 |
| timeout 后服务端已接受（outcome-unknown） | **前端行为正确**：不自动重发、无幻影行、失败保留输入（steer 草稿事务既有测试覆盖） | 新增 Q10 正控 |
| receipt 查询/重试 | **服务端协议已存在**（`queue_delivery_ledger`/`queue_idempotency_index`/`retry_pending_item`；同 clientMessageId 重试解析原 receipt，绝不二次入队）；**Steer 无稳定 request identity/receipt 协议为服务端协议缺口（非前端回归），如实记录，不扩 scope** | 新增 Q9 正控（同 ID 重试不产生第二行、不永久遮蔽） |
| partial snapshot hint + equal-revision full GET | **两个真实缺陷（已修）**：hint 先推进 revision 后，同 revision 完整 GET 被无条件拒绝——(Q7) 交付事件丢失时已交付项永久滞留本地队列；(Q8) reorder 被服务端拒绝后本地乐观顺序永久分叉 | Q7/Q8 红→绿；修复为 `loadAgentQueue` fresh-observation 判据（请求时与响应时本地 revision 一致才允许 equal-rev 修复），Q4/Q6 的幂等拒绝契约不受影响 |
| 自同 ID 业务重试 | **代码正确**：服务端按 clientMessageId 幂等；delivered → 不重复入 history；deleted → tombstone 遮蔽与"同 id 不再入队"的服务端语义一致（重试解析的是已删除 receipt，不会重建排队项） | Q9 覆盖 delivered 形状 |

修改范围纪律：仅 `queueStore.ts` 的 `loadAgentQueue`/`setSnapshot` 两处最小改动；未重构 queueStore 其他逻辑；Q1-Q6 与全部已通过路径保留且全绿。

## 4. 红绿测试证据（命令与退出码）

| 命令 | 退出码 | 结果 |
|---|---|---|
| **Part A 红**：`node evidence/probe_frontend_reconcile.cjs`（修复前） | 0 | case 输出 7 行重复（红态证据，探针本身只读不 assert） |
| **Part A 红转绿**：`E:/software/miniforge/python.exe -m pytest tests/test_reaudit_frontend_reconcile_order.py -q -rA` | 修复前 0（2 PASSED + 1 XFAIL）→ 修复后 **0（3 PASSED，XFAIL 移除）** | XFAIL 转正 |
| `node node_modules/vitest/vitest.mjs run`（Part A 后） | 0 | 72 files / **631 tests 全过** |
| **Part B 红**：`vitest run src/stores/reaudit.queue.test.ts src/services/api.test.ts`（修复前） | 1 | **2 failed（Q7/Q8）/ 12 passed** |
| **Part B 绿**：同上（修复后） | 0 | 4 files / **31 tests 全过** |
| `node node_modules/vitest/vitest.mjs run`（Part B 后全量） | 0 | 72 files / **636 tests 全过**（首跑出现 1 例 flaky，复跑两次均 636/636 exit 0） |
| `node node_modules/typescript/bin/tsc -b` | 0 | 两次提交 pre-commit 均通过 |
| `AUDIT_DEPS=<sibling node_modules> node audit/probe-after-repair.cjs` | 0 | **9/9 PASS** |
| `node node_modules/vite/bin/vite.js build` | 0 | 产物 `packages/web/dist`（gitignored） |
| **E2E #1（8798）**：`PAN_E2E_BASE_URL=http://127.0.0.1:8798 node ../../audit/run-e2e-consistency.mjs` | 0 | **8/8 断言 PASS**：store 终态顺序、server canonical（8 行 + epoch/revision）、DOM 同序、reload 后与 server canonical 逐行一致；证据 `audit/e2e-runtime-followup/e2e-consistency-evidence.json` |
| **E2E #2（8799）**：`PAN_AUDIT_PORT=8799 node e2e/audit-cbc-strict.mjs` | 0 | `status: passed`、`failures: []`（CBC 原始形状 3 轮 strict + P 阶段）；证据 `evidence/cbc-strict/cbc-strict.json` |
| `E:/software/miniforge/python.exe -m pytest tests/ --disable-warnings -q`（全库） | 1 | 1149 passed / 1 failed / 4 skipped；唯一失败 `test_codex_quota_api.py::…permission_boundaries…` 为**基线既有**（断言字符串在产品源中不存在，与本 diff 无关；两次提交均未改任何 Python 产品文件） |

E2E 服务进程（8798）为本人启动、`Get-CimInstance` 核对命令行后 `Stop-Process` 停止；8799 由 harness 自行起停。

## 5. 剩余未验证（不标完成）

1. **5000 消息长历史性能**（矩阵 6）：未新增长历史 E2E、未采 frame/long-task/内存样本；仅保留 reducer 级 `sessionStore.stage3.performance.test.ts`（H/L 矩阵）并通过。
2. **WS 断线重连 / gap / cursor expired**（矩阵 3）：单连接 snapshot 顺序由两套 E2E 覆盖；断线恢复、重复/缺口、旧 socket、延迟 HTTP 未做 E2E。注意 Q7 修复恰好堵住了"gap 丢失交付事件"在 queue 侧的后果，但 gap 本身的 WS 层行为仍未端到端验证。
3. **真实 provider**：全部使用真实事件形状（CBC 无 id、Codex item_id 仅挂 tool）+ harness 注入；无真实模型验收。
4. **Steer receipt 协议**（服务端）：steer 请求无稳定 request identity，超时后 outcome-unknown 只能保守处理（不自动重发、保留输入）；完整 receipt 查询/重试协议需服务端协议变更，超出本次"只读审查+最小红测"边界。

## 6. 是否达到完整交付

- **Part A：达到**。唯一明确未闭合的顺序/重复类缺陷（strict XFAIL）已红→绿转正，收敛设计满足全部约束（不猜 identity、不破坏旧无 id 兼容、不吞合法 delta），原协议/历史/观察探针/两套浏览器 strict E2E 全部重跑通过。
- **Part B：达到（审查定义的范围）**。矩阵五项全部有结论：2 个真实缺陷修复（红→绿），3 项确认代码正确并落为正控；Steer receipt 属服务端协议缺口，已如实记录未扩 scope。Q1-Q10 与全部既有通过路径全绿。
- **整体：本两部分的交付完整**；§5 所列四项仍不在本次范围，维持 REPAIR_REPORT 的"未验证"状态。
