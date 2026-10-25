# Phase 4b Completion Matrix against design §8

状态只表示本隔离候选的源码/单元证据，不表示已经完成真实数据迁移、部署或用户验收。

| §8 item | status | evidence | remaining work |
|---|---|---|---|
| Legacy Scheduler creation owns Schedule → Event → Trigger → Job | **partial** | `LegacySchedulerBridge.switch` creates one `scheduler.time.v1` source, one filtered managed Trigger, and one `system_scheduled_task` Job; `test_switched_task_flows_event_trigger_job_and_system_action_audit` passes | Real MCP/HTTP/Jobs editor transport and production service acceptance |
| Legacy `taskId`/`jobId`, list/get/runs and next-fire projection | **done in isolated code path** | scheduler store compatibility projection and `test_switch_preserves_task_and_job_ids_and_creates_one_owner` | Real legacy client compatibility and data-volume performance |
| Explicit idempotent owner switch | **done in isolated code path** | `/owner-switch`, stable source creation key, stable Trigger ID, repeated switch test | Operator UI/CLI rollout and real migration receipts |
| Existing records with unknown fields/forms | **partial** | `_entry`/`_action_args` fail closed; unmappable test leaves legacy record and no source | Inventory and user decisions for actual production records |
| Exactly one scheduler owner/no dual fire | **done in isolated code path** | Action Jobs are skipped by legacy `run_due_scheduled_tasks`; source is sole Event owner; one source link/Trigger test passes | Crash-level cross-process deployment test |
| Event identity, durable admission, restart dedupe | **done by Phase 2 + reused** | Phase 2 scheduler Event tests plus switched Event path test | Real filesystem/power-loss and remote CI evidence |
| Manual run-now does not move periodic plan | **done in isolated code path** | `test_run_now_does_not_move_periodic_next_fire` | Live MCP and GUI acceptance |
| Misfire policy/grace, pause/resume, nextFireAt and pending accounting | **partial** | Source retains legacy schedule/misfire/grace and adopts legacy nextFireAt; existing scheduler/time-event tests remain green | End-to-end switched pause/resume/misfire/undelivered tests and production data review |
| Scheduled shell, QQ, resume_legal_running | **partial** | Lossless system Action schema mapping and old effect path are wired; unsupported fields reject | Real provider/shell/notification acceptance |
| Unknown/uncertain does not replay | **done by reused Action/Event runtime** | Phase 1/2 unknown barriers plus switched Job audit path; no legacy pass runs Action Jobs | Live crash/restart acceptance |
| Legacy HTTP/MCP/GUI contracts | **partial** | Existing scheduler API/store tests and compatibility matrix; old routes remain | Real MCP stdio and Jobs schedule editor screenshots/user acceptance |
| §8 real migration/deployment/remote CI | **not done** | Explicitly outside this isolated candidate | MA integration deployment, real-data migration, remote CI, user acceptance |
