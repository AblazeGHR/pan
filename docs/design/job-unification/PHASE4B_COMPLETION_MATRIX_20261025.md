# Phase 4b Completion Matrix against design §8

状态只表示本隔离候选的源码/单元证据，不表示已经完成真实数据迁移、部署或用户验收。

Phase 5 已增加真实隔离 host、实际 MCP stdio 和真实浏览器证据，更新如下；原表的整体 §8 结论仍为 **partial**，不得作为部署或迁移完成声明。

| Phase 5 验证项 | 当前状态 | 证据与限制 |
|---|---|---|
| lossless legacy scheduler MCP 样例 → Source/Trigger/Job | done for isolated sample | 实际 `scheduler_create` + 幂等显式 owner-switch；due 只有 1 Event / 1 Trigger Run / 1 system_scheduled_task Attempt |
| taskId/jobId、next-fire、manual、pause/resume、runs、delete | done for isolated samples | `tests/test_job_chain_phase5_e2e.py`；manual 不移计划；once cursor 更新旧 nextFireAt；删除只清理所属 Trigger，保留共享 source |
| 真实 host 重启后的 Event 去重 | done for tested restart | 正常 stop/start 后 Event 与 Attempt 均仍为 1；电源故障/文件系统损坏不在证据内 |
| 真实进程执行期间 host crash → uncertain 不重放 | done for isolated native process | 命令 marker 只写一次；原 ID 可查；retry 拒绝；原 PID/创建时间路径取消成功；第二次重启不重放 |
| unknown schedule 字段保留并留在旧 owner | done for isolated fixture | 仅向临时 profile 写入旧格式记录；owner-switch 拒绝；字段保留；旧 pause 可用。生产记录盘点和迁移尚未执行 |
| Automation 三页、2-step chain、共享 Scheduler 引用 | done for isolated browser sample | 真实 HTTP/WS、生产 bundle、10 张 1440/390 截图；浏览器创建后真实执行两个步骤；无 API response mocks |
| 所有 misfire/grace、时区/DST、pending/undelivered 组合 | partial | 有相关后端回归；本阶段实例样例没有穷举这些组合 |
| QQ、真实 provider receipts、部署/迁移/远端 CI/用户验收 | not done | 仍需各层单独授权和验收；本阶段没有启用这些环境 |

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
