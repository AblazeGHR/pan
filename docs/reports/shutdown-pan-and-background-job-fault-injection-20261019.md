# Pan + Background Job 同时强杀故障注入

本次在隔离分支 `review/legal-offline-illegal-lock-20261018` 完成，源码 HEAD `68771ba63274dd54fa4e2ba3ce9db177f19f0917`。没有使用 8768 或现有 Pan 服务；服务使用动态端口 `51666`，数据根为工作树下的 `audit/shutdown-job-live-20261019`，target Session 为 `ses_a080eccb792e214b`，Job 为 `job_9af540402c4e5e0acdb45bae`。

## 注入步骤

1. 启动隔离 Pan 服务和本地 deterministic fake CLI。
2. 创建 `real-fifo-shutdown-job` Session，启动 Worker。
3. 入队 `hold-A`，等待 fake CLI 在真实 workdir 收到该消息并阻塞，确认 Session 摘要为 `workerStatus=running`、`legalOffline=false`、`illegalOffline=false`。
4. 通过真实 `POST /api/background-jobs` 创建长时间 background-process Job：本地 Python `time.sleep(86400)`，target/creator 都是该 running Session。记录 Job runner、Job process、Pan 进程树的 PID/create-time。
5. 在同一个 barrier 同时强杀 Pan 进程树和 background runner/Job process。没有调用 graceful Exit、Cancel 或 Restart API。
6. 用同一源码、同一数据根重新启动隔离 Pan，等待启动恢复完成，读取 Job registry、Session API、Session JSON、history、queue delivery ledger 和 fake CLI 日志。

## 最终结果

### Job 本身

重启后的 Job 记录：

- `status=failed`
- `error=orphaned: runner identity missing, unavailable, dead, or PID reused`
- `exitCode=null`（强杀没有正常退出码）
- `notificationState=delivered`
- `terminalEventId=job_9af540402c4e5e0acdb45bae:terminal`

Job 日志只包含 `JOB_LONG_STARTED`，说明它确实进入长时间运行，没有自然完成。

### Failure 提醒的路径

这次故障注入明确证明，Job failure 提醒**先进入了持久队列，然后由重启后的新 Worker 发送到 CLI**：

1. 启动恢复检测 Job runner 身份丢失，Job registry 先持久化为 failed。
2. Pan 生成一条 `noticeKind=background_job_terminal` 的 automation/report queue item，固定 `eventId=job_9af540402c4e5e0acdb45bae:terminal`。
3. Session history 中保存了该 queue item 的 `queueItemId=q_f5a6b7c678d74c7cae7f88e8a36a819a`，`delivered_keys` 已记录 report delivery key。
4. 最终 Session JSON 的 `queue_delivery_ledger` 对该 item 是：`deliveryState=sent_to_cli`、`dispatchState=sent_to_cli`，并有 `reservedBy=worker-1`、`reservedGeneration=0`、`receiptAt`、`deliveredAt`。
5. fake CLI 日志显示重启后新 PID `29876` 实际收到完整 failure 文本；原 Worker PID `12372` 只收到最初的 `hold-A`。这排除了“Pan 只更新状态、实际没有写 stdin”的情况。
6. history 中同时有 report user envelope 和 fake CLI 的 `ack:...` assistant 响应；`terminal_results` 记录该响应为 `status=done`。

因此，这条提醒不是“Illegal 结算后的普通新消息直接绕过队列”，也不是 Pan 虚报已发送。它是**故障恢复后产生的 automation report，持久入队，再由 successor Worker 真实交付到 CLI**。

### Illegal / successor 状态

初始强杀前 Session 是 Running 且未判 Illegal。重启对账后：

- 旧 foreground operation 形成 `illegal_batches`，原因 `unfinished-run`。
- 新 Worker 取得有效 successor owner 后，该 batch 的 `recovered=true`，旧事故责任保留 tombstone，旧事故锁成员为空（本次没有待锁消息）。
- Session 最终 `legalOffline=true`、`illegalOffline=false`，Worker 安全进入 Idle。
- queue_pending 最终为空；failure report 的 ledger 是 sent_to_cli，而不是丢失或未确认。
- historyTotal 从 1 增至 4，包含 failure report 和 CLI 响应。

## 证据文件

`audit/shutdown-job-live-20261019/` 保留：

- `before.json`：故障前 Session、Job、Pan/runner/Job PID 身份和 create-time。
- `result.json`：重启后的 Job、Session、账本和 CLI 记录摘要。
- `fake-cli.jsonl`：原 Worker 收到 `hold-A`，重启后的 successor PID 收到完整 failure report。
- `background_jobs/jobs/job_9af540402c4e5e0acdb45bae.json`：Job 最终 failed/delivered 状态。
- `background_jobs/logs/job_9af540402c4e5e0acdb45bae.log`：长任务实际启动记录。
- `sessions/ses_a080eccb792e214b.json` 和 history：Illegal batch、delivery ledger、history/terminal proof。
- `pan-first.log`、`pan-restart.log`：两次隔离服务生命周期日志。

测试后检查没有该数据根相关的 Pan、runner、Job 或 fake CLI 进程残留。该数据根是本工作树的审计产物，没有接触 practical 数据或现有服务。

## 边界

这次模拟的是 Pan 服务和其拥有的 background runner/Job process 同时被强杀，随后同一版本启动恢复。没有模拟 Windows SCM 的额外进程包装层，也没有使用真实远端模型。结果证明了本路径的持久 Job failure、queue 入队和 successor CLI 交付；不能把它外推为所有外部客户端/未观测 provider 的逐消息可见性保证。
