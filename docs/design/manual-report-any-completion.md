# 手动汇报：任意已完成运行（含无 taskId 普通对话）— 后端契约与接入说明

> 基线 6b1d7ad9，工作树 `feature/manual-report-any-completion-20261016`。
> 本文是**后端/API 契约的单一事实源**（与代码 docstring 同步）：前端按钮接入由书签
> TA `ses_7d9b4df72a1f79cf` 负责，本文 §4 为其接入清单。本分支只改后端/API/类型
> 契约与测试，不修旧按钮、不新增前端入口。

## 1. 变更摘要

扩展现有手动汇报（`POST /api/sessions/{id}/manual-report`）：**任何已完成运行均可
汇报**，包括无 taskId 的普通对话与原生轮次。taskId 不再是必要条件，但汇报必须可靠
绑定具体完成运行：复用既有**持久**运行身份 `taskSeq` / `resultCursor`（终态行自带，
经会话身份视图 `terminalResults` 暴露），服务端正文权威，禁止猜最新、禁止跨运行错发。

改动文件（均在后端）：

- `packages/core/worker.py`
  - `manual_report_key()`：新增可选参数 `result_cursor`——仅当终态行无 `taskSeq`
    时以 `rc:<cursor>` 定型，保证同一运行经不同身份路径汇报只有一把 key。
  - 新增 `durable_terminal_by_cursor()`：按持久 resultCursor 解析终态行（精确原始值
    优先；旧行无该字段时回退 `taskSeq` 兼容 cursor——与身份视图暴露值一致；歧义 →
    None，绝不猜）。
  - `enqueue_manual_report()`：新增可选参数 `result_cursor`；三种运行身份都不提供 →
    `no_run_identity` 明确失败（不退化为"汇报最新终态"）；resultCursor 与
    taskId/taskSeq 矛盾 → `run_mismatch` 明确失败。
- `packages/web/server.py`：路由校验并透传 `resultCursor`（`invalid_result_cursor`），
  docstring 更新为新契约。
- `tests/test_manual_task_report.py`：+26 个定向测试（见 §6）；`_cleanup()` 增加回收
  跨事件循环的陈旧的 per-session queue lock（测试隔离修复，被新双击测试暴露）。

**订阅语义不变**：手动汇报不读取、不修改 `report_subscriptions`；manager 退订后显式
汇报依然送达。**item 形状不变**：与自动汇报同一队列目标、同一形状（`manual: true`）。

## 2. API 契约

```
POST /api/sessions/{session_id}/manual-report
Body: {
  "messageId":   <str，非空 ≤200，必填>   # 该运行末块（归属证明，不参与去重）
  "taskId":      <str|null>              # 正式 assign 任务 id；普通对话传 null
  "taskSeq":     <int|null>              # ≥0；该运行的持久队列序号（无 taskId 时推荐）
  "resultCursor":<int|null>              # ≥0；该运行的持久终态游标（新增）
}
成功: {"ok": true, "sessionId", "managerId", "queueItemId", "alreadyReported"}
失败: {"ok": false, "error": {"code", "message"}, "retryable": bool}
```

### 运行身份解析规则（服务端权威，按优先级）

| # | 提供的身份 | 解析 | 冲突时 |
|---|-----------|------|--------|
| 1 | `resultCursor`（**仅正数**，±其他） | `durable_terminal_by_cursor` 精确定位一行 | 与 `taskSeq`/`taskId` 描述不同运行 → `run_mismatch`（不猜向任何一边） |
| 2 | `taskSeq`（taskId 可选） | `durable_terminal_for_task`：taskId 为 null 时匹配普通对话运行 | 序号非该会话运行 → `no_task_result` |
| 3 | 仅 `taskId` | 该 taskId 的最新运行（既有语义；末块证明防错发） | — |
| 4 | 三者全无 / 仅 `resultCursor ≤ 0` | **`no_run_identity` 明确失败** | 绝不汇报"最新终态"；派生 0 不构成身份 |

**可证明持久身份才算绑定**：非空 `taskId`、已存储的 `taskSeq`、或**正**
`resultCursor`（生产只分配正游标，`max(existing)+1`；身份视图对无身份旧行显示的
`resultCursor: 0` 是派生兼容值，不构成绑定）。`resultCursor ≤ 0` 按未提供处理（不能
引用任何可证明运行，丢弃不会误绑）；解析出的终态行若 `taskId`/`taskSeq`/正游标三者
皆无 → `no_run_identity` 明确拒绝——**不允许派生的 0 把无身份旧行解析成可投递运行**
（其 key 会退化为 `manual:{session}:-:-`，跨时间会撞上幸存回执而吞掉新运行）。

无论经哪条路径，`messageId` 必须正是解析出的终态行的**末块**（由其 history
epoch/revision 推出），否则 `block_mismatch`；历史被改写（epoch 变）/冷缓存未加载/
无 assistant 行 → `block_unproven`。

### 错误码

| code | 含义 | retryable |
|------|------|-----------|
| `no_run_identity` | 未提供任何运行身份（含 `resultCursor ≤ 0`），或解析出的终态行无可证明持久身份（旧行仅派生 0） | false |
| `run_mismatch` | resultCursor 与 taskId/taskSeq 矛盾 | false |
| `invalid_result_cursor` | resultCursor 类型非法（HTTP 层：非 ≥0 整数；语义上 ≤0 由服务层按身份缺失处理） | —（400 级 body 拒绝） |
| `no_task_result` | 索引中无该运行终态（含未知正 resultCursor/序号） | false |
| `block_unproven` / `block_mismatch` | 末块归属无法证明 / 不匹配 | false |
| `no_report_target` | 无 manager 或 manager 不存在 | false |
| `persist_failed` | 落盘失败/请求断开未落盘（已回滚） | true |
| `session_not_found` / `shutting_down` / `invalid_*` | 既有 | — |

### 幂等

key 由**解析后的终态行**推导：`manual:{session}:{taskId|"-"}:{taskSeq 或 rc:resultCursor}`。
同一运行经 `(taskId=null, taskSeq)` 或 `resultCursor` 汇报 → 同一 key → 一次送达
（`alreadyReported: true`）；并发双击只送达一次；取消/落盘失败在持久化 ticket 内整体
回滚，不留失败候选。

## 3. 身份视图（前端绑定数据来源）

会话 summary/detail 的 `terminalResults`（≤16 行，仅身份无正文）**已包含 taskId 为
null 的普通对话运行**，字段：`taskId / taskSeq / resultCursor / terminalKey / status /
historyEpoch / historyRevision`。前端绑定普通对话运行的末块时，用与正式任务相同的
epoch/revision 算术（`covered_end = total - (revision - terminal.historyRevision)`，
末块 = 边界内最后一条 assistant 行）。

## 4. 前端接入清单（书签 TA ses_7d9b4df72a1f79cf）

按钮位置与交互由该书签 TA 决定；后端契约要求如下（均为既有文件的**扩展**，不是新入口）：

1. `packages/web/src/components/chat/manualTaskReport.ts`
   - `ManualReportTarget.taskId` 类型放宽为 `string | null`（第 12 行）。
   - `findManualReportTargets`：删除/放宽 `if (!taskId) continue` 门（第 101 行）——
     普通对话运行同样开段：以 user 行（无 formal assign）开段、以 `terminalResults` 中
     `taskId === null` 的终态行闭段；`resultCursor` 带入 target（接口已预留）。
   - 歧义保护保持不变：同一 covered_end 对应多个候选 → 不绑定（既有逻辑）。
2. `packages/web/src/services/api.ts`：`reportTaskManually` 已支持 null 参数；增加
   `resultCursor` 透传（body 增加该字段，缺省不发送）。
3. `packages/web/src/stores/sessionStore.ts`：`manualTaskReportKey` 已容忍 null；
   `ManualTaskReportEntry` 无需改（可选：记录 resultCursor 便于审计）。
4. `packages/web/src/demo/mockBackend.ts`：镜像新校验——`resultCursor` 类型检查、
   `no_run_identity`（含 ≤0 游标与无身份终态——派生 0 不是身份）、`run_mismatch`、
   按 cursor 解析（与 `durable_terminal_by_cursor` 同语义：仅正游标、精确优先、
   旧行仅 taskSeq 兼容、歧义拒绝）。
5. `packages/web/src/types/index.ts`：`ApiSessionManualReportResponse` 形状不变；如需
   请求体类型，增加 `resultCursor?: number | null`。
6. E2E（`packages/web/e2e/manual-task-report.mock.mjs` 同风格）：补普通对话可见性/
   路由/无身份拒绝/跨路径去重用例。

**验收对照（后端已证，前端需自证）**：普通对话按钮可见且路由 body 含
`taskId: null`；无身份请求前端不应发出（按钮不出现）；双击/重试只送达一次。

## 5. 迁移/兼容边界（诚实声明，不伪造）

- **有界窗口**：持久终态索引 `terminal_results` ≤64 行、身份视图 ≤16 行；更早的运行
  已逐出索引 → `no_task_result` 明确失败。没有 taskSeq/resultCursor 键控的归档索引
  （现有索引仅 taskId/clientMessageId 桶），运行查找是窗口内线性扫描——够用，故不新增
  索引；这是**已知边界**，不是缺陷隐瞒。
- **旧行**：无 `resultCursor` 字段的旧终态行仅在**存有 taskSeq**时以 `taskSeq` 兼容
  cursor 解析（身份视图暴露同值）；无身份旧行（taskId/taskSeq/正游标皆无）的派生 0
  **不是身份**：cursor ≤0 一律按未提供处理，无身份终态解析成功也一律 `no_run_identity`
  拒绝（前端已隐藏派生 0 入口）。旧行有 taskId 无 taskSeq 时 key 退化为
  `manual:{session}:{taskId}:-`（既有行为，taskId 即其身份）。
- **跨时间回执安全**：身份解析失败发生在去重之前——无身份运行永远不会拿"下一个无身份
  运行"去撞旧回执（`manual:{session}:-:-` 类劣化 key 不再可能被新运行命中并返回
  `alreadyReported` 而吞掉投递）。
- **executionSeq / generation 故意不用作绑定身份**：均为运行时字段（进程重启重置/
  worker 代际 fence），不跨重启持久；绑定它们会造成跨运行错发。持久身份只有
  `taskSeq` / `resultCursor`（正）/ 非空 `taskId`。
- **历史无运行指针**：history 行只有 `messageId`/`ts`，运行→末块唯一链接是终态行的
  epoch/revision 算术；历史被整体重写（`replace_history` 换 epoch）→ `block_unproven`。
- **无可靠身份的普通历史**：功能上线前的普通对话运行若终态行缺少 taskId/taskSeq/正
  resultCursor（极旧会话），无法可靠绑定 → `no_run_identity` 明确失败，不用"最新"
  近似顶替、不伪造兼容身份。
- `taskId=null + taskSeq` 在本变更前已能偶然解析（`durable_terminal_for_task` 本就
  taskId-agnostic）；本变更是把偶然能力升格为受支持契约，并堵上无身份时的最新猜测。

## 6. 测试覆盖（tests/test_manual_task_report.py，83 通过）

- 普通对话：delivers / by_result_cursor_only / multi_turn / older_while_newest_running /
  survives_late_new_terminal / rejects_block_of_another_run / double_click /
  retry_after_consumption / works_after_unsubscribe / plain_and_auto_coexist
- 身份拒绝：without_any_run_identity / result_cursor_unknown /
  result_cursor_conflicting_identity（run_mismatch）
- 派生 0 / 无身份终态（窄修）：identity_less_done_by_derived_cursor_zero_is_refused /
  never_derives_zero_identity（单元：派生 0 与 raw-0 不解析、taskSeq 兼容保持）/
  evicted_identity_less_run_cannot_swallow_a_new_one（跨时间 A 淘汰后 B 不被旧回执吞并）/
  derived_zero_and_raw_zero_are_refused / legal_run_identities_are_untouched（合法
  taskSeq / 正 cursor / taskId-only 契约不变）/ 真实 HTTP+真实落盘+冷重载回执持久
- 跨路径去重：multi_turn（seq↔cursor 同 key）/ per_run_dedup_survives_cold_reload /
  endpoint plain by cursor + replay
- 冷加载：cold_cache_history_hydrates（浅加载水化）/ cold_reload（真实落盘+冷重载）
- 持久化取消/回滚：save_failure_rolls_back / cancelled_failed_write_leaves_no_candidate
- 单元：durable_terminal_by_cursor（精确/旧行兼容/歧义/last_result 回退/非 int 与 ≤0 拒绝）、
  manual_report_key 身份路径、endpoint（plain ok / bad resultCursor / identity-less /
  conflict / unknown cursor）、summary 普通身份被路由接受
- 回归：既有 77 个正式任务/订阅/幂等/回滚/边界测试全绿；
  `tests/test_report_subscription.py`、`tests/test_replay_resync.py` 全绿
