# R1 WP05b：canonical independent execution / main lifecycle

基线 `ee0af90fc4cab507c92f9b99691d68398c5c7618`。本包只增加 executor 与公共
supervisor 后端；不切换 main POST、产品 CLI、server/MCP/React，也不删除 R4 旧 writer。

## 调用与持久身份

`packages.jobs.final_runtime.IndependentExecutor(job_id=..., client=...)` 实现 WP02
`ExecutorPort`。`TypedExecutor(independent_executor=...)` 把 process 操作交给它。
调用者必须传原 `jobId`、running Attempt 的 `attemptId/runId/stepId/leaseId/ownerEpoch/generation`
和原 `effectKey`；host 不生成 Job、Run 或 Attempt。`effectId` 是原 effectKey 的确定性摘要。

独立服务的新内部接口：

| 接口 | 行为 |
|---|---|
| POST `/executions` | typed process intent；重复相同 fence/plan 只查询；禁止 lifecycle/high_risk |
| GET `/executions/{effectId}` | 不依赖 Pan 主服务的 phase、supervisor/process identity、result 查询 |
| POST `/executions/{effectId}/cancel`，body `{attempt: ...}` | 仅 effect 前窗口；已关闭返回 conflict，不谎报已取消 |
| GET `/execution-events` | canonical receipt outbox，与旧 `/events` 分离 |
| POST `/execution-events/{eventId}/ack` | 幂等确认；不等于用户已读 |

这些路由沿用独立服务的 loopback Host/Origin/JSON 门禁，不能充当 WP06 的产品授权路由。
`executions.sqlite3` 只保存 caller-owned execution receipt/lease，不含 Job 配置账本。
旧 `jobs.sqlite3` 和 `/jobs` 不变，新路径零调用 `JobStore.create/retry`、
`system_actions.prepare/finish` 或 `create_service_job/transition_service_job`。
没有删除旧调用点：它们仍服务 R4 兼容入口。不得将本包称为旧 owner 已清零。

intent 在 spawn 前持久化，worker 原子领取一次 claim，再关取消窗口。启动使用固定 Python
module 和 argv，无 shell。短命 bootstrap 退出后，host 通过 retained Popen handle 的 wait
原子保存带 nonce 的 exit receipt；worker 核验后才 claim。仅建新 process group 不证明
脱离 main 的进程树。receipt 保存 supervisor birth identity、process identity 和 owned-tree
cleanup proof。命令环境移除 PAN_* 与 PYTHONPATH；内部 worker 的模块导入路径不传给命令。

same fence 重连不启动第二个 worker。新 Attempt 只允许显式 retry 已明确 rejected 且
cleanup confirmed 的效果；保留原 Job/Run/Step/effectKey 和历史收据，generation 必须增加，
Attempt ID 不可复用。成功、unknown、partial、已取消均不可借新 Attempt 重放。
服务 reload 只探测原 supervisor；无法确认存活时写 unknown，不 kill、不重发、不假定完成。
`ExecutionStore.reconcile` 接受可信 host 的只读 lookup callback，按原外部身份查证，完整校验
Attempt fence 并保留 unknown 历史；新证据使用独立 reconciliation outbox event，不被旧 ack
吞掉。本包不提供自动 lease 接管、自动 reconcile 或历史/活资源 GC。

ExecutorPort 只有效果终态：host 尚在执行时返回 `unknown` / `execution_pending`，不把 accepted
当成功；再次调用相同身份重查 host。持久结果不经过外层内存 pending cache。
`recover_execution_events(client, persist)` 仅在 canonical consumer 返回 durable `True` 后 ack。
consumer 必须按 eventId 幂等并验证完整 Attempt fence；ack 丢失仍交付原 event。
若协调器已记 uncertain，应用最终证据必须走其 reconcile 契约，不自动创建新 Attempt。
WP06 负责将这个 consumer 接入产品启动/查询控制链；本包不修改其 transport。

## 受保护的 lifecycle

`ProtectedLifecycleExecutor(job_id=..., store=..., authorize=...)` 只接受固定
`service.restart` / `service.exit`。trusted host callback 实时核验授权，返回从 canonical
Job/Run/Step 验证的 scope：`jobId/runId/stepId/operation/root/port/stepCount=1`。
callback 不能只是回显不可信请求；产品 grant 和 canonical 单步骤检查由 WP06 接线。
普通 `TypedExecutor` 仍拒绝任意 high_risk，payload 的 system/source/授权声明不能放行。
`service.lifecycle` 不支持的平台返回 `capability_unsupported`，不启动 supervisor。

| phase | 取消与结算 |
|---|---|
| admitted / preparing | 可在 SQLite 事务中证明 effect 尚未开始，取消写 cancelled receipt |
| stopping | 先持久关闭取消窗口，再验证调用 launcher stop；此后拒绝可撤销承诺 |
| stopped | 同时保存 stop receipt 与 offline proof；stop 的 stopped=true 单独不足以成功 |
| starting / checking_ready | restart 专用；只查询原 supervisor，不能恢复时重做 stop/start |
| ready | 新 PID + birth、checkout root、目标 port/listener、identity 和 HTTP ready 全部成立才成功 |
| offline | exit 专用；旧 PID 已死/复用且目标 port 确认无 listener 才成功；探针失败保持 unknown |
| uncertain | 证据不全/异常/失联；保持原 effect 和 outbox，人工或显式 reconcile |

supervisor phase、launch receipt、external messageId/processIdentity、终态与 outbox 持久可查。
terminal fact 与 outbox 同一事务；SQLite 写失败不能形成已完成但无 outbox 的状态。
旧生命周期 supervisor/writer 以及 inventory §5.3 中活资源的 owner/取消入口原样保留。
未触碰真实 profile，不宣称 activeLegacy=0、外部资源已排空或可以删除旧宿主。

## 证据与剩余边界

FE02/FE05 新增契约位于 `tests/test_job_final_independent_execution.py`：
并发准入、fence/claim、intent/outbox 原子性、显式 Retry、unknown 禁止重放、离线 reload、
真实独立 worker 与 admitting host 进程退出、messageId、HTTP 查询/ack、取消窗口、
restart ready 缺项、exit offline、探针异常、privilege/capability 与 bootstrap exit proof。
FE01/FE03/FE04 沿用 `test_job_final_executors.py` 与 ProcessOwner 契约测试。

额外回归发现 `test_job_delete_main_combination.py::test_authorized_action_delete_preserves_active_snapshot_and_legacy_guard`
在候选及原基线均失败（uncertain finalizer 后 DELETE 没有 deleted 字段）。保留红色证据，
不降低断言、不更改既有删除保护以变绿；该项不作为本包已通过的证据。

全 `tests/ --collect-only` 在候选/基线均因当前 Python 缺少 playwright 出现相同 4 个
导入错误；不宣称全量收集或全量后端回归通过。受影响回归与平台导入链门禁合计
375 passed / 20 skipped，其中新增契约 50 passed。导入链含模拟 POSIX，非实机证据。

本包未执行真实 main restart/exit、外部 Task Scheduler host 停机验收、真实 Session/QQ
发送、真实数据迁移/全 profile 盘点、React 构建/浏览器、POSIX 实机或远端三平台 CI、部署、
push 和人工验收。最终命令、计数、源码 SHA 与本地 JUnit 摘要见交付 manifest。
