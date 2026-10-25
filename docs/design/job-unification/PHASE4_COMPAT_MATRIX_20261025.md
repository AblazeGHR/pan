# Phase 4a Legacy MCP Compatibility Matrix

本矩阵只覆盖设计文档 §2 的 legacy MCP creation entries。兼容分流只在受信任的 stdio MCP IPC 请求上启用；普通 HTTP/GUI 请求继续走原有入口，保持旧入口可见且不产生双写。所有已迁移记录仍落在同一 `job_*.json` registry，`jobId` 是创建、查询、取消和重试路径共同使用的身份。

| legacy tool | old contract / validation | new Action/Job expression | reused execution path | GUI/HTTP/MCP entry | evidence / boundary |
|---|---|---|---|---|---|
| `agent_background_start` | `packages/mcp/server.py:2180`；必填 `name`、`argv`、`cwd`，`target_session_id` 默认 caller，`hide_window=True`；返回 durable background Job；非法 name/target/argv/cwd 和通知仍由旧错误语义决定 | `kind=background-process` Action Job，`actionId=system_background_process`，single immediate run，arguments `{sessionId,argv,cwd,hideWindow}`；记录保留 legacy kind/fields 和 `legacyCompat` | `packages/jobs/action_runtime.py:Runtime.launch/execute` → `packages/actions/executor.py:execute_dynamic` → `packages/jobs/system_actions.py:execute` → 原 `background_jobs.start` 子进程 Runner；父 Job 不再由旧 start 创建 | MCP `agent_background_start` → 原 `/api/background-jobs` 路径的 trusted-MCP 分支；旧 Jobs GUI/HTTP 路径不切换 | `tests/test_legacy_mcp_compat.py::test_trusted_legacy_http_entry_returns_action_job_in_old_shape`、`test_background_mcp_shape_uses_system_action_and_same_job_id`；目标 Session/权限仍由原门禁检查；MCP transport key 可使重试复用同一记录 |
| `agent_message_job_create`（单 Session） | `packages/mcp/server.py:2225`；`text`、`schedule`、单 target，prefix/source/description 语义保留；once/interval/weekly 校验沿用 `background_jobs._normalize_message_schedule` | `kind=session-message`，`actionId=system_session_message`，schedule/weekly 转为统一 Job schedule，arguments `{sessionId,text}`；legacy schedule/target 字段保留 | `Runtime.tick/launch` → `execute_dynamic` → `system_actions.execute` → 原 `worker.send_session` queue/delivery path；按 target receipt 恢复，不重发 unknown | MCP `agent_message_job_create` → `/api/session-message-jobs` trusted-MCP 分支；旧 `/api/session-message-jobs` HTTP/GUI 不切换 | `test_message_and_broadcast_keep_legacy_targets_and_schedule`；once delay 在接纳时冻结为绝对 occurrence，原 schedule 保存在兼容字段；无法表示的未知字段不自动迁移 |
| `agent_message_job_create`（broadcast） | 同一 tool 的 `target_session_ids` 分支；稳定去重顺序、逐目标失败隔离、返回同一 broadcast Job | `kind=session-broadcast`，`actionId=system_session_broadcast`，arguments `{sessionIds,text}`，每个 target 使用 Action checkpoint/receipt | 同上；`system_actions.execute` 对每个目标独立记录结果，已知/unknown receipt 都不再次发送 | MCP 同上；旧 broadcast GUI/HTTP 不切换 | `test_message_and_broadcast_keep_legacy_targets_and_schedule`、既有 `tests/test_session_message_jobs.py` broadcast 回归；多目标映射保留稳定顺序 |

## 后续同一 Job 的寻址

- `agent_background_get/list/cancel/retry` 仍请求原 `/api/background-jobs` 路径；`packages/web/server.py` 只对 `legacyCompat` Action Job 做兼容投影，查询返回旧字段，内部仍是同一 Job 文件。
- cancel 调用 `Runtime.cancel`，不会创建第二条旧执行实例；background Action 的运行中子进程仍通过既有 ownership/PID 检查的 Runner 路径终止。
- retry 保留旧“新 Job ID + `retryOf`”语义，但新记录仍由 `Runtime.create_legacy` 创建并由 system Action 执行。相同 transport retry key 重复创建不再生成第二条记录。
- Action Runtime 的 `unknown/uncertain` 结果进入不可自动重放状态；重启恢复只保留审计和人工可见状态，不由 legacy retry 自动重投原 effect。
- `/api/jobs/{jobId}` 和既有 Jobs panel 通过统一 registry 看到同一记录；Action audit 位于该 Job 的 `attempts/lastExecution`，没有隐藏的另一 Job ID。

## 明确未迁移的形状与 Phase 4b 缺口

| entry / shape | 本阶段处理 | 原因与后续 |
|---|---|---|
| legacy `/api/background-jobs`、`/api/session-message-jobs` 的普通 HTTP/GUI creation | 保持旧路径 | 4a 只切 MCP compatibility adapter；不改变旧 HTTP/GUI owner |
| `agent_message_job_update` 的无法证明等价的历史 schedule/unknown fields | 保留旧路径；Action 记录只接受已声明 mapping | 不静默猜测 legacy shape；需显式 migration review |
| `scheduled-task`/shell、QQ、`resume_legal_running` 等旧 MCP/HTTP schedule entry | 未迁移 | 它们依赖旧 Schedule record 与 scheduler owner，属于 Phase 4b 的 Schedule → Event → Trigger → Job 单 owner 切换 |
| `main-lifecycle`/supervisor lifecycle | 未迁移 | `system_main_lifecycle` 是受保护 supervisor entry，不是 agent 可创建的普通 Job |
| 旧 Schedule records、时间计算、Scheduler Event、Trigger 注册和真实数据迁移 | 未迁移 | Phase 4b；本阶段不创建第二 scheduler、不迁移既有 schedule、不切换旧 owner |

真实代码锚点：`packages/jobs/legacy_compat.py`（参数/结果适配）、`packages/jobs/action_runtime.py`（统一 Job 生命周期）、`packages/jobs/system_actions.py`（产品维护的 system Actions）、`packages/web/server.py`（legacy route 的 trusted-MCP 分流）、`packages/mcp/server.py`（原 tool names/parameters）。

## Phase 4b Scheduler owner switch

| legacy entry | switch expression | owner/evidence | remaining gap |
|---|---|---|---|
| MCP `scheduler_create/update/run_now/list/get/delete/pause/resume/runs`, HTTP `/api/scheduler/tasks*`, Jobs schedule editor | `packages/scheduler/legacy_bridge.py::LegacySchedulerBridge` keeps `taskId`/`jobId`, creates one `Sources` row, one managed Ledger Trigger filtered by `payload.scheduleId`, and converts the Job to `system_scheduled_task` Action config | `tests/test_scheduler_legacy_bridge.py` proves stable identities, one source link, Event→Trigger→Job audit, run-now, explicit switch and idempotent repeat; `background_jobs.run_due_scheduled_tasks` skips `is_action_job` records | Real production MCP/GUI transport and deployment acceptance remain untested |
| Existing legacy schedule records | `POST /api/scheduler/tasks/{task_id}/owner-switch` is explicit/idempotent; unmappable Action/schedule records remain legacy | unknown Action, multi-entry and unknown schedule fields raise `legacy_unmappable` and leave the old record untouched | Bulk real-data migration and operator review are Phase 4b deployment work |
| Scheduled shell, QQ text, `resume_legal_running` | `system_scheduled_task` preserves the old `action` payload and delegates to the existing `_execute_job_action` effect path | system Action schema validation occurs before switch; old error/receipt fields remain on the compatibility record | Provider-specific QQ and shell process recovery need live acceptance |
