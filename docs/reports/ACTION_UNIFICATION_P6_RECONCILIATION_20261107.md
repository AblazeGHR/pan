# P6 legacy Scheduler/Trigger/JSON reconciliation and lifecycle slice

Baseline: `f2244b22ad66c89787676ee0f69f193d830ef7d3`.
Branch: `implement/action-unification-p6-20261106`.
Worktree: `D:/project/pan-worktrees/action-unification-p6-20261106`.

本片没有新增第二套 scheduler/ledger/engine。既有 JsonTaskHandoff、FinalService、
OwnerFence、FinalService Run/Attempt/effect、operation receipt、Creator/Trigger
和 retained facade 已完成本片要求；本片补充了具名回归、对照证据和报告。

## 具名兼容矩阵

| 旧能力 | 现行 successor / 边界 | 绿证据 |
|---|---|---|
| 纯旧 dispatch journal | `json_dispatch_receipts.review` 只接受原 dispatchKey、old run id、canonical frozen Run、Attempt/effect 和 provider receipt 全部一致的记录；缺任一事实 preserve/blocked | `reconcile-receipts-final.xml`，含真实 shell/process receipt 与所有 unknown/locator/count/notice/lease 分支 |
| 缺少 canonical Run/Attempt/effect | 不创建 Run、不伪造 Attempt/effect、不重放 provider；原 bytes 和 journal 保留 | JSON receipt failure matrix；`P6_JSON_RECEIPT_02` |
| owner/lease/generation | OwnerFence epoch/nonce、legacy lease、source digest、generation 和 operation intent 均 CAS；竞争或失效只返回 conflict/blocked | `P6_JSON_03/10/11`、`P6_SCHED_05` |
| task-wide pause/resume | 同一 Creator state/cursor 和 binding revision；暂停不取消已接纳 Run，恢复不重放旧 occurrence | `P6_JSON_04/06/08/09` |
| run-now | 原 task ID 映射到同一 Job 的手动 Run，固定 dispatchKey/requestKey；replay 查询原 operation，不新建 Run | `P6_JSON_04/06/07`、`P6_SCHED_01/04` |
| cancel | HTTP/MCP 需要原 Job/Run/revision/stateRevision/requestKey；foreign run、stale CAS、unknown outcome 都不触碰 provider | `P6_SCHED_01/02/04/05` |
| delete | 只有无 unsettled Run/publication 时才能 retire Job/Creator/Trigger；最后一项删除保持 tombstone，不创建空 Creator | `P6_JSON_06`、multi-entry final-delete evidence |
| history | 保留旧 `runId/taskId/fireAt/actualAt/dispatchKey/status/sessionId/error/result` 形状，并追加 canonical successor/receipt projection | `P6_SCHED_01`、`legacy_runs` receipt assertions |
| notification | 使用同一 FinalService outbox；Session/WS 一次投递，lost/unknown receipt 保持 uncertain，冷恢复不重发 | `P6_SCHED_03/06`、CONFIG-13 |
| Trigger | Event + Trigger identity 固定，`(eventId, triggerId)` 去重，冷查询复用同一 Run/operation | `P6_SCHED_07` |
| HTTP/MCP flat aliases | `/api/scheduler/tasks` lifecycle、`scheduler_*` MCP、legacy status/history/cancel 通过同一 canonical graph；旧错误和返回字段保留 | `P6_JSON_06/07`、`P6_SCHED_01/04` |

## Red-to-green evidence

本片保留上一候选和更早的红 JUnit/fixture，未删除或改写其失败原因。新组合绿证据：

- `reconcile-final-combination.xml`: **33 passed**, 0 failure/error/skip；JSON scheduler lifecycle、retained Scheduler/Trigger、HTTP/MCP alias。
- `reconcile-receipts-final.xml`: **52 passed**, 0 failure/error/skip；纯旧 dispatch receipt、process/shell identity、unknown/no-replay、lease/notice/checkpoint preserve。
- `bulk-final-combination.xml`: 上一独立 multi-entry/Chromium slice **102 passed**，与本片不重复计数。
- Chromium/build/projection 证据继续沿用上一候选；本片没有修改前端。

`P6_JSON_RECEIPT_01` 是唯一成功 reconciliation：旧 journal 只映射到已有 canonical
Run/Attempt/effect，旧 `old-journal-id` 作为 alias 返回，canonical run identity 和
runtime aggregate 不变，冷恢复 operation 仍可查询。

`P6_JSON_RECEIPT_02`、`P6_JSON_SCHEDULER_02` 和 `P6_SCHED_02/05/06` 覆盖：缺 journal、
未知 status、locator mismatch、counter 不足、pending/notification outbox、未知 lease、
source digest 冲突、foreign Run、权限撤销、失效 owner 和 lost notification receipt。
所有分支都保留原字节、禁止 provider 调用或重放，并返回原语义的 blocked/unknown/conflict。

## 验证边界

本片执行了 isolated Windows Python 3.14 测试、动态隔离 HTTP、真实 standalone stdio
和固定 core IPC。没有使用真实 data/profile、8767/8768、真实外部 provider/QQ/model CLI，
也没有运行远端 CI、Linux/macOS、部署或人工验收。这些不是本片阻断，但仍是未验证层。

`bulk-stdio-rerun-20261107.xml` 是工作树中此前遗留的未跟踪运行产物，来源未确认，未纳入本候选计数；其余本片证据均有明确命令和 JUnit 入口。

无 push、无 main/practical 变更、无部署。旧 unknown writer/lease、无 durable receipt、
复杂或不匹配记录继续 preserve/unknown/blocked，不能因有历史投影而宣布迁移完成。
