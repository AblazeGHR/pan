# Scheduler Event 第2阶段实施契约

日期：2026-10-25。本阶段基于已交付的 Phase 1 `cf9bbf3d42a43a3e9c197991926f3af46ce2c704`，只实现设计§4/4.1/4.2及§7的事件部分。Phase 1 不重复实施；旧入口切换、Automation/Event Register UI、MCP兼容包装及真实数据迁移继续留在后续阶段。

## 源码路径和唯一 owner

既有 `packages/scheduler/engine.py:tick_once/_ensure_registered` → `packages/core/background_jobs.py:run_due_scheduled_tasks` 是唯一调度 pass；它调用已有 Action Job Runtime 的 `tick`。该 Runtime 新增可选 `time_events` host，由 `packages/automation/service.py:Service.start` 在原 profile owner 锁成功后挂载。没有新增后台 sleep Action、调度 loop 或定时进程。未启动 automation host 时，新来源保留进度，旧任务仍按原路径运行。

旧 scheduled-task 仍只在既有 Jobs JSON 注册表中，由原认领/派发/恢复路径处理。旧 MCP、HTTP、GUI 创建入口完全没有切换。新来源只在 scheduler root 的 `time-events/sources.sqlite3` 中，ID 为随机 `schedule_*`，持久 owner 固定为 `scheduler-event-v1`；新 API 不接受 Job ID/owner/内部游标。新来源不生成 scheduled-task JSON，不调用 `_run_job_action`，不会与旧 scanner 同时认领一个记录。未知 owner 拒绝发布。

新后端端点：`/api/scheduler/event-sources`（GET/POST）、`/{scheduleId}`（PUT/DELETE）。仅用户 Bearer；没有开放既有 MCP bridge allowlist。草稿默认禁用，写入必须给 expectedRevision，删除必须禁用且无待结算发布/保留清理。单个来源保存一个 once/interval/cron 时间规则；同一来源可被多个已有 Trigger 订阅。此阶段不自动建立 Job/Scheduler/Trigger 组合对象。

## 时间、身份和精确崩溃边界

`packages/scheduler/time_events.py:Sources` 复用 `scheduler.store._normalize_schedule` 和 `background_jobs._entry_next_fire` / `jobs.cron.next_fire_after`，不另写时间计算器。时区、Windows缺tzdata时既有降级、interval anchor和cron搜索规则沿用现有实现。新来源interval最少1秒，与现有秒级游标/tick一致；旧入口接受范围不变。

时间类型为 `scheduler.time.v1`，schemaVersion=1，运行时来源为 Bus 的 opaque core handle `scheduler`。插件不能以此handle发布。字段位置：

| 路径 | 契约 |
|---|---|
| `id` 与 `payload.eventId` | 相同的稳定事件ID |
| `payload.scheduleId` | 来源稳定ID |
| `payload.scheduleRevision` | 首次预留发生点时的来源配置revision |
| `payload.scheduledAt` | 现有calculator返回的host-local朴素ISO时间，秒精度；不是目标timezone墙钟或UTC |
| `payload.emittedAt` | 首次预留envelope时的UTC ISO时间，重试不刷新；表示生成时间，EventStore另存receivedAt表示持久接纳时间 |
| `occurredAt` | 与冻结的emittedAt相同 |
| `source` | `{type:core,id:scheduler}`；不从payload推断可信system身份 |

身份算法：`evt_schedule_ + sha256(UTF8(JSON(["scheduler-event-v1", scheduleId, scheduleRevision, scheduledAt], separators=(",", ":"))))`。不使用随机ID、实际重试时间或Trigger ID。来源时区/时间规则属于revision，已预留envelope不会被改写。

发生点发布使用三个持久边界；SQLite使用synchronous=FULL，写事务BEGIN IMMEDIATE：

1. Sources在**不推进nextFireAt/publishCursor**的事务中保存 `pending={event,nextFireAt}`。完整Event envelope和后继网格点固定；同来源并发预留只会返回同一个pending。
2. `Runtime.tick` await可信 `Service.accept_time` → Bus校验 → `Service.accept` → EventStore.append事务提交。该事务保存Event、首次订阅快照及Event保留pin。返回 `persisted=True` 且ID匹配才允许下一步；没有等待任何Action执行。
3. Sources.advance事务按pending Event ID比较并提交 `publishCursor` 和nextFireAt，移除pending。随后释放EventStore pin，再记录cursor.eventStoreReleased。来源已禁用但清理未确认时仍可在下一tick补确认。

崩溃在1之前：进度未动，可重新预留。崩溃在1之后/2之前：复用pending重发。**崩溃在EventStore COMMIT之后、Sources.advance COMMIT之前**（包括持久回执丢失）：重启复用完整pending，EventStore按同ID/事实指纹返回duplicate，保持第一次Event和订阅快照，再推进来源游标；不会产生第二个事件事实或重复Run。崩溃在3之后/释放pin之前：来源游标已经推进，只补清理，不再发旧发生点。释放成功但cursor清理确认写入失败也可幂等补确认。

EventStore保留pin防止“游标长期提交失败但已处理Event先被7天保留策略删掉”后重新接纳。pin只有在游标提交之后释放；无法确认清理时拒绝删除来源，不以删除cursor掩盖残留。不是跨SQLite文件的天然原子事务。DB备份回滚、共享多机文件系统、系统时区变更后的旧朴素时间解释不在本轮实测范围。

## 错过触发和编辑

默认 `misfirePolicy=fire_now`、`graceSec=300`，与旧scheduler规则对照：once超grace→expired且禁用，不产生Event；周期skip超grace→skipped并跳到now之后网格；周期fire_now超grace→只为原due发生点产生一次Event，再跳到预留时now之后网格，不补全停机期间所有点。publishCursor仅在成功接纳Event后更新；skip/expired只推进时间求值的nextFireAt并保存lastOutcome，不伪造发布回执。此决策在Sources内，与Trigger重试、Job overlap、步骤recovery无关。全局scheduler.enabled=false时不预留、不发布、不推进。

配置编辑使revision递增；时间规则不变时保留nextFireAt，规则变化则调用旧calculator从编辑时刻求下一点。已经预留pending时拒绝配置修改/删除（409），必须先结算同一发生点，避免版本覆盖丢事件。已经接纳的Event及Phase 1冻结Run不受后续修改影响。没有为新来源添加旧run-now语义；旧run-now路径保持原样且不移动周期计划。

## 独立广播回执

`Service.accept`持久化所有当时已订阅该type的Trigger快照；disabled/unavailable也记录为非执行接纳。Action Job Event目标同样保留非执行快照。`Service.deliver`对每个target深复制Event和配置，逐个隔离异常；不等待Action完成。EventStore `subscriptions` 主键为 `(event_id, "trigger:"+triggerId)` 或 `(event_id, "job:"+jobId)`。

- admission时disabled/unavailable：分别保存状态，无Run或副作用；不因以后启用而自动消费旧事实。与旧过滤语义相比新增审计，不新增旧Event效果。
- admission时eligible：保留既有当前revision、enabled、授权、依赖、条件、容量与Run admission检查。运行中的availability丢失只留下该订阅error，恢复后只重试该缺口。
- 已知匹配失败/条件skipped也各有自己的Run和回执；副作用失败或unknown仍归Run/Step处理，Event重投不重开该Run。
- 回执持久化失败时允许补写；Event+Trigger已有Run去重仍阻止第二次效果。其他订阅继续匹配。成功回执不因另一订阅失败而删除。
- 失败回执按原有有界投递页批量commit，避免不可用旧Event逐条fsync饿死后续独立Event；取消时finally刷新已完成的失败诊断。Event只有全部订阅接纳结果可持久确认后才标done，接纳成功不代表Job完成。

`/api/automation/events`追加subscriptions；直接Trigger回执保存admissionResult、runId、revision，仅显式查询`includeExecution=true`并通过既有trigger.read授权后，根据Run补execution状态/结果/错误；默认旧GET不公开新增执行结果。Job目标回执链接既有job/eventReceipts，不虚构单独Trigger对象。历史Event没有回执时在原targets上补写，不把后建Trigger加进历史快照。插件Bus通知仍是既有best effort层。

Trigger新增可选 `eventFilter={"payload.scheduleId":scheduleId}` 等最多16个payload字典点路径的标量等值匹配，用来引用**来源**而非历史eventId；未匹配/缺字段为skipped。原State condition继续独立检查。旧客户端编辑省略eventFilter时保留已保存过滤，不默默放宽订阅。此字段不支持脚本、表达式或system身份覆盖。

## §9 默认、备选和影响

| 项目 | 本阶段默认 | 备选 | 影响 |
|---|---|---|---|
| 类型名/来源 | scheduler.time.v1，Bus core scheduler | 每来源动态type | 静态schema，payload.scheduleId引用来源；不消耗每来源一个Bus注册名额 |
| 时间路径 | payload五字段，scheduledAt沿旧host-local秒级表示 | UTC/offset+fold字段 | 保持旧calculator；跨主机时区/重复墙钟解释需后续冻结，未改旧记录 |
| 身份 | owner/id/revision/scheduledAt SHA256 | 随机ID或按emittedAt | 丢回执仍可去重；不得手工重建同ID来源 |
| 游标 | nextFireAt+publishCursor，pending outbox及EventStore pin | 同库事务/全局发布日志 | 双存储明确恢复协议；不假定原子跨库 |
| missed | 原once过期、周期skip/fire_now一次合并 | 全量补发/catch-up | 只作用显式新来源，未扩旧重投授权 |
| 编辑 | pending时409；规则变化从now重算；已接纳事实固定 | 预留期间允许并行revision/saga | 不丢旧发生点；故障期间可能需先恢复EventStore再编辑 |
| 多Trigger/Job | 多直接Trigger可共享来源，eventFilter选源；现有Job Event目标可收广播 | 自动创建管理关联Trigger | 本阶段没有Job多时间关联UI或跨对象组合协议，后续阶段处理 |
| disabled/unavailable | 审计订阅快照，不新增Run；eligible后故障保留error重试 | 后启用自动补历史 | 保留旧非执行含义，审计新增；不重投已有Run |
| owner过渡 | 新Sources独立owner，旧JSON原owner；无切换/迁移 | 给旧记录新增mode并迁移 | 没有同记录双调度或旧入口默认改变 |

这些选择限显式新来源或广播审计，未要求改变旧入口执行语义，交互卡IDs=[]。后续UI/兼容迁移/组合创建仍须MA分配范围；本阶段不能据此宣称完整设计已全部实现。

验证命令、计数、失败实验与候选文件清单见 `artifacts/job-chain-phase2/REPORT.md`。未测试真实服务/HTTP监听/MCP传输、生产数据迁移、多机存储、真实断电/fsync故障与供应商消费；新HTTP测试使用ASGITransport。
