# Job unification 第 1 阶段源码盘点与实施矩阵

日期：2026-10-25。基线：`550ff4b193e5c9db07408170be23561733464e08`。
范围：只增加后端有序 Action 链和数据模型；不切换任何旧创建入口，不迁移真实数据，不做 Scheduler Event 或 GUI。

执行顺序纠正：首次暂停前已开始代码草稿但未先落盘本矩阵，不符合任务要求；恢复后先补齐源码矩阵，再继续实现。下表来自源码检查，不能解释为迁移已经完成。

## 旧能力到新表达的逐项映射

代码位置按基线符号定位；后续增行可能改变行号。`packages/` 与 `tests/` 是本工作树内文件。

| 旧能力 | 旧 MCP / HTTP / GUI 入口 | 真实执行路径（文件、符号） | 新 Action / Job 表达与复用路径 | 本阶段缺口与后续确认 |
|---|---|---|---|---|
| background task：argv/cwd/name、独立 creator/target、隐藏窗口、日志、进程树取消 | `packages/mcp/server.py:2180 agent_background_start`；`packages/web/server.py:10807 api_background_job_start`，`/api/background-jobs`；`packages/jobs/api.py:495 create_job`；`packages/web/src/views/JobsView.tsx` New legacy Job | `packages/core/background_jobs.py:798 start` → `_validate_command` → `_create` → `_spawn_background_runner`；`packages/core/background_runner.py:14 main`、`packages/jobs/owned_process.py`、`process_cleanup.py`；`cancel/retry` 保留身份检查 | 已有 `packages/jobs/system_actions.py:definition('background-process')`、`execute` → `jobs.start` → `wait_process`。新链可调用 `system_background_process`；完成契约是子进程终态，monitoring 另需结构化信号 | 旧 MCP 仍创建旧 Job，本阶段不做 wrapper。独立 Job service 与显式 registry_root 选择不能无声改变；后续迁移需覆盖独立服务存活/恢复/通知 |
| 独立 Job service 执行与 outbox | 同 background MCP/HTTP；`independent_jobs` 配置控制 `packages/jobs/pan_bridge.py:configured_client` | `background_jobs.start` 无显式 registry_root 时 `pan_bridge.request('start')`；`packages/jobs/independent_service.py:JobStore.create/request_cancel/retry/recover/events/ack`；`independent_runner.py`；`pan_bridge.recover_events` 只在持久 enqueue 后 ack，不作本地 fallback | 复用服务/进程真相与 terminal outbox；Action Job 的 root/owner 与独立服务是分开的契约，不用新链替代 Job service | 本阶段不重新接线、不安装/启停独立服务；后续需明确 Action child 的 owner/root 与原 Job ID 的兼容保持 |
| Session 延时/周期消息，发送者前缀、目标身份、重定向 | `packages/mcp/server.py:2225 agent_message_job_create`；`/api/session-message-jobs` → `packages/web/server.py:10855 api_session_message_job_start`；`/api/jobs` 创建/编辑；JobsView | `background_jobs.py:1108 start_message`、`update_message/cancel_message`；`:1244 run_due_message_jobs` claim → `system_actions.invoke` → `_worker.send_session`；原消息 Queue/Illegal/Pause gate 由 Worker 保持 | `system_session_message` + Job trigger/steps；系统 Action `execute` 复用 `send_session`，持久入队只表示 queued，不表示 CLI 消费完成 | 旧 message schedule 是 `type=once(delaySeconds/at)/interval(intervalSeconds)/weekly(weekday/time)`；不是直接等同新 cron schema。前缀、source/creator、retarget、通知必须在后续 wrapper 对照保留 |
| Session 群发，稳定去重与每目标结果 | 同 `agent_message_job_create(target_session_ids=...)`；`api_session_message_job_start`；JobsView broadcast | `background_jobs.py:1145 start_broadcast`、`run_due_message_jobs` 逐目标；`targetSessionIds/lastDelivery`；`system_actions.execute` 的 `session-broadcast` 使用 checkpoint `targetIds/targetResults`，已知及未知收据不重发 | `system_session_broadcast`，复用目标固定、逐目标收据、Queue 持久化；链只在该 Action 完成后进入下一步 | 旧群发对失败目标与通知的语义不能用简化 aggregate 取代。后续迁移需真实 Queue ID/partial failure/source 对照；本阶段只保留原入口 |
| 定时 assign（任务），显式 run-now 不移动周期 | `packages/mcp/server.py scheduler_create/update/list/get/delete/pause/resume/run_now/runs`；`packages/scheduler/api.py /tasks*`；`packages/jobs/api.py /api/jobs*`；JobsView schedule editor | `scheduler/store.py:378 create_task` → `_job_from_payload`；`scheduler/engine.py:_ensure_registered` → `background_jobs.py:2900 run_due_scheduled_tasks` → `_fire_scheduled_entry` → `_run_job_action/_execute_job_action` → `_worker.assign(task_id=dispatch_key)` | `system_scheduled_task` 参数 `action.api=assign`；新 Job 链直接复用现有 Action runtime。Scheduler→Event 出口不在本阶段实现 | 旧 jobId/taskId、dispatchKey、undeliveredFires、paused 推进与手动 run-now 保持旧路径；后续才改为 Schedule→Event→Trigger→Job |
| 定时普通 Session 消息 | 同 scheduler MCP/HTTP，`action.api=send_session` | `background_jobs.py:2194 _execute_job_action` 的 `send_session` 分支直接 `_worker.send_session` | `system_scheduled_task(action.api=send_session)`；原队列完成边界 | 本阶段不把 queued 当消费成功；不切旧 MCP |
| 定时恢复 Illegal/offline Session | 同 scheduler 创建，`resume_legal_running`；启动恢复/显式 wake HTTP | `background_jobs._execute_job_action` fire 时筛选 Illegal 且无活 Worker；按 `dispatch_key + session.id` 生成稳定 `job-resume:*`；`_worker.wake_session` 复用持久回执 | `system_scheduled_task(action.api=resume_legal_running)`；原固定恢复消息/去重和显式 wake gate 继续负责效果 | 不把普通 send 当恢复授权；后续迁移保持动态目标选择时点与既有逐 Session 幂等身份 |
| 定时 QQ 文本通知，联系人/bot 选择 | scheduler / Jobs HTTP `send_qq`；JobsView QQ action editor | `packages/jobs/actions.py:normalize_scheduled_qq_action`；`background_jobs._execute_job_action` 使用 `_scheduled_task_hooks.qq_send`，返回 messageId/qqErrorCode；`_replay_undelivered_fires` 保持原处理 | `system_scheduled_task(action.api=send_qq,args=...)` 调用原 sender；text 仍为显式输入 | 后续 wrapper 不能损失 QQ 错误码/收据；本阶段不连接真实 QQ 或修改旧重投契约 |
| 定时 shell 子进程，每 fire 一个稳定 child | Jobs/scheduler HTTP shell；JobsView shell editor；MCP 能力以 `scheduler_create` 当前参数为准 | `background_jobs.start_scheduled_process:853`、`_execute_job_action` shell 分支；按 parent+dispatchKey 找同一个 child，`_reconcile_scheduled_processes` 对账；`owned_process/process_cleanup` | `system_scheduled_task(action.api=shell)`，通过原 parent admission 校验、子 Job 和 `wait_process`；链需要每步独立 dispatch 身份 | 本阶段补齐链 host 的逐步骤 admission，不绕过 shell 的 parent/action/args 校验；不创建真实外部进程做迁移 |
| Pan restart / exit 生命周期 | `packages/web/server.py:3966 api_main_restart` (`/api/main/restart`)、`:4298 api_main_exit` (`/api/main/exit`)；Settings/CLI 生命周期入口；无普通 Job MCP 等价授权 | `background_jobs.py:3157 create_service_job`；`packages/core/main_lifecycle.py:run_supervisor/run_exit_supervisor`、进程/port/root 身份；server `_snapshot_main_lifecycle_options` 与 supervisor 接管 | `system_main_lifecycle` 是受保护 high_risk 系统定义；普通 Job config 明确拒绝该 Action。保留既有 supervisor，不开放链式生命周期旁路 | 不能将生命周期作为普通 enabled Action 执行；后续迁移需用户决定合法性、running offline/preserve/ask、独立监督进程边界 |
| 查询/取消/删除/重试/通知/完成清理 | `agent_background_get/list/cancel/retry`、`agent_message_job_*`、`scheduler_*`；`/api/background-jobs*`、`/api/jobs/{id}*`、`/api/scheduler/tasks*`；JobsView list/drawer | `background_jobs.get/list_jobs/cancel/delete_job/retry/recover_notifications`；`jobs/api.py:_find_job` 接受 jobId/taskId 并联 independent service；`cleanup.py`、`pins.py`、completed retention | 原 Query/management 保持旧路由、原 Job 身份和通知。新链继承 `action_runtime.finalize/publish_events`，额外增加每步 audit | 本阶段不改旧通知 target/terminal event/queue ID。新 Run 的 step audit 是新增字段，不伪造历史 Run/Step/Attempt |
| once / interval / cron / weekly、时区、多 schedule entry、misfire/maxRuns/暂停积压 | `scheduler_create/update`；`jobs/api.py:_validate_scheduled_specs`；`scheduler/api.py:_validate_schedule`；JobsView schedule editors | `scheduler/store.py:_normalize_schedule/_spec_to_entry/compute_next_fire/_job_from_legacy_task`；`packages/jobs/cron.py`；`background_jobs._entry_next_fire/skip/misfire/undeliveredFires` | 原 schedule 字段仍使用现有时间计算器；本阶段只允许旧配置继续执行，不产生时间 Event | 不将 weekly 默认为 immediate，不修改 nextFireAt，不重算发布游标。时间 Event schema、missed strategy、多个关联 Trigger 留待后续冻结 |
| Schedule 模板与完整 Job Template | `/api/jobs/templates` → `packages/jobs/templates.py`；`job_config_drafts` MCP；`/api/jobs/job-templates*` → `action_api.py` | Schedule 模板仅展开 time spec；`action_templates.py:Templates.put/reserve/snapshot` 完整 Job 配置、revision、幂等实例化、引用保护；`actions/store.py:check_action_graph` | 完整 Job Template 保存全部 steps，仍是被动配置；既有 template Action 使用固定 revision，不变成 Group chain | 区分 `tpl_` 与 `jtpl_`，后续 UI/兼容 MCP 不混用；第 1 阶段无 UI |
| Event Trigger 直接执行 Action 与 Action Job Event 触发 | `/api/automation/triggers` → `packages/automation/api.py`；Job config.trigger event；Actions/Automation GUI；没有新增 Event 发布 MCP | `automation/engine.py:Engine.match/drain/run_attempt`、`automation/store.py:Ledger`；`automation/service.py` 与 `event_store.py`；`jobs/action_runtime.py:event_targets/queue_event/launch` | 两种现存后端配置均新增 steps；直接 Trigger 保留 Run 状态 `succeeded`，Action Job 保留 `completed`。共享映射/串行执行语义，保持各宿主通知/重试契约 | 不新增 Event Register/Trigger 页面或时间 Event。旧 Trigger 的 actionId/mapping 仍按旧接纳/执行语义；steps 为显式 opt-in |

## §9 本阶段默认选择、备选与影响

| 未冻结契约 | 本阶段默认选择 | 备选 | 影响 / 用户决策状态 |
|---|---|---|---|
| 链 schema | 可选 `steps[1..32]`，每步 `stepId/actionId/arguments`；稳定 ID 由调用者提供，更新不按序号重建；旧 `actionId+arguments` 继续合法 | 单独链对象/服务器分配 stepId | 新增配置 opt-in；旧记录不改写、不伪造 Step audit |
| 参数映射与输出 | steps.arguments 每字段绑定 `{from:literal,value}` / `{from:input,path}` / `{from:event,path}` / `{from:step,stepId,path}`；保留 Job 端普通 literal 值便捷写法；仅允许前序 step，字典点路径，无表达式或脚本 | JSON Pointer/字符串模板/任意表达式 | 缺失路径、未声明字段、输入类型错误在副作用前显式失败；不默认 null、不类型转换。`structuredContent` 是优先结构化输出，否则使用原 JSON 结果；显式绑定不损失 MCP rich result |
| Run/Step/Attempt 与历史映射 | 新链建立 Run→step executions→attempts；旧 Run/Attempt 原样读取，新增结构只给新链；全成功才完成，失败/取消/uncertain 阻断 | 回填所有历史单步结构 | 不执行真实迁移；不会声称历史已经过新链 |
| 接纳版本 | 新链 Run 接纳冻结全部 Action 定义、映射、输入、Event、版本和 completion/recovery；每步副作用检查当前 enabled/授权；已完成步骤不重跑 | 各 step 开始时读取最新 Action | 防止运行中编辑混入；撤权仍生效。旧无 steps API 的既有执行含义保留；无法确认当前副作用时停在 uncertain |
| 逐步骤恢复 | 持久完成边界可继续未开始步骤；正在执行而无可信终态的 step→uncertain，不能自动重投；monitoring 仍遵循原 opt-in recovery，重试只作用当前 step | 整链重跑/自动重投 uncertain | 不新增补偿或回滚。已接纳链可恢复；不扩大旧 Job 自动重投/重试授权 |
| 多个时间触发、关联命名/编辑/删除 | 不实现新关联契约，原 schedule array 原样保留 | 多 Trigger/单聚合 Trigger | 后续阶段待用户确认，无本阶段新行为 |
| 时间类型、misfire、修改后的 nextFireAt/游标 | 继续旧 calculator 与字段；本阶段不创建时间 Event | 新统一时间 schema/补发策略 | 后续需要无损映射和单 owner 切换证据 |
| Scheduler Event 名称/字段/身份/授权 | 不定义、不发布 | `scheduleId/revision/scheduledAt` 哈希等 | 全部留待后续冻结 |
| Job/Scheduler/Trigger 跨存储恢复 | 本阶段只复用现有 Job Template intent；不创建新 Scheduler/Trigger 组合 | saga/事务协调器 | 不静默引入启用半成品/自动清理新规则 |
| 共享 Scheduler 删除/停用/影响 UI | 不实现 | 引用计数保护/显式强制解除 | 后续产品决策；无本阶段用户可见行为变化 |
| 旧 UI/MCP 对应、输出与错误兼容 | 旧入口保留原路径；本矩阵是盘点，不是完成迁移的声明 | 同阶段包装所有旧 MCP | 用户明确排除 wrapper/GUI/真实迁移，后续须逐项对照 |

本阶段技术选择只影响显式新建的 steps 配置；若发现确实改变旧行为且阻塞正确实现的产品问题，按本 Session 创建 interaction_card，记录 card ID，不以文档默认代替用户回答。当前没有已创建的交互卡。

## 实施落点与恢复材料

- `packages/jobs/chains.py` 负责纯配置校验、冻结、映射和串行完成契约；`chain_runtime.py` 接入现存 Action Job 的锁、Run、通知和清理；`packages/automation/chain_engine.py` 接入现存直接 Trigger 的 ledger 与有限重试。没有第二个调度器。
- Job 的 `arguments` 在显式 steps 配置中是链输入；Trigger 的原 `arguments` 绑定先解析为链输入。兼容 `actionId` 若同时出现必须等于第一步；可省略而由第一步补齐。普通 literal 对象若含保留键 `from`，必须用 `{from:literal,value:...}` 包裹。额外 `system` 来源只走既有可信 system fields 校验，不能从 payload 自报身份。
- `completion` 沿用现有 Job 配置，显式链的每一步都必须满足该契约；monitoring/recoverable 的原声明与 opt-in 不放宽。Trigger 不新增 monitoring 策略。Run 内每步的完成状态、结果/错误、版本 digest、Attempt 和 checkpoint 独立持久化；缺失/类型错误在创建副作用 Attempt 前失败。Step 的 `completed` 对应直接 Trigger 的 Run `succeeded` / Action Job 的 Run `completed`。
- 接纳时冻结 snapshot version=1、configRevision、registryRevision、各 Action revision/generation/digest 和固定 Job Template 配置。各步仍检查当前 enabled/删除代次/委派授权；内容编辑不会替换已接纳定义。已完成步骤在恢复时跳过；每步使用 `runId:stepId` 稳定执行身份，shell child 仍过原 parent admission 校验。
- snapshot 属私有可执行恢复材料；公开 HTTP/WS 只给配置、版本摘要和逐步骤审计。Trigger 的 snapshot 在最终终态清理，只有 queued/running/retry_wait 保留。Job 的临时 registry snapshot 在原 finalize 路径清理；未完结恢复或可显式重试的已知失败保留私有 `chainRecovery`，在下一次 admission 消费/替换或删除 Job 时移除。此保留是冻结重试所必需的本阶段技术默认；备选是单独 Run 存储及归档，此阶段没有真实数据迁移或新增保留管理 UI。
- 安全边界续作不等于重投副作用，不受默认关闭自动 retry 阻挡，也不消耗 monitoring 失败预算。运行中的 Step 在中断后转 uncertain，禁止自动和 confirm-recovery 重投；需未来提供按原身份对账机制后才能解除。旧无 steps 的恢复入口与许可保持原样。
- 已有绑定型 `pan.interaction.responded` Event 携带 triggerRef 时，原单 Action verifier 的授权边界不可扩展为整链；新链显式失败并保留审计。普通既有 Event 映射可用。本阶段不实现交互绑定链协议；备选是逐步绑定/验签契约，留待产品冻结，不绕过旧 verifier。
- 输出无新静态 output-schema 类型推导；实际映射值在每步副作用前依 Action input_schema 校验。只支持字典点路径（空字符串为根），不支持数组索引、模板表达式、分支、并行步骤、补偿、回滚或自动跳过失败。每链最多32步、每步10次 Attempt，snapshot 与单步 JSON 结果上限1 MiB，继续遵守宿主总容量限制。

## 自证与未测边界

最终命令、计数、失败实验与候选交付文件见 `artifacts/job-chain-phase1/REPORT.md`。专项覆盖旧单步等价与通知、顺序/映射、失败/取消/unknown、冻结及固定模板、Run/Step/Attempt 审计隔离、已知失败与 monitoring 只重试当前步骤、运行中撤权、落盘故障、步骤边界独立子进程退出恢复，以及真实自有命令输出传递。ASGI API 检查不启动 HTTP 服务。

不宣称已测真实用户 Session/Worker/QQ/供应商消费、真实 shell 树取消、GUI、网络 HTTP/MCP transport、部署重启、多机共享存储或所有文件系统故障。原有网络/服务型 e2e 不运行，避免启停服务；生产端口8767/8768与真实数据不接触。前端未改，不要求 tsc/build/92资产。时间 Event、时间关联编辑/删除、旧 MCP wrapper 与真实迁移均未实施。
