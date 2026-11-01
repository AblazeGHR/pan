# R1 退场盘点：旧入口 / 旧宿主落码核对

日期：2026-10-25。工作树 `D:/project/pan-worktrees/job-r1-inventory-20261025`；分支 `r1/inventory-20261025`；基线 `e8cd8c1b`（merge `cc5b616e`）。

**交付性质：只读盘点。** 本轮未修改产品代码、未改配置、未启停服务、未触碰 8767/8768、未运行测试或构建、未读写任何 profile 数据目录；只新增本文档。行号是基线上的快照，会随代码漂移；定位以 `文件:符号` 为准，行号仅用于本轮复核。

依据文档：[终局模型 §1/§6/§12](JOB_FINAL_MODEL_20261025.md)、[Phase 1 矩阵](IMPLEMENTATION_MATRIX_20261025.md)、[Phase 4 兼容矩阵](PHASE4_COMPAT_MATRIX_20261025.md)、[Phase 4b 完成矩阵](PHASE4B_COMPLETION_MATRIX_20261025.md)、[Phase 5 E2E](PHASE5_E2E_REPORT_20261025.md)。本文不重述终局设计，只核对代码现状与退场路径。

术语首次出现：账本（ledger）、收据（receipt）、幂等（idempotent）、租约（lease）、准入（admission）。

## 0. 口径与状态定义

| 状态 | 含义 |
|---|---|
| `present` | 设计所列符号在基线代码中存在且语义相符 |
| `present_state_only` | 名字存在，但已从函数退化为状态容器/桩，不再是独立能力 |
| `already_gone` | 设计所列符号在基线不存在（本轮 **0 项**） |
| `UNLISTED` | 设计 §1 未列、但实际承担旧写/旧 owner/旧授权的符号（见 §4） |
| `needs_verification` | 只能靠代码推断，需真实 profile / 运行态 / 外部文档确认 |

阶段归属（R1–R6）取自终局模型 §6 的阶段出口与本轮代码依赖，不是新的决策。`R3` = 新建与客户端切换；`R4` = 简单迁移 / 旧 owner 清零；`R5` = 旧读控与兼容层退场；`R6` = 迁移运行时移除。

## 1. 核对总表

| 编号 | 设计条目（简） | 状态 | 关键落点（基线行号） | 删除阶段 |
|---|---|---|---|---|
| E01 | `/api/background-jobs` 六个 handler | `present` | `packages/web/server.py:api_background_job_start` 10928 / `_list` 10960 / `_get` 10978 / `_cancel` 10988 / `_update` 11070 / `_retry` 11115 | R3（写）/ R5（读） |
| E02 | `/api/session-message-jobs` | `present` | `packages/web/server.py:api_session_message_job_start` 11012 | R3 |
| E03 | `_legacy_mcp_authority` / `_legacy_action_job` | `present` | `packages/web/server.py` 10896 / 10910 | R3（新准入）/ R5（全删） |
| E04 | `/api/jobs` 混合 facade | `present` | `packages/jobs/api.py:list_jobs` 131 / `create_job` 495 / `job_kinds` 711 / `next_preview` 773 / `get_job` 814 / `pin_job` 827 / `patch_job` 848 / `delete_job` 1121 / `job_runs` 1159 / `run_job_now` 1174 | R3（C/U/X/R）/ R5（全 router） |
| E05 | 时间片段模板 API + store | `present` | `packages/jobs/api.py:list_templates` 747 / `create_template` 753 / `delete_template` 765；`packages/jobs/templates.py:55/60/106` | R3 |
| E06 | `/api/scheduler/*` 全套 | `present` | `packages/scheduler/api.py:list_tasks` 298 / `create_task` 309 / `get_task` 362 / `switch_owner` 374 / `update_task` 386 / `delete_task` 447 / `pause_task` 470 / `resume_task` 485 / `run_task_now` 500 / `task_runs` 525 / `next_preview` 539 / `engine_status` 556 | R3（写）/ R4（owner-switch 收口）/ R5（router） |
| E07 | background MCP 工具组 | `present` | `packages/mcp/server.py:agent_background_start` 2196 / `_get` 2372 / `_list` 2392 / `_cancel` 2415 / `_retry` 2430 | R3（写）/ R5（读） |
| E08 | message/broadcast MCP 工具组 | `present` | `packages/mcp/server.py:agent_message_job_create` 2241 / `_get` 2287 / `_list` 2310 / `_update` 2332 / `_cancel` 2360 | R3（写）/ R5（读） |
| E09 | scheduler MCP 九工具 | `present` | `packages/mcp/server.py:scheduler_create` 2499 / `_list` 2576 / `_get` 2605 / `_update` 2627 / `_delete` 2674 / `_pause` 2688 / `_resume` 2705 / `_run_now` 2719 / `_runs` 2736 | R3（写）/ R5（读） |
| E10 | `/api/main/restart`、`/exit` | `present` | `packages/web/server.py:api_main_restart_status` 4006 / `api_main_restart` 4012 / `api_main_exit_status` 4340 / `api_main_exit` 4346 | R3（POST）/ R5（status） |
| E11 | JobsView legacy tab / 表单 / 详情 | `present` | `packages/web/src/views/JobsView.tsx:65`（`Tab` 含 `legacy`）/ `handleLegacyCreate` 856 / `handleUpdate` 866 / tab 按钮 1046；`components/jobs/NewJobForm.tsx:60`（`CREATABLE_KINDS`）/ `JobDetailDrawer.tsx:351` / `LegacyJobDetails.tsx:7` | R3（新建）/ R5（读与详情） |
| E12 | 旧 Scheduler transport | `present` | `packages/web/src/services/api.ts:fetchScheduledTasks` 2419 / `createScheduledTask` 2425 / `updateScheduledTask` 2436 / `deleteScheduledTask` 2448 / `pauseScheduledTask` 2457 / `resumeScheduledTask` 2466 / `runScheduledTaskNow` 2475 / `fetchTaskRuns` 2484 / `fetchSchedulerNext` 2493 / `fetchSchedulerStatus` 2504 | R3（调用方迁移）/ R5（删函数） |
| E13 | 旧 kind 创建/取消/重试 | `present` | `api.ts:fetchJobKinds` 2612 / `createJob` 2627 / `patchJob` 2638 / `runJobNow` 2728 / `cancelJob` 2774 / `fetchLegacyJob` 2780 / `retryLegacyJob` 2787；保留项 `fetchJobs` 2602 / `fetchJob` 2619 / `deleteJob` 2710 / `fetchJobRuns` 2719 | R3（写）/ R5（facade 去解析） |
| E14 | Settings/CLI/console/脚本 直连旧 HTTP | `present` | `api.ts:fetchMainRestartStatus` 2027 / `restartMainService` 2031 / `fetchMainExitStatus` 2042 / `exitMainService` 2046；`packages/core/cli.py:lifecycle` 98（动态拼接 101、直读旧 JSON 106）；`packages/core/console_exit.py:ConsoleExit` 12 / `start_console_exit` 67；`packages/core/host_console.py:HostConsole.start_input` 136；`scripts/restart.bat`、`scripts/stop.bat` 整文件 | R3（调用迁移）/ R5（删字面量与读文件） |
| E15 | 独立服务第二套 Job 生命周期 | `present` | `packages/jobs/independent_service.py:JobStore.create` 117 / `request_cancel` 234 / `retry` 245 / `events` 255 / `ack` 260 / `Handler._run` 350；`packages/jobs/pan_bridge.py:request` 41 / `recover_events` 61 / `public_job` 52 | R4（第二账本）/ R5（余量） |
| S01 | 旧创建/更新/重试 + 旧 JSON 事实源 | `present` | `packages/core/background_jobs.py:start` 800 / `start_message` 1139 / `start_broadcast` 1176 / `update_message` 1208 / `cancel_message` 1263 / `retry` 3139；`get` 281 / `list_jobs` 338 / `_create` 258 / `_update` 268 / `_write_job` 92 / `_job_path` 66 / `_normalize_job` 218 | R4（写）/ R5（宿主） |
| S02 | 旧时间认领与旧 api 分发 | `present` | 同文件 `run_due_message_jobs` 1275 / `run_due_scheduled_tasks` 2936 / `_fire_scheduled_entry` 2810 / `_run_job_action` 2219 / `_execute_job_action` 2231 / `_redeliver_undelivered` 2615 / `_advance_paused_entries` 2740 / `_repair_missing_next_fire` 2755 | R4 |
| S03 | hooks / runs JSONL / 进程对账 | `present` | 同文件 `register_scheduled_tasks` 1477、`_scheduled_task_hooks` 1467（**是 dict，不是函数**）/ `append_run_record` 2106 / `list_run_records` 2139 / `_append_task_run` 2480 / `_append_run_record_once` 2517 / `_reconcile_scheduled_processes` 2531 | R4（hooks/写）/ R5（余量） |
| S04 | task↔Job 投影、旧 task 导入 | `present` | `packages/scheduler/store.py:_job_from_payload` 283 / `_task_from_job` 256 / `_job_for_task` 346 / `create_task` 378 / `update_task` 489 / `delete_task` 624 / `replace_task_schedule` 633 / `set_task_misfire_policy` 678 / `migrate_legacy_tasks` 742 / `_job_from_legacy_task` 775 / `_merge_legacy_runs` 835 / `legacy_root` 73 | R4（运行时 loader）/ R6（迁移实现） |
| S05 | 调度 shim、tick、启停 | `present_state_only` | `packages/scheduler/engine.py:_ensure_registered` 105 / `start_loop` 122 / `stop_loop` 138（桩）/ `drain` 143（桩，仅测试调用）/ `status` 148 / `tick_once` 159 / `run_now` 171 / `preview_next` 263 | R2（隐式迁移与桩）/ R4（旧 pass 接线） |
| S06 | 兼容投影与 owner switch | `present` | `packages/jobs/legacy_compat.py:config_for` 42 / `_schedule` 24 / `request_key` 81 / `legacy_view` 94 / `is_legacy_action_job` 113；`packages/scheduler/legacy_bridge.py:LegacySchedulerBridge` 21 / `_switch` 133 / `_entry` 71 / `project_cursor` 181 | R3/R6（switch）/ R5（整文件） |
| S07 | 旧 kind 系统 Action 工厂 | `present` | `packages/jobs/system_actions.py:KINDS` 16 / `definition` 19 / `arguments` 75 / `prepare` 97 / `finish` 137 / `sync` 151 / `invoke` 161 / `settle` 199 / `execute` 259 | R4（definition）/ R5（余量） |
| S08 | Runtime 旧 builder 与第二 Event 匹配 | `present` | `packages/jobs/action_runtime.py:config_job` 47 / `Runtime.create_legacy` 200 / `event_targets` 609 / `queue_event` 616 / `tick` 694 / `consume_entry` 582 / `skip_misfire` 771 / `publish_events` 447；`packages/jobs/unified.py:project` 64 / `projected_schedule` 8 / `_unknown_legacy_fields` 59 | R2（event/inline schedule）/ R5（builder+投影） |
| S09 | 直接 Trigger Action 执行宿主 | `present` | `packages/automation/engine.py:Engine.drain` 240 / `run_attempt` 330 / `match` 126；`packages/automation/store.py:Ledger.claim` 181 / `finish` 255 / `prepare_action` 270 / `confirm_uncertain` 299 / `sync_attempt` 390；`packages/automation/chain_engine.py:run_chain` 9 | **R1** |
| S10 | 旧 lifecycle kind writer + supervisor | `present` | `packages/core/background_jobs.py:create_service_job` 3193 / `transition_service_job` 3242 / `find_service_job` 3301 / `get_active_service_job` 3177；`packages/core/main_lifecycle.py:run_supervisor` 148 / `run_exit_supervisor` 115 | R4（writer）/ supervisor 保留 |
| S11 | 旧通知生产与恢复 | `present` | `background_jobs.py:recover_notifications` 3625 / `_broadcast_independent_terminals` 3567 / `_terminal_broadcast_candidate` 3504 / `_recovery_loop` 3707 / `start_recovery_loop` 3719 / `stop_recovery_loop` 3728；`pan_bridge.recover_events` 61 | R4/R5 |
| S12 | 第二 Job store / runner wire | `present` | `packages/jobs/independent_service.py:JobStore` 72；`packages/jobs/independent_runner.py:run` 252 / `main` 339；`packages/core/background_runner.py:main` 14 | R4 |
| S13 | handoff / 删除 / pin / retention | `present` | `background_jobs.py:retarget_session_jobs` 546 / `delete_job` 1507 / `cleanup_jobs_by_statuses` 1654 / `cleanup_job_log_files` 1831 / `run_completed_job_retention` 1953；`packages/jobs/api.py:pin_job` 827 / `get_completed_retention_settings` 211 / `put_completed_retention_settings` 217 | R5（能力保留、旧 schema 处理删除） |
| S14 | 进程身份与清理安全算法 | `present` | `packages/jobs/process_cleanup.py:terminate_owned_jobs` 111 / `_capture_process` 16 / `_verify_completed_proof` 80；`packages/jobs/owned_process.py:job_startup_info` 11；`packages/jobs/cleanup.py:CleanupStack` 19；`background_jobs.py:_owns_process` 3061 / `_kill_tree` 3123 / `runner_spawn` 3340 / `runner_update` 3379 / `reconcile_running` 3422 | 安全原语先迁（R1/R5），旧宿主 R5 |

结论：**设计 §1 的 29 条编号全部仍存在于基线，`already_gone` = 0。** 这说明当前代码处在"设计已定、实现未退场"的阶段，与 §0 的"待实现"表述一致。`S05` 的 `drain/stop_loop` 已是兼容桩，按 `present_state_only` 处理。

## 2. E 项逐条：入口、旧写条件、调用方、证据

统计口径：一个 handler 只要会创建/修改旧形状记录、或把控制权交给旧 owner，就计为"写"。GET 详情/列表/状态计为"读"。

### 2.1 HTTP（E01–E06、E10）

| 编号 | 创建 / 读取 / 控制路径 | 仍写旧记录或旧 owner 的条件 | 调用方 / 测试 | 可观测证据 |
|---|---|---|---|---|
| E01 | `POST /api/background-jobs` → `api_background_job_start` 10928；`GET` 列表/详情 10959/10977；`POST /{id}/cancel` 10987；`PATCH /{id}` 11069；`POST /{id}/retry` 11114 | **分支敏感**：请求带私有 IPC 标记（`_legacy_mcp_authority` 非 None）时走 `_legacy_action_job` → `Runtime.create_legacy`（新 owner，但记录仍是 `legacyCompat` 形状）；否则直接 `background_jobs.start` / `cancel` / `update_message` / `retry`，写入 `data/background_jobs/jobs/*.json` 并由 `background_runner` 子进程持有 | MCP `agent_background_*`（server.py 2237/2368/2426/2441）；`tests/test_background_job_api.py`、`tests/test_legacy_mcp_compat.py`、`tests/test_job_delete_safety.py` | WS `job.created`；registry JSON 的 `kind=background-process`；`runs.jsonl`；`jobs/.terminal-broadcast-ledger.json` |
| E02 | `POST /api/session-message-jobs` 11012 | 同一 IPC 分支：trusted MCP → `Runtime.create_legacy(session-message / session-broadcast)`；普通 HTTP → `start_message` 1139 / `start_broadcast` 1176，写旧 kind 记录 | MCP `agent_message_job_create` 2241；`tests/test_jobs_api.py`、`tests/test_legacy_mcp_compat.py` | registry `kind=session-message/session-broadcast`；`lastDelivery`；WS `job.partial_failed`（background_jobs.py:1404） |
| E03 | 不是路由，是分流器：`_legacy_mcp_authority` 10896 返回 `{'type':'legacy-mcp'}`；`_legacy_action_job` 10910 调 `Runtime.create_legacy` | 该伪身份是 E01/E02/E04 部分分支写"新 owner"的唯一凭证；删除前必须先有通用 principal/grant，否则 trusted MCP 创建会同时失去授权与落点 | 被 server.py 10934/10964/10994/11051/11058/11076/11130 调用；`automation/authorization.py:object_path` 第 20 行仍把 `/api/background-jobs`、`/api/session-message-jobs` 列为可转发对象（**UNLISTED U01**） | `packages/automation/authorization.py:delegated`（scope marker）；gateway catalog 仍列旧工具名（`packages/mcp/manifest.json:29/38/39`） |
| E04 | `packages/jobs/api.py` router 41：`GET ""` 129、`POST ""` 493、`GET /kinds` 710、`GET /next` 772、`GET /{id}` 813、`PUT /{id}/pin` 826、`PATCH /{id}` 847、`DELETE /{id}` 1120、`GET /{id}/runs` 1158、`POST /{id}/run-now` 1173 | `create_job` 495：`kind=scheduled-task` → `scheduler_store.create_task`（写 registry）；`session-message/broadcast` → `background_jobs.start_message/start_broadcast`；`main-lifecycle` 拒绝。`patch_job` 848 按 kind 分支：`legacySchedulerCompat` 走 `bridge`，其余走 `background_jobs.update_job_field` 改 registry JSON。`_view` 64 强制附加 `unified.project` 旧投影 | React `JobsView`（api.ts 2602/2619/2627/2638/2710/2719）；`tests/test_jobs_api.py`、`tests/test_jobs_api_retention.py`、`tests/test_job_delete_safety.py` | 三套 router 同挂 `/api/jobs`（server.py 11153 `action_api`、11154 `jobs_api`）——E04 说"单一 canonical router"目前**尚未成立**（**UNLISTED U15**）；WS `job.created/updated` |
| E05 | `packages/jobs/api.py:746/752/764`；store `packages/jobs/templates.py:39`（`_templates_path`）、`55/60/106` | 写 `data/background_jobs/schedule_templates.json`；内置 4 条 `BUILTIN_TEMPLATES` 与用户自定义同表；只承载时间片段，不含完整 Job 配置 | React `JobTemplateEditor`/`JobsView` 快捷创建；`tests/test_jobs_api.py` | `schedule_templates.json`；`GET /api/jobs/templates` 返回 `builtin=true` 项 |
| E06 | `packages/scheduler/api.py` router 28：12 个 handler（298–556） | `create_task` 309 → `scheduler_store.create_task` → `background_jobs._create`（`kind=scheduled-task`）；`update_task` 386 / `delete_task` 447 / `pause_task` 470 / `resume_task` 485 直接改 registry；`run_task_now` 500 → `engine.run_now` 171 → **`background_jobs._run_job_action`**（旧 dispatcher，活路径）；`switch_owner` 374 → `LegacySchedulerBridge.switch` 129（迁移期双写：保留旧 task，另建 Source/Trigger/Job） | MCP `scheduler_*`（2483–2748）；React `scheduleStore.ts` 9–16；`tests/test_scheduler_api.py`、`tests/test_scheduler_integration.py`、`tests/test_legacy_job_transition_http.py` | `scheduler.task.created/fired`；registry `kind=scheduled-task`；`legacySchedulerCompat` 字段；`registry.json`（automation Ledger） |
| E10 | `GET /api/main/restart/status` 4005；`POST /api/main/restart` 4011；`GET /api/main/exit/status` 4339；`POST /api/main/exit` 4345 | POST 经 `_schedule_main_restart` → `background_jobs.create_service_job`（4076）；exit 分支 4400 同构。写 `kind=main-lifecycle` 记录，并拉起 detached `python -m packages.core.main_lifecycle --supervise`（server.py:916）。**旧 POST 本身即旧 Job 构建代码** | React `AppSettingsModal.tsx` 36–39/1135/1178；`scripts/restart.bat`、`stop.bat`；CLI `pan restart|exit`；`tests/test_lifecycle_shortcuts.py`、`tests/test_platform_api.py` | registry `kind=main-lifecycle`、`phase`；supervisor 进程；`launcher` state |

### 2.2 MCP（E07–E09）

三者都是**薄转发**：`packages/mcp/server.py` 用 `_api(...)` 调 HTTP，因此没有独立写路径，但有**独立的导出面**（`@mcp.tool()`，共 84 个工具中的 19 个）。删除顺序必须先 R3 撤工具导出、再 R5 删 HTTP，否则 MCP 客户端会拿到"工具存在但后端 404"。

| 编号 | 工具 → HTTP | 旧写条件 | 调用方 / 测试 | 证据 |
|---|---|---|---|---|
| E07 | `agent_background_start` 2196 → `POST /api/background-jobs`；`_cancel` 2415 → `/cancel`；`_retry` 2430 → `/retry`；`_get` 2372、`_list` 2392 → `GET` | 继承 E01 分支：MCP transport 触发 `legacy-mcp` 分支 | Agent 侧真实调用；`tests/test_legacy_mcp_compat.py`、`tests/test_mcp_*.py` | `packages/mcp/manifest.json:29/38` 仍在系统提示中宣传这 5 个工具名 |
| E08 | `agent_message_job_create` 2241 → `POST /api/session-message-jobs`；`_update` 2332 → `PATCH`；`_cancel` 2360 → `/cancel` | 继承 E02 | 同上 | manifest.json:38 |
| E09 | 9 个 `scheduler_*` 2499–2736 → `GET/POST/PATCH/DELETE /api/scheduler/tasks*` | 继承 E06；`scheduler_run_now` 直接触发旧 dispatcher | 同上 | manifest.json:39；`scheduler/tasks` 路径常量 2448 |

### 2.3 UI / CLI（E11–E14）

| 编号 | 路径 | 旧写/旧读条件 | 调用方 | 证据 |
|---|---|---|---|---|
| E11 | `JobsView.tsx`：`Tab` 含 `legacy` 65、`legacyCreateOpen` 470、`handleLegacyCreate` 856、`handleUpdate` 866、tab 按钮 1046–1049、New legacy Job 弹窗 1419；`NewJobForm.tsx` 60–64 的 4 个 kind、133 kind state；`JobDetailDrawer.tsx` 351 渲染 `LegacyJobDetails`、585 显示 "Legacy execution history" | 通过 E13 的 `createJob/patchJob/cancelJob/retryLegacyJob` 直接写旧记录；详情读 `fetchLegacyJob`（2780） | 仅 React 自身；`JobsView.bulk-search.test.tsx`、`JobDetailDrawer.test.tsx`、`NewJobForm.*.test.tsx`、`api.legacyJobs.test.ts` | DOM 上的 "Legacy Jobs / 旧 Job" tab；`job.unified.compatibility` 分支 |
| E12 | `api.ts` 10 个函数 2419–2504；消费方 `stores/scheduleStore.ts:9-16/91/115` | 全部指向 `/api/scheduler/*`，读 `ScheduledTask` 旧类型 | `scheduleStore` → `AutomationView`/`JobsView` 面板 | 网络面板 `/api/scheduler/tasks` |
| E13 | `api.ts` 2612/2627/2638/2728/2774/2780/2787 | `createJob` 按 `kind` 走 E04；`cancelJob`/`retryLegacyJob` 走 E01；`fetchLegacyJob` 读 E01 详情 | `JobsView.tsx:38/895`、`LegacyJobDetails.tsx:3/15/22` | `api.legacyJobs.test.ts` 明确断言"不回退到新执行" |
| E14 | `AppSettingsModal.tsx` 36–39；`cli.py:lifecycle` 98–111（`f"/api/main/{operation}"` 101、`root/"data/background_jobs/jobs"/(job_id+".json")` 106）；`console_exit.py:12/67`；`host_console.py:136/146`；`restart.bat`/`stop.bat` 整文件 | CLI **直接读旧 Job JSON** 判断终态；console 走 `POST /api/main/exit`；脚本硬编码 `/api/main/restart|exit` | 用户操作 + `pan` CLI；`tests/test_pan_cli.py`、`tests/test_lifecycle_shortcuts.py` | CLI 输出依赖 `data/background_jobs/jobs/*.json` 的 `operation/oldPid/phase`；脚本 curl 状态码 |

### 2.4 独立服务（E15）

| 项 | 内容 |
|---|---|
| 协议 | `packages/jobs/independent_service.py:Handler._run` 350：`POST /jobs` 361、`GET /jobs` 362、`GET /jobs/{id}` 366、`POST /jobs/{id}/cancel` 371、`POST /jobs/{id}/retry` 372、`GET /events` 363、`POST /events/{id}/ack` 364 |
| 第二套身份 | `JobStore.create` 117 自行分配 job id（**不接收 canonical admission**）；`retry` 245 复用 `create` → **新建 Job**，与已定 D3"Retry = 原 Job 原 Run 新 Attempt"直接冲突 |
| 存储与 owner | `<root>/jobs.sqlite3`（WAL）+ `logs/`；`ServiceLock`（33）供 `action_runtime.Runtime` 复用为 profile 级互斥锁 |
| 桥接 | `background_jobs.get` 281–290 在本地未命中时调 `pan_bridge.request("get")` → 独立服务读；`recover_notifications` 3625 调 `recover_events` 拉 outbox 并在持久入队后 ack |
| 旧写条件 | 仅当 `config.independent_jobs.enabled=true` 且 endpoint 为 loopback 时启用（`pan_bridge.configured_client` 17）。工作树无 `config.json`，**是否启用 `needs_verification`** |
| 调用方 / 测试 | `tests/test_independent_job_service.py` 等 8 个测试文件引用 `independent_service` |
| 证据 | `jobs.sqlite3` 的 `jobs`/`outbox` 表；`executionOwner=independent-job-service`（`public_job` 52）；`notification_state` |

## 3. S 项逐条：写路径与依赖

| 编号 | 当前职责 | 写入/副作用点 | 依赖方（谁在用） | 删除阶段 |
|---|---|---|---|---|
| S01 | 旧 kind 记录的事实源 | `_create` 258 / `_update` 268 / `_write_job` 92 / `_atomic_write` 72 全部落在 `data/background_jobs/jobs/*.json` | **15 个产品模块** import（见 §6.3）；新 `Runtime` 也用它 | R4 写 / R5 宿主 |
| S02 | 时间认领与旧 api 分发 | `run_due_*` 改状态、`_fire_scheduled_entry` 写 `nextFireAt`/`runCount`/`undeliveredFires`；`_execute_job_action` 末尾未知 api **回落 assign**（2373 注释与代码） | `_recovery_loop` 3710；`scheduler/engine.py:tick_once` 162；`scheduler/engine.py:run_now` 195 | R4 |
| S03 | hooks 注册、`runs.jsonl`、父子进程对账 | `_scheduled_task_hooks` 1467（dict）由 `register_scheduled_tasks` 写入；`append_run_record` 写 `runs.jsonl` | `web/server.py:266`、`scheduler/engine.py:111` | R4/R5 |
| S04 | task↔Job 投影 + 旧 task 导入 | `create_task` 485 `background_jobs._create`；`migrate_legacy_tasks` 742 在 `start_loop` 内**每次启动隐式执行**（engine.py:126–131） | `action_runtime.config_job` 48（每次新建 Action Job 都调用 `_job_from_payload`）、`automation/quota_policy`、`automation/service` | R4 运行时 / R6 迁移实现 |
| S05 | 调度 shim | `_ensure_registered` 只做注册；`start_loop` 触发迁移；`stop_loop`/`drain` 空实现 | `web/server.py:274/377`；测试直调 `drain` | R2/R4 |
| S06 | 兼容投影 / owner switch | `LegacySchedulerBridge._switch` 133 建 Source+Trigger+Job 并写 `legacySchedulerCompat` | `automation/service.py`（bridge）、`action_runtime.create_legacy` 202（`config_for`）、`web/server.py:10911`（`legacy_view`） | R5（整文件） |
| S07 | 5 个旧 kind 的系统 Action 定义 | `definition` 19 的 `scheduled-task` schema 内嵌 `action.api` 枚举；`prepare/finish/sync/invoke/settle` 写 attempt snapshot 与终态 | `install` 由 `Runtime._start` 124 调用 | R4 |
| S08 | 新 Job 仍依赖旧 builder / 第二 Event 匹配 | `config_job` 47 调 `_job_from_payload` 并保留 inline `schedule`；`queue_event` 616 写 `eventReceipts`；`consume_entry` 582 写 entry 游标 | `automation/service.py:160-161/196` 把同一 Event 同时喂给 `Engine.match` 与 `Runtime.event_targets` | R2/R5 |
| S09 | **直接 Trigger→Action 第二执行宿主** | `Engine.run_attempt` 330 → `execute_dynamic`；`Ledger.claim/finish/confirm_uncertain` 写 `registry.json` 的 run 状态 | `automation/service.py:35` 用 `execute_dynamic` 构建 Engine | **R1** |
| S10 | lifecycle Job writer + 外部 supervisor | `create_service_job` 3193 写 `kind=main-lifecycle`；`transition_service_job` 3242 改 `phase` | `web/server.py:4076/4400`、`main_lifecycle.py:67/100/134/173/187` | R4（writer）/ supervisor 保留 |
| S11 | 通知生产与恢复 | `recover_notifications` 3625 发送终态便条；`_broadcast_independent_terminals` 3567 写 `.terminal-broadcast-ledger.json` | `_recovery_loop` 3710；`pan_bridge` outbox | R4/R5 |
| S12 | 第二 Job store / runner wire | `jobs.sqlite3` 行状态、`runner_pid/runner_filetime/task_pid` | `pan_bridge`、`independent_client` | R4 |
| S13 | handoff/删除/保留 | `retarget_session_jobs` 546 改所有记录 target；`delete_job` 1507 处理进程后删文件；`cleanup_*` 删文件与日志；`pins.json` | `jobs/api.py` PATCH/DELETE/PUT pin；`_recovery_loop` 的 retention 分支 | R5（能力保留） |
| S14 | 进程身份与清理安全算法 | `terminate_owned_jobs` 111 / `_verify_completed_proof` 80 / `_owns_process` 3061 / `_kill_tree` 3123 / `runner_spawn` 3340 | `background_jobs.delete_job`、`reconcile_running`、`independent_runner`、`jobs/desktop.py` | 原语先迁（R1/R5），宿主 R5 |

## 4. UNLISTED：设计 §1 未列但仍在承载旧语义

| 编号 | 符号:行 | 为何必须登记 | 建议处置 |
|---|---|---|---|
| U01 | `packages/automation/authorization.py:object_path` 15（正则 20） | 旧 `/api/background-jobs*`、`/api/session-message-jobs` 仍是 IPC bridge 的合法转发目标；这是"旧路径仍可用"的授权侧证据 | 随 E01/E02 一起删；先迁到通用 principal |
| U02 | `packages/core/background_jobs.py:cancel` 3075 | 旧取消写路径（改 status、必要时杀树），S01 未列 | 并入 S01 退场 |
| U03 | `background_jobs.py:start_action_process` 855 | 旧 Action 子进程创建（写 `scheduledParentJobId`），S01/S02/S14 均未列 | 并入 S02 |
| U04 | `background_jobs.py:start_scheduled_process` 878 | 每 fire 一个 hidden child Job；S02 只列了对账函数 | 并入 S02 |
| U05 | `background_jobs.py:claim_hidden_runner` 3309 | 旧 hidden runner 认领（与 supervisor/终端生命周期相关） | 迁 executor 协议后删 |
| U06 | `packages/jobs/system_actions.py:validate` 53 / `install` 59 / `recovery_ready` 210 / `notify` 221 / `wait_process` 237 | 旧 kind 工厂的校验、安装、通知与等待逻辑；S07 只列了 8 个符号 | 随 S07 |
| U07 | `packages/jobs/api.py:_normalize_scheduled_action` 283 / `_validate_scheduled_specs` 366 / `_validate_message_schedule` 439 | 旧 kind 的 payload 校验器，E04 只列了 handler | 随 E04 |
| U08 | `packages/scheduler/store.py:_normalize_schedule` 118 / `_entry_from_schedule` 170 / `_spec_to_entry` 199 / `compute_next_fire` 239 | 旧时间归一化/计算器；R2 要求"迁 pure calculator 后删原副本" | R2/R4 |
| U09 | `scheduler/store.py:append_run` 705 / `list_runs` 711 | 旧 run log 写入/读取（`runs.jsonl` 500 行滚动） | 随 S03/S04 |
| U10 | `packages/scheduler/cron.py` 整文件 | 再导出垫片，PLAN 明示 P4 退役 | R5 |
| U11 | `packages/jobs/templates.py:BUILTIN_TEMPLATES` 30 附近 | 内置 4 条日程模板作为"产品内容"存在，E05 只说了"内置快捷项成为普通完整 Template" | R3（导入为完整 Template 后删） |
| U12 | `packages/jobs/pins.py:read` 5 / `set_pin` 19 / `snapshot` 37 | pin 元数据写 `data/background_jobs/pins.json`；S13 只列了 API 层 | 保留能力，迁 canonical metadata |
| U13 | `packages/web/server.py:_snapshot_main_lifecycle_options` 732 / `_launch_main_restart_supervisor` 908 / `_watch_main_restart` 899 | E10 的旧 Job 构建与 supervisor 拉起细节，未在 §1 出现 | 随 E10/S10 |
| U14 | `packages/mcp/server.py:job_config_drafts` 3314 | **不是 legacy**，是新 Action Job/Template 的 MCP 工具；登记以防 denylist 误杀 | 保留，纳入 canonical tools |
| U15 | `packages/jobs/action_api.py` 整文件（`/api/jobs/action-jobs*`、`/api/jobs/job-templates*`） | 第三个挂在 `/api/jobs` 前缀的 router；E04 要求"单一 canonical router"，现状是三个 router 并存 | 收敛为唯一 canonical router（R3/R5） |
| U16 | `packages/mcp/manifest.json:29/38/39` | 系统提示词仍在向 Agent 宣传 19 个旧工具名；删除工具导出后此处会变成误导 | R3 同步更新 |

## 5. 删除顺序与风险

### 5.1 可以立即删（无产品依赖、或纯桩）

| 项 | 依据 | 注意 |
|---|---|---|
| `scheduler/engine.py:drain` 143 | 产品零调用，仅 `tests/test_scheduler_engine.py:138`、`test_scheduler_integration.py:60` 直调 | 删时同步改测试；`stop_loop` 138 在 `server.py:377` 关机路径仍被调用，**不能一起删** |
| E05 的"内置模板 → 完整 Template"导入 | 只影响 UI 快捷项 | 必须先有 `job-templates` 的等价内容，否则用户快捷创建消失 |
| S04 `migrate_legacy_tasks` 的启动隐式调用（engine.py:126–131） | 迁移已在 Phase 5 证据中跑过 | 关闭前要确认所有 profile 已迁移；否则旧 task 永不再导入 |

除上述外，**本轮没有任何 E/S 编号可以单独立即删除**：每一条都还有活调用方或活数据。

### 5.2 必须先等接管（硬依赖）

| 被删项 | 阻塞它的新能力 | 证据 |
|---|---|---|
| S01 / S04 store | 新 canonical store（WP02）。**新 Action Job 就写在旧 JSON registry 里**：`action_runtime.py:169` `jobs._create`、277/361/387 `jobs._write_job`、72 `Runtime.root`；`scheduler/store.py:63 data_root()` 与 `background_jobs.DEFAULT_ROOT` 同根 | 删 S01 = 删新 Job 的存储层 |
| S04 `_job_from_payload` | 新 Job builder。`action_runtime.config_job` 48 每次创建都调用它 | 删它 = 新 Job 无法创建 |
| S02 两个 pass | R2 Scheduler Event Creator + R4 cohort 迁移。当前 `_recovery_loop`（3707）每秒驱动 message/scheduled 两条 pass | 删它 = 定时消息与定时任务全停 |
| S07 `system_scheduled_task` | R4：需 Creator 发 Event + Trigger admission 接管 | 现在 `scheduler_create` 直接落这个 kind |
| S09 执行宿主 | R1/WP03：直接 Trigger 必须先生成受管 Job | `automation/service.py:35` 仍以 `execute_dynamic` 构建 Engine |
| S14 安全算法 | WP05：PID+birth、进程树、Job Object、cleanup proof 必须先有 platform backend 等价实现 | 先删会把"安全取消/清理"一起删掉 |
| E15 / S12 | WP02/WP05：canonical Attempt lease + executor 协议 | `JobStore.create` 117 自行发号；`retry` 245 新建 Job，违反 D3 |
| E10 / S10 | WP05/WP06：受保护系统 Action + 外部 supervisor + 跨停机 operation receipt | `main_lifecycle.py` 仍靠 `create_service_job` 记录 phase |
| E14 CLI | WP06：可跨服务停止读取的 operation receipt | `cli.py:106` 直接读旧 JSON 文件 |
| E03 | 通用 principal/grant（D5 待实现项） | 现依赖 `{'type':'legacy-mcp'}` 伪身份 |

### 5.3 活资源风险（删除前必须排空或证明归属）

| 资源 | 载体 | 风险 | 现状证据 |
|---|---|---|---|
| 运行中的 background-process | 独立 `python -m packages.core.background_runner` 子进程 + `runner_pid`/`runner_filetime` | 删 registry 或 runner 入口会让进程失去 owner，无法再取消 | `background_jobs.py:746/754`、`background_runner.py:14` |
| 定时 shell 子进程 | hidden child Job（`scheduledParentJobId`） | 父 Job 删除后 child 成孤儿；`_reconcile_scheduled_processes` 是唯一对账 | `start_scheduled_process` 878、`_reconcile_scheduled_processes` 2531 |
| 独立 Job 服务 | 独立 HTTP 服务 + `jobs.sqlite3` + 自身 runner | Pan 停机后仍在执行；删除 Pan 侧 `pan_bridge` 会让 outbox 无人 ack | `independent_service.py:382 serve`、`pan_bridge.recover_events` 61 |
| main lifecycle supervisor | detached `packages.core.main_lifecycle --supervise` | restart/exit 进行中删除 E10/E14 会留下不可查询的半程状态 | `server.py:916`、`main_lifecycle.py:202` |
| 未 ack 通知 / terminal ledger | `.terminal-broadcast-ledger.json`、独立服务 `outbox` | 先删 producer 会永久丢通知 | `background_jobs.py:3547/3567`、`independent_service.ack` 260 |
| in-process Automation runner | `asyncio.Task`（`automation-attempt-*`） | 删 `Engine.drain` 前需等在途 Attempt 结束或转 uncertain | `automation/engine.py:298` |
| uncertain / cleanup_pending Job | registry JSON status | 删除文件前必须能证明进程树已结束 | `S14 _verify_completed_proof` 80 |

**无法静态判定**：当前 profile 是否真有上述活资源。工作树没有 `data/`，本轮不接触真实 profile → `needs_verification`。

### 5.4 建议删除顺序

1. **R1（先做）**：删 S09 执行/重试宿主与 `chain_engine.run_chain`；`Ledger` 只保留定义与 admission。此时 E 全部保留。
2. **R3（客户端切换）**：按 E07/E08/E09 → E01/E02 → E04/E05/E06 → E10 → E11/E12/E13/E14 的顺序撤写入口；每个 E 先改调用方再删 handler，且先更新 `mcp/manifest.json`（U16）与 `object_path`（U01），否则 Agent 会拿到失效工具。
3. **R4（owner 清零）**：先迁 S04 数据，再删 S02/S03 hooks/S07/S10 writer/S12/E15/S05 接线。顺序不能反：`_job_from_payload` 是新 Job 创建的前置。
4. **R5（读控/兼容）**：删剩余 E 读入口、S01/S06/S08/S11 heavy 宿主、E11 Legacy tab、E12/E13 旧 transport；S14 的安全原语必须在同阶段前完成迁移。
5. **R6**：删 S04 迁移实现、owner-switch、legacy loader/flag。

## 6. 统计

### 6.1 入口数量

| 层 | 总数 | 写 | 读 |
|---|---|---|---|
| HTTP legacy handlers（E01/E02/E04/E05/E06/E10） | 36 | 21 | 15 |
| MCP legacy tools（E07/E08/E09） | 19 | 12 | 7 |
| 独立服务 routes（E15） | 7 | 4 | 3 |
| **顶层入口合计** | **62** | **37** | **25** |

说明：12 个 MCP 写工具全部转发到 HTTP，**独立后端写入口是 21 个 HTTP handler**；62 是"客户端可达面"的数量，用于 R3/R5 的 route/tools 快照门禁。HTTP 计数含 `PUT /api/jobs/{id}/pin`（该 handler 同属 E04 路由面与 S13 能力，只计一次）。

非 HTTP 的旧写后端（服务层/Pass）另计 **41 个符号**：S01 7、S02 6、S03 4、S04 6（含 `migrate_legacy_tasks`）、S07 5、S08 2、S10 2、S11 2、S12 3、S13 5、S05 1（`run_now` dispatch）。

### 6.2 旧 store / Pass

**8 个存储**：

1. `data/background_jobs/jobs/*.json`（旧 registry；**同时承载新 Action Job**）
2. `data/background_jobs/runs.jsonl`（滚动 500 行）
3. `data/background_jobs/schedule_templates.json`
4. `data/background_jobs/pins.json`
5. `data/background_jobs/jobs/.terminal-broadcast-ledger.json`
6. `data/scheduler/tasks/*.json` + `data/scheduler/runs.jsonl`（只读迁移源）
7. `<independent root>/jobs.sqlite3`（第二 Job store + outbox）
8. `automation/registry.json`（Trigger/Run/Event receipt 账本）

**11 个扫描/迁移/对账 Pass**：

1. `_recovery_loop`（1s，background_jobs.py:3707）
2. `run_due_message_jobs`（1275）
3. `run_due_scheduled_tasks`（2936）
4. `recover_notifications`（3625）
5. `reconcile_running`（3422）
6. `_reconcile_scheduled_processes`（2531）
7. `run_completed_job_retention`（24h，1953）
8. `Engine._runner` / `Engine.drain`（automation/engine.py:209/240）
9. `Runtime.tick` / `recover` / `publish_events`（action_runtime.py:694/791/447）
10. `migrate_legacy_tasks`（启动时隐式，scheduler/store.py:742）
11. `JobStore.recover`（independent_service.py:269）

### 6.3 仍被产品代码 import 的旧模块（14 个）

| 模块 | 产品 importers | 代表调用点 |
|---|---|---|
| `packages.core.background_jobs` | **15** | `web/server.py`、`jobs/action_runtime.py`、`scheduler/engine.py`、`scheduler/store.py`、`automation/quota_policy.py` 等 |
| `packages.jobs.unified` | 4 | background_jobs、action_runtime:143、jobs/api:70、independent_service:230 |
| `packages.scheduler.store` | 3 | action_runtime:48、automation/service:307、automation/quota_policy |
| `packages.jobs.legacy_compat` | 2 | web/server:10911、action_runtime:202 |
| `packages.jobs.independent_runner` | 2 | jobs/desktop.py:50、jobs/restricted_token.py:183 |
| `packages.jobs.owned_process` | 2 | desktop、independent_runner |
| `packages.jobs.cleanup` | 2 | desktop、independent_runner |
| `packages.scheduler.legacy_bridge` | 1 | automation/service.py |
| `packages.scheduler.engine` | 1 | web/server.py:136 |
| `packages.jobs.independent_service` | 1 | action_runtime.py:72（`ServiceLock`） |
| `packages.jobs.templates` | 1 | jobs/api.py:38 |
| `packages.jobs.pan_bridge` | 1 | background_jobs.py:288（函数内延迟 import） |
| `packages.jobs.process_cleanup` | 1 | background_jobs.py |
| `packages.automation.chain_engine` | 1 | automation/engine.py:354（延迟 import） |

另有 1 个**子进程入口**（非 import）：`packages/core/main_lifecycle.py` 由 `web/server.py:916` 以 `-m` 拉起。

### 6.4 阶段归属

| 阶段 | E | S | 计数 |
|---|---|---|---|
| R1 | — | S09 | 1 |
| R3 | E01–E14 | — | 14 |
| R4 | E15 | S02、S03、S05、S07、S10、S12 | 1 + 6 |
| R5 | E01/E06/E07–E11/E14 的读与控制残余 | S01、S06、S08、S11、S13、S14 | 6 |
| R6 | — | S04（迁移实现 + owner-switch） | 1 |

合计 15 个 E + 14 个 S = 29，与 §1 一致。**R1 阶段可独立删掉的 E 编号为 0 个**；S09 只有执行/重试部分归 R1，`Ledger` 的定义与 admission 能力保留。其余 28 项分布在 R3–R6，其中 **12 项**（S01、S02、S04、S07、S09、S10、S12、S14、E03、E10、E14、E15）被 §5.2 的硬依赖直接阻塞。

## 7. Top 5 阻塞项

1. **旧 registry 就是新 Job 的存储层。** `action_runtime.py:169/277/361/387` 通过 `background_jobs._create/_write_job` 落 `data/background_jobs/jobs/*.json`，`scheduler/store.py:63 data_root()` 同根。S01/S04 的删除不是"删旧代码"，而是"换存储内核（WP02）"，必须整体迁移，不能逐函数删。
2. **`config_job` 每次创建都调用 `scheduler.store._job_from_payload`（action_runtime.py:48-52）。** S04 被新 Job 的创建主路径强依赖；先删 S04 会让 `/api/jobs/action-jobs` 全新功能 500。
3. **启动即接线：`web/server.py:266-267/274`。** `register_scheduled_tasks` + `start_recovery_loop` + `scheduler_engine.start_loop` 是当前**唯一**的时间驱动；R2 Creator 未接管前删 S02/S05 = 定时任务与延时消息全停。且 `start_loop` 内含隐式 `migrate_legacy_tasks`，删它要同时决定旧 task 的最终处置。
4. **`scheduler_run_now` 是活的旧 dispatcher 入口。** `scheduler/api.py:500 → engine.run_now:171 → background_jobs._run_job_action:2219`。只删 E06 路由（R3）不够——`engine.run_now` 仍可被测试或内部直调，且 R4 前它仍会写旧 `runCount/nextFireAt`。
5. **同一 Event 仍有两套执行宿主。** `automation/service.py:160-161` 把 Event 同时投给 `job_runtime.event_targets`（Action Job）与 `Engine.match`（直接 Trigger→Action），`service.py:35` 仍以 `execute_dynamic` 构建 Engine。S09 的删除必须由 WP03 先接管直接 Trigger，否则 D2-A 无法成立。

**次一级（第 6）**：E03 的 `legacy-mcp` 伪身份与 `authorization.object_path` 旧路径白名单（authorization.py:20）耦合，是"旧 URL 仍被授权层认可"的隐藏入口；删 E01/E02 前必须先落到通用 principal，否则 trusted MCP 创建会同时失去授权与落点。

## 8. 未验证项（`needs_verification`）

| 项 | 为什么无法本轮确认 |
|---|---|
| 真实 profile 的旧记录数量、`simpleMigrated/complexPreserved` 分布 | 工作树无 `data/`；本轮不接触真实 profile、不启停服务 |
| 是否存在 `status=running/restarting/uncertain` 的活 Job 与仍在运行的 runner/supervisor | 需要进程表与 registry 读权限，本轮不执行 |
| `PAN_SCHEDULER_DIR` / `PAN_BACKGROUND_JOBS_DIR` 是否在生产设置 | 代码默认同根；若部署设置不同，"旧 store 与新 store 同根"这一阻塞会减弱，但删除顺序不变 |
| `independent_jobs.enabled` 是否开启、endpoint 是否配置 | 工作树无 `config.json` |
| `data/scheduler/` 旧目录是否仍存在、是否已被 `migrate_legacy_tasks` 消化 | 需真实数据目录 |
| 19 个旧 MCP 工具是否仍有外部 Agent 客户端在用 | 只能看 manifest 宣传与代码，无法观测真实调用 |
| `tests/` 当前是否全绿、15 个引用旧 URL 的测试文件的通过状态 | 本轮按要求未运行测试 |
| `_scheduled_task_hooks` 是否被测试 patch（决定 S03 删除时的测试改写量） | 未逐文件核对 patch 目标 |
| Windows Job Object / 进程树取消在目标机的真实行为 | 需要在 Windows 实际起进程；本轮禁止启停 |
| `scheduler/engine.py:drain` 之外是否还有零调用旧符号 | 仅核对了本文列出的符号，未做全量 AST 死代码分析 |

## 9. 复核方式（下一轮可直接复用）

```bash
# 1) 入口是否仍在：按文件查符号
rg -n "def api_background_job_start|def api_session_message_job_start" packages/web/server.py
# 2) 旧写路径是否仍被新代码调用
rg -n "jobs\._create|jobs\._write_job|_job_from_payload" packages/jobs packages/scheduler
# 3) import 图（AST 版比文本搜索可靠）
python - <<'PY'
import ast, pathlib
targets = {"packages.core.background_jobs", "packages.jobs.legacy_compat",
           "packages.jobs.unified", "packages.scheduler.store"}
for p in pathlib.Path("packages").rglob("*.py"):
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        mod = n.module if isinstance(n, ast.ImportFrom) else None
        if mod in targets:
            print(f"{p}:{n.lineno} -> {mod}")
PY
```

本轮只写了这一份文档；未改产品代码、配置、测试，未启停服务，未触碰 8767/8768。
