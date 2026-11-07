# Action 统一 P0：旧能力覆盖矩阵与真实调用链审计

日期：2026-11-06。状态：P0 设计/审计交付，**未实现 P1 产品代码**。

- 工作树：`D:/project/pan-worktrees/action-unification-p0-20261106`
- 分支：`design/action-unification-p0-20261106`
- 代码基线：`f5fb73c5035a2ebed6ece33167205bb984be531d`（`main` 当前真实 HEAD）
- 审计方法：只读静态阅读 + 符号定位（`文件:符号`），未启动服务、未跑产品测试、未构建、未触碰 8767/8768 与真实 data/profile。
- 本文只回答"旧能力现在落在哪、复用哪条执行路径、缺口在哪、拿什么验收"，不提出第二个 Job engine / scheduler / ledger。

## 0. 输入与来源核对

| 输入 | 原位置 | SHA256 | 本次处置 |
|---|---|---|---|
| Action 统一详细方案 v1 | `Pan/data/workdirs/PAN_SMA/design/ACTION_UNIFICATION_DETAILED_PLAN_20261104.md`（被 `data/` 忽略，未入版本控制） | `39c9d2be…b2103` | 复制入本分支 `docs/design/action-unification/`，追加 §19 P0 修订记录 |
| P0 只读审计接收 | 同目录 `ACTION_UNIFICATION_P0_AUDIT_RECONCILIATION_20261104.md` | `5d5dcd0f…d816a` | 原样复制入本分支，不改内容（作为输入证据） |
| Job 终局模型 | `Pan-main/docs/design/job-unification/JOB_FINAL_MODEL_20261025.md` | `1219d18d…fe15` | 已在版本控制内；按 U01/U02/U11 追加取代标注 |
| Phase 4 兼容矩阵 | 同目录 `PHASE4_COMPAT_MATRIX_20261025.md` | `bf76c8a5…a9a` | 已在版本控制内；追加"当前实现状态"核对段 |
| Action 链 / Scheduler Event 设计 | 同目录 `JOB_ACTION_CHAIN_SCHEDULER_EVENT_DESIGN_20261024.md`（在 `Pan-main` 为未跟踪文件） | `c4e29ca4…9a` | 复制入本分支；追加与 D2-A/旧入口退休的对照说明 |

`c4e29ca4…9a` 与 `JOB_FINAL_MODEL_20261025.md` §0 记录的参考 SHA256 一致，说明读到的是同一份 20261024 汇总。

改动前的唯一 dirty 事实：`Pan-main`（`main`，`f5fb73c5`）工作树有他人未提交改动；本任务在其上新建 worktree，未修改 `Pan-main`、`Pan`（`practical`）或任何其他 worktree 的文件。

## 1. 结论摘要

1. **旧"六入口"已不是同一状态。** 旧 MCP 创建工具（background/message/scheduler）已从 `tools/list` 移除并以 `capability_unsupported` + guidance 拒绝；旧 HTTP 写路由返回 `410`；旧 Job/Scheduler 读、取消与历史查询保留。P0 矩阵必须按"读投影 / 显式拒绝 / canonical 替代"三分类记录，不能笼统写"已迁移"。
2. **统一执行内核真实存在，且只有一套。** `packages/jobs/final_service.py:FinalService`（document 存储）+ `packages/jobs/final_runtime/{coordinator,ledger}.py`（Run/Step/Attempt/effect/outbox）+ `packages/actions/typed_executor.py:TypedExecutor`（provider 端口）。旧 `packages/jobs/action_runtime.py:Runtime` 与 `packages/automation/chain_engine.py` 已无生产装配点（`automation.job_runtime` 恒为 `None`），`packages/jobs/system_actions.py:execute/invoke` 已被 `__getattr__` 拒绝。**不存在需要再建的第二引擎；缺的是把旧入口接上它，而不是平行实现。**
3. **canonical 目录只有产品 typed Action。** `FinalService._install_product_actions` 发布 `product_process`、`product_session_message`、`product_session_broadcast`、`product_channel`（QQ provider 绑定时）、`product_assign_v2`、`product_resume_legal_running_v1`、quota 及各自的 `_v2/_runtime_v1/_cleanup_v1/_logs_v1` 版本；没有 `shell` 字符串 Action、没有 `scheduled-task` Action、没有 `session pause` Action。用户自定义 Action 仍在旧文件型 registry。
4. **两个目录并存。** 旧 `packages/actions/api.py` 的 `/api/actions` CRUD/Group build/草稿仍可写（JSON 文件 store）；canonical `/api/jobs/actions` 读目录在 `packages/jobs/final_api.py:install` 里把 `GET /api/actions` 别名到同一 handler，因此**旧 GET 被遮蔽、旧写仍在**。这正是 P1 要收口的核心。
5. **`shell`/外部脚本能力目前只活在旧 scheduled-task payload 里。** `system_scheduled-task.action.api='shell'`（`packages/actions/store.py:system_definition`、`packages/core/background_jobs.py:validate_shell_action`）是唯一的 shell 字符串语义；新建入口已 410，canonical 目录没有等价 Action。实施方案 §5.1/§16 明确"不得因 Action 化禁止 shell/外部脚本"，因此 **P2 必须先给出具名 successor，才允许 P6 退役旧记录**；否则就是能力缩减。
6. **授权没有被 Action 化提权。** canonical 路径统一走 `packages/jobs/final_service.py:_permit` + `packages/automation/auth.py:UserAuth`（read/create/updateConfig/lifecycle/delete/execute + `manageSystemActions`）；旧 compat 读/取消仍用私有 IPC marker（`packages/web/server.py:_legacy_mcp_authority`），该 marker 只是遗留桥梁，不是新 Action 授权模型。**没有出现 Action 专属鉴权层**；但 marker 的存废是待决项（§5 C-3）。
7. **U11 尚未落地。** canonical Trigger 定义强制 `targetJobId`（`packages/jobs/final_contracts.py`），`packages/automation/engine.py` 只把准入交给 `admission_port.target(...)->targetJobId`。Trigger 直指 Action 是 P4 交付，不能提前宣称"统一完成"。

## 2. 现状执行原语（矩阵引用的真实锚点）

| 原语 | 真实符号 | 作用 |
|---|---|---|
| 定义/版本/发布 | `packages/jobs/final_service.py:FinalService.publish_action`、`write`、`control`、`retire` | Action/Trigger/Event Creator/Job/Template 的唯一 canonical 定义存储；发布不可变 |
| 冻结与准入 | `FinalService._admit`、`packages/jobs/final_contracts.py:_check_action_ref` | Run 接纳时固定 ActionRef（id+revision+digest）、参数、策略 |
| Run/Step/Attempt/effect | `packages/jobs/final_runtime/ledger.py:Ledger.admit/begin_attempt/admit_effect/record_effect` | 唯一执行账本 |
| 调度唤醒与派发 | `packages/jobs/final_runtime/coordinator.py:FinalRuntime` | Run 生命周期、通知结算、保留/删除 |
| provider 端口 | `packages/actions/typed_executor.py:TypedExecutor.execute` | 按 `frozenPlan.operation` 调用既有业务原语 |
| 进程执行宿主 | `packages/jobs/independent_executor.py:IndependentExecutor`、`packages/jobs/independent_execution_runner.py` | 独立执行 + `ExecutionStore`（`executions.sqlite3`）持久回执 |
| 旧产品执行定义 | `packages/actions/store.py:system_definition`（`execution.kind='system_job'`） | 仅审计可读；`packages/jobs/system_actions.py:recovery_ready` 明确拒绝再准入 |
| 旧 Action 动态执行 | `packages/actions/executor.py:execute_dynamic` | `session_operation`(pause)/`job_template`/`system_job` 分支；生产无 `Runtime` 装配 |
| 旧 provider 直执行 | `packages/actions/provider.py:execute` → `run`（`--snapshot` stdio） | Group build 产出的 `*-provider` 插件能力调用 |
| 旧 registry | `packages/actions/api.py:install` + `packages/actions/store.py:Store` | 仍可写的文件型 Action/Group 目录 |

一句话链：**canonical 写入 = FinalService（sqlite document）→ 冻结 → Ledger → TypedExecutor/IndependentExecutor → provider/原业务函数 → effect receipt → outbox 通知**；旧 registry 与旧 Job 记录只作为**输入与历史**存在。

## 3. 逐项覆盖矩阵

列说明：**旧入口**=真实可达符号；**当前状态**=`读取保留` / `写入口已拒绝` / `工具已移除` / `仅历史`；**复用路径**只列真正执行的地方。

| # | 旧能力族 | 旧入口（真实符号） | 当前状态 | canonical 表达 | 复用执行路径 | 兼容返回 / 身份 | 当前缺口 | 后续验收证据 |
|---|---|---|---|---|---|---|---|---|
| A1 | background process | MCP `agent_background_start/get/list/cancel/retry`（`packages/mcp/server.py`）；HTTP `/api/background-jobs`（`packages/web/server.py:api_background_job_*`） | 创建与 retry：工具已移除、HTTP POST 410；get/list/cancel 保留 | Job（`steps[].actionRef` → `product_process`，`arguments{argv,cwd,hideWindow}`；另有 `product_process_cleanup_v1`、`product_process_logs_v1`） | `FinalService._admit` → `Ledger` → `TypedExecutor` → `_external_adapter`(`final_service.py:_external_adapter`) → `IndependentExecutor`/`ExecutionHost`；进程身份与清理在 `packages/jobs/owned_process.py`、`packages/jobs/session_delivery_runtime.py` 之外的 owner/PID+birth 路径 | 新 canonical Job 有独立 `jobId`；旧 `GET /api/background-jobs` 只列 `kind∈{background-process,…}` 且非 `scheduledParentJobId` 的旧记录，**新 canonical Job 不会出现在旧列表**（缺口） | 旧面板看不到新 Job；`legacyCompat` 投影只覆盖 Phase 4 记录；retry 语义（新 Job + `retryOf`）与 D3 `job_run_retry` 未对齐 | 隔离 profile：创建 → Run/Attempt/effect 各一条 → 宿主崩溃后按 `jobId/runId` 可查、`unknown` 不重投；旧 `GET` 与 canonical `GET /api/jobs/{id}` 指向同一事实（P5/P6 才允许） |
| A2 | command（argv，无 shell 解释） | 旧自定义 Action `execution.kind='command'`（`packages/actions/store.py:validate_action`）；Group 插件能力调用 | 仍可用（旧目录 + Group build） | `product_process`（argv 元素级）或用户 Action `command` | `packages/actions/provider.py:execute`：`asyncio.create_subprocess_exec`，Windows 经 `packages/actions/command_runner.py` + Job 对象，POSIX `start_new_session`/`killpg`；512 KiB 输出上限、`timeout_sec` | `capability.invoke` 的 MCP content/structuredContent；`isError` 表示已确认失败 | 该路径**不经过** FinalService/Ledger，没有 canonical Job、effect receipt 或 `requestKey`；`high_risk` 直接拒绝（`provider.py:execute`、`actions/executor.py:execute_dynamic`） | 同 argv 在 direct 与 Job 上下文产生同一 `commands[]` 回执；部分成功（git add/commit 成功、push 失败）逐条退出码；`>512KiB` 截断标记 |
| A3 | shell 字符串 / 外部脚本 | 旧 `scheduled-task.action.api='shell'`（`packages/actions/store.py:39-42`、`packages/core/background_jobs.py:validate_shell_action`、`packages/jobs/api.py:_normalize_scheduled_action`） | 仅历史：创建入口 410，canonical 无等价 Action | **缺**（应为 `process.shell` 一类具名 Action，保留 `command` 字符串、`cwd`、`hideWindow`、解释器/外部路径语义） | 旧实现 `background_jobs._execute_job_action` 已退役（`_RETIRED_ENTRYPOINTS`）；现无执行宿主 | 旧记录仍可按原 `taskId/jobId` 读出 `action` 原文 | **能力缺口**：现行 canonical 目录无法表达 shell 字符串与外部脚本路径；与方案 §5.1/§16 冲突（§5 C-4） | 同一 shell 命令经 Action 执行：退出码、stdout/stderr、截断、超时清理、进程树结束证据；外部脚本路径变更需在详情显示"revision 固定 ≠ 文件字节固定" |
| A4 | Session message（即时） | MCP `agent_message_job_create`（已移除）、`agent_message_job_get/list/cancel`（保留）；HTTP `POST /api/session-message-jobs`（410） | 创建已拒绝；读/取消保留 | Job → `product_session_message` / `_v2` / `_runtime_v1`（`arguments{sessionId,text}`） | `TypedExecutor._session`（`session-message`）→ `WorkerSessionProvider` → `packages/core/worker.py:send_session`；运行时先过 `packages/jobs/session_delivery_runtime.py` 的 per-target fence | 旧 `legacy_view` 保留 `targetSessionId/text/lastDelivery`；`jobId` 不变（Phase 4 记录）；新 canonical 记录不出现在旧 message 列表 | 旧 GUI 不可见新 Job；`once` 绝对时间冻结、`interval/weekly` 转 cron（`packages/jobs/legacy_compat.py:_schedule`）只能在旧记录上读 | 一次调用 = 一个 Attempt + 一条 queue delivery；agent prefix/source 保留；`nextRunAt` 终态为 null；未知不重发 |
| A5 | Session message（定时 once/interval/weekly） | 同上 `schedule` 字段；旧时间 loop | loop 已退役（`background_jobs.run_due_message_jobs` 拒绝）；记录只读 | Event Creator（时间规则）+ Trigger(`targetJobId`) + Job | `packages/scheduler/creators.py:CreatorRuntime` → `packages/scheduler/time_events.py` → Event → `automation/engine.py` 准入 → Job | 旧记录保留 `legacySchedule/nextRunAt`；不再有新触发 | 旧 schedule 形状与新 Creator 字段的映射仅 `legacy_compat._schedule` 三种形式；weekly 秒精度、DST 未验证 | 同一 Event 只产生一个 admission；重启不补发、不重复；停用 Job 不停用来源 |
| A6 | broadcast | MCP 同一 `agent_message_job_create` 的 `target_session_ids` 分支（已移除）；`agent_background_*` 之外的广播 GUI | 创建已拒绝；读/取消保留 | Job → `product_session_broadcast` / `_v2` / `_runtime_v1`（`arguments{sessionIds,text}`） | `TypedExecutor._session_broadcast` → 原 Worker fanout；逐目标 receipt 由 `session_delivery_runtime` 记录，单目标成功不重发 | 旧记录保留 `targetSessionIds`（即使去重后只剩一个）与 broadcast 身份 | 多目标真实 provider 回执未做端到端（Phase 5 已声明同类边界）；并发上限/背压未验证 | 稳定目标顺序、逐目标成功/失败隔离、同一 broadcast 只产生一条 Job、未知目标不重试 |
| A7 | QQ / channel | 旧 scheduled-task `action.api='send_qq'`（`_normalize_scheduled_action`）；插件 `/api/qq/send` | 旧创建入口 410；canonical 仅在 provider 绑定后发布 | `product_channel` / `product_channel_runtime_v1`（`{targetType,targetId,botUin,text}`） | `TypedExecutor._channel` → `packages/actions/qq_channel_provider.py:QQChannelProvider.send` → `_send_scheduled_qq`（`packages/web/server.py:_send_scheduled_qq`）→ 插件 `/api/qq/send` | provider receipt（`messageId`/error code）原样进入 effect；`is_pre_send_phase` 区分可重连与未知 | 默认 `FinalService` 执行器无 `channel_provider`，需 `web/server.py:_bind_qq_channel_provider` 在 `start()` 前绑定；脱离 web 宿主（CLI/独立服务）时目录无 channel Action | 真实 provider 超时/失败/未知三态；`messageId` 可查；未发送阶段可重连、已发送不重发 |
| A8 | scheduled task（旧 Scheduler CRUD） | MCP `scheduler_*`（create/update/delete/pause/resume/run_now 已移除，list/get/runs 保留）；HTTP `/api/scheduler/tasks*`（`packages/scheduler/api.py`） | 写路由 410（`RETIRED_ROUTES`）；读/历史/status/next 保留 | Event Creator（`event_creator_*`）+ Trigger(`targetJobId`) + Job；手动立即执行 = `POST /api/jobs/{id}/runs` | `packages/scheduler/creators.py` + `providers.py`（时间/时区/DST/`misfire_outcome`）→ `scheduler.time.v1` Event（`packages/scheduler/time_contract.py`）→ `automation/engine.py` 准入 → `FinalService` Run | 旧 `taskId/jobId` 仅剩读投影；旧 `nextFireAt` 不再推进 | `packages/scheduler/legacy_bridge.py:LegacySchedulerBridge` 仍在源码中但**无任何 import/实例化**（死源）；真实 owner switch / 批量迁移未在本次验证 | 单 owner：到期只产生一个 Event、一个 admission、一个 Attempt；`run-now` 不动游标；停用/删除的引用影响可查 |
| A9 | 旧 Schedule 存量记录 | 旧 tasks JSON、`nextFireAt`、run JSONL | 仅历史（loop 与 writer 均已退役） | 逐条映射为 Creator+Trigger+Job（或按 D6 保留不迁移） | 无执行；`packages/jobs/final_runtime/coordinator.py:FinalRuntime.legacy_history` 提供只读历史 | 原 `taskId`、时间配置、run 记录保留 | 未知 schedule 字段/复杂记录必须原状保留并排除执行；本次**未跑**任何数据盘点或 dry-run | 扫描报告：可执行旧 owner/lease = 0；复杂记录字节不变且不可加载执行 |
| A10 | main lifecycle / supervisor | 旧 `POST /api/main/restart`、`/api/main/exit`（已无 POST，仅 `/status` 保留）；CLI `packages/core/cli.py:lifecycle` | 路由形态已变 | `system_main_lifecycle` Action（`packages/core/lifecycle_action.py:ACTION_ID`）+ 固定入口 `POST /api/system-actions/main-lifecycle` | `lifecycle_action.protected_plan` → `ProtectedLifecycleExecutor` → `TypedExecutor(lifecycle_executor=…)` → 外部 supervisor（`packages/core/main_lifecycle.py`）；PID+birth 与 `requestId` 幂等 | 返回 `actionId/jobId/requestId/effectId`；`status=busy` 表示同一 fact 已有 pending | canonical 目录**不发布**该 Action（`high_risk` 被 `FinalService`/`execute_dynamic` 拒绝），因此它不在统一目录内；GUI/CLI 旧 URL 字面量仍在（`packages/web/src/services/api.ts`） | 响应丢失后按 `requestId` 查原 effect 不二次重启；PID/birth 不符直接拒绝；`start` 本地 host-only |
| A11 | assign（Agent 委派任务） | 旧 `action.api='assign'`（scheduled-task payload）；`resume_legal_running` 同族 | 旧创建 410 | `product_assign_v2`（`{sessionId,text,taskId,source,sourceSessionId}`） | `TypedExecutor`（`assign`）→ `WorkerAssignProvider` → 原 worker assign | 旧 payload 形状保留在只读记录 | `source/sourceSessionId` 仅 Bearer 用户可填（`FinalService._session_source`）；delegated Agent 传输无法断言 Session 身份 | 伪造 source 被拒；assign 与 message 走同一 queue 门禁；无提权 |
| A12 | resume_legal_running | 旧 `action.api='resume_legal_running'` | 旧创建 410 | `product_resume_legal_running_v1`（空参数） | `TypedExecutor`（`resume-legal-running.v1`）→ `packages/actions/session_resume.py:select`（`legal_offline.is_illegal` + 无存活 Worker）→ 原 Wake | 每个 effect 目标一条 receipt；`client_message_id` 由 effectKey 派生 | 选择集在 Run 内冻结的断言未单独验证 | 两次触发不重复 Wake 同一 Session；非法/离线判定沿用 `legal_offline` |
| A13 | Session control（pause） | 旧 `execution.kind='session_operation'`（`packages/actions/store.py`、`packages/actions/session_query.py:template`、`GET /api/actions/session-operation-template`） | 定义模板可读；执行宿主未装配 | **缺 canonical Action**（应落到 `session.pause` 一类受控命令，沿用 UserAuth/owner/lease 与目标选择） | 旧路径 `execute_dynamic` 的 `session_operation` 分支（`packages/actions/executor.py`）→ `progress.pause`；生产 `automation.job_runtime` 为 `None`，`action_runtime.Runtime` 无装配点 | 旧模板字段与响应形状保留 | **直连/Job 两条链都没有活的 pause 执行者**；selector 四种模式（explicit/event/query/targetIds）只在旧代码里 | 逐 Session 结果、部分失败、`mode=query` 的 matched/candidateCount 冻结；无 Session 被误 pause |
| A14 | Group build / pin / binding | `packages/actions/api.py:release`（`POST /api/actions/groups/{id}/build` 与 `/unbuild`）、`packages/actions/store.py:build` | 仍可用（旧目录） | Group release 固定 `{id,revision,digest}`；Session 绑定沿用插件绑定 + `packages/plugins/lifecycle.pinned` | 构建写插件 catalog（`catalog.servers[pid+'-provider']`，stdio `packages/actions/provider.py:run`）；Session 运行时 pin 由 Worker release 固定 | 返回 `groups[]` 视图 + `blockers/sessions/loadedVersion` | 没有"单 Action 全 Group 升级 / 全 Group 升级到 latest"接口（U04/U05）；pin 只是 build 快照，无显式引用升级与 CAS | v1 发布后 v2 不影响已 pin Worker；显式升级只改草稿；历史 release 可查 |
| A15 | Action registry CRUD | `packages/actions/api.py`：`GET/POST/PUT/DELETE /api/actions…`、`/lifecycle`、`/api/action-drafts/*` | 写仍可用；`GET /api/actions` 被 canonical 别名遮蔽 | `FinalService` 的 action 对象（`/api/jobs/actions` 只读目录） | 旧：`Store`（`data/actions/*.json`）→ build 快照；新：`final_service.publish_action`（`system`/产品动作，非公开路由） | 旧 `{revision,actions,groups}`；新 `{items,nextCursor,references}` | **双目录**：没有用户 canonical 草稿/发布/版本 API；旧 `system_*` 定义仍在 `state['actions']` 但与 canonical 无映射 | fresh profile 只有一套目录；旧 ID→稳定 ActionRef 清单；复杂/未知定义原字节保留且排除执行 |
| A16 | job template | `POST /api/job-templates/{id}/instantiate`、`FinalService.instantiate_template`；旧时间模板 `/api/jobs/templates` | canonical 可实例化；旧模板写入口 410、读保留 | `template`（完整 Job 配置 + composition），`job_template` Action 只用于固定模板 | `packages/jobs/action_templates.py:Templates`（CAS/引用/图校验）→ `FinalService._normalize_template_form` → `_admit` | 返回 `jobId` + operationId；`requestKey` 幂等 | 旧"时间片段模板"未导入为完整 Template 的 time composition；Action→Job 参数覆盖的字段级禁止项未在代码里逐项断言 | 覆盖不改 Template revision；缺必填即拒绝；重复 requestKey 只产生一个 Job |
| A17 | Trigger 准入 | `packages/automation/engine.py:Engine.match`、`admission_port.admit` | canonical 可用（仅 Job 目标） | `trigger` 定义：`eventTypes/creatorSelector/targetJobId`；U11 的 `target.type=action` **未实现** | `Engine.match` → target=`targetJobId` → `FinalService._admit` → 同一 Run 路径 | `(eventId,triggerId)` 唯一；多个 Trigger 同 Job 以 `triggerId` 区分 | Action 目标缺失；`targetJobId` 是硬约束（`packages/jobs/final_contracts.py`） | 重复 Event/并发领取/冷恢复只对应原 admission；改 Trigger revision 不重放旧 Event |
| A18 | 通知 / outbox | `packages/core/background_jobs.py:recover_notifications`（已退役）、旧 terminal broadcast | 仅 canonical | `Ledger.ensure_outbox/ack_outbox` + `FinalRuntime.settle_notification` + `packages/jobs/notification_delivery.py:binding` | outbox reservation + job config CAS → `outbox_sender`（`packages/web/server.py:_send_final_notice`）→ `/api/notifications/send` | 保留原 `outboxId/runId/jobId`；ack ≠ 已读 | 旧 notice ID 分支与迁移后回执的"不重发"未在新路径上逐条验证 | 一次终结一条 outbox；重复结算不重复发；旧 `outboxId` 不重发 |

## 4. required 家族调用链（逐条对照）

### 4.1 background process
旧：`agent_background_start`（MCP）→ `/api/background-jobs` trusted-MCP 分流 → `background_jobs.start` → `background_runner` 第二 Job 记录。
现：工具移除 + HTTP 410；canonical `job_create`（`product_process` ref）→ `_admit` → `Ledger` → `TypedExecutor` → `_external_adapter` → `IndependentExecutor` → 原 Runner/ProcessOwner（PID+birth、进程树、日志）。旧记录仍可 `GET`/`cancel`，cancel 走 `Runtime.cancel` 或 PID-guarded `background_jobs.cancel`（`web/server.py:api_background_job_cancel`）。

### 4.2 command / shell / external script
`command`（argv）真实存在两条：canonical `product_process`（argv 元素级、无 shell 解释）与旧自定义 Action `execution.kind='command'`（`provider.execute`，仅 Group 插件调用的 stdio 路径）。
`shell`（字符串）**只剩** `system_scheduled-task` 的 `action.api='shell'` 定义与校验；执行者 `_execute_job_action` 已退役。→ 见 §5 C-4。

### 4.3 Session message
旧 MCP/HTTP 写入拒绝；canonical `product_session_message*` → `TypedExecutor._session` → `worker.send_session`；运行时由 `session_delivery_runtime` 先写 per-target 预留，再调用 provider，再落 receipt。`once` 在 `legacy_compat._schedule` 里冻结为绝对时间。

### 4.4 broadcast
同链路的 `_session_broadcast`；`sessionIds` 稳定去重但保留 broadcast 身份（`legacy_compat/config_for` 的 `targetSessionIds`），逐目标 `targetResults` 由 `session_delivery_runtime.deliver/slots` 维护，单目标成功不重发。

### 4.5 QQ / channel
`product_channel`（provider 绑定时发布）→ `TypedExecutor._channel` → `QQChannelProvider.send` → 插件 `/api/qq/send`；`receipt_outcome` 与 `reconnectable_no_send` 决定能否重连未发送阶段；已发送一律 `unknown` 查询，不重发。

### 4.6 scheduled task
旧 `scheduler_*` 写工具移除、HTTP 写 410、旧 tick/loop 退役（`background_jobs` `_RETIRED_ENTRYPOINTS` 含 `run_due_scheduled_tasks`、`_fire_scheduled_entry`）。canonical：Creator（时间规则）→ `scheduler.time.v1` Event → `engine.match` → `trigger.targetJobId` → Job Run。`legacy_bridge.py` 只剩源码，无装配。

### 4.7 main lifecycle
`POST /api/system-actions/main-lifecycle` → `lifecycle_action.validate_operation`（仅 `PROTECTED_OPERATIONS={exit,restart}` 等）→ `protected_plan`（PID/birth/requestId/effectKey）→ `ProtectedLifecycleExecutor` → `TypedExecutor` → `packages/core/main_lifecycle.py` supervisor。外层 `_lifecycle_operation_lock` 与持久 effect 查询共同保证"同 fact 不二次重启"。

### 4.8 assign / resume / session control
`assign` 与 `resume_legal_running` 均有 canonical Action 与 typed 分支；`pause`（`session_operation`）只有旧定义模板与 `execute_dynamic` 分支，且 `automation.job_runtime` 为 `None`，无生产执行者。`resume` 的选择集由 `session_resume.select` 用 `legal_offline.is_illegal` 重新计算，不信任旧 projection。

### 4.9 job template
`instantiate_template` → `action_templates.Templates`（覆盖字段校验、引用生成、图内 `job_template` 递归保护）→ `_normalize_template_form` → `_admit`；固定模板的 Action（`execution.kind='job_template'`）只允许空参数（`execute_dynamic` 显式拒绝带参调用）。

## 5. 冲突、修订与待决

### C-1 旧 MCP"无感迁移（工具名不变）"vs 已定旧入口退场
- **旧行为**：20261024 汇总 §2 要求旧工具名、参数、返回、Job ID、通知契约不变，Agent 不改调用。
- **新约束**：`JOB_FINAL_MODEL_20261025.md` §0 明确取代"长期保留原 MCP 工具名"；当前代码已 `remove_tool` 并返回 `capability_unsupported`（`packages/mcp/server.py:3242-3319`）。
- **冲突证据**：同一份 20261024 汇总仍被 `JOB_FINAL_MODEL` §0 列为外部参考；两者不能同时作为验收依据。
- **选项**：A 以 `JOB_FINAL_MODEL` 为准，旧工具退休、给 migration guidance（当前实现）；B 恢复旧名适配器（与 §0 及"不恢复 dispatcher"冲突）。
- **本次处置**：不自行改变模型；在 20261024 文档加对照说明，在 §6 验收表中把"工具名不变"标为**已被取代**，需 MA 明确签收 A。

### C-2 "兼容适配必须返回原形状"vs 当前显式拒绝
- **旧行为**：方案 §16 要求兼容层返回旧参数、Job ID、错误、通知、查询、取消、删除、恢复结果。
- **新约束/现状**：写入口统一 `410 retired`（`packages/jobs/final_api.py:retired_legacy_write`，`packages/web/server.py:11286-11293`）；只保留读投影与取消。
- **冲突证据**：`final_api.py:122-133` 把除 `GET` 与 `/cancel` 外的旧动作全部判为 retired；这与"返回原结果"不是同一语义。
- **选项**：A 定义"显式拒绝 + guidance + 读投影"为具名处置（不需要第二个 dispatcher）；B 为写操作补兼容适配器（等于恢复旧 producer，与 C-1 的 A 冲突）。
- **本次处置**：矩阵按"读保留/写拒绝/工具移除"记录；§16 原文标注"待 MA 裁决"，不把拒绝写成迁移完成。

### C-3 `legacy-mcp` transport 授权 vs 统一矩阵
- **旧行为**：私有 IPC marker 直接获得 `legacy-mcp` 写/读权（`packages/web/server.py:_legacy_mcp_authority`），不要求四对象 grants。
- **新约束**：`JOB_FINAL_MODEL` E03 要删除该分流；方案 §8 要求所有 Action/Job 走同一 UserAuth 矩阵、禁止 Action 专属授权。
- **冲突证据**：当前读/取消仍依赖 marker；canonical 工具走 grants。两者并存。
- **选项**：A 只在旧读/取消保留 marker 到 P6，随旧路由一起删；B 立刻改 grants（旧 Agent 读/取消会断）。
- **结论**：**不新增任何 Action 专属鉴权**；marker 只是遗留桥梁，不在新模型中作为授权来源（`FinalService._permit` 不读取它）。

### C-4 shell / 外部脚本无 canonical successor（能力缺口，不是文字冲突）
- **旧行为**：`shell` 字符串 + `cwd` + `hideWindow` 由 scheduled-task payload 承载，另有 `validate_shell_action` 的 16000 字符与 cwd 校验。
- **新约束**：方案 §5.1/§16 明确"不得因 Action 化禁止旧 Job 已支持的 shell 或外部脚本"，且"shell Action 保留旧 command 字符串语义"。
- **证据**：canonical 目录无 shell Action；`provider.execute` 只接受 `executable+argv`；`high_risk` 在 `provider.py:execute`、`actions/executor.py:execute_dynamic`、`packages/jobs/chains.py:freeze` 被拒绝。
- **结论**：必须在 P2 增加具名 `process.shell` 类 Action（复用旧 runner/窗口/清理门禁），否则 P6 退役旧记录 = 静默缩减能力。**本次不实现**，仅登记为 P2 前置。

### C-5 U11（Trigger 可指向 Action）尚未实现
- 现状 `targetJobId` 是定义硬约束；不是模型冲突，是 P4 交付缺口。任何"Trigger 目标已统一"的表述在 P4 前不成立。

### C-6 main lifecycle 不在统一目录
- `system_main_lifecycle` 通过专用路由 + `protected_plan` 执行，`FinalService` 目录不发布它（`high_risk` 拒绝）。这符合"保留原保护门禁、不新增 Action 专属确认"，但意味着"唯一目录"在 P1 必须给出该 Action 的受控登记方式，不能靠普通发布绕过 high_risk 拒绝。

### 已完成的文字修订（不改用户模型）
- `JOB_FINAL_MODEL_20261025.md`：在 §0、§2.2（Action 行、D2 段）、§3 M15 处标注 U01/U02/U11 的取代范围，原文保留可追溯。
- `JOB_ACTION_CHAIN_SCHEDULER_EVENT_DESIGN_20261024.md`：标注其"工具名不变/直接 Trigger→Action"与 D2-A、旧入口退休的关系。
- `PHASE4_COMPAT_MATRIX_20261025.md`：追加"当前实现状态"段，说明 4a/4b 适配器与 `system_actions.execute` 已退场，矩阵是历史证据。
- `ACTION_UNIFICATION_DETAILED_PLAN_20261104.md`：追加 §19 源码核对结果与本文 C-1…C-6 索引。

## 6. 已覆盖 / 仍属独立后续阶段的 Scheduler·Event 细节

| 内容 | 现状证据 | 归属 | 不得声称 |
|---|---|---|---|
| 时间规则、时区、DST、misfire | `packages/scheduler/providers.py`（`misfire_outcome`、zone 解析）、`creators.py` | 已实现，需真实时区/组合验收 | "全部时区/DST 已验证" |
| Event 类型与身份 | `packages/scheduler/time_contract.py`（`scheduler.time.v1`）、`session_resume.time_template`（`scheduler.time.v2`） | 两代类型并存，需统一说明 | "事件身份算法定稿" |
| 多时间 Trigger、命名、编辑、删除 | 无 canonical 多 Trigger UI/接口约定 | 后续阶段 | "已支持多时间触发" |
| 跨存储创建/崩溃恢复/半成品清理 | `FinalService.transaction` + `activation_intent`；`packages/scheduler/pending_adoption.py` | 已有机制，未逐路径验收 | "跨存储原子" |
| 共享来源删除保护、引用影响 | `FinalService._check_creator_references`、`creator_references` | 已实现读取/阻断 | "删除语义已验收" |
| 旧 Scheduler MCP/HTTP/GUI 字段映射 | 写入口 410；`Retired` 列表 | P6 | "旧字段已无损迁移" |
| owner switch / 存量迁移 | `legacy_bridge.py` 无装配；本次未跑数据 | P6 + 部署 | "零 legacy 已完成" |

结论：Action/Group/Job 核心可进入 P1；Scheduler/Event 的上述细则继续作为独立阶段，不阻塞 P1，也不得在整体验收时跳过。

## 7. 每族验收证据计划（本轮未运行）

| 能力 | 必测断言 | 可复用现有测试（静态存在） | 仍需新增/实机 |
|---|---|---|---|
| A1 process | 同 Job 一个 Attempt、进程树结束、崩溃后原 ID 可查、unknown 不重投 | `tests/test_job_final_executors.py`、`tests/test_job_final_independent_execution.py`、`tests/test_job_final_runtime.py` | 真实 Runner + host 崩溃 + 旧面板可见性 |
| A2 command | argv 元素绑定、512 KiB 截断、部分成功 | `tests/test_job_final_executors.py` | direct 与 Job 同回执 |
| A3 shell | 字符串语义、cwd/解释器、超时清理 | 无（能力缺失） | P2 新增 |
| A4/A5 message | 一次 Attempt、`once` 冻结、终态 `nextRunAt=null` | `tests/test_session_message_jobs.py`、`tests/test_legacy_mcp_compat.py` | 新 canonical Job 与旧读投影一致性 |
| A6 broadcast | 稳定顺序、逐目标隔离、单目标保留 broadcast 身份 | `tests/test_session_message_jobs.py` | 多目标真实 provider |
| A7 QQ | `messageId` 可查、未发送可重连、已发送不重发 | `tests/test_job_qq_native_provider.py`、`tests/test_job_migration_qq_runtime.py` | 真实 QQ 插件链路 |
| A8 scheduled | 单 owner、单 Event、`run-now` 不动游标 | `tests/test_job_final_creators.py`、`tests/test_scheduler_legacy_bridge.py`（针对已退场桥） | 生产 GUI/MCP transport |
| A10 lifecycle | requestId 幂等、PID/birth 拒绝、busy 语义 | `tests/test_p5b_lifecycle_action.py` | 真实重启/退出演练（隔离 host） |
| A11/A12 assign/resume | 不可伪造 source、不重复 Wake | `tests/test_job_final_admission.py` | 真实 Worker |
| A13 pause | 逐 Session 结果、query 选择冻结 | 无 | P2 新增 |
| A14 Group | v1/v2 pin 隔离、显式升级 | `tests/test_action_groups.py`、`tests/test_job_action_templates.py` | 真实 stdio 调用 + 升级 UI |
| A15 registry | 单目录、旧 ID 映射、复杂定义排除执行 | `tests/test_action_ids.py`、`tests/test_job_final_retirement.py` | fresh profile 单目录验收 |
| A16 template | 覆盖不改 revision、requestKey 幂等 | `tests/test_job_action_templates.py` | 与旧时间模板的合成 |
| A17 trigger | 同一 `(eventId,triggerId)` 一次、revision 不重放 | `tests/test_job_final_creators.py`、`tests/test_automation_action_chain.py` | Action 目标（P4） |

## 8. 未验证层（明确不宣称）

- 未启动任何服务、未跑 pytest/pnpm build、未做真实 HTTP/stdio/Chromium 链路。
- 未做数据盘点、dry-run、owner switch、真实迁移；未触碰真实 `data/` 与 profile。
- 未验证 Windows/POSIX 双平台实际执行差异（仅读代码分支）。
- 未验证 plugin 侧 `/api/qq/send` 与真实 QQ 回执。
- `packages/scheduler/legacy_bridge.py` 虽无装配点，仍保留源码；不得据此写"已删除"。
- 矩阵中"当前状态"均指本基线静态事实；其他 worktree 的未提交候选不参与判断。

## 9. 本分支变更

| 文件 | 变更 |
|---|---|
| `docs/design/action-unification/ACTION_UNIFICATION_P0_CAPABILITY_MATRIX_20261106.md` | 新增（本文） |
| `docs/design/action-unification/ACTION_UNIFICATION_DETAILED_PLAN_20261104.md` | 新增（原样复制 + §19 修订记录） |
| `docs/design/action-unification/ACTION_UNIFICATION_P0_AUDIT_RECONCILIATION_20261104.md` | 新增（原样复制） |
| `docs/design/job-unification/JOB_FINAL_MODEL_20261025.md` | 修改（U01/U02/U11 取代标注） |
| `docs/design/job-unification/PHASE4_COMPAT_MATRIX_20261025.md` | 修改（当前状态核对段） |
| `docs/design/job-unification/JOB_ACTION_CHAIN_SCHEDULER_EVENT_DESIGN_20261024.md` | 新增（原样复制 + 对照说明） |

不在本分支范围内：P1 产品代码、路由、MCP 工具、UI、数据迁移、任何真实服务操作。
