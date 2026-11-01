# Phase 2 Scheduler Event 候选交付

分支 `feature/job-chain-phase1-20261025`，工作树 `D:/project/pan-worktrees/job-chain-phase1-20261025`。父提交 `cf9bbf3d42a43a3e9c197991926f3af46ce2c704`；Phase 1 已经MA验收集成，本阶段只增量实施。新候选的完整SHA在交付消息中给出，可在本分支 `git rev-parse HEAD` 复核。本报告、契约、实现与证据处于同一候选提交。

## 结果

新增用户Bearer保护的 `/api/scheduler/event-sources` 后端CRUD；Sources按稳定ID/revision保存once/interval/cron规则、enabled及发布进度。事件来源接入原统一scheduler pass，不创建调度loop、不调用Action/Worker/shell执行器。旧scheduled-task JSON、原MCP/HTTP/GUI及run-now路径保持原owner。

`Service.accept`保存所有订阅快照（含disabled/unavailable），EventStore逐target保存独立回执；`Service.deliver`复制Event并隔离匹配/持久化失败，成功订阅不随其他订阅重试。条件、当前enabled/revision、授权、availability、容量及Phase1链冻结/uncertain规则继续生效。新增eventFilter按payload.scheduleId等payload字段选择来源，现有State condition继续执行。Event列表追加订阅回执；关联Run执行状态/结果仅在includeExecution=true且通过既有trigger.read授权时返回，原GET继续可用。

## 身份与游标

完整契约：[SCHEDULER_EVENT_CONTRACT_20261025.md](../../docs/design/job-unification/SCHEDULER_EVENT_CONTRACT_20261025.md)。类型为 `scheduler.time.v1`，source为可信Bus core scheduler；payload包含scheduleId、scheduleRevision、scheduledAt、emittedAt、eventId。scheduledAt沿现有host-local朴素ISO秒级表示，emittedAt/occurredAt为首次预留时UTC时间。identity为 `evt_schedule_` 加SHA256紧凑JSON数组 `[scheduler-event-v1,scheduleId,revision,scheduledAt]`。

Sources先持久保存完整pending Event及后继点，**不推进游标**；await EventStore事务接纳后，才提交publishCursor/nextFireAt。EventStore COMMIT后、游标COMMIT前崩溃或回执丢失时，重启按同pending ID和完整字段重发，Store返回duplicate，保持原Event和订阅快照，随后推进游标。EventStore pin防止未结算事实先过保留期；游标提交后释放pin、再确认清理。清理确认丢失可补做，不再发旧发生点；未确认时来源不可删除。

旧时间calculator/时区/anchor继续复用。新来源默认fire_now、grace300秒；once超grace过期，周期skip跳过，周期fire_now合并补一次且沿网格推进，不全量补停机点。全局scheduler关闭不推进。pending时修改/删除409，规则编辑在无pending时按revision并由原calculator重算，元信息编辑保留下一点。新interval最小1秒，仅新来源适用，旧参数范围不变。

## 改动文件

- `packages/scheduler/time_events.py`
- `packages/scheduler/event_api.py`
- `packages/scheduler/api.py`
- `packages/jobs/action_runtime.py`
- `packages/automation/service.py`
- `packages/automation/event_store.py`
- `packages/automation/engine.py`
- `packages/automation/store.py`
- `packages/automation/api.py`
- `tests/test_scheduler_time_events.py`
- `docs/design/job-unification/SCHEDULER_EVENT_CONTRACT_20261025.md`
- `artifacts/job-chain-phase2/REPORT.md`、`manifest.json` 和JUnit证据；失败原字节以无损`.xml.gz`保存。

## 验证

PowerShell，cwd为指定工作树：

```powershell
$env:PYTHONPATH='.'
python -m pytest -q tests/test_scheduler_time_events.py tests/test_scheduler_api.py tests/test_scheduler_dispatch.py tests/test_scheduler_engine.py tests/test_scheduler_cron.py tests/test_scheduler_mcp.py tests/test_scheduler_store.py tests/test_automation_core.py tests/test_automation_concurrency.py tests/test_automation_retry_parallel.py tests/test_event_store.py tests/test_event_trigger_reliability.py tests/test_plugin_events.py tests/test_plugin_event_lifecycle.py tests/test_job_action_chain.py tests/test_automation_action_chain.py tests/test_job_action_templates.py tests/test_job_completion_recovery.py tests/test_job_system_actions.py tests/test_job_overlap.py tests/test_system_fields.py tests/test_jobs_api.py tests/test_background_job_api.py tests/test_session_message_jobs.py --junitxml=artifacts/job-chain-phase2/final.xml
python -m compileall -q packages/scheduler/time_events.py packages/scheduler/event_api.py packages/scheduler/api.py packages/automation/engine.py packages/automation/event_store.py packages/automation/service.py packages/automation/store.py packages/automation/api.py packages/jobs/action_runtime.py
git diff --check
```

最终 **517 passed、0 failed/errors/skipped，exit=0**；新增Phase2专项17项，现有scheduler/automation/Event/Job/Phase1链回归500项。`final.xml`与`manifest.json`给出逐项结果和命令；compileall exit=0，diff检查exit=0。既有告警是pytest timeout配置、Pydantic前向引用和Starlette/httpx弃用；没有新增skip/xfail。接口权限收尾后的auth-final针对集通过；最终汇总包含该新增权限测试。

新专项覆盖持久接纳先于游标、提交间崩溃/回执丢失、重启去重、并发预留身份、未确认事实跨保留期、清理确认丢失、Source编辑revision/启停及删除、missed三种映射、同一pass旧任务和新来源各自owner、广播N订阅独立失败/skip/disabled/unavailable/深复制、单订阅落盘失败的独立重试、接纳后availability丢失恢复、时间Event→过滤Trigger→冻结两步链的单元端到端、ASGI用户权限/冲突、执行结果读取权限和来源存储失败隔离。

首轮 `first.xml.gz` 共43项2失败：新回执逐条fsync导致旧不可用Event饿死后续事实，以及空targets未经过既有matching fence；实现改为失败回执按有界页批量commit，空订阅保留原fence，原测试/断言未弱化。`second.xml`43/43。`special.xml.gz`13项1失败是旧once测试fixture把过期创建记录当作due，修正为在隔离记录中显式设置due；`special-second.xml`13/13。其余阶段：initial-regression115/115、regression513/513、special-final50/50；阶段计数与最终重叠，不累加。所有红证据保留。

现有插件单元测试生成的三份`artifacts/event-plugin-adapter/*-transport.json`已核对并恢复为原提交内容，不夹带本轮随机Run ID和时间戳。没有启动HTTP监听或产品服务；新API验证仅ASGITransport。原scheduler后台测试只运行测试进程内的自有统一loop和Worker替身，不启动Pan实例。

## 未决与边界

§9默认/备选/影响已写契约：Event类型/路径/host-local时间/身份、pending+cursor+pin、missed映射、编辑冲突、多Trigger共享来源与eventFilter、owner过渡、disabled/unavailable快照。没有需要改变旧行为才能完成本阶段的阻塞产品选择；交互卡 **0，IDs=[]**。

未实施Automation/Event Register UI、旧创建入口切换、MCP wrapper、真实数据迁移或自动Job/Scheduler/Trigger关联组合。Job多时间关联的命名/编辑/删除与跨存储创建协议仍属后续阶段；本阶段支持多个现存直接Trigger订阅同一来源，以及既有Job Event目标接收广播，不伪造自动管理Trigger。

未测真实服务/HTTP网络/MCP transport、供应商消费、真实生产数据迁移、断电/fsync失败、多机共享文件系统、数据库备份回滚、host timezone变更与DST fold特殊现场。崩溃证据为单位测试注入事务间故障并重建磁盘Store/Runtime，不声称做了真实断电演练。执行中的Action失败、unknown和已完成Step不因Event重投重开；它们继续由Run/Step证据约束。

未移动main/practical，未合并integration、push、远端CI、启停产品服务；未使用8767/8768或操作真实数据。候选由MA后续验收集成；设计剩余阶段未宣称完成。
