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

## Phase 5 实例验证与修正（当前实现）

以下证据更新前面的 Phase 4 静态/单元结论。真实 stdio 使用 `python -m packages.mcp.server`；message/background 走产品私有 IPC，scheduler 走真实 HTTP；独立 HTTP 客户端和 Chromium 连接随机 loopback 端口。profile 全部由 pytest 临时目录持有。

| entry / contract | 当前执行路径与修正 | 实例证据 |
|---|---|---|
| legacy MCP 默认授权 | `server._legacy_mcp_authority` 只识别私有 IPC marker，记录 `legacy-mcp` 来源；继续使用旧 tool 的 caller/managed Session 门禁，不额外要求新四对象授权。显式 Action/Job 工具的权限规则保持原路径 | `test_real_mcp_legacy_message_and_process_become_action_jobs` 在所有 agent 四对象 grants 为 false 时成功；授权回归仍覆盖显式工具拒绝 |
| `agent_background_start/get/cancel/retry` | `Runtime.execute` → `system_actions.execute` → `background_jobs.start_action_process` → 原 native Runner；Runner 直接附着到返回的 Job，**不再调用旧 `start` 创建第二条 Job**。进程 Attempt 与 effect 使用同一 Job ID | 同一 profile 的记录集合断言；`processJobId == jobId`；短进程与 Runner 退出；host crash 后原 ID 可查，unknown retry 拒绝，安全取消后 PID 清零 |
| `agent_message_job_create/get/cancel` | 统一 Job 保留运行时 schedule 列表，`legacySchedule` 供旧 MCP 投影；旧 message 扫描跳过 Action Job。`system_actions.execute` 复用原 `send_session` 并保留 agent/sourceSessionId | 实际 once 调用 → 一次 `system_session_message` Attempt → 真实 queue → 隔离 stream CLI 收到一次带 agent prefix 的正文；完成后 MCP `nextRunAt=None`；未来消息可取消 |
| broadcast 的单目标列表 | storage 按 kind 保留 `targetSessionIds`，即使稳定去重后只有一个目标也保留 broadcast 身份 | 既有 broadcast 单元回归；本阶段未另做真实多目标 stdio/provider 验证 |
| Scheduler 周期 owner | `Runtime.tick` 对 `schedulerOwner=event` 跳过 inline plan；`TimeEvents.tick` 的游标提交后调用 `LegacySchedulerBridge.project_cursor` 更新旧 nextFireAt 投影，重启也能修复投影 | 到期只有一个 Event、一个 Trigger Run、一个 system Action Attempt；正常重启无新 Attempt/Event；once 的旧 nextFireAt 变为 null |
| pause/resume、run-now、旧未迁移记录 | bridge update 从 config 中取回原 scheduled Action，paused 与 enabled 独立保存；未切换记录仍由旧 store 更新 | 实际 MCP pause/resume；run-now 前后 nextFireAt 相同；带未知 schedule 字段的记录拒绝 owner-switch、保留原字段且旧 pause 可用 |
| Scheduler delete 与共享 source | `LegacySchedulerBridge.delete` 复用同步 `Runtime.delete` 和通用 managed cleanup；先检查运行状态，再删自己的 Job/Trigger；source 按 Phase 3 独立生命周期保留 | 真实 MCP delete 后，共享 source 仍存在且只剩浏览器链 Job 的引用 |

证据入口：`artifacts/job-chain-phase5/e2e-final.xml`、`http-stdio.json`、`crash-restart.json`、`browser/browser.json`；命令和未验证边界见 `PHASE5_E2E_REPORT_20261025.md`。本阶段未执行真实数据迁移；weekly 秒精度、所有时区/DST、所有 misfire/grace 组合、多目标 QQ/provider 回执及旧 Jobs panel 编辑/取消不因这些样例而宣称已完成端到端验证。

## Phase 4b Scheduler owner switch

| legacy entry | switch expression | owner/evidence | remaining gap |
|---|---|---|---|
| MCP `scheduler_create/update/run_now/list/get/delete/pause/resume/runs`, HTTP `/api/scheduler/tasks*`, Jobs schedule editor | `packages/scheduler/legacy_bridge.py::LegacySchedulerBridge` keeps `taskId`/`jobId`, creates one `Sources` row, one managed Ledger Trigger filtered by `payload.scheduleId`, and converts the Job to `system_scheduled_task` Action config | `tests/test_scheduler_legacy_bridge.py` proves stable identities, one source link, Event→Trigger→Job audit, run-now, explicit switch and idempotent repeat; `background_jobs.run_due_scheduled_tasks` skips `is_action_job` records | Real production MCP/GUI transport and deployment acceptance remain untested |
| Existing legacy schedule records | `POST /api/scheduler/tasks/{task_id}/owner-switch` is explicit/idempotent; unmappable Action/schedule records remain legacy | unknown Action, multi-entry and unknown schedule fields raise `legacy_unmappable` and leave the old record untouched | Bulk real-data migration and operator review are Phase 4b deployment work |
| Scheduled shell, QQ text, `resume_legal_running` | `system_scheduled_task` preserves the old `action` payload and delegates to the existing `_execute_job_action` effect path | system Action schema validation occurs before switch; old error/receipt fields remain on the compatibility record | Provider-specific QQ and shell process recovery need live acceptance |
